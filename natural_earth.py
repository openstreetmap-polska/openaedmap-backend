from typing import Sequence

from shapely.geometry import Point, shape

from config import COUNTRIES_GEOJSON_URL
from models.country import Country
from utils import get_http_client, retry_exponential


@retry_exponential(None)
async def _get_countries() -> Sequence[dict]:
    async with get_http_client() as http:
        r = await http.get(COUNTRIES_GEOJSON_URL)
        r.raise_for_status()
    return r.json()['features']


async def validate_countries(countries: Sequence[Country]) -> None:
    ne_countries = await _get_countries()
    ne_countries = tuple(c for c in ne_countries if not any(
        n in c['properties']['NAME'] for n in (
            # ignore some countries
            'Antarctica',
            'Sahara',
        )))

    # validate country geometries
    for ne_country in ne_countries:
        ne_geom = shape(ne_country['geometry'])
        ne_point = ne_geom.representative_point()

        if not any(c.geometry.contains(ne_point) for c in countries):
            raise ValueError(f'Country geometry not found: {ne_country["properties"]["NAME"]!r}')
