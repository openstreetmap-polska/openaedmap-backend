import secrets

from sqlalchemy import ForeignKey, Index, Unicode
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.db.base import Base
from models.db.created_at_mixin import CreatedAtMixin
from models.db.photo import Photo


class PhotoReport(Base, CreatedAtMixin):
    __tablename__ = 'photo_report'

    id: Mapped[str] = mapped_column(
        Unicode(32),
        init=False,
        nullable=False,
        primary_key=True,
        default=lambda: secrets.token_urlsafe(16),
    )

    photo_id: Mapped[str] = mapped_column(ForeignKey(Photo.id), nullable=False)
    photo: Mapped[Photo] = relationship(init=False, lazy='joined', innerjoin=True)

    __table_args__ = (
        Index('photo_report_photo_id_idx', photo_id, unique=True),
        Index('photo_report_created_at_idx', 'created_at'),
    )
