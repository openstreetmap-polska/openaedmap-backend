from collections.abc import Sequence
from itertools import chain
from typing import NamedTuple, Self

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

        new_p1_lon = max(-180, min(180, self.p1.lon - lon_delta))
        new_p1_lat = max(-90, min(90, self.p1.lat - lat_delta))
        new_p2_lon = max(-180, min(180, self.p2.lon + lon_delta))
        new_p2_lat = max(-90, min(90, self.p2.lat + lat_delta))

        return BBox(LonLat(new_p1_lon, new_p1_lat), LonLat(new_p2_lon, new_p2_lat))

    @classmethod
    def from_tuple(cls, bbox: tuple[float, float, float, float]) -> Self:
        return cls(LonLat(bbox[0], bbox[1]), LonLat(bbox[2], bbox[3]))

    def to_tuple(self) -> tuple[float, float, float, float]:
        return (self.p1.lon, self.p1.lat, self.p2.lon, self.p2.lat)

    def to_polygon(self, *, nodes_per_edge: int = 2) -> Polygon:
        if nodes_per_edge <= 2:
            return Polygon(
                [
                    (self.p1.lon, self.p1.lat),
                    (self.p2.lon, self.p1.lat),
                    (self.p2.lon, self.p2.lat),
                    (self.p1.lon, self.p2.lat),
                    (self.p1.lon, self.p1.lat),
                ]
            )

        x_interval = (self.p2.lon - self.p1.lon) / (nodes_per_edge - 1)
        y_interval = (self.p2.lat - self.p1.lat) / (nodes_per_edge - 1)

        bottom_edge = tuple((self.p1.lon + i * x_interval, self.p1.lat) for i in range(nodes_per_edge))
        top_edge = tuple((self.p1.lon + i * x_interval, self.p2.lat) for i in range(nodes_per_edge))
        left_edge = tuple((self.p1.lon, self.p1.lat + i * y_interval) for i in range(1, nodes_per_edge - 1))
        right_edge = tuple((self.p2.lon, self.p1.lat + i * y_interval) for i in range(1, nodes_per_edge - 1))

        return Polygon(chain(bottom_edge, right_edge, reversed(top_edge), reversed(left_edge)))

    def correct_for_dateline(self) -> Sequence[Self]:
        if self.p1.lon > self.p2.lon:
            return (BBox(self.p1, LonLat(180, self.p2.lat)), BBox(LonLat(-180, self.p1.lat), self.p2))
        else:
            return (self,)
