from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from models.lonlat import LonLat


@dataclass(frozen=True, slots=True)
class CountryLabel:
    position: LonLat


@dataclass(frozen=True, slots=True)
class Country:
    names: dict[str, str]
    code: str
    geometry: BaseGeometry
    label: CountryLabel

    @property
    def name(self) -> str:
        return self.names['default']

    def get_name(self, lang: str) -> str:
        return self.names.get(lang.upper(), self.name)
