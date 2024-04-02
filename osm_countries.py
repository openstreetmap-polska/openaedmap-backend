from collections.abc import Sequence

from sentry_sdk import trace
from shapely.geometry import shape
from zstandard import ZstdDecompressor

from config import COUNTRY_GEOJSON_URL
from models.osm_country import OSMCountry
from utils import JSON_DECODE, get_http_client

_zstd_decompress = ZstdDecompressor().decompress


@trace
async def get_osm_countries() -> Sequence[OSMCountry]:
    async with get_http_client() as http:
        r = await http.get(COUNTRY_GEOJSON_URL)
        r.raise_for_status()

    buffer = r.read()
    buffer = _zstd_decompress(buffer)
    data: dict = JSON_DECODE(buffer)

    result = []

    for feature in data['features']:
        props = feature['properties']
        geometry = feature['geometry']

        result.append(
            OSMCountry(
                tags=props['tags'],
                geometry=shape(geometry),
                representative_point=shape(props['representative_point']),
                timestamp=props['timestamp'],
            )
        )

    return result
