from typing import Optional, Dict

from geoalchemy2 import WKTElement
from pydantic import BaseModel, validator


class OsmNodesBase(BaseModel):
    node_id: int
    version: int
    creator_id: Optional[int]
    added_in_changeset: Optional[int]
    geometry: WKTElement
    tags: Dict[str, str]

    @validator('geometry', pre=True)
    def create_spatial_object(self, val: str) -> WKTElement:
        return WKTElement(val, srid=4326)

    @validator('country_code')
    def country_code_len_eq_2(self, val: Optional[str]) -> Optional[str]:
        if val is not None and len(val) != 2:
            raise ValueError('country_code must be null or 2 character string')

        return val

    @validator('node_id')
    def node_id_gte_zero(self, val):
        if val < 0:
            raise ValueError('node_id must be gte 0')

        return val


class OsmNodesCreate(OsmNodesBase):
    class Config:
        orm_mode = True
