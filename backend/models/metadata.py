from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    UniqueConstraint,
    String,
)

from backend.database.base import Base


class Metadata(Base):
    __tablename__ = "metadata"
    __table_args__ = (
        CheckConstraint("id = 0"),  # make sure there is single row in this table
        CheckConstraint("total_count >= 0"),
    )
    id = Column(
        Integer, nullable=False, default=0, primary_key=True, autoincrement=False
    )
    total_count = Column(Integer, nullable=False, default=0)
    last_updated = Column(DateTime(timezone=True), nullable=False)
    last_processed_sequence = Column(String, nullable=False)
