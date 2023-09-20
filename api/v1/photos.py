import traceback
from datetime import UTC, datetime, timedelta
from typing import Annotated

import magic
import orjson
from fastapi import (APIRouter, File, Form, HTTPException, Request, Response,
                     UploadFile)
from fastapi.responses import FileResponse
from feedgen.feed import FeedGenerator

from middlewares.cache_middleware import configure_cache
from openstreetmap import OpenStreetMap, osm_user_has_active_block
from osm_change import update_node_tags_osm_change
from states.aed_state import AEDStateDep
from states.photo_report_state import PhotoReportStateDep
from states.photo_state import PhotoStateDep
from utils import upgrade_https

router = APIRouter(prefix='/photos')


@router.get('/view/{id}.webp')
@configure_cache(timedelta(days=365), stale=timedelta(days=365))
async def view(request: Request, id: str, photo_state: PhotoStateDep) -> FileResponse:
    info = await photo_state.get_photo_by_id(id)

    if info is None:
        raise HTTPException(404, f'Photo {id!r} not found')

    return FileResponse(info.path)


@router.post('/upload')
async def upload(request: Request, node_id: Annotated[str, Form()], file_license: Annotated[str, Form()], file: Annotated[UploadFile, File()], oauth2_credentials: Annotated[str, Form()], aed_state: AEDStateDep, photo_state: PhotoStateDep) -> bool:
    file_license = file_license.upper()
    accept_licenses = ('CC0',)

    if file_license not in accept_licenses:
        raise HTTPException(400, f'Unsupported license {file_license!r}, must be one of {accept_licenses}')

    if file.size <= 0:
        raise HTTPException(400, 'File must not be empty')

    content_type = magic.from_buffer(file.file.read(2048), mime=True)
    accept_content_types = ('image/jpeg', 'image/png', 'image/webp')

    if content_type not in accept_content_types:
        raise HTTPException(400, f'Unsupported file type {content_type!r}, must be one of {accept_content_types}')

    try:
        oauth2_credentials_ = orjson.loads(oauth2_credentials)
    except Exception:
        raise HTTPException(400, 'OAuth2 credentials must be a JSON object')

    if 'access_token' not in oauth2_credentials_:
        raise HTTPException(400, 'OAuth2 credentials must contain an access_token field')

    aed = await aed_state.get_aed_by_id(node_id)

    if aed is None:
        raise HTTPException(404, f'Node {node_id!r} not found, perhaps it is not an AED?')

    osm = OpenStreetMap(oauth2_credentials_)
    osm_user = await osm.get_authorized_user()

    if osm_user is None:
        raise HTTPException(401, 'OAuth2 credentials are invalid')

    if osm_user_has_active_block(osm_user):
        raise HTTPException(403, 'User has an active block on OpenStreetMap')

    photo_info = await photo_state.set_photo(node_id, str(osm_user['id']), file)
    photo_url = upgrade_https(str(request.url_for('view', id=photo_info.id)))

    node_xml = await osm.get_node_xml(node_id)

    osm_change = update_node_tags_osm_change(node_xml, {
        'image': photo_url,
        'image:license': file_license,
    })

    await osm.upload_osm_change(osm_change)
    return True


@router.post('/report')
async def report(id: Annotated[str, Form()], photo_report_state: PhotoReportStateDep) -> bool:
    return await photo_report_state.report_by_photo_id(id)


@router.get('/report/rss.xml')
async def report_rss(request: Request, photo_state: PhotoStateDep, photo_report_state: PhotoReportStateDep) -> Response:
    fg = FeedGenerator()
    fg.title('AED Photo Reports')
    fg.description('This feed contains a list of recent AED photo reports')
    fg.link(href=upgrade_https(str(request.url)), rel='self')

    for report in await photo_report_state.get_recent_reports():
        info = await photo_state.get_photo_by_id(report.photo_id)

        if info is None:
            continue

        fe = fg.add_entry(order='append')
        fe.id(report.id)
        fe.title('🚨 Received photo report')
        fe.content('<br>'.join((
            f'File name: {info.path.name}',
            f'Node: https://osm.org/node/{info.node_id}',
        )), type='CDATA')
        fe.link(href=upgrade_https(f'{request.base_url}api/v1/photos/view/{report.photo_id}.webp'))
        fe.published(datetime.utcfromtimestamp(report.timestamp).astimezone(tz=UTC))

    return Response(content=fg.rss_str(pretty=True), media_type='application/rss+xml')