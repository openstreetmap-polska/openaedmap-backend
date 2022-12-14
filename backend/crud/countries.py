from dataclasses import dataclass
from typing import List, Optional

from geoalchemy2 import func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from backend.models import Countries


@dataclass(frozen=True)
class CountryInfo:
    country_code: str
    country_name: str
    feature_count: int
    data_path: str


@dataclass(frozen=True)
class CountryNames:
    country_code: str
    country_names: dict
    feature_count: int
    data_path: str


def get_countries_without_geom(db: Session) -> List[CountryInfo]:
    query = db.query(
        Countries.country_code,
        Countries.country_names['default'],
        Countries.feature_count,
        func.concat("/data/", Countries.country_code, ".geojson"),
    )
    return [CountryInfo(*row) for row in query.all()]


def get_countries_geojson(country_code: Optional[str], db: Session) -> dict:
    query = db.query(
        Countries.country_code,
        Countries.country_names["default"].label("country_name"),
        Countries.feature_count,
        func.concat("/data/", Countries.country_code, ".geojson").label("data_path"),
        Countries.geometry
    )
    if country_code:
        query = query.filter(Countries.country_code == country_code.upper())
    return db.scalar(
        func.json_build_object(
            "type", "FeatureCollection",
            "features", func.json_agg(func.ST_AsGeoJSON(query.subquery(), "geometry", 6).cast(JSONB))
        )
    )


def get_countries_names(country_code: Optional[str], db: Session) -> List[CountryNames]:
    query = db.query(
        Countries.country_code,
        Countries.country_names,
        Countries.feature_count,
        func.concat("/data/", Countries.country_code, ".geojson"),
    )
    if country_code:
        query.filter(Countries.country_code == country_code)
    return [CountryNames(*row) for row in query.all()]
