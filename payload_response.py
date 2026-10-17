import re
from datetime import UTC, datetime
from email.utils import format_datetime

from starlette.requests import Request
from starlette.responses import Response

from models.payload import Payload

_ENTITY_TAG = re.compile(r'(?:W/)?"[\x21\x23-\x7e\x80-\xff]*"')


def is_not_modified(request: Request, etag: str) -> bool:
    condition = request.headers.get('if-none-match')
    if condition is None:
        return False
    return condition.strip() == '*' or any(
        match[0].removeprefix('W/') == etag.removeprefix('W/') for match in _ENTITY_TAG.finditer(condition)
    )


def payload_response(request: Request, payload: Payload) -> Response:
    headers = {
        'ETag': payload.etag,
        'Date': format_datetime(payload.date, usegmt=True),
        'Age': str(max(0, int((datetime.now(UTC) - payload.date).total_seconds()))),
        'Vary': 'Accept-Encoding',
    }
    if is_not_modified(request, payload.etag):
        return Response(status_code=304, headers=headers)
    return Response(payload.content, headers=headers, media_type=payload.content_type)
