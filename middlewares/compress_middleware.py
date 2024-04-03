import gzip
import logging
import re
from functools import lru_cache
from io import BytesIO

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Encoding
_accept_encoding_re = re.compile(r'[a-z]{2,8}')


@lru_cache(maxsize=128)
def parse_accept_encoding(accept_encoding: str) -> frozenset[str]:
    """
    Parse the accept encoding header.

    Returns a set of encodings.

    >>> _parse_accept_encoding('br;q=1.0, gzip;q=0.8, *;q=0.1')
    {'br', 'gzip'}
    """
    return frozenset(_accept_encoding_re.findall(accept_encoding))


def _is_response_start_satisfied(message: Message) -> bool:
    status_code: int = message['status']
    if status_code != 200:
        return False
    headers = Headers(raw=message['headers'])
    if 'Content-Encoding' in headers:
        return False
    content_type: str | None = headers.get('Content-Type')
    if (content_type is not None) and not content_type.startswith(('text/', 'application/')):
        return False
    return True


def _is_response_body_satisfied(body: bytes, more_body: bool) -> bool:
    return more_body or len(body) >= 1024


class CompressMiddleware:
    """
    Response compressing middleware.
    """

    __slots__ = ('app',)

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        request_headers = Headers(scope=scope)
        accept_encoding = request_headers.get('Accept-Encoding')

        if not accept_encoding:
            await self.app(scope, receive, send)
            return

        accept_encodings = parse_accept_encoding(accept_encoding)

        if 'gzip' in accept_encodings:
            await GZipResponder(self.app)(scope, receive, send)
        else:
            await self.app(scope, receive, send)


class GZipResponder:
    __slots__ = ('app', 'send', 'initial_message', 'gzip_compressor', 'gzip_buffer')

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.send: Send = None
        self.initial_message: Message | None = None
        self.gzip_compressor: gzip.GzipFile | None = None
        self.gzip_buffer: BytesIO | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.send = send
        await self.app(scope, receive, self.wrapper)

    async def wrapper(self, message: Message) -> None:
        message_type: str = message['type']
        if message_type == 'http.response.start':
            if _is_response_start_satisfied(message):
                # delay initial message until response body is satisfied
                self.initial_message = message
            else:
                await self.send(message)
            return

        # skip further processing if not satisfied
        if self.initial_message is None:
            await self.send(message)
            return

        # skip unknown messages
        if message_type != 'http.response.body':
            logging.warning('Unsupported ASGI message type %r', message_type)
            await self.send(message)
            return

        body: bytes = message.get('body', b'')
        more_body: bool = message.get('more_body', False)

        if self.gzip_compressor is None:
            if not _is_response_body_satisfied(body, more_body):
                await self.send(self.initial_message)
                await self.send(message)
                self.initial_message = None  # skip further processing
                return

            headers = MutableHeaders(raw=self.initial_message['headers'])
            headers['Content-Encoding'] = 'gzip'
            headers.add_vary_header('Accept-Encoding')

            if not more_body:
                # one-shot
                compressed_body = gzip.compress(body, compresslevel=3)
                headers['Content-Length'] = str(len(compressed_body))
                message['body'] = compressed_body
                await self.send(self.initial_message)
                await self.send(message)
                return

            # streaming
            del headers['Content-Length']
            await self.send(self.initial_message)
            self.gzip_buffer = gzip_buffer = BytesIO()
            self.gzip_compressor = gzip.GzipFile(mode='wb', fileobj=gzip_buffer, compresslevel=3)
        else:
            # read property once for performance
            gzip_buffer = self.gzip_buffer

        # streaming
        self.gzip_compressor.write(body)
        if not more_body:
            self.gzip_compressor.close()
        compressed_body = gzip_buffer.getvalue()
        if more_body:
            if compressed_body:
                gzip_buffer.seek(0)
                gzip_buffer.truncate()
            else:
                return
        await self.send({'type': 'http.response.body', 'body': compressed_body, 'more_body': more_body})
