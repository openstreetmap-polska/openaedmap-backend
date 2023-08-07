import functools
from datetime import timedelta

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


def make_cache_control(max_age: timedelta, stale: timedelta):
    return f'public, max-age={int(max_age.total_seconds())}, stale-while-revalidate={int(stale.total_seconds())}'


class CacheMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, max_age: timedelta, stale: timedelta):
        super().__init__(app)
        self.max_age = max_age
        self.stale = stale

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        try:
            max_age = request.state.max_age
        except AttributeError:
            max_age = self.max_age

        try:
            stale = request.state.stale
        except AttributeError:
            stale = self.stale

        if 'Cache-Control' not in response.headers:
            response.headers['Cache-Control'] = make_cache_control(max_age, stale)

        return response


def configure_cache(max_age: timedelta, stale: timedelta):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            request: Request = kwargs['request']
            request.state.max_age = max_age
            request.state.stale = stale
            return await func(*args, **kwargs)
        return wrapper
    return decorator
