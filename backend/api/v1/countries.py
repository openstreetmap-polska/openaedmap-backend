from typing import List, Optional

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from backend.api.deps import get_db
from backend.crud.countries import (
    get_countries_without_geom,
    CountryInfo,
    get_countries_geojson,
    get_countries_names,
    CountryNames,
)

router = APIRouter()


@router.get('/countries')
async def metadata(db: Session = Depends(get_db)) -> List[CountryInfo]:
    return get_countries_without_geom(db)


@router.get('/countries.geojson')
async def metadata(country_code: Optional[str] = None, db: Session = Depends(get_db)) -> dict:
    return get_countries_geojson(country_code, db)


@router.get('/countries/names')
async def metadata(country_code: Optional[str] = None, db: Session = Depends(get_db)) -> List[CountryNames]:
    return get_countries_names(country_code, db)
