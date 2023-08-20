from dataclasses import dataclass
from math import inf
from typing import Iterable

from models.lonlat import LonLat


@dataclass(frozen=True, slots=True)
class AEDGroup:
    position: LonLat
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

        min_access = 'no', tiered['no']

        for access in accesses:
            if access == 'yes':
                return 'yes'  # early stopping

            tier = tiered.get(access, inf)
            if tier < min_access[1]:
                min_access = access, tier

        return min_access[0]
