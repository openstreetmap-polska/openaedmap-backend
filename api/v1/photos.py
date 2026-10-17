import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from io import BytesIO
from typing import Annotated
from urllib.parse import unquote_plus

import httpx
import magic
from bs4 import BeautifulSoup, Tag
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from feedgen.feed import FeedGenerator
from httpx_secure import SSRFProtectionError
from pydantic import SecretStr
from starlette.staticfiles import StaticFiles

from config import IMAGE_CONTENT_TYPES, IMAGE_REMOTE_MAX_FILE_SIZE
from middlewares.cache_control_middleware import cache_control
from models.payload import ImageKey, Payload
from openstreetmap import OpenStreetMap, osm_user_has_active_block
from osm_change import update_node_tags_osm_change
from payload_response import payload_response
from services.aed_service import AEDService
from services.byte_cache import byte_cache
from services.photo_report_service import PhotoReportService
from services.photo_service import PhotoService
from utils import HTTP, get_wikimedia_commons_url, run_blocking

router = APIRouter(prefix='/photos')
_files = StaticFiles()
_IMAGE_TTL = timedelta(days=7).total_seconds()


def _image_unavailable(status_code: int) -> HTTPException:
    return HTTPException(status_code, 'Image unavailable', headers={'Cache-Control': 'no-store'})


@asynccontextmanager
async def _proxy_request(url: str) -> AsyncGenerator[httpx.Response]:
    try:
        async with HTTP.stream('GET', url) as response:
            response.raise_for_status()
            yield response
    except httpx.HTTPStatusError as exc:
        status_code = 404 if exc.response.status_code in {401, 403, 404, 410} else 502
        raise _image_unavailable(status_code) from exc
    except httpx.TimeoutException as exc:
        raise _image_unavailable(504) from exc
    except (httpx.InvalidURL, httpx.UnsupportedProtocol, SSRFProtectionError) as exc:
        raise _image_unavailable(400) from exc
    except (
        httpx.NetworkError,
        httpx.RemoteProtocolError,
        httpx.ProxyError,
        httpx.DecodingError,
        httpx.TooManyRedirects,
    ) as exc:
        raise _image_unavailable(502) from exc


async def _fetch_image(url: str) -> tuple[bytes, str]:
    async with _proxy_request(url) as r:
        # Early detection of unsupported types
        content_type = r.headers.get('Content-Type')
        if content_type and content_type not in IMAGE_CONTENT_TYPES:
            raise _image_unavailable(502)

        with BytesIO() as buffer:
            async for chunk in r.aiter_bytes(1024 * 1024):
                buffer.write(chunk)
                if buffer.tell() > IMAGE_REMOTE_MAX_FILE_SIZE:
                    raise _image_unavailable(502)
            file = buffer.getvalue()

    # Check if file type is supported
    content_type = magic.from_buffer(file[:2048], mime=True)
    if content_type not in IMAGE_CONTENT_TYPES:
        raise _image_unavailable(502)

    return file, content_type


@router.get('/view/{id}.webp')
@cache_control(timedelta(days=365), stale=timedelta(days=365))
async def view(request: Request, id: str):
    photo = await PhotoService.get_by_id(id)
    if photo is None:
        return Response(f'Photo {id!r} not found', 404)

    stat = await run_blocking(photo.file_path.stat)
    return _files.file_response(photo.file_path, stat, request.scope)


@router.get('/proxy/direct/{url_encoded:path}')
@cache_control(timedelta(days=7), stale=timedelta(days=7))
async def proxy_direct(request: Request, url_encoded: str):
    url = unquote_plus(url_encoded)

    # For some reason, some requests are double-encoded
    if not url.lower().startswith(('https://', 'http://')):
        url = unquote_plus(url)

    try:
        payload = await byte_cache.get(ImageKey('direct', url), lambda: _image_payload(url), ttl=_IMAGE_TTL)
    except HTTPException as exc:
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=exc.headers)
    return payload_response(request, payload)


async def _image_payload(url: str) -> Payload:
    file, content_type = await _fetch_image(url)
    return Payload.create(file, content_type)


@router.get('/proxy/wikimedia-commons/{path_encoded:path}')
@cache_control(timedelta(days=7), stale=timedelta(days=7))
async def proxy_wikimedia_commons(request: Request, path_encoded: str):
    meta_url = get_wikimedia_commons_url(unquote_plus(path_encoded))
    try:
        payload = await byte_cache.get(
            ImageKey('wikimedia', meta_url), lambda: _wikimedia_payload(meta_url), ttl=_IMAGE_TTL
        )
    except HTTPException as exc:
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=exc.headers)
    return payload_response(request, payload)


async def _wikimedia_payload(meta_url: str) -> Payload:
    async with _proxy_request(meta_url) as r:
        await r.aread()
    bs = await run_blocking(BeautifulSoup, r.text, 'lxml')
    og_image = bs.find('meta', property='og:image')
    if not isinstance(og_image, Tag) or not isinstance(image_url := og_image.get('content'), str):
        raise _image_unavailable(404)
    return await _image_payload(image_url)


@router.post('/upload')
async def upload(
    request: Request,
    node_id: Annotated[int, Form()],
    file_license: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    oauth2_credentials: Annotated[str, Form()],
):
    file_license = file_license.upper()
    accept_licenses = ('CC0',)

    if file_license not in accept_licenses:
        return Response(f'Unsupported license {file_license!r}, must be one of {accept_licenses}', 400)
    if file.size is None or file.size <= 0:
        return Response('File must not be empty', 400)

    content_type = magic.from_buffer(file.file.read(2048), mime=True)
    file.file.seek(0)
    if content_type not in IMAGE_CONTENT_TYPES:
        return Response(f'Unsupported file type {content_type!r}, must be one of {IMAGE_CONTENT_TYPES}', 400)

    try:
        oauth2_credentials_dict: dict = json.loads(oauth2_credentials)
    except json.JSONDecodeError:
        return Response('OAuth2 credentials must be a JSON object', 400)
    if 'access_token' not in oauth2_credentials_dict:
        return Response('OAuth2 credentials must contain an access_token field', 400)
    oauth2_token = SecretStr(oauth2_credentials_dict['access_token'])
    del oauth2_credentials_dict

    aed = await AEDService.get_by_id(node_id)
    if aed is None:
        return Response(f'Node {node_id} not found, perhaps it is not an AED?', 404)

    osm = OpenStreetMap(oauth2_token)
    osm_user = await osm.get_authorized_user()
    if osm_user is None:
        return Response('OAuth2 credentials are invalid', 401)
    if osm_user_has_active_block(osm_user):
        return Response('User has an active block on OpenStreetMap', 403)

    user_id = osm_user['id']
    photo = await PhotoService.upload(node_id, user_id, file)
    photo_url = f'{request.base_url}api/v1/photos/view/{photo.id}.webp'
    node_xml = await osm.get_node_xml(node_id)
    if node_xml is None:
        return Response(f'Node {node_id} not found on remote', 404)

    osm_change = update_node_tags_osm_change(
        node_xml,
        {
            'image': photo_url,
            'image:license': file_license,
        },
    )

    await osm.upload_osm_change(osm_change)
    return True


@router.post('/report')
async def report(id: Annotated[str, Form()]):
    await PhotoReportService.create(id)
    return Response()


@router.get('/report/rss.xml')
async def report_rss(request: Request):
    fg = FeedGenerator()
    fg.title('AED Photo Reports')
    fg.description('This feed contains a list of recent AED photo reports')
    fg.link(href=str(request.url), rel='self')

    for report in await PhotoReportService.get_recent():
        photo = report.photo

        fe = fg.add_entry(order='append')
        fe.id(report.id)
        fe.title('🚨 Received photo report')
        fe.content(
            '<br>'.join((
                f'File name: {photo.file_path.name}',
                f'Node: https://osm.org/node/{photo.node_id}',
            )),
            type='CDATA',
        )
        fe.link(href=f'{request.base_url}api/v1/photos/view/{report.photo_id}.webp')
        fe.published(report.created_at)

    return Response(content=fg.rss_str(pretty=True), media_type='application/rss+xml')
