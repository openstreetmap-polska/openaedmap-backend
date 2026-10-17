from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Path
from shapely import get_coordinates

from middlewares.cache_control_middleware import cache_control
from middlewares.skip_serialization import skip_serialization
from services.aed_service import AEDService
from services.country_service import CountryService

router = APIRouter(prefix='/countries')


@router.get('/names')
@cache_control(timedelta(hours=1), stale=timedelta(days=7))
@skip_serialization()
async def get_names(language: str | None = None):
    countries = await CountryService.get_names()
    country_count_map = await AEDService.count_by_country_codes(code for code, _ in countries)

    def limit_country_names(names: dict[str, str]) -> dict[str, str]:
        return {language: name} if (language and (name := names.get(language))) else names

    result = [
        {
            'country_code': code,
            'country_names': limit_country_names(names),
            'feature_count': country_count_map[code],
            'data_path': f'/api/v1/countries/{code}.geojson',
        }
        for code, names in countries
    ]
    result.append({
        'country_code': 'WORLD',
        'country_names': {'default': 'World'},
        'feature_count': sum(country_count_map.values()),
        'data_path': '/api/v1/countries/WORLD.geojson',
    })
    return result


@router.get('/{country_code}.geojson')
@cache_control(timedelta(hours=1), stale=timedelta(seconds=0))
@skip_serialization({
    'Content-Disposition': 'attachment',
    'Content-Type': 'application/geo+json; charset=utf-8',
})
async def get_geojson(country_code: Annotated[str, Path(min_length=2, max_length=5)]):
    if country_code == 'WORLD':
        aeds = await AEDService.get_all()
    else:
        aeds = await AEDService.get_by_country_code(country_code)

    positions = get_coordinates([aed.position for aed in aeds]).tolist()
    return {
        'type': 'FeatureCollection',
        'features': [
            {
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': position,
                },
                'properties': {
                    '@osm_type': 'node',
                    '@osm_id': aed.id,
                    '@osm_version': aed.version,
                    **aed.tags,
                },
            }
            for aed, position in zip(aeds, positions, strict=True)
        ],
    }
