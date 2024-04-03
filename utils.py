import functools
import logging
import time
from datetime import timedelta

import anyio
import msgspec
from httpx import AsyncClient, Timeout

from config import USER_AGENT


def typed_msgpack_decoder(t: type | None) -> msgspec.msgpack.Decoder:
    """
    Create a MessagePack decoder which returns a specific type.
    """
    return msgspec.msgpack.Decoder(t) if (t is not None) else msgspec.msgpack.Decoder()


def typed_json_decoder(t: type | None) -> msgspec.json.Decoder:
    """
    Create a JSON decoder which returns a specific type.
    """
    return msgspec.json.Decoder(t) if (t is not None) else msgspec.json.Decoder()


MSGPACK_ENCODE = msgspec.msgpack.Encoder(decimal_format='number', uuid_format='bytes').encode
MSGPACK_DECODE = typed_msgpack_decoder(None).decode
JSON_ENCODE = msgspec.json.Encoder(decimal_format='number').encode
JSON_DECODE = typed_json_decoder(None).decode


def retry_exponential(timeout: timedelta | float | None, *, start: float = 1):
    if timeout is None:
        timeout_seconds = float('inf')
    elif isinstance(timeout, timedelta):
        timeout_seconds = timeout.total_seconds()
    else:
        timeout_seconds = timeout

    def decorator(func):
        @functools.wraps(func)
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


def get_http_client(base_url: str = '', *, auth=None) -> AsyncClient:
    return AsyncClient(
        auth=auth,
        base_url=base_url,
        headers={'User-Agent': USER_AGENT},
        timeout=Timeout(60, connect=15),
        http1=True,
        http2=True,
        follow_redirects=True,
    )


def abbreviate(num: int) -> str:
    for suffix, divisor in (('m', 1_000_000), ('k', 1_000)):
        if num >= divisor:
            return f'{num / divisor:.1f}{suffix}'
    return str(num)


def get_wikimedia_commons_url(path: str) -> str:
    return f'https://commons.wikimedia.org/wiki/{path}'
