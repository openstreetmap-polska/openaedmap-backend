import logging
from asyncio import AbstractEventLoop, Future, get_running_loop, shield
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from io import BytesIO
from weakref import WeakKeyDictionary

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# private, but the alternative is duplicating q-value parsing here: a naive
# token scan reads `br;q=0, gzip` as br-capable and would poison the variant
from starlette_compress._utils import parse_accept_encoding
from valkey.asyncio import Valkey

from db import valkey
from middlewares.cache_control_middleware import parse_cache_control
from models.cached_response import CachedResponse
from services.response_cache import ResponseCacheEntry

type CacheConnection = Callable[[], AbstractAsyncContextManager[Valkey]]


class _FlightResult(Enum):
    RETRY = auto()
    UNCACHEABLE = auto()


@dataclass(slots=True)
class _Flight:
    cached: Future[CachedResponse | None]
    result: Future[CachedResponse | _FlightResult]


class CacheResponseMiddleware:
    """
    Cache responses based on Cache-Control header.

    Wraps CompressMiddleware, so entries are stored already content-encoded and
    a hit costs no compression. The key carries the encodings the client will
    accept, because the stored body only satisfies those.
    """

    __slots__ = ('app', 'cache_connection', 'flights')

    def __init__(self, app: ASGIApp, *, cache_connection: CacheConnection = valkey) -> None:
        self.app = app
        self.cache_connection = cache_connection
        self.flights: WeakKeyDictionary[AbstractEventLoop, dict[str, _Flight]] = WeakKeyDictionary()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        if scope['method'] not in ('GET', 'HEAD'):
            await self.app(scope, receive, send)
            return

        key = _cache_key(scope)
        if scope['method'] == 'HEAD':
            async with self.cache_connection() as client:
                cached = await ResponseCacheEntry(client, key).get()
            if cached is not None:
                await _deliver_cached_response(cached, send, head=True)
            else:
                await self.app(scope, receive, send)
            return

        loop = get_running_loop()
        flights = self.flights.setdefault(loop, {})
        while (flight := flights.get(key)) is not None:
            cached = await shield(flight.cached)
            if cached is not None and datetime.now(UTC) < cached.expires_at:
                await _deliver_cached_response(cached, send)
                return
            result = await shield(flight.result)
            if isinstance(result, CachedResponse) and datetime.now(UTC) < result.expires_at:
                await _deliver_cached_response(result, send)
                return
            if result is not _FlightResult.RETRY:
                await self.app(scope, receive, send)
                return

        flight = flights[key] = _Flight(loop.create_future(), loop.create_future())
        sent_stale = False
        try:
            async with self.cache_connection() as client:
                entry = ResponseCacheEntry(client, key)
                cached = await entry.get()
                flight.cached.set_result(cached)
                if cached is not None:
                    if await _deliver_cached_response(cached, send):
                        return
                    sent_stale = True

                async with entry.regenerate(wait=not sent_stale) as regenerate:
                    if not regenerate:
                        return
                    # Another worker may have populated the cache while we waited.
                    cached = await entry.get()
                    if cached is not None and datetime.now(UTC) < cached.fresh_until:
                        if not sent_stale:
                            flight.result.set_result(cached)
                            await _deliver_cached_response(cached, send)
                        return
                    responder = CachingResponder(self.app, flight.result)
                    await responder(scope, receive, None if sent_stale else send)
                    if responder.complete is not None:
                        await entry.store(responder.complete)
                    elif not flight.result.done():
                        flight.result.set_result(_FlightResult.UNCACHEABLE)
        except Exception:
            if not sent_stale:
                raise
            logging.exception('Failed to refresh a stale response')
        finally:
            if not flight.cached.done():
                flight.cached.set_result(None)
            if not flight.result.done():
                flight.result.set_result(_FlightResult.RETRY)
            del flights[key]


def _cache_key(scope: Scope) -> str:
    accept_encoding = ','.join(
        value.decode('latin-1') for name, value in scope['headers'] if name == b'accept-encoding'
    )
    # the parsed set is a subset of the encodings CompressMiddleware supports, so
    # the variant space stays bounded no matter what a client sends
    encodings = parse_accept_encoding(accept_encoding) if accept_encoding else None
    variant = '+'.join(sorted(encodings)) if encodings else 'identity'
    return f'cache3:{variant}:{scope["path"]}:{scope["query_string"].decode()}'


async def _deliver_cached_response(cached: CachedResponse, send: Send, *, head: bool = False) -> bool:
    now = datetime.now(UTC)
    headers = MutableHeaders(raw=cached.headers.copy())
    headers['Age'] = str(int((now - cached.date).total_seconds()))

    fresh = now < cached.fresh_until
    headers['X-Cache'] = 'HIT' if fresh else 'STALE'
    if not fresh:
        headers['Cache-Control'] = ', '.join(
            'max-age=0' if part.strip().partition('=')[0].lower() == 'max-age' else part.strip()
            for part in headers['Cache-Control'].split(',')
        )
    await send({
        'type': 'http.response.start',
        'status': cached.status_code,
        'headers': headers.raw,
    })
    await send({
        'type': 'http.response.body',
        'body': b'' if head else cached.content,
    })
    return fresh


class CachingResponder:
    __slots__ = ('app', 'body_buffer', 'cached', 'complete', 'flight', 'send')

    def __init__(self, app: ASGIApp, flight: Future[CachedResponse | _FlightResult]) -> None:
        self.app = app
        self.flight = flight
        self.complete: CachedResponse | None = None
        self.send: Send | None = None
        self.cached: CachedResponse | None = None
        self.body_buffer: BytesIO = BytesIO()

    async def __call__(self, scope: Scope, receive: Receive, send: Send | None) -> None:
        self.send = send
        try:
            await self.app(scope, receive, self.wrapper)
        finally:
            self.body_buffer.close()

    async def wrapper(self, message: Message) -> None:
        # capture before forwarding: the middlewares above rewrite messages in place
        complete = self.capture(message)
        if complete is not None:
            self.complete = complete
            if not self.flight.done():
                self.flight.set_result(complete)

        if self.send is not None:
            await self.send(message)

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
        if not cache_control or 'set-cookie' in headers:
            return
        directives = {part.strip().split('=', 1)[0].lower() for part in cache_control.split(',')}
        if directives & {'private', 'no-store', 'no-cache'}:
            return
        if not (200 <= message['status'] < 300 or message['status'] == 301):
            return

        try:
            max_age, stale = parse_cache_control(cache_control)
        except ValueError, NotImplementedError:
            return
        if max_age < timedelta() or stale < timedelta() or max_age + stale <= timedelta():
            return
        headers['Age'] = '0'
        headers['X-Cache'] = 'MISS'

        self.cached = CachedResponse(
            date=datetime.now(UTC),
            max_age=max_age,
            stale=stale,
            status_code=message['status'],
            # snapshot: the middlewares above rewrite this list in place
            headers=headers.raw.copy(),
            content=b'',
        )
