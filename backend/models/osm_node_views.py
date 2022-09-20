from sqlalchemy import (
    Column,
    DateTime,
    BigInteger,
)

from backend.database.base import Base


class OsmNodesViews(Base):
    __tablename__ = 'osm_nodes_views'
    __table_args__ = ()
    node_id = Column(BigInteger, indexed=True, nullable=False)
    last_updated = Column(DateTime(timezone=True), nullable=False)
