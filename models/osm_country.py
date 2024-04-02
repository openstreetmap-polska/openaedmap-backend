from typing import NamedTuple

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry


class OSMCountry(NamedTuple):
    tags: dict[str, str]
    geometry: BaseGeometry
    representative_point: Point
    timestamp: float
