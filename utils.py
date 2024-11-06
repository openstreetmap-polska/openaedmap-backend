import logging
import time
from datetime import timedelta
from functools import wraps

import anyio
from httpx import AsyncClient, Timeout

from config import USER_AGENT

HTTP = AsyncClient(
    headers={'User-Agent': USER_AGENT},
    timeout=Timeout(60, connect=15),
    follow_redirects=True,
)


def retry_exponential(timeout: timedelta | float | None, *, start: float = 1):
    if timeout is None:
        timeout_seconds = float('inf')
    elif isinstance(timeout, timedelta):
        timeout_seconds = timeout.total_seconds()
    else:
        timeout_seconds = timeout

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            ts = time.perf_counter()
            sleep = start

            while True:
                try:
                    return await func(*args, **kwargs)
                except Exception:
                    logging.warning('%s failed', func.__qualname__, exc_info=True)
                    if (time.perf_counter() + sleep) - ts > timeout_seconds:
                        raise
                    await anyio.sleep(sleep)
                    sleep = min(sleep * 2, 4 * 3600)  # max 4 hours

        return wrapper

    return decorator


def abbreviate(num: int) -> str:
    for suffix, divisor in (('m', 1_000_000), ('k', 1_000)):
        if num >= divisor:
            return f'{num / divisor:.1f}{suffix}'
    return str(num)


def get_wikimedia_commons_url(path: str) -> str:
    return f'https://commons.wikimedia.org/wiki/{path}'
