from dataclasses import dataclass

from shapely.geometry import MultiPolygon, Polygon

from models.lonlat import LonLat


@dataclass(frozen=True, slots=True)
class CountryLabel:
    position: LonLat
    min_z: float
    max_z: float


@dataclass(frozen=True, slots=True)
class Country:
    names: dict[str, str]
    code: str
    geometry: MultiPolygon | Polygon
    label: CountryLabel

    @property
    def name(self) -> str:
        return self.names['default']

    def get_name(self, lang: str) -> str:
        return self.names.get(lang.upper(), self.name)
