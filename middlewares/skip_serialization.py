import functools
from collections.abc import Mapping

from fastapi import Response

from json_response import JSONResponseUTF8


def skip_serialization(headers: Mapping[str, str] | None = None):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            raw_response = await func(*args, **kwargs)
            if isinstance(raw_response, Response):
                return raw_response
            return JSONResponseUTF8(raw_response, headers=headers)

        return wrapper

    return decorator
