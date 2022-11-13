from geoalchemy2 import Geometry
from geoalchemy2 import func
from sqlalchemy import (
    Column,
    Integer,
    String,
    select,
    column,
)
from sqlalchemy.dialects.postgresql import JSONB

from backend.database.base import Base


def find_label_point(context):
    geom = context.get_current_parameters()['geometry']
    max_inscribed_circle = func.ST_MaximumInscribedCircle(geom)
    return context.connection.scalar(
        select(column('center')).select_from(max_inscribed_circle)
    )


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
        default=find_label_point,
    )
