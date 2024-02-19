from typing import Annotated

from pydantic import BaseModel, ConfigDict
from shapely import Point
from shapely.geometry.base import BaseGeometry

from validators.geometry import GeometrySerializer, GeometryValidator


class CountryLabel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    position: Annotated[Point, GeometryValidator, GeometrySerializer]


class Country(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    names: dict[str, str]
    code: str
    geometry: Annotated[BaseGeometry, GeometryValidator, GeometrySerializer]
    label: CountryLabel

    @property
    def name(self) -> str:
        return self.names['default']

    def get_name(self, lang: str) -> str:
        return self.names.get(lang.upper(), self.name)
