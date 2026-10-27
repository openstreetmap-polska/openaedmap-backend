import logging
import time
from asyncio import CancelledError, create_task, shield, sleep, to_thread
from collections.abc import Awaitable, Callable
from datetime import timedelta
from functools import wraps


async def run_blocking[**P, T](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Keep ownership of file/native work until it actually stops after cancellation."""
    work = create_task(to_thread(function, *args, **kwargs))
    try:
        return await shield(work)
    except CancelledError:
        while not work.done():
            try:
                await shield(work)
            except CancelledError:
                continue
            except Exception:
                break
        if not work.cancelled():
            work.exception()
        raise


def retry_exponential(timeout: timedelta | float | None, *, start: float = 1):
    if timeout is None:
        timeout_seconds = float('inf')
    elif isinstance(timeout, timedelta):
        timeout_seconds = timeout.total_seconds()
    else:
        timeout_seconds = timeout

    def decorator[**P, T](func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        name = getattr(func, '__qualname__', type(func).__qualname__)

        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            ts = time.perf_counter()
            sleep_time = start

            while True:
                try:
                    return await func(*args, **kwargs)
                except Exception:
                    logging.warning('%s failed', name, exc_info=True)
                    if (time.perf_counter() + sleep_time) - ts > timeout_seconds:
                        raise
                    await sleep(sleep_time)
                    sleep_time = min(sleep_time * 2, 4 * 3600)  # max 4 hours

        return wrapper

    return decorator


def get_wikimedia_commons_url(path: str) -> str:
    return f'https://commons.wikimedia.org/wiki/{path}'
