import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from io import BytesIO
from urllib.parse import quote_plus

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

IMAGE_URL = 'https://images.example/aed.png?signature=private-value'
DIRECT_PATH = f'/api/v1/photos/proxy/direct/{quote_plus(IMAGE_URL)}'
COMMONS_PATH = '/api/v1/photos/proxy/wikimedia-commons/File:Defibrillator.png'


@asynccontextmanager
async def _proxy_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> AsyncGenerator[httpx.AsyncClient]:
    from api.v1 import photos
    from middlewares.cache_control_middleware import CacheControlMiddleware
    from services.byte_cache import byte_cache

    byte_cache.invalidate(lambda _: True)
    app = FastAPI()
    app.include_router(photos.router, prefix='/api/v1')
    app.add_middleware(CacheControlMiddleware)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        monkeypatch.setattr(photos, 'HTTP', upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://backend') as client:
            try:
                yield client
            finally:
                await byte_cache.close()


def _commons_metadata() -> httpx.Response:
    return httpx.Response(200, text=f'<html><meta property="og:image" content="{IMAGE_URL}"></html>')


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['direct', 'commons-metadata', 'commons-image'])
@pytest.mark.parametrize(
    ('upstream_status', 'expected_status'),
    [(401, 404), (403, 404), (404, 404), (410, 404), (429, 502), (500, 502), (503, 502)],
)
async def test_upstream_http_failures_are_handled(monkeypatch, phase, upstream_status, expected_status):
    def handler(request):
        if phase == 'commons-image' and request.url.host == 'commons.wikimedia.org':
            return _commons_metadata()
        return httpx.Response(upstream_status, text='Upstream diagnostic containing private information')

    path = DIRECT_PATH if phase == 'direct' else COMMONS_PATH
    async with _proxy_client(monkeypatch, handler) as client:
        response = await client.get(path)

    assert response.status_code == expected_status
    assert response.json() == {'detail': 'Image unavailable'}
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['direct', 'commons-metadata', 'commons-image'])
@pytest.mark.parametrize(
    ('failure', 'expected_status'),
    [
        (httpx.ConnectError, 502),
        (httpx.ReadTimeout, 504),
        (httpx.TooManyRedirects, 502),
        (httpx.InvalidURL, 400),
        (httpx.UnsupportedProtocol, 400),
    ],
)
async def test_upstream_transport_failures_are_handled(monkeypatch, phase, failure, expected_status):
    def handler(request):
        if phase == 'commons-image' and request.url.host == 'commons.wikimedia.org':
            return _commons_metadata()
        raise failure('Upstream diagnostic containing private information')

    path = DIRECT_PATH if phase == 'direct' else COMMONS_PATH
    async with _proxy_client(monkeypatch, handler) as client:
        response = await client.get(path)

    assert response.status_code == expected_status
    assert response.json() == {'detail': 'Image unavailable'}
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
@pytest.mark.parametrize('path', [DIRECT_PATH, COMMONS_PATH], ids=('direct', 'commons'))
async def test_valid_images_preserve_bytes_and_cache_policy(monkeypatch, path):
    buffer = BytesIO()
    Image.new('RGB', (2, 2), color=(31, 92, 173)).save(buffer, format='PNG')
    image = buffer.getvalue()

    def handler(request):
        if request.url.host == 'commons.wikimedia.org':
            return _commons_metadata()
        return httpx.Response(200, content=image, headers={'Content-Type': 'image/png'})

    async with _proxy_client(monkeypatch, handler) as client:
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
async def test_commons_metadata_selects_first_image_and_decodes_html_entities(monkeypatch, metadata):
    buffer = BytesIO()
    Image.new('RGB', (2, 2), color=(47, 82, 193)).save(buffer, format='PNG')
    image = buffer.getvalue()
    requests = []

    def handler(request):
        if request.url.host == 'commons.wikimedia.org':
            return httpx.Response(200, text=metadata)
        requests.append(str(request.url))
        return httpx.Response(200, content=image, headers={'Content-Type': 'image/png'})

    async with _proxy_client(monkeypatch, handler) as client:
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
async def test_commons_missing_image_metadata_returns_not_found(monkeypatch, metadata):
    async with _proxy_client(monkeypatch, lambda _: httpx.Response(200, text=metadata)) as client:
        response = await client.get(COMMONS_PATH)

    assert response.status_code == 404
    assert response.headers['cache-control'] == 'no-store'


class _BrokenImageStream(httpx.AsyncByteStream):
    def __init__(self, failure):
        self.failure = failure
        self.closed = False

    async def __aiter__(self):
        yield b'partial image'
        raise self.failure('Interrupted download')

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize(('failure', 'expected_status'), [(httpx.ReadError, 502), (httpx.ReadTimeout, 504)])
async def test_interrupted_downloads_are_handled_and_closed(monkeypatch, failure, expected_status):
    stream = _BrokenImageStream(failure)

    def handler(request):
        return httpx.Response(200, stream=stream, headers={'Content-Type': 'image/png'})

    async with _proxy_client(monkeypatch, handler) as client:
        response = await client.get(DIRECT_PATH)

    assert response.status_code == expected_status
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('content_type', ['text/html', None])
async def test_non_image_responses_are_handled(monkeypatch, content_type):
    def handler(request):
        headers = {'Content-Type': content_type} if content_type else {}
        return httpx.Response(200, content=b'<html>Not an image</html>', headers=headers)

    async with _proxy_client(monkeypatch, handler) as client:
        response = await client.get(DIRECT_PATH)

    assert response.status_code == 502
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
async def test_oversized_images_are_handled(monkeypatch):
    from api.v1 import photos

    monkeypatch.setattr(photos, 'IMAGE_REMOTE_MAX_FILE_SIZE', 8)

    def handler(request):
        return httpx.Response(200, content=b'oversized image', headers={'Content-Type': 'image/png'})

    async with _proxy_client(monkeypatch, handler) as client:
        response = await client.get(DIRECT_PATH)

    assert response.status_code == 502
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['direct', 'commons-metadata', 'commons-image'])
async def test_expected_failures_do_not_report_errors_to_sentry(monkeypatch, phase):
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

    def handler(request):
        if unexpected:
            raise RuntimeError('Unexpected application failure')
        if phase == 'commons-image' and request.url.host == 'commons.wikimedia.org':
            return _commons_metadata()
        return httpx.Response(503)

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
        async with _proxy_client(monkeypatch, handler) as client:
            response = await client.get(path)
            assert response.status_code == 502
            assert events == []

            unexpected = True
            with pytest.raises(RuntimeError):
                await client.get(path)
            assert events
            assert events[-1]['exception']['values'][-1]['type'] == 'RuntimeError'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [asyncio.CancelledError, RuntimeError, httpx.LocalProtocolError])
async def test_cancellation_and_unexpected_errors_propagate(monkeypatch, failure):
    stream = _BrokenImageStream(failure)

    def handler(request):
        return httpx.Response(200, stream=stream, headers={'Content-Type': 'image/png'})

    async with _proxy_client(monkeypatch, handler) as client:
        with pytest.raises(failure):
            await client.get(DIRECT_PATH)

    assert stream.closed
