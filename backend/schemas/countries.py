from pydantic import BaseModel, validator
from geoalchemy2 import WKTElement


class CountriesBase(BaseModel):
    country_code: str
    feature_count: int = 0
    label_point: str
    geometry: str
    country_names: dict

    @validator("feature_count")
    def total_count_gte_zero(cls, val: int) -> int:
        if val < 0:
            raise ValueError("feature_count must be gte 0")

        return val


class CountriesCreate(CountriesBase):
    class Config:
        orm_mode = True
