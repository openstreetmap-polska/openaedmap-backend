from dataclasses import dataclass
from typing import List, NamedTuple, Dict, Optional

from geoalchemy2 import func
from sqlalchemy.orm import Session

from backend.models import Countries


@dataclass(frozen=True)
class CountryInfo:
    country_code: str
    country_name: str
    feature_count: int


@dataclass(frozen=True)
class CountryNames:
    country_code: str
    country_names: dict


def get_countries_without_geom(db: Session) -> List[CountryInfo]:
    query = db.query(Countries.country_code, Countries.country_names['default'], Countries.feature_count)
    return [CountryInfo(*row) for row in query.all()]


def get_countries_geojson(country_code: Optional[str], db: Session) -> dict:
    query = db.query(
        Countries.country_code,
        Countries.country_names['default'].label("country_name"),
        Countries.feature_count,
        Countries.geometry
    )
    if country_code:
        query = query.filter(Countries.country_code == country_code)
    return db.scalar(func.AsGeoJSON(query, "geometry", 6))


def get_countries_names(country_code: Optional[str], db: Session) -> List[CountryNames]:
    query = db.query(Countries.country_code, Countries.country_names)
    if country_code:
        query.filter(Countries.country_code == country_code)
    return [CountryNames(*row) for row in query.all()]
