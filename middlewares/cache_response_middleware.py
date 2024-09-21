import logging
from datetime import UTC, datetime, timedelta
from io import BytesIO

from sentry_sdk import trace
from starlette.datastructures import URL, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from zstandard import ZstdCompressor, ZstdDecompressor

from db import valkey
from middlewares.cache_control_middleware import make_cache_control, parse_cache_control
from models.cached_response import CachedResponse

_compress = ZstdCompressor(level=1).compress
_decompress = ZstdDecompressor().decompress


class CacheResponseMiddleware:
    """
    Cache responses based on Cache-Control header.
    """

    __slots__ = ('app',)

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        if scope['method'] not in ('GET', 'HEAD'):
            await self.app(scope, receive, send)
            return

        url = URL(scope=scope)
        cached = await _get_cached_response(url)
        maybe_send: Send | None = send

        if cached is not None:
            if await _deliver_cached_response(cached, send):
                # served fresh response
                return
            else:
                # served stale response, refresh cache
                maybe_send = None

        await CachingResponder(self.app, url)(scope, receive, maybe_send)


async def _deliver_cached_response(cached: CachedResponse, send: Send) -> bool:
    now = datetime.now(UTC)
    headers = MutableHeaders(raw=cached.headers)
    headers['Age'] = str(int((now - cached.date).total_seconds()))

    if now < (cached.date + cached.max_age):
        headers['X-Cache'] = 'HIT'
        await send(
            {
                'type': 'http.response.start',
                'status': cached.status_code,
                'headers': headers.raw,
            }
        )
        await send(
            {
                'type': 'http.response.body',
                'body': cached.content,
            }
        )
        return True

    else:
        headers['Cache-Control'] = make_cache_control(max_age=timedelta(), stale=cached.stale)
        headers['X-Cache'] = 'STALE'
        await send(
            {
                'type': 'http.response.start',
                'status': cached.status_code,
                'headers': headers.raw,
            }
        )
        await send(
            {
                'type': 'http.response.body',
                'body': cached.content,
            }
        )
        return False


class CachingResponder:
    __slots__ = ('app', 'url', 'send', 'cached', 'body_buffer')

    def __init__(self, app: ASGIApp, url: URL) -> None:
        self.app = app
        self.url = url
        self.send: Send | None = None
        self.cached: CachedResponse | None = None
        self.body_buffer: BytesIO = BytesIO()

    async def __call__(self, scope: Scope, receive: Receive, send: Send | None) -> None:
        self.send = send
        await self.app(scope, receive, self.wrapper)

    async def wrapper(self, message: Message) -> None:
        if self.send is not None:
            await self.send(message)

        message_type: str = message['type']
        if message_type == 'http.response.start':
            self.satisfy_response_start(message)
            return

        # skip if not satisfied
        if self.cached is None:
            return

        # skip unknown messages
        if message_type != 'http.response.body':
            logging.warning('Unsupported ASGI message type %r', message_type)
            return

        body: bytes = message.get('body', b'')
        more_body: bool = message.get('more_body', False)

        self.body_buffer.write(body)

        if not more_body:
            self.cached.content = self.body_buffer.getvalue()
            self.body_buffer.close()
            await _set_cached_response(self.url, self.cached)

    def satisfy_response_start(self, message: Message) -> None:
        headers = MutableHeaders(raw=message['headers'])
        cache_control: str | None = headers.get('Cache-Control')
        if not cache_control:
            return

        headers['Age'] = '0'
        headers['X-Cache'] = 'MISS'
        max_age, stale = parse_cache_control(cache_control)

        self.cached = CachedResponse(
            date=datetime.now(UTC),
            max_age=max_age,
            stale=stale,
            status_code=message['status'],
            headers=headers.raw,
            content=b'',
        )


@trace
async def _get_cached_response(url: URL) -> CachedResponse | None:
    key = f'cache:{url.path}:{url.query}'

    async with valkey() as conn:
        value: bytes | None = await conn.get(key)

    if value is None:
        return None

    logging.debug('Found cached response for %r', key)
    return CachedResponse.from_bytes(_decompress(value))


@trace
async def _set_cached_response(url: URL, cached: CachedResponse) -> None:
    key = f'cache:{url.path}:{url.query}'
    value = _compress(cached.to_bytes())
    ttl = int((cached.max_age + cached.stale).total_seconds())

    logging.debug('Caching response for %r', key)

    async with valkey() as conn:
        await conn.set(key, value, ex=ttl)
