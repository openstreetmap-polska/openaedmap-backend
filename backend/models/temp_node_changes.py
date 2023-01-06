from sqlalchemy import (
    Column,
    Integer,
    String,
    BigInteger,
    DateTime,
    PrimaryKeyConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, DOUBLE_PRECISION

from backend.database.base import Base


class TempNodeChanges(Base):
    """Table for nodes staged in update process."""
    __tablename__ = 'temp_node_changes'
    __table_args__ = (
        PrimaryKeyConstraint('node_id', 'version'),
        {'prefixes': ['UNLOGGED']}
    )
    change_type = Column(String, nullable=False)
    node_id = Column(BigInteger, nullable=False)
    version = Column(Integer, nullable=False)
    uid = Column(Integer)
    user = Column(String)
    changeset = Column(Integer)
    latitude = Column(DOUBLE_PRECISION)
    longitude = Column(DOUBLE_PRECISION)
    tags = Column(JSONB)
    version_timestamp = Column(DateTime(timezone=True))
