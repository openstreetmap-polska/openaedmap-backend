import functools
import math
import time
import traceback
from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta
from typing import Generator

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


def retry_exponential(timeout: timedelta | None):
    if timeout is None:
        timeout_seconds = math.inf
    else:
        timeout_seconds = timeout.total_seconds()

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            ts = time.perf_counter()
            sleep = 1

            while True:
                try:
                    return await func(*args, **kwargs)
                except Exception:
                    print(f'[⛔] {func.__name__} failed')
                    traceback.print_exc()
                    if (time.perf_counter() + sleep) - ts > timeout_seconds:
                        raise
                    await anyio.sleep(sleep)
                    sleep *= 2

        return wrapper
    return decorator


def get_http_client(base_url: str = '') -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        headers={'User-Agent': USER_AGENT},
        timeout=httpx.Timeout(60, connect=15),
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
