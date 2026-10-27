import json
from asyncio import to_thread
from compression.zstd import decompress

from sentry_sdk import trace

import http_client
from config import COUNTRY_GEOJSON_URL
from models.osm_country import OSMCountry


@trace
async def get_osm_countries() -> list[OSMCountry]:
    async with http_client.client().get(COUNTRY_GEOJSON_URL) as response:
        response.raise_for_status()
        content = await response.read()
    return await to_thread(_decode_countries, content)


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
