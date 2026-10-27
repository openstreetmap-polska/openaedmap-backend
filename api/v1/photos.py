import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from html.parser import HTMLParser
from io import BytesIO
from typing import Annotated
from urllib.parse import unquote_plus
from xml.etree.ElementTree import Element, SubElement, tostring

import aiohttp
import magic
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from starlette.staticfiles import StaticFiles

import http_client
from config import IMAGE_CONTENT_TYPES, IMAGE_REMOTE_MAX_FILE_SIZE
from middlewares.cache_control_middleware import cache_control
from models.payload import ImageKey, Payload
from openstreetmap import OpenStreetMap, osm_user_has_active_block
from payload_response import payload_response
from services.aed_service import AEDService
from services.byte_cache import byte_cache
from services.photo_report_service import PhotoReportService
from services.photo_service import PhotoService
from utils import get_wikimedia_commons_url, run_blocking

router = APIRouter(prefix='/photos')
_files = StaticFiles()
_IMAGE_TTL = timedelta(days=7).total_seconds()


def _image_unavailable(status_code: int) -> HTTPException:
    return HTTPException(status_code, 'Image unavailable', headers={'Cache-Control': 'no-store'})


@asynccontextmanager
async def _proxy_request(url: str) -> AsyncGenerator[aiohttp.ClientResponse]:
    try:
        async with http_client.client().get(http_client.request_url(url)) as response:
            response.raise_for_status()
            yield response
    except aiohttp.ClientResponseError as exc:
        status_code = 404 if exc.status in {401, 403, 404, 410} else 502
        raise _image_unavailable(status_code) from exc
    except TimeoutError as exc:
        raise _image_unavailable(504) from exc
    except aiohttp.ClientConnectorError as exc:
        status_code = 400 if isinstance(exc.os_error, http_client.UnsafeAddressError) else 502
        raise _image_unavailable(status_code) from exc
    except (aiohttp.InvalidURL, aiohttp.NonHttpUrlClientError, http_client.UnsafeAddressError) as exc:
        raise _image_unavailable(400) from exc
    except aiohttp.ClientError as exc:
        raise _image_unavailable(502) from exc


async def _fetch_image(url: str) -> tuple[bytes, str]:
    async with _proxy_request(url) as r:
        # Early detection of unsupported types
        content_type = r.headers.get('Content-Type')
        if content_type and content_type not in IMAGE_CONTENT_TYPES:
            raise _image_unavailable(502)

        with BytesIO() as buffer:
            async for chunk in r.content.iter_chunked(1024 * 1024):
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
        text = await r.text()
    parser = _OpenGraphImageParser()
    await run_blocking(parser.feed, text)
    parser.close()
    if parser.image_url is None:
        raise _image_unavailable(404)
    return await _image_payload(parser.image_url)


class _OpenGraphImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.image_url: str | None = None
        self.found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != 'meta' or self.found:
            return
        attributes = dict(attrs)
        if attributes.get('property') == 'og:image':
            self.found = True
            self.image_url = attributes.get('content')


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

    content_type = magic.from_buffer(await file.read(2048), mime=True)
    await file.seek(0)
    if content_type not in IMAGE_CONTENT_TYPES:
        return Response(f'Unsupported file type {content_type!r}, must be one of {IMAGE_CONTENT_TYPES}', 400)

    try:
        oauth2_credentials_dict: dict = json.loads(oauth2_credentials)
    except json.JSONDecodeError:
        return Response('OAuth2 credentials must be a JSON object', 400)
    if not isinstance(oauth2_credentials_dict, dict):
        return Response('OAuth2 credentials must be a JSON object', 400)
    if not isinstance(oauth2_credentials_dict.get('access_token'), str) or not oauth2_credentials_dict['access_token']:
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

    await osm.update_node_tags(
        node_xml,
        {
            'image': photo_url,
            'image:license': file_license,
        },
    )

    return True


@router.post('/report')
async def report(id: Annotated[str, Form()]):
    await PhotoReportService.create(id)
    return Response()


@router.get('/report/rss.xml')
async def report_rss(request: Request):
    rss = Element('rss', version='2.0', attrib={'xmlns:atom': 'http://www.w3.org/2005/Atom'})
    channel = SubElement(rss, 'channel')
    SubElement(channel, 'title').text = 'AED Photo Reports'
    SubElement(channel, 'link').text = str(request.url)
    SubElement(channel, 'description').text = 'This feed contains a list of recent AED photo reports'
    SubElement(channel, 'atom:link', href=str(request.url), rel='self')
    SubElement(channel, 'lastBuildDate').text = format_datetime(datetime.now(UTC))

    for report in await PhotoReportService.get_recent():
        photo = report.photo

        item = SubElement(channel, 'item')
        SubElement(item, 'title').text = '🚨 Received photo report'
        SubElement(item, 'link').text = f'{request.base_url}api/v1/photos/view/{report.photo_id}.webp'
        SubElement(
            item, 'description'
        ).text = f'File name: {photo.file_path.name}<br>Node: https://osm.org/node/{photo.node_id}'
        SubElement(item, 'guid', isPermaLink='false').text = report.id
        SubElement(item, 'pubDate').text = format_datetime(report.created_at)

    return Response(content=tostring(rss, encoding='utf-8', xml_declaration=True), media_type='application/rss+xml')
