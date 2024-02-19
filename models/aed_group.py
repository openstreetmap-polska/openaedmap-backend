from collections.abc import Iterable
from typing import NamedTuple

from shapely import Point


class AEDGroup(NamedTuple):
    position: Point
    count: int
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

        min_access = '', float('inf')

        for access in accesses:
            if access == 'yes':
                return 'yes'  # early stopping

            tier = tiered.get(access, float('inf'))
            if tier < min_access[1]:
                min_access = access, tier

        return min_access[0]
