import asyncio
import re
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from io import BytesIO
from urllib.parse import quote_plus

import aiohttp
import httpx
import pytest
from aiointercept import CallbackResult
from fastapi import FastAPI
from multidict import CIMultiDict, CIMultiDictProxy
from PIL import Image
from yarl import URL

from tests.http import mock_http

IMAGE_URL = 'https://images.example/aed.png?signature=private-value'
DIRECT_PATH = f'/api/v1/photos/proxy/direct/{quote_plus(IMAGE_URL)}'
COMMONS_PATH = '/api/v1/photos/proxy/wikimedia-commons/File:Defibrillator.png'
REQUEST_INFO = aiohttp.RequestInfo(URL(IMAGE_URL), 'GET', CIMultiDictProxy(CIMultiDict()))


@asynccontextmanager
async def _proxy_client(handler: Callable[..., CallbackResult], *, middlewares=()) -> AsyncGenerator[httpx.AsyncClient]:
    from api.v1 import photos
    from middlewares.cache_control_middleware import CacheControlMiddleware
    from services.byte_cache import byte_cache

    byte_cache.invalidate(lambda _: True)
    app = FastAPI()
    app.include_router(photos.router, prefix='/api/v1')
    app.add_middleware(CacheControlMiddleware)
    async with mock_http(middlewares=middlewares) as upstream:
        upstream.get(re.compile(r'https://.*'), callback=handler, repeat=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://backend') as client:
            try:
                yield client
            finally:
                await byte_cache.close()


def _commons_metadata() -> CallbackResult:
    return CallbackResult(status=200, body=f'<html><meta property="og:image" content="{IMAGE_URL}"></html>')


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['direct', 'commons-metadata', 'commons-image'])
@pytest.mark.parametrize(
    ('upstream_status', 'expected_status'),
    [(401, 404), (403, 404), (404, 404), (410, 404), (429, 502), (500, 502), (503, 502)],
)
async def test_upstream_http_failures_are_handled(phase, upstream_status, expected_status):
    def handler(url, **kwargs):
        if phase == 'commons-image' and url.host == 'commons.wikimedia.org':
            return _commons_metadata()
        return CallbackResult(status=upstream_status, body='Upstream diagnostic containing private information')

    path = DIRECT_PATH if phase == 'direct' else COMMONS_PATH
    async with _proxy_client(handler) as client:
        response = await client.get(path)

    assert response.status_code == expected_status
    assert response.json() == {'detail': 'Image unavailable'}
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['direct', 'commons-metadata', 'commons-image'])
@pytest.mark.parametrize(
    ('failure', 'expected_status'),
    [
        (aiohttp.ClientConnectionError, 502),
        (TimeoutError, 504),
        (aiohttp.TooManyRedirects, 502),
        (aiohttp.InvalidURL, 400),
        (aiohttp.NonHttpUrlClientError, 400),
    ],
)
async def test_upstream_transport_failures_are_handled(phase, failure, expected_status):
    async def fail_request(request, handler):
        if phase == 'commons-image' and request.url.host == 'commons.wikimedia.org':
            return await handler(request)
        if failure is aiohttp.TooManyRedirects:
            raise failure(REQUEST_INFO, ())
        raise failure('Upstream diagnostic containing private information')

    path = DIRECT_PATH if phase == 'direct' else COMMONS_PATH
    async with _proxy_client(lambda *_args, **_kwargs: _commons_metadata(), middlewares=(fail_request,)) as client:
        response = await client.get(path)

    assert response.status_code == expected_status
    assert response.json() == {'detail': 'Image unavailable'}
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
@pytest.mark.parametrize('path', [DIRECT_PATH, COMMONS_PATH], ids=('direct', 'commons'))
async def test_valid_images_preserve_bytes_and_cache_policy(path):
    buffer = BytesIO()
    Image.new('RGB', (2, 2), color=(31, 92, 173)).save(buffer, format='PNG')
    image = buffer.getvalue()

    def handler(url, **kwargs):
        if url.host == 'commons.wikimedia.org':
            return _commons_metadata()
        return CallbackResult(status=200, body=image, headers={'Content-Type': 'image/png'})

    async with _proxy_client(handler) as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert response.content == image
    assert response.headers['content-type'] == 'image/png'
    assert response.headers['cache-control'].startswith('public,')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'metadata',
    [
        '<META CONTENT="https://images.example/aed.png?a=1&amp;b=2" PROPERTY="og:image">',
        (
            '<script>const fake = \'<meta property="og:image" content="https://wrong.example">\';</script>'
            '<meta property="og:image" content="https://images.example/aed.png?a=1&amp;b=2"/>'
            '<meta property="og:image" content="https://wrong.example">'
        ),
    ],
    ids=('uppercase-attributes', 'script-and-later-image'),
)
async def test_commons_metadata_selects_first_image_and_decodes_html_entities(metadata):
    buffer = BytesIO()
    Image.new('RGB', (2, 2), color=(47, 82, 193)).save(buffer, format='PNG')
    image = buffer.getvalue()
    requests = []

    def handler(url, **kwargs):
        if url.host == 'commons.wikimedia.org':
            return CallbackResult(status=200, body=metadata)
        requests.append(str(url))
        return CallbackResult(status=200, body=image, headers={'Content-Type': 'image/png'})

    async with _proxy_client(handler) as client:
        response = await client.get(COMMONS_PATH)

    assert response.status_code == 200
    assert response.content == image
    assert requests == ['https://images.example/aed.png?a=1&b=2']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'metadata',
    [
        '<html>No image</html>',
        '<meta property="og:image"><meta property="og:image" content="https://images.example/later.png">',
    ],
    ids=('missing-meta', 'missing-content'),
)
async def test_commons_missing_image_metadata_returns_not_found(metadata):
    async with _proxy_client(lambda *_args, **_kwargs: CallbackResult(status=200, body=metadata)) as client:
        response = await client.get(COMMONS_PATH)

    assert response.status_code == 404
    assert response.headers['cache-control'] == 'no-store'


@asynccontextmanager
async def _stream_client(monkeypatch, *, stall, read_timeout=None):
    import http_client
    from api.v1.photos import router
    from services.byte_cache import byte_cache

    sent, closed = asyncio.Event(), asyncio.Event()

    async def serve(reader, writer):
        try:
            await reader.readuntil(b'\r\n\r\n')
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 100\r\n\r\npartial image')
            await writer.drain()
            sent.set()
            if stall:
                await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    byte_cache.invalidate(lambda _: True)
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    async with (
        asyncio.TaskGroup() as tasks,
        await asyncio.start_server(
            lambda reader, writer: tasks.create_task(serve(reader, writer)), '127.0.0.1', 0
        ) as server,
    ):
        url = f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/image'
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_read=read_timeout)) as upstream:
            monkeypatch.setattr(http_client, 'client', lambda: upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                try:
                    yield client, f'/api/v1/photos/proxy/direct/{quote_plus(url)}', sent, closed
                finally:
                    await byte_cache.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(('stall', 'expected_status'), [(False, 502), (True, 504)], ids=('truncated', 'stalled'))
async def test_interrupted_downloads_are_handled_and_closed(monkeypatch, stall, expected_status):
    async with _stream_client(monkeypatch, stall=stall, read_timeout=0.05) as (client, path, sent, closed):
        response = await client.get(path)

    assert response.status_code == expected_status
    assert sent.is_set()
    assert closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize('content_type', ['text/html', 'application/octet-stream'])
async def test_non_image_responses_are_handled(content_type):
    def handler(url, **kwargs):
        return CallbackResult(status=200, body=b'<html>Not an image</html>', headers={'Content-Type': content_type})

    async with _proxy_client(handler) as client:
        response = await client.get(DIRECT_PATH)

    assert response.status_code == 502
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
async def test_oversized_images_are_handled(monkeypatch):
    from api.v1 import photos

    monkeypatch.setattr(photos, 'IMAGE_REMOTE_MAX_FILE_SIZE', 8)

    def handler(url, **kwargs):
        return CallbackResult(status=200, body=b'oversized image', headers={'Content-Type': 'image/png'})

    async with _proxy_client(handler) as client:
        response = await client.get(DIRECT_PATH)

    assert response.status_code == 502
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['direct', 'commons-metadata', 'commons-image'])
async def test_expected_failures_do_not_report_errors_to_sentry(phase):
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    from sentry_sdk.transport import Transport

    events = []
    unexpected = False

    class CaptureTransport(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                event = item.get_event()
                if event is not None:
                    events.append(event)

    def handler(url, **kwargs):
        if phase == 'commons-image' and url.host == 'commons.wikimedia.org':
            return _commons_metadata()
        return CallbackResult(status=503)

    async def fail_unexpectedly(request, handler):
        if unexpected:
            raise RuntimeError('Unexpected application failure')
        return await handler(request)

    path = DIRECT_PATH if phase == 'direct' else COMMONS_PATH
    with (
        sentry_sdk.Client(
            dsn='https://public@sentry.example/1',
            transport=CaptureTransport(),
            default_integrations=False,
            integrations=[FastApiIntegration(), StarletteIntegration()],
        ) as sentry_client,
        sentry_sdk.isolation_scope() as scope,
    ):
        scope.set_client(sentry_client)
        async with _proxy_client(handler, middlewares=(fail_unexpectedly,)) as client:
            response = await client.get(path)
            assert response.status_code == 502
            assert events == []

            unexpected = True
            with pytest.raises(RuntimeError):
                await client.get(path)
            assert events
            assert events[-1]['exception']['values'][-1]['type'] == 'RuntimeError'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [RuntimeError, TypeError])
async def test_unexpected_errors_propagate(failure):
    async def fail_request(request, handler):
        raise failure('Unexpected application failure')

    async with _proxy_client(lambda *_args, **_kwargs: _commons_metadata(), middlewares=(fail_request,)) as client:
        with pytest.raises(failure):
            await client.get(DIRECT_PATH)


@pytest.mark.asyncio
async def test_cancelled_download_closes_upstream_connection(monkeypatch):
    async with _stream_client(monkeypatch, stall=True) as (client, path, sent, closed):
        request = asyncio.create_task(client.get(path))
        await sent.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    assert closed.is_set()
