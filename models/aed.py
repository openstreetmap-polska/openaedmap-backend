from typing import Annotated

from pydantic import BaseModel, ConfigDict
from shapely import Point

from validators.geometry import GeometrySerializer, GeometryValidator


class AED(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    id: int
    position: Annotated[Point, GeometryValidator, GeometrySerializer]
    country_codes: list[str] | None
    tags: dict[str, str]
    version: int

    @property
    def access(self) -> str:
        return self.tags.get('access', '')
