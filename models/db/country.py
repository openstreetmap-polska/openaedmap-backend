from shapely import MultiPolygon, Point, Polygon
from sqlalchemy import Index, Unicode
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.db.base import Base
from models.geometry import PointType, PolygonType


class Country(Base):
    __tablename__ = 'country'

    code: Mapped[str] = mapped_column(
        Unicode(8),
        nullable=False,
        primary_key=True,
    )

    names: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    geometry: Mapped[Polygon | MultiPolygon] = mapped_column(PolygonType, nullable=False)
    label_position: Mapped[Point] = mapped_column(PointType, nullable=False)

    __table_args__ = (Index('country_geometry_idx', geometry, postgresql_using='gist'),)

    @property
    def name(self) -> str:
        return self.names['default']

    def get_name(self, lang: str) -> str:
        return self.names.get(lang.upper(), self.name)
