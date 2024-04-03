import secrets

from anyio import Path
from sqlalchemy import BigInteger, Unicode
from sqlalchemy.orm import Mapped, mapped_column

from config import PHOTOS_DIR
from models.db.base import Base
from models.db.created_at_mixin import CreatedAtMixin


class Photo(Base, CreatedAtMixin):
    __tablename__ = 'photo'

    id: Mapped[str] = mapped_column(
        Unicode(32),
        init=False,
        nullable=False,
        primary_key=True,
        default=lambda: secrets.token_urlsafe(16),
    )

    node_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    @property
    def file_path(self) -> Path:
        return PHOTOS_DIR / f'{self.user_id}_{self.node_id}_{self.id}.webp'
