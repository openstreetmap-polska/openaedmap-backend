from dataclasses import dataclass

from models.lonlat import LonLat


@dataclass(frozen=True, slots=True)
class AEDGroup:
    position: LonLat
    count: int
    access: str
