from collections.abc import Sequence

from msgspec.json import Decoder
from sentry_sdk import trace
from shapely.geometry import shape
from zstandard import ZstdDecompressor

from config import COUNTRIES_GEOJSON_URL
from models.osm_country import OSMCountry
from utils import get_http_client

_zstd_decompress = ZstdDecompressor().decompress
_json_decode = Decoder().decode


@trace
async def get_osm_countries() -> Sequence[OSMCountry]:
    async with get_http_client() as http:
        r = await http.get(COUNTRIES_GEOJSON_URL)
        r.raise_for_status()

    buffer = r.read()
    buffer = _zstd_decompress(buffer)
    data: dict = _json_decode(buffer)

    result = []

    for feature in data['features']:
        props = feature['properties']
        geometry = feature['geometry']

        result.append(
            OSMCountry(
                tags=props['tags'],
                timestamp=props['timestamp'],
                representative_point=shape(props['representative_point']),
                geometry=shape(geometry),
            )
        )

    return tuple(result)
