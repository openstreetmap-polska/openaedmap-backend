from sqlalchemy import Unicode
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.db.base import Base


class State(Base):
    __tablename__ = 'state'

    key: Mapped[str] = mapped_column(
        Unicode,
        nullable=False,
        primary_key=True,
    )

    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
