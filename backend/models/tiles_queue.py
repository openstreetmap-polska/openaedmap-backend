from sqlalchemy import (
    Column,
    Integer,
    PrimaryKeyConstraint,
    DateTime,
    func,
)
from sqlalchemy.dialects.postgresql import SMALLINT

from backend.database.base import Base


class TilesQueue(Base):
    __tablename__ = 'tiles_queue'
    __table_args__ = (
        PrimaryKeyConstraint('z', 'x', 'y'),
        {'prefixes': ['UNLOGGED']}
    )
    z = Column(SMALLINT, nullable=False)
    x = Column(Integer, nullable=False)
    y = Column(Integer, nullable=False)
    inserted_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
