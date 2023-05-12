from pydantic import BaseModel, validator

from datetime import datetime


class MetadataBase(BaseModel):
    id: int
    total_count: int
    last_updated: datetime
    last_processed_sequence: str

    @validator("id")
    def id_eq_0(cls, val):
        if val != 0:
            raise ValueError("id must be 0")

        return val

    @validator("total_count")
    def total_count_gte_zero(cls, val):
        if val < 0:
            raise ValueError("total_count must be gte 0")

        return val


class MetadataCreate(MetadataBase):
    class Config:
        orm_mode = True
