from collections.abc import Sequence

import brotlicffi as brotli
import orjson
from shapely.geometry import shape

from config import COUNTRIES_GEOJSON_URL
from models.osm_country import OSMCountry
from utils import get_http_client


async def get_osm_countries() -> Sequence[OSMCountry]:
    async with get_http_client() as http:
        r = await http.get(COUNTRIES_GEOJSON_URL)
        r.raise_for_status()

    buffer = r.read()
    buffer = brotli.decompress(buffer)
    data = orjson.loads(buffer)

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
