from io import BytesIO

import httpx
import pytest
from fastapi import FastAPI, UploadFile
from PIL import Image


def image_file():
    content = BytesIO()
    Image.new('RGB', (4, 4), color=(37, 83, 149)).save(content, format='PNG')
    content.seek(0)
    return UploadFile(content, filename='aed.png')


@pytest.mark.asyncio
async def test_uploaded_photo_has_durable_bytes_before_report_publication(database):
    from services.photo_report_service import PhotoReportService
    from services.photo_service import PhotoService

    first = await PhotoService.upload(123, 456, image_file())
    second = await PhotoService.upload(789, 456, image_file())
    assert first.id != second.id
    assert first.file_path.read_bytes().startswith(b'RIFF')
    assert await PhotoReportService.create(first.id)
    assert await PhotoReportService.create(first.id)
    reports = await PhotoReportService.get_recent()
    assert len(reports) == 1
    assert reports[0].photo.node_id == 123
    assert reports[0].photo_id == first.id


@pytest.mark.asyncio
async def test_range_request_cannot_poison_full_photo_response(database):
    from api.v1.photos import router
    from middlewares.cache_control_middleware import CacheControlMiddleware
    from services.photo_service import PhotoService

    photo = await PhotoService.upload(1001, 2002, image_file())
    content = photo.file_path.read_bytes()
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.add_middleware(CacheControlMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        partial = await client.get(f'/api/v1/photos/view/{photo.id}.webp', headers={'Range': 'bytes=0-3'})
        full = await client.get(f'/api/v1/photos/view/{photo.id}.webp')
    assert partial.status_code == 206
    assert partial.content == content[:4]
    assert full.status_code == 200
    assert full.content == content


@pytest.mark.asyncio
async def test_local_photo_conditionals_and_ranges_preserve_file_identity(database):
    from api.v1.photos import router
    from middlewares.cache_control_middleware import CacheControlMiddleware
    from services.photo_service import PhotoService

    photo = await PhotoService.upload(1031, 2042, image_file())
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.add_middleware(CacheControlMiddleware)
    path = f'/api/v1/photos/view/{photo.id}.webp'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        full = await client.get(path)
        unchanged = await client.get(path, headers={'If-None-Match': f'"other", W/{full.headers["etag"]}'})
        since = await client.get(path, headers={'If-Modified-Since': full.headers['last-modified']})
        changed = await client.get(
            path,
            headers={
                'If-None-Match': '"different"',
                'If-Modified-Since': full.headers['last-modified'],
            },
        )
        partial = await client.get(path, headers={'Range': 'bytes=1-4', 'If-Range': full.headers['etag']})
        fallback = await client.get(path, headers={'Range': 'bytes=1-4', 'If-Range': 'W/' + full.headers['etag']})
    assert unchanged.status_code == since.status_code == 304
    assert unchanged.content == b''
    assert unchanged.headers['cache-control'] == full.headers['cache-control']
    assert changed.content == fallback.content == full.content
    assert partial.status_code == 206
    assert partial.content == full.content[1:5]
