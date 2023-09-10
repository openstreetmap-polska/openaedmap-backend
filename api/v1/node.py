from datetime import datetime

from dateutil import tz
from fastapi import APIRouter, HTTPException
from timezonefinder import TimezoneFinder

from models.lonlat import LonLat
from states.aed_state import AEDStateDep
from states.photo_state import PhotoStateDep

router = APIRouter()

tf = TimezoneFinder()


def _get_timezone(lonlat: LonLat) -> tuple[str | None, str | None]:
    timezone_name = tf.timezone_at(lng=lonlat.lon, lat=lonlat.lat)

    if timezone_name:
        try:
            dt = datetime.now(tz=tz.gettz(timezone_name))
            offset = dt.strftime('%z')
            timezone_offset = f'UTC{offset[:3]}:{offset[3:]}'
        except Exception:
            timezone_offset = None
    else:
        timezone_offset = None

    return timezone_name, timezone_offset


@router.get('/node/{node_id}')
async def get_node(node_id: str, aed_state: AEDStateDep, photo_state: PhotoStateDep):
    aed = await aed_state.get_aed_by_id(node_id)

    if aed is None:
        raise HTTPException(404, f'Node {node_id!r} not found')

    timezone_name, timezone_offset = _get_timezone(aed.position)

    if (photo_info := await photo_state.get_photo_by_node_id(node_id)) is not None:
        photo_dict = {
            '@photo_id': photo_info.id,
            '@photo_url': f'/api/v1/photos/view/{photo_info.id}',
        }
    else:
        photo_dict = {
            '@photo_id': None,
            '@photo_url': None,
        }

    return {
        'version': 0.6,
        'copyright': 'OpenStreetMap and contributors',
        'attribution': 'http://www.openstreetmap.org/copyright',
        'license': 'http://opendatacommons.org/licenses/odbl/1-0/',
        'elements': [{
            **photo_dict,
            '@timezone_name': timezone_name,
            '@timezone_offset': timezone_offset,
            'type': 'node',
            'id': int(aed.id),
            'lat': aed.position.lat,
            'lon': aed.position.lon,
            'tags': aed.tags,
            'version': 0,
        }]
    }
