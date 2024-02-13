import functools
import time
import traceback
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta
from typing import Any

import anyio
import httpx
from shapely.geometry import mapping

from config import USER_AGENT


@contextmanager
def print_run_time(message: str | list) -> Generator[None, None, None]:
    start_time = time.perf_counter()
    try:
        yield
    finally:
        end_time = time.perf_counter()
        elapsed_time = end_time - start_time

        # support message by reference
        if isinstance(message, list):
            message = message[0]

        print(f'[⏱️] {message} took {elapsed_time:.3f}s')


def retry_exponential(timeout: timedelta | None, *, start: float = 1):
    timeout_seconds = float('inf') if timeout is None else timeout.total_seconds()

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            ts = time.perf_counter()
            sleep = start

            while True:
                try:
                    return await func(*args, **kwargs)
                except Exception:
                    print(f'[⛔] {func.__name__} failed')
                    traceback.print_exc()
                    if (time.perf_counter() + sleep) - ts > timeout_seconds:
                        raise
                    await anyio.sleep(sleep)
                    sleep = min(sleep * 2, 4 * 3600)  # max 4 hours

        return wrapper

    return decorator


def get_http_client(base_url: str = '', *, auth: Any = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        auth=auth,
        base_url=base_url,
        headers={'User-Agent': USER_AGENT},
        timeout=httpx.Timeout(60, connect=15),
        http1=True,
        http2=True,
        follow_redirects=True,
    )


def abbreviate(num: int) -> str:
    for suffix, divisor in (('m', 1_000_000), ('k', 1_000)):
        if num >= divisor:
            return f'{num / divisor:.1f}{suffix}'

    return str(num)


def as_dict(data) -> dict:
    d = asdict(data)

    for k, v in d.items():
        if hasattr(v, '__geo_interface__'):
            d[k] = mapping(v)

    return d


def get_wikimedia_commons_url(path: str) -> str:
    return f'https://commons.wikimedia.org/wiki/{path}'
