from geoalchemy2 import Geometry
from sqlalchemy import (
    Column,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB

from backend.database.base import Base


class Countries(Base):
    __tablename__ = 'countries'
    __table_args__ = ()
    country_code = Column(String(length=2), primary_key=True, doc='2 letter language code ISO 639-1')
    feature_count = Column(Integer, default=0, nullable=False)
    geometry = Column(
        Geometry(geometry_type='GEOMETRY', srid=4326, spatial_index=True),
        nullable=False,
        doc='(MULTI)POLYGON'
    )
    country_names = Column(JSONB, nullable=False)
    label_point = Column(
        Geometry(geometry_type='POINT', srid=4326, spatial_index=False),
        nullable=False,
    )
