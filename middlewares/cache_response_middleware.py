import logging
from compression.zstd import compress, decompress
from datetime import UTC, datetime, timedelta
from io import BytesIO

from sentry_sdk import trace
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# private, but the alternative is duplicating q-value parsing here: a naive
# token scan reads `br;q=0, gzip` as br-capable and would poison the variant
from starlette_compress._utils import parse_accept_encoding

from db import valkey
from middlewares.cache_control_middleware import make_cache_control, parse_cache_control
from models.cached_response import CachedResponse


class CacheResponseMiddleware:
    """
    Cache responses based on Cache-Control header.

    Wraps CompressMiddleware, so entries are stored already content-encoded and
    a hit costs no compression. The key carries the encodings the client will
    accept, because the stored body only satisfies those.
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

        key = _cache_key(scope)
        cached = await _get_cached_response(key)
        maybe_send: Send | None = send

        if cached is not None:
            if await _deliver_cached_response(cached, send):
                # served fresh response
                return
            else:
                # served stale response, refresh cache
                maybe_send = None

        await CachingResponder(self.app, key)(scope, receive, maybe_send)


def _cache_key(scope: Scope) -> str:
    accept_encoding = ','.join(
        value.decode('latin-1') for name, value in scope['headers'] if name == b'accept-encoding'
    )
    # the parsed set is a subset of the encodings CompressMiddleware supports, so
    # the variant space stays bounded no matter what a client sends
    encodings = parse_accept_encoding(accept_encoding) if accept_encoding else None
    variant = '+'.join(sorted(encodings)) if encodings else 'identity'
    return f'cache3:{variant}:{scope["path"]}:{scope["query_string"].decode()}'


async def _deliver_cached_response(cached: CachedResponse, send: Send) -> bool:
    now = datetime.now(UTC)
    headers = MutableHeaders(raw=cached.headers)
    headers['Age'] = str(int((now - cached.date).total_seconds()))

    if now < (cached.date + cached.max_age):
        headers['X-Cache'] = 'HIT'
        await send({
            'type': 'http.response.start',
            'status': cached.status_code,
            'headers': headers.raw,
        })
        await send({
            'type': 'http.response.body',
            'body': cached.content,
        })
        return True

    else:
        headers['Cache-Control'] = make_cache_control(max_age=timedelta(), stale=cached.stale)
        headers['X-Cache'] = 'STALE'
        await send({
            'type': 'http.response.start',
            'status': cached.status_code,
            'headers': headers.raw,
        })
        await send({
            'type': 'http.response.body',
            'body': cached.content,
        })
        return False


class CachingResponder:
    __slots__ = ('app', 'body_buffer', 'cached', 'key', 'send')

    def __init__(self, app: ASGIApp, key: str) -> None:
        self.app = app
        self.key = key
        self.send: Send | None = None
        self.cached: CachedResponse | None = None
        self.body_buffer: BytesIO = BytesIO()

    async def __call__(self, scope: Scope, receive: Receive, send: Send | None) -> None:
        self.send = send
        await self.app(scope, receive, self.wrapper)

    async def wrapper(self, message: Message) -> None:
        # capture before forwarding: the middlewares above rewrite messages in place
        complete = self.capture(message)

        if self.send is not None:
            await self.send(message)

        if complete is not None:
            await _set_cached_response(self.key, complete)

    def capture(self, message: Message) -> CachedResponse | None:
        """
        Record the encoded response, returning it once it is complete.
        """
        message_type: str = message['type']
        if message_type == 'http.response.start':
            self.satisfy_response_start(message)
            return None

        # skip if not satisfied
        if self.cached is None:
            return None

        if message_type == 'http.response.pathsend':
            # The file is already local and cheap to read; avoid duplicating it in Valkey.
            self.cached = None
            self.body_buffer.close()
            return None

        # skip unknown messages
        if message_type != 'http.response.body':
            logging.warning('Unsupported ASGI message type %r', message_type)
            return None

        body: bytes = message.get('body', b'')
        more_body: bool = message.get('more_body', False)

        self.body_buffer.write(body)

        if more_body:
            return None

        cached = self.cached
        cached.content = self.body_buffer.getvalue()
        self.body_buffer.close()
        return cached

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
            # snapshot: the middlewares above rewrite this list in place
            headers=headers.raw.copy(),
            content=b'',
        )


@trace
async def _get_cached_response(key: str) -> CachedResponse | None:
    async with valkey() as conn:
        value: bytes | None = await conn.get(key)

    if value is None:
        return None

    logging.debug('Found cached response for %r', key)
    return CachedResponse.from_bytes(decompress(value))


@trace
async def _set_cached_response(key: str, cached: CachedResponse) -> None:
    value = compress(cached.to_bytes(), level=1)
    ttl = int((cached.max_age + cached.stale).total_seconds())

    logging.debug('Caching response for %r', key)

    async with valkey() as conn:
        await conn.set(key, value, ex=ttl)
