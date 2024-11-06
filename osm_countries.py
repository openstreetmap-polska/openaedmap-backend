import json
from collections.abc import Sequence
from typing import cast

from sentry_sdk import trace
from shapely import MultiPolygon, Point, Polygon
from shapely.geometry import shape
from zstandard import ZstdDecompressor

from config import COUNTRY_GEOJSON_URL
from models.osm_country import OSMCountry
from utils import HTTP

_zstd_decompress = ZstdDecompressor().decompress


@trace
async def get_osm_countries() -> Sequence[OSMCountry]:
    r = await HTTP.get(COUNTRY_GEOJSON_URL)
    r.raise_for_status()

    buffer = _zstd_decompress(r.content)
    data: dict = json.loads(buffer)
    result = []

    for feature in data['features']:
        props = feature['properties']
        geometry = feature['geometry']
        result.append(
            OSMCountry(
                tags=props['tags'],
                geometry=cast(Polygon | MultiPolygon, shape(geometry)),
                representative_point=cast(Point, shape(props['representative_point'])),
                timestamp=props['timestamp'],
            )
        )

    return result
