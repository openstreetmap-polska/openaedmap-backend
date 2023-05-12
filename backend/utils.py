import functools
import time
from logging import Logger
from typing import Callable


def print_runtime(logger: Logger) -> Callable:
    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapped(*args, **kwargs):
            start_time = time.perf_counter()
            result = f(*args, **kwargs)
            end_time = time.perf_counter()
            duration = end_time - start_time
            logger.info(f"Function {f.__name__!r} took: {duration:.4f} seconds.")
            return result

        return wrapped

    return decorator
