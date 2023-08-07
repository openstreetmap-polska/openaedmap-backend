from typing import NamedTuple, Self, Sequence

from shapely.geometry import Polygon

from models.lonlat import LonLat


class BBox(NamedTuple):
    p1: LonLat
    p2: LonLat

    def extend(self, percentage: float) -> Self:
        lon_span = self.p2.lon - self.p1.lon
        lat_span = self.p2.lat - self.p1.lat
        lon_delta = lon_span * percentage
        lat_delta = lat_span * percentage
        return BBox(
            LonLat(self.p1.lon - lon_delta, self.p1.lat - lat_delta),
            LonLat(self.p2.lon + lon_delta, self.p2.lat + lat_delta))

    @classmethod
    def from_tuple(cls, bbox: tuple[float, float, float, float]) -> Self:
        return cls(LonLat(bbox[0], bbox[1]), LonLat(bbox[2], bbox[3]))

    def to_tuple(self) -> tuple[float, float, float, float]:
        return (self.p1.lon, self.p1.lat, self.p2.lon, self.p2.lat)

    def to_polygon(self) -> Polygon:
        return Polygon([
            (self.p1.lon, self.p1.lat),
            (self.p2.lon, self.p1.lat),
            (self.p2.lon, self.p2.lat),
            (self.p1.lon, self.p2.lat),
            (self.p1.lon, self.p1.lat)])

    def correct_for_dateline(self) -> Sequence[Self]:
        if self.p1.lon > self.p2.lon:
            return (
                BBox(self.p1, LonLat(180, self.p2.lat)),
                BBox(LonLat(-180, self.p1.lat), self.p2))
        else:
            return (self,)