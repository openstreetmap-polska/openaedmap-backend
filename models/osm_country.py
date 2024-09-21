from typing import NamedTuple

from shapely import MultiPolygon, Polygon
from shapely.geometry import Point


class OSMCountry(NamedTuple):
    tags: dict[str, str]
    geometry: Polygon | MultiPolygon
    representative_point: Point
    timestamp: float
