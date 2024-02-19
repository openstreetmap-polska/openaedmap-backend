from datetime import timedelta
from typing import Annotated

from anyio import create_task_group
from fastapi import APIRouter, Path, Response
from sentry_sdk import start_span
from shapely.geometry import mapping

from middlewares.cache_middleware import configure_cache
from models.country import Country
from states.aed_state import AEDState
from states.country_state import CountryState

router = APIRouter(prefix='/countries')


@router.get('/names')
@configure_cache(timedelta(hours=1), stale=timedelta(days=7))
async def get_names(language: str | None = None):
    countries = await CountryState.get_all_countries()
    country_count_map: dict[str, int] = {}

    with start_span(description='Counting AEDs'):

        async def count_task(country: Country) -> None:
            count = await AEDState.count_aeds_by_country_code(country.code)
            country_count_map[country.name] = count

        async with create_task_group() as tg:
            for country in countries:
                tg.start_soon(count_task, country)

    def limit_country_names(names: dict[str, str]):
        if language and (name := names.get(language)):
            return {language: name}
        return names

    return [
        {
            'country_code': country.code,
            'country_names': limit_country_names(country.names),
            'feature_count': country_count_map[country.name],
            'data_path': f'/api/v1/countries/{country.code}.geojson',
        }
        for country in countries
    ] + [
        {
            'country_code': 'WORLD',
            'country_names': {'default': 'World'},
            'feature_count': sum(country_count_map.values()),
            'data_path': '/api/v1/countries/WORLD.geojson',
        }
    ]


@router.get('/{country_code}.geojson')
@configure_cache(timedelta(hours=1), stale=timedelta(seconds=0))
async def get_geojson(
    response: Response,
    country_code: Annotated[str, Path(min_length=2, max_length=5)],
):
    if country_code == 'WORLD':
        aeds = await AEDState.get_all_aeds()
    else:
        aeds = await AEDState.get_aeds_by_country_code(country_code)

    response.headers['Content-Disposition'] = 'attachment'
    response.headers['Content-Type'] = 'application/geo+json; charset=utf-8'
    return {
        'type': 'FeatureCollection',
        'features': [
            {
                'type': 'Feature',
                'geometry': mapping(aed.position.shapely),
                'properties': {
                    '@osm_type': 'node',
                    '@osm_id': aed.id,
                    '@osm_version': aed.version,
                    **aed.tags,
                },
            }
            for aed in aeds
        ],
    }
