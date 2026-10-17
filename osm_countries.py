import json
from asyncio import to_thread
from compression.zstd import decompress

from sentry_sdk import trace

from config import COUNTRY_GEOJSON_URL
from models.osm_country import OSMCountry
from utils import HTTP


@trace
async def get_osm_countries() -> list[OSMCountry]:
    r = await HTTP.get(COUNTRY_GEOJSON_URL)
    r.raise_for_status()

    return await to_thread(_decode_countries, r.content)


def _decode_countries(content: bytes) -> list[OSMCountry]:
    data: dict = json.loads(decompress(content))
    result = []

    for feature in data['features']:
        props = feature['properties']
        geometry = feature['geometry']
        result.append(
            OSMCountry(
                tags=props['tags'],
                geometry=geometry,
                representative_point=props['representative_point'],
                timestamp=props['timestamp'],
            )
        )

    return result
