from typing import override

from fastapi.responses import JSONResponse
from msgspec.json import Encoder

_encode = Encoder(decimal_format='number').encode


class CustomJSONResponse(JSONResponse):
    media_type = 'application/json; charset=utf-8'

    @override
    def render(self, content) -> bytes:
        return _encode(content)
