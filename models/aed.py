from dataclasses import dataclass

from models.lonlat import LonLat


@dataclass(frozen=True, slots=True)
class AED:
    id: int
    position: LonLat
    country_codes: list[str] | None
    tags: dict[str, str]
    version: int

    @property
    def access(self) -> str:
        return self.tags.get('access', '')
