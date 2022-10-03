from pydantic import BaseModel, validator
from geoalchemy2 import WKTElement


class CountriesBase(BaseModel):
    country_code: str
    feature_count: int
    geometry: WKTElement
    country_names: dict

    @validator('geometry', pre=True)
    def create_spatial_object(self, val: str) -> WKTElement:
        return WKTElement(val, srid=4326)

    @validator('feature_count')
    def total_count_gte_zero(self, val: int) -> int:
        if val < 0:
            raise ValueError('feature_count must be gte 0')

        return val


class CountriesCreate(CountriesBase):
    class Config:
        orm_mode = True
