from typing import NamedTuple

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry


class OSMCountry(NamedTuple):
    tags: dict[str, str]
    timestamp: float
    representative_point: Point
    geometry: BaseGeometry
