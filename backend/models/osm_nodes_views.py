from sqlalchemy import (
    Column,
    DateTime,
    BigInteger,
)

from backend.database.base import Base


class OsmNodesViews(Base):
    __tablename__ = "osm_nodes_views"
    __table_args__ = ()
    view_id = Column(
        BigInteger, primary_key=True
    )  # don't really need id but orm needs pk
    node_id = Column(BigInteger, index=True, nullable=False)
    seen_at = Column(DateTime(timezone=True), nullable=False)
