from datetime import timedelta
from typing import Annotated
from urllib.parse import quote_plus, unquote_plus

import anyio
from anyio.streams.memory import MemoryObjectSendStream
from fastapi import APIRouter, HTTPException, Path, Request, Response
from shapely.geometry import Point, mapping

from middlewares.cache_middleware import configure_cache
from models.country import Country
from states.aed_state import AEDState, AEDStateDep
from states.country_state import CountryStateDep

router = APIRouter()


async def _count_aed_in_country(country: Country, aed_state: AEDState, send_stream: MemoryObjectSendStream) -> None:
    count = await aed_state.count_aeds(country.geometry)
    await send_stream.send((country, count))


@router.get('/countries/names')
@configure_cache(timedelta(hours=1), stale=timedelta(days=2))
async def get_country_names(request: Request, country_state: CountryStateDep, aed_state: AEDStateDep):
    countries = await country_state.get_all_countries()

    send_stream, receive_stream = anyio.create_memory_object_stream()
    country_count_map = {}

    async with anyio.create_task_group() as tg, send_stream, receive_stream:
        for country in countries:
            tg.start_soon(_count_aed_in_country, country, aed_state, send_stream)

        for _ in range(len(countries)):
            country, count = await receive_stream.receive()
            country_count_map[country.name] = count
    return [
        {
            'country_code': country.code,
            'country_names': country.names,
            'feature_count': country_count_map[country.name],
            'data_path': f'/api/v1/countries/{country.code}.geojson',
        } for country in countries
    ] + [{
        'country_code': 'WORLD',
        'country_names': {'default': 'World'},
        'feature_count': sum(country_count_map.values()),
        'data_path': '/api/v1/countries/WORLD.geojson',
    }]


@router.get('/countries/{country_code}.geojson')
@configure_cache(timedelta(hours=1), stale=timedelta(seconds=0))
async def get_country_geojson(request: Request, response: Response, country_code: Annotated[str, Path(min_length=2, max_length=5)], country_state: CountryStateDep, aed_state: AEDStateDep):
    if country_code == 'WORLD':
        aeds = await aed_state.get_all_aeds()
    else:
        country = await country_state.get_country_by_code(country_code)
        if country is None:
            raise HTTPException(404, f'Country code {country_code!r} not found')

        aeds = await aed_state.get_aeds_within_geom(country.geometry, group_eps=None)

    response.headers['Content-Disposition'] = 'attachment'
    response.headers['Content-Type'] = 'application/geo+json'
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
