import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route
from starlette_compress import CompressMiddleware

from middlewares.cache_control_middleware import CacheControlMiddleware, cache_control
from models.payload import Payload
from payload_response import payload_response


@pytest.mark.asyncio
async def test_conditional_response_preserves_policy_and_per_request_encoding_and_cors():
    from datetime import timedelta

    content = b'public representation ' * 1000
    payload = Payload.create(content, 'text/plain')

    @cache_control(timedelta(seconds=60), timedelta(seconds=300))
    async def endpoint(request):
        return payload_response(request, payload)

    app = Starlette(routes=[Route('/payload', endpoint)])
    app.add_middleware(CacheControlMiddleware)
    app.add_middleware(CompressMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=['https://allowed.example'])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        first = await client.get('/payload', headers={'Accept-Encoding': 'gzip', 'Origin': 'https://allowed.example'})
        unchanged = await client.get(
            '/payload',
            headers={
                'If-None-Match': f'"unrelated", {first.headers["etag"].removeprefix("W/")}',
                'Accept-Encoding': 'identity',
                'Origin': 'https://other.example',
            },
        )
        mismatch = await client.get(
            '/payload',
            headers={
                'If-None-Match': '"different"',
                'If-Modified-Since': 'Thu, 01 Jan 2099 00:00:00 GMT',
                'Accept-Encoding': 'identity',
            },
        )
        wildcard = await client.get('/payload', headers={'If-None-Match': '*'})
    assert first.content == mismatch.content == content
    assert first.headers['content-encoding'] == 'gzip'
    assert first.headers['access-control-allow-origin'] == 'https://allowed.example'
    assert unchanged.status_code == wildcard.status_code == 304
    assert unchanged.content == b''
    assert 'content-encoding' not in unchanged.headers
    assert 'access-control-allow-origin' not in unchanged.headers
    assert unchanged.headers['cache-control'] == first.headers['cache-control']
    assert unchanged.headers['etag'] == first.headers['etag']
    assert 'Accept-Encoding' in unchanged.headers['vary']
