from contextvars import ContextVar
from datetime import timedelta
from functools import lru_cache, wraps

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_cache_context: ContextVar[list[str]] = ContextVar('Cache_context')


@lru_cache(128)
def make_cache_control(max_age: timedelta, stale: timedelta):
    return f'public, max-age={int(max_age.total_seconds())}, stale-while-revalidate={int(stale.total_seconds())}'


def parse_cache_control(header: str) -> tuple[timedelta, timedelta]:
    max_age = None
    stale = None

    for part in header.split(','):
        part = part.strip()
        if part.startswith('max-age='):
            max_age = timedelta(seconds=int(part[8:]))
        elif part.startswith('stale-while-revalidate='):
            stale = timedelta(seconds=int(part[23:]))

    if max_age is None or stale is None:
        raise NotImplementedError(f'Unsupported Cache-Control header: {header}')

    return max_age, stale


class CacheControlMiddleware:
    """
    Add Cache-Control header from `@cache_control` decorator.
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

        async def wrapper(message: Message) -> None:
            if message['type'] == 'http.response.start':
                status_code: int = message['status']

                if (200 <= status_code < 300 or status_code == 301) and context:
                    headers = MutableHeaders(raw=message['headers'])
                    headers.setdefault('Cache-Control', context[0])

            await send(message)

        context = []
        token = _cache_context.set(context)
        try:
            await self.app(scope, receive, wrapper)
        finally:
            _cache_context.reset(token)


def cache_control(max_age: timedelta, stale: timedelta):
    """
    Decorator to set the Cache-Control header for an endpoint.
    """

    header = make_cache_control(max_age, stale)

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            context = _cache_context.get(None)
            if context is not None:
                context.append(header)
            return await func(*args, **kwargs)

        return wrapper

    return decorator
