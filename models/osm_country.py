from typing import NamedTuple

from shapely.geometry import MultiPolygon, Point, Polygon


class OSMCountry(NamedTuple):
    tags: dict[str, str]
    timestamp: float
    representative_point: Point
    geometry: Polygon | MultiPolygon
