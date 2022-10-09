from geoalchemy2 import Geometry
from sqlalchemy import (
    Column,
    Integer,
    String,
    BigInteger,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import JSONB

from backend.database.base import Base


class OsmNodes(Base):
    __tablename__ = 'osm_nodes'
    __table_args__ = ()
    node_id = Column(BigInteger, primary_key=True, autoincrement=False)
    version = Column(Integer, nullable=False)
    creator_id = Column(Integer, nullable=True)
    added_in_changeset = Column(Integer, nullable=True)
    country_code = Column(
        String(2), ForeignKey('countries.country_code'), nullable=True, index=True,
        doc='2 letter language code ISO 639-1'
    )
    geometry = Column(Geometry(geometry_type='POINT', srid=4326, spatial_index=True), nullable=False)
    tags = Column(JSONB, nullable=False)
