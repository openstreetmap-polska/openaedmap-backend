from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Annotated
from urllib.parse import unquote_plus

import magic
from bs4 import BeautifulSoup
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from feedgen.feed import FeedGenerator
from msgspec.json import Decoder

from config import IMAGE_CONTENT_TYPES, REMOTE_IMAGE_MAX_FILE_SIZE
from middlewares.cache_middleware import configure_cache
from openstreetmap import OpenStreetMap, osm_user_has_active_block
from osm_change import update_node_tags_osm_change
from states.aed_state import AEDState
from states.photo_report_state import PhotoReportState
from states.photo_state import PhotoState
from utils import get_http_client, get_wikimedia_commons_url

_json_decode = Decoder().decode

router = APIRouter(prefix='/photos')


async def _fetch_image(url: str) -> tuple[bytes, str]:
    # NOTE: ideally we would verify whether url is not a private resource
    async with get_http_client() as http:
        r = await http.get(url)
        r.raise_for_status()

    # Early detection of unsupported types
    content_type = r.headers.get('Content-Type')
    if content_type and content_type not in IMAGE_CONTENT_TYPES:
        raise HTTPException(500, f'Unsupported file type {content_type!r}, must be one of {IMAGE_CONTENT_TYPES}')

    with BytesIO() as buffer:
        async for chunk in r.aiter_bytes(chunk_size=1024 * 1024):
            buffer.write(chunk)
            if buffer.tell() > REMOTE_IMAGE_MAX_FILE_SIZE:
                raise HTTPException(500, f'File is too large, max allowed size is {REMOTE_IMAGE_MAX_FILE_SIZE} bytes')

        file = buffer.getvalue()

    # Check if file type is supported
    content_type = magic.from_buffer(file[:2048], mime=True)
    if content_type not in IMAGE_CONTENT_TYPES:
        raise HTTPException(500, f'Unsupported file type {content_type!r}, must be one of {IMAGE_CONTENT_TYPES}')

    return file, content_type


@router.get('/view/{id}.webp')
@configure_cache(timedelta(days=365), stale=timedelta(days=365))
async def view(id: str):
    info = await PhotoState.get_photo_by_id(id)

    if info is None:
        raise HTTPException(404, f'Photo {id!r} not found')

    return FileResponse(info.path)


@router.get('/proxy/direct/{url_encoded:path}')
@configure_cache(timedelta(days=7), stale=timedelta(days=7))
async def proxy_direct(url_encoded: str):
    url = unquote_plus(url_encoded)
    file, content_type = await _fetch_image(url)
    return Response(file, media_type=content_type)


@router.get('/proxy/wikimedia-commons/{path_encoded:path}')
@configure_cache(timedelta(days=7), stale=timedelta(days=7))
async def proxy_wikimedia_commons(path_encoded: str):
    meta_url = get_wikimedia_commons_url(unquote_plus(path_encoded))

    async with get_http_client() as http:
        r = await http.get(meta_url)
        r.raise_for_status()

    bs = BeautifulSoup(r.text, 'lxml')
    og_image = bs.find('meta', property='og:image')
    if not og_image:
        raise HTTPException(404, 'Missing og:image meta tag')

    image_url = og_image['content']
    file, content_type = await _fetch_image(image_url)
    return Response(file, media_type=content_type)


@router.post('/upload')
async def upload(
    request: Request,
    node_id: Annotated[int, Form()],
    file_license: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    oauth2_credentials: Annotated[str, Form()],
) -> bool:
    file_license = file_license.upper()
    accept_licenses = ('CC0',)

    if file_license not in accept_licenses:
        raise HTTPException(400, f'Unsupported license {file_license!r}, must be one of {accept_licenses}')
    if file.size <= 0:
        raise HTTPException(400, 'File must not be empty')

    content_type = magic.from_buffer(file.file.read(2048), mime=True)
    if content_type not in IMAGE_CONTENT_TYPES:
        raise HTTPException(400, f'Unsupported file type {content_type!r}, must be one of {IMAGE_CONTENT_TYPES}')

    try:
        oauth2_credentials_: dict = _json_decode(oauth2_credentials)
    except Exception as e:
        raise HTTPException(400, 'OAuth2 credentials must be a JSON object') from e

    if 'access_token' not in oauth2_credentials_:
        raise HTTPException(400, 'OAuth2 credentials must contain an access_token field')

    aed = await AEDState.get_aed_by_id(node_id)
    if aed is None:
        raise HTTPException(404, f'Node {node_id} not found, perhaps it is not an AED?')

    osm = OpenStreetMap(oauth2_credentials_)
    osm_user = await osm.get_authorized_user()
    if osm_user is None:
        raise HTTPException(401, 'OAuth2 credentials are invalid')

    if osm_user_has_active_block(osm_user):
        raise HTTPException(403, 'User has an active block on OpenStreetMap')

    photo_info = await PhotoState.set_photo(node_id, osm_user['id'], file)
    photo_url = f'{request.base_url}api/v1/photos/view/{photo_info.id}.webp'

    node_xml = await osm.get_node_xml(node_id)

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
    return await PhotoReportState.report_by_photo_id(id)


@router.get('/report/rss.xml')
async def report_rss(request: Request):
    fg = FeedGenerator()
    fg.title('AED Photo Reports')
    fg.description('This feed contains a list of recent AED photo reports')
    fg.link(href=str(request.url), rel='self')

    for report in await PhotoReportState.get_recent_reports():
        info = await PhotoState.get_photo_by_id(report.photo_id)

        if info is None:
            continue

        fe = fg.add_entry(order='append')
        fe.id(report.id)
        fe.title('🚨 Received photo report')
        fe.content(
            '<br>'.join(
                (
                    f'File name: {info.path.name}',
                    f'Node: https://osm.org/node/{info.node_id}',
                )
            ),
            type='CDATA',
        )
        fe.link(href=f'{request.base_url}api/v1/photos/view/{report.photo_id}.webp')
        fe.published(datetime.utcfromtimestamp(report.timestamp).astimezone(tz=UTC))

    return Response(content=fg.rss_str(pretty=True), media_type='application/rss+xml')
