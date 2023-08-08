from dataclasses import dataclass

from models.lonlat import LonLat


@dataclass(frozen=True, slots=True)
class AED:
    id: str
    position: LonLat
    country_codes: list[str] | None
    tags: dict[str, str]

    @property
    def access(self) -> str:
        return self.tags.get('access', '')
