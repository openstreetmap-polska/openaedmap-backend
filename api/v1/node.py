import re
from datetime import datetime, timedelta
from urllib.parse import quote_plus

from fastapi import APIRouter, Response
from pytz import timezone
from shapely import get_coordinates
from tzfpy import get_tz

from middlewares.cache_control_middleware import cache_control
from middlewares.skip_serialization import skip_serialization
from services.aed_service import AEDService
from services.photo_service import PhotoService
from utils import get_wikimedia_commons_url

router = APIRouter()

_photo_id_re = re.compile(r'view/(?P<id>\S+)\.')


def _get_timezone(x: float, y: float) -> tuple[str | None, str | None]:
    timezone_name: str | None = get_tz(x, y)
    timezone_offset = None

    if timezone_name:
        try:
            dt = datetime.now(tz=timezone(timezone_name))
            offset = dt.strftime('%z')
            timezone_offset = f'UTC{offset[:3]}:{offset[3:]}'
        except Exception:  # noqa: S110
            pass

    return timezone_name, timezone_offset


async def _get_image_data(tags: dict[str, str]) -> dict:
    image_url: str = tags.get('image', '')

    if (
        image_url
        and (photo_id_match := _photo_id_re.search(image_url))
        and (photo_id := photo_id_match.group('id'))
        and (await PhotoService.get_by_id(photo_id)) is not None
    ):
        return {
            '@photo_id': photo_id,
            '@photo_url': f'/api/v1/photos/view/{photo_id}.webp',
            '@photo_source': None,
        }

    if image_url:
        return {
            '@photo_id': None,
            '@photo_url': f'/api/v1/photos/proxy/direct/{quote_plus(image_url)}',
            '@photo_source': image_url,
        }

    if (
        (wikimedia_commons := tags.get('wikimedia_commons', '').partition(';')[0])  #
        and wikimedia_commons[:4].casefold() != 'http'
    ):
        return {
            '@photo_id': None,
            '@photo_url': f'/api/v1/photos/proxy/wikimedia-commons/{quote_plus(wikimedia_commons)}',
            '@photo_source': get_wikimedia_commons_url(wikimedia_commons),
        }

    if (
        (panoramax := tags.get('panoramax', '').partition(';')[0])  #
        and panoramax[:4].casefold() != 'http'
    ):
        photo_url = f'https://api.panoramax.xyz/api/pictures/{panoramax}/sd.jpg'
        photo_source = f'https://api.panoramax.xyz/#focus=pic&pic={panoramax}'
        return {
            '@photo_id': None,
            '@photo_url': f'/api/v1/photos/proxy/direct/{quote_plus(photo_url)}',
            '@photo_source': photo_source,
        }

    return {
        '@photo_id': None,
        '@photo_url': None,
        '@photo_source': None,
    }


@router.get('/node/{node_id}')
@cache_control(timedelta(minutes=1), stale=timedelta(minutes=5))
@skip_serialization()
async def get_node(node_id: int):
    aed = await AEDService.get_by_id(node_id)
    if aed is None:
        return Response(f'Node {node_id} not found', 404)

    photo_dict = await _get_image_data(aed.tags)

    x, y = get_coordinates(aed.position)[0].tolist()
    timezone_name, timezone_offset = _get_timezone(x, y)
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
                'id': node_id,
                'lat': y,
                'lon': x,
                'tags': aed.tags,
                'version': aed.version,
            }
        ],
    }
