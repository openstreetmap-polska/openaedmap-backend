from dataclasses import dataclass

from models.lonlat import LonLat


@dataclass(frozen=True, slots=True)
class AED:
    id: str
    position: LonLat
    tags: dict[str, str]

    @property
    def access(self) -> str:
        return self.tags.get('access', '')
