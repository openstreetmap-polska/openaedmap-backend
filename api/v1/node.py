import re
from datetime import datetime, timedelta
from urllib.parse import quote_plus

from fastapi import APIRouter, HTTPException
from pytz import timezone
from tzfpy import get_tz

from middlewares.cache_middleware import configure_cache
from models.lonlat import LonLat
from states.aed_state import AEDStateDep
from states.photo_state import PhotoStateDep
from utils import get_wikimedia_commons_url

router = APIRouter()

photo_id_re = re.compile(r'view/(?P<id>\S+)\.')


def _get_timezone(lonlat: LonLat) -> tuple[str | None, str | None]:
    timezone_name: str | None = get_tz(lonlat.lon, lonlat.lat)
    timezone_offset = None

    if timezone_name:
        try:
            dt = datetime.now(tz=timezone(timezone_name))
            offset = dt.strftime('%z')
            timezone_offset = f'UTC{offset[:3]}:{offset[3:]}'
        except Exception:  # noqa: S110
            pass

    return timezone_name, timezone_offset


async def _get_image_data(tags: dict[str, str], photo_state: PhotoStateDep) -> dict:
    image_url: str = tags.get('image', '')

    if (
        image_url
        and (photo_id_match := photo_id_re.search(image_url))
        and (photo_id := photo_id_match.group('id'))
        and (photo_info := await photo_state.get_photo_by_id(photo_id))
    ):
        return {
            '@photo_id': photo_info.id,
            '@photo_url': f'/api/v1/photos/view/{photo_info.id}.webp',
            '@photo_source': None,
        }

    if image_url:
        return {
            '@photo_id': None,
            '@photo_url': f'/api/v1/photos/proxy/direct/{quote_plus(image_url)}',
            '@photo_source': image_url,
        }

    wikimedia_commons: str = tags.get('wikimedia_commons', '')

    if wikimedia_commons:
        return {
            '@photo_id': None,
            '@photo_url': f'/api/v1/photos/proxy/wikimedia-commons/{quote_plus(wikimedia_commons)}',
            '@photo_source': get_wikimedia_commons_url(wikimedia_commons),
        }

    return {
        '@photo_id': None,
        '@photo_url': None,
        '@photo_source': None,
    }


@router.get('/node/{node_id}')
@configure_cache(timedelta(minutes=1), stale=timedelta(minutes=5))
async def get_node(node_id: int, aed_state: AEDStateDep, photo_state: PhotoStateDep):
    aed = await aed_state.get_aed_by_id(node_id)

    if aed is None:
        raise HTTPException(404, f'Node {node_id} not found')

    photo_dict = await _get_image_data(aed.tags, photo_state)

    timezone_name, timezone_offset = _get_timezone(aed.position)
    timezone_dict = {
        '@timezone_name': timezone_name,
        '@timezone_offset': timezone_offset,
    }

    return {
        'version': 0.6,
        'copyright': 'OpenStreetMap and contributors',
        'attribution': 'https://www.openstreetmap.org/copyright',
        'license': 'https://opendatacommons.org/licenses/odbl/1-0/',
        'elements': [
            {
                **photo_dict,
                **timezone_dict,
                'type': 'node',
                'id': aed.id,
                'lat': aed.position.lat,
                'lon': aed.position.lon,
                'tags': aed.tags,
                'version': aed.version,
            }
        ],
    }
