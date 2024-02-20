from typing import NamedTuple, Self

import numpy as np
from shapely import Point, get_coordinates, points
from shapely.geometry import Polygon


class BBox(NamedTuple):
    p1: Point
    p2: Point

    def extend(self, percentage: float) -> Self:
        p1_coords = get_coordinates(self.p1)[0]
        p2_coords = get_coordinates(self.p2)[0]

        spans = p2_coords - p1_coords
        deltas = spans * percentage

        p1_coords = np.clip(p1_coords - deltas, [-180, -90], [180, 90])
        p2_coords = np.clip(p2_coords + deltas, [-180, -90], [180, 90])

        p1, p2 = points((p1_coords, p2_coords))
        return BBox(p1, p2)

    @classmethod
    def from_tuple(cls, bbox: tuple[float, float, float, float]) -> Self:
        p1, p2 = points(((bbox[0], bbox[1]), (bbox[2], bbox[3])))
        return cls(p1, p2)

    def to_tuple(self) -> tuple[float, float, float, float]:
        p1_x, p1_y = get_coordinates(self.p1)[0]
        p2_x, p2_y = get_coordinates(self.p2)[0]

        return (p1_x, p1_y, p2_x, p2_y)

    def to_polygon(self, *, nodes_per_edge: int = 2) -> Polygon:
        p1_x, p1_y = get_coordinates(self.p1)[0]
        p2_x, p2_y = get_coordinates(self.p2)[0]

        if nodes_per_edge <= 2:
            return Polygon(
                (
                    (p1_x, p1_y),
                    (p2_x, p1_y),
                    (p2_x, p2_y),
                    (p1_x, p2_y),
                    (p1_x, p1_y),
                )
            )

        x_vals = np.linspace(p1_x, p2_x, nodes_per_edge)
        y_vals = np.linspace(p1_y, p2_y, nodes_per_edge)

        bottom_edge = np.column_stack((x_vals, np.full(nodes_per_edge, p1_y)))
        top_edge = np.column_stack((x_vals, np.full(nodes_per_edge, p2_y)))
        left_edge = np.column_stack((np.full(nodes_per_edge - 2, p1_x), y_vals[1:-1]))
        right_edge = np.column_stack((np.full(nodes_per_edge - 2, p2_x), y_vals[1:-1]))

        all_coords = np.concatenate((bottom_edge, right_edge, top_edge[::-1], left_edge[::-1]))

        return Polygon(all_coords)

    def correct_for_dateline(self) -> tuple[Self, ...]:
        if self.p1.x > self.p2.x:
            b1_p1 = self.p1
            b2_p2 = self.p2
            b1_p2, b2_p1 = points(((180, self.p2.y), (-180, self.p1.y)))
            return (BBox(b1_p1, b1_p2), BBox(b2_p1, b2_p2))
        else:
            return (self,)
