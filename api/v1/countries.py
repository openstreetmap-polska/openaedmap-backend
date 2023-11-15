from datetime import timedelta
from typing import Annotated

import anyio
from anyio.streams.memory import MemoryObjectSendStream
from fastapi import APIRouter, Path, Request, Response
from shapely.geometry import Point, mapping

from middlewares.cache_middleware import configure_cache
from models.country import Country
from states.aed_state import AEDState, AEDStateDep
from states.country_state import CountryStateDep

router = APIRouter(prefix='/countries')


async def _count_aed_in_country(country: Country, aed_state: AEDState, send_stream: MemoryObjectSendStream) -> None:
    count = await aed_state.count_aeds_by_country_code(country.code)
    await send_stream.send((country, count))


@router.get('/names')
@configure_cache(timedelta(hours=1), stale=timedelta(days=7))
async def get_names(request: Request, country_state: CountryStateDep, aed_state: AEDStateDep, language: str | None = None):
    countries = await country_state.get_all_countries()

    send_stream, receive_stream = anyio.create_memory_object_stream()
    country_count_map = {}

    async with anyio.create_task_group() as tg, send_stream, receive_stream:
        for country in countries:
            tg.start_soon(_count_aed_in_country, country, aed_state, send_stream)

        for _ in range(len(countries)):
            country, count = await receive_stream.receive()
            country_count_map[country.name] = count

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
        } for country in countries
    ] + [{
        'country_code': 'WORLD',
        'country_names': {'default': 'World'},
        'feature_count': sum(country_count_map.values()),
        'data_path': '/api/v1/countries/WORLD.geojson',
    }]


@router.get('/{country_code}.geojson')
@configure_cache(timedelta(hours=1), stale=timedelta(seconds=0))
async def get_geojson(request: Request, response: Response, country_code: Annotated[str, Path(min_length=2, max_length=5)], country_state: CountryStateDep, aed_state: AEDStateDep):
    if country_code == 'WORLD':
        aeds = await aed_state.get_all_aeds()
    else:
        aeds = await aed_state.get_aeds_by_country_code(country_code)

    response.headers['Content-Disposition'] = 'attachment'
    response.headers['Content-Type'] = 'application/geo+json; charset=utf-8'
    return {
        'type': 'FeatureCollection',
        'features': [
            {
                'type': 'Feature',
                'geometry': mapping(Point(*aed.position)),
                'properties': {
                    '@osm_type': 'node',
                    '@osm_id': int(aed.id),
                    **aed.tags
                }
            } for aed in aeds
        ]
    }
