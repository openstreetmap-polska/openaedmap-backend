from pydantic import BaseModel

from datetime import datetime


class OsmNodesViewsBase(BaseModel):
    node_id: int
    seen_at: datetime


class OsmNodesViewsCreate(OsmNodesViewsBase):
    class Config:
        orm_mode = True
