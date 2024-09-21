from collections.abc import Iterable
from typing import NamedTuple

from shapely import Point


class AEDGroup(NamedTuple):
    position: Point
    count: int  # pyright: ignore[reportIncompatibleMethodOverride]
    access: str

    @staticmethod
    def decide_access(accesses: Iterable[str]) -> str:
        tiered = {
            'yes': 0,
            'permissive': 1,
            'customers': 2,
            '': 3,
            'unknown': 3,
            'private': 4,
            'no': 5,
        }

        min_access = ''
        min_tier = float('inf')

        for access in accesses:
            tier = tiered.get(access)

            if (tier is not None) and (tier < min_tier):
                min_access = access
                min_tier = tier

                # early stopping
                if min_tier == 0:
                    break

        return min_access
