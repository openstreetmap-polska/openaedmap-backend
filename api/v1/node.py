from datetime import datetime

from dateutil import tz
from fastapi import APIRouter, HTTPException
from timezonefinder import TimezoneFinder

from models.lonlat import LonLat
from states.aed_state import AEDStateDep

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
async def get_node(node_id: str, aed_state: AEDStateDep):
    aed = await aed_state.get_aed_by_id(node_id)

    if aed is None:
        raise HTTPException(404, f'Node {node_id!r} not found')

    timezone_name, timezone_offset = _get_timezone(aed.position)

    return {
        'version': 0.6,
        'copyright': 'OpenStreetMap and contributors',
        'attribution': 'http://www.openstreetmap.org/copyright',
        'license': 'http://opendatacommons.org/licenses/odbl/1-0/',
        'elements': [{
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
