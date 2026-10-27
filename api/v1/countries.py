from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Path

from json_response import JSONResponseUTF8
from middlewares.cache_control_middleware import cache_control
from services.country_service import CountryService
from services.export_service import GeoJSONResponse

router = APIRouter(prefix='/countries')


@router.get('/names')
@cache_control(timedelta(hours=1), stale=timedelta(days=7))
async def get_names(language: str | None = None):
    countries = await CountryService.get_names()
    return JSONResponseUTF8([
        {
            'country_code': country.code,
            'country_names': (
                {language: country.names[language]} if language and language in country.names else country.names
            ),
            'feature_count': country.feature_count,
            'data_path': f'/api/v1/countries/{country.code}.geojson',
        }
        for country in countries
    ])


@router.get('/{country_code}.geojson')
@cache_control(timedelta(hours=1), stale=timedelta(seconds=0))
async def get_geojson(country_code: Annotated[str, Path(min_length=2, max_length=8)]):
    return GeoJSONResponse(country_code)
