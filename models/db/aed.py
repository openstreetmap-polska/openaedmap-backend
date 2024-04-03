from shapely import Point
from sqlalchemy import ARRAY, BigInteger, Index, Unicode
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.db.base import Base
from models.geometry import PointType


class AED(Base):
    __tablename__ = 'aed'

    id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        primary_key=True,
    )

    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tags: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    position: Mapped[Point] = mapped_column(PointType, nullable=False)

    country_codes: Mapped[list[str] | None] = mapped_column(
        ARRAY(Unicode(8), dimensions=1),
        nullable=True,
        default=None,
    )

    __table_args__ = (
        Index('aed_position_idx', position, postgresql_using='gist'),
        Index('aed_country_codes_idx', country_codes, postgresql_using='gin'),
    )

    @property
    def access(self) -> str:
        return self.tags.get('access', '')
