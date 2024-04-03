from typing import override

from fastapi.responses import JSONResponse

from utils import JSON_ENCODE


class CustomJSONResponse(JSONResponse):
    media_type = 'application/json; charset=utf-8'

    @override
    def render(self, content) -> bytes:
        return JSON_ENCODE(content)
