from typing import NamedTuple, Self

import numpy as np
from shapely import Point
from shapely.geometry import Polygon


class BBox(NamedTuple):
    p1: Point
    p2: Point

    def extend(self, percentage: float) -> Self:
        lon_span = self.p2.x - self.p1.x
        lat_span = self.p2.y - self.p1.y
        lon_delta = lon_span * percentage
        lat_delta = lat_span * percentage

        new_p1_lon = max(-180, min(180, self.p1.x - lon_delta))
        new_p1_lat = max(-90, min(90, self.p1.y - lat_delta))
        new_p2_lon = max(-180, min(180, self.p2.x + lon_delta))
        new_p2_lat = max(-90, min(90, self.p2.y + lat_delta))

        return BBox(
            p1=Point(new_p1_lon, new_p1_lat),
            p2=Point(new_p2_lon, new_p2_lat),
        )

    @classmethod
    def from_tuple(cls, bbox: tuple[float, float, float, float]) -> Self:
        return cls(Point(bbox[0], bbox[1]), Point(bbox[2], bbox[3]))

    def to_tuple(self) -> tuple[float, float, float, float]:
        return (self.p1.x, self.p1.y, self.p2.x, self.p2.y)

    def to_polygon(self, *, nodes_per_edge: int = 2) -> Polygon:
        if nodes_per_edge <= 2:
            return Polygon(
                [
                    (self.p1.x, self.p1.y),
                    (self.p2.x, self.p1.y),
                    (self.p2.x, self.p2.y),
                    (self.p1.x, self.p2.y),
                    (self.p1.x, self.p1.y),
                ]
            )

        x_vals = np.linspace(self.p1.x, self.p2.x, nodes_per_edge)
        y_vals = np.linspace(self.p1.y, self.p2.y, nodes_per_edge)

        bottom_edge = np.column_stack((x_vals, np.full(nodes_per_edge, self.p1.y)))
        top_edge = np.column_stack((x_vals, np.full(nodes_per_edge, self.p2.y)))
        left_edge = np.column_stack((np.full(nodes_per_edge - 2, self.p1.x), y_vals[1:-1]))
        right_edge = np.column_stack((np.full(nodes_per_edge - 2, self.p2.x), y_vals[1:-1]))

        all_coords = np.concatenate((bottom_edge, right_edge, top_edge[::-1], left_edge[::-1]))

        return Polygon(all_coords)

    def correct_for_dateline(self) -> tuple[Self, ...]:
        if self.p1.x > self.p2.x:
            return (BBox(self.p1, Point(180, self.p2.y)), BBox(Point(-180, self.p1.y), self.p2))
        else:
            return (self,)
