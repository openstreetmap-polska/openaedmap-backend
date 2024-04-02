from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Annotated
from urllib.parse import unquote_plus

import magic
from bs4 import BeautifulSoup
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from feedgen.feed import FeedGenerator

from config import IMAGE_CONTENT_TYPES, IMAGE_REMOTE_MAX_FILE_SIZE
from middlewares.cache_middleware import configure_cache
from openstreetmap import OpenStreetMap, osm_user_has_active_block
from osm_change import update_node_tags_osm_change
from services.aed_service import AEDService
from services.photo_report_service import PhotoReportService
from services.photo_service import PhotoService
from utils import JSON_DECODE, get_http_client, get_wikimedia_commons_url

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
            if buffer.tell() > IMAGE_REMOTE_MAX_FILE_SIZE:
                raise HTTPException(500, f'File is too large, max allowed size is {IMAGE_REMOTE_MAX_FILE_SIZE} bytes')

        file = buffer.getvalue()

    # Check if file type is supported
    content_type = magic.from_buffer(file[:2048], mime=True)
    if content_type not in IMAGE_CONTENT_TYPES:
        raise HTTPException(500, f'Unsupported file type {content_type!r}, must be one of {IMAGE_CONTENT_TYPES}')

    return file, content_type


@router.get('/view/{id}.webp')
@configure_cache(timedelta(days=365), stale=timedelta(days=365))
async def view(id: str):
    photo = await PhotoService.get_by_id(id)
    if photo is None:
        return Response(f'Photo {id!r} not found', 404)

    return FileResponse(photo.file_path)


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
        return Response('Missing og:image meta tag', 404)

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
        return Response(f'Unsupported license {file_license!r}, must be one of {accept_licenses}', 400)
    if file.size <= 0:
        return Response('File must not be empty', 400)

    content_type = magic.from_buffer(file.file.read(2048), mime=True)
    if content_type not in IMAGE_CONTENT_TYPES:
        return Response(f'Unsupported file type {content_type!r}, must be one of {IMAGE_CONTENT_TYPES}', 400)

    try:
        oauth2_credentials_: dict = JSON_DECODE(oauth2_credentials)
    except Exception:
        return Response('OAuth2 credentials must be a JSON object', 400)
    if 'access_token' not in oauth2_credentials_:
        return Response('OAuth2 credentials must contain an access_token field', 400)

    aed = await AEDService.get_by_id(node_id)
    if aed is None:
        return Response(f'Node {node_id} not found, perhaps it is not an AED?', 404)

    osm = OpenStreetMap(oauth2_credentials_)
    osm_user = await osm.get_authorized_user()
    if osm_user is None:
        return Response('OAuth2 credentials are invalid', 401)
    if osm_user_has_active_block(osm_user):
        return Response('User has an active block on OpenStreetMap', 403)

    user_id = osm_user['id']
    photo = await PhotoService.upload(node_id, user_id, file)
    photo_url = f'{request.base_url}api/v1/photos/view/{photo.id}.webp'
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
            '<br>'.join(
                (
                    f'File name: {photo.file_path.name}',
                    f'Node: https://osm.org/node/{photo.node_id}',
                )
            ),
            type='CDATA',
        )
        fe.link(href=f'{request.base_url}api/v1/photos/view/{report.photo_id}.webp')
        fe.published(datetime.fromtimestamp(report.timestamp, UTC))

    return Response(content=fg.rss_str(pretty=True), media_type='application/rss+xml')
