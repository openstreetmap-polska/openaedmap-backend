from dataclasses import dataclass

from shapely import Point


@dataclass(frozen=True, slots=True)
class LonLat:
    lon: float
    lat: float

    def __iter__(self):
        return iter((self.lon, self.lat))

    @property
    def shapely(self) -> Point:
        return Point(self.lon, self.lat)
