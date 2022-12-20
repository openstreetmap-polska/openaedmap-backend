from geoalchemy2 import func
from sqlalchemy import (
    Column,
    Integer,
    PrimaryKeyConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import BYTEA, SMALLINT

from backend.database.base import Base


class Tiles(Base):
    __tablename__ = 'tiles'
    __table_args__ = (
        PrimaryKeyConstraint('z', 'x', 'y'),
    )
    z = Column(SMALLINT, nullable=False)
    x = Column(Integer, nullable=False)
    y = Column(Integer, nullable=False)
    mvt = Column(BYTEA, nullable=False)

    bbox_index = Index('bbox', func.ST_TileEnvelope(z, x, y))
