from pydantic import BaseModel


class TilesBase(BaseModel):
    z: int
    x: int
    y: int
    mvt: bytes


class TilesCreate(TilesBase):
    class Config:
        orm_mode = True
