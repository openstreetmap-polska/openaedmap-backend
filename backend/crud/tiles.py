from typing import Optional

from sqlalchemy.orm import Session

from backend.models.tiles import Tiles


def get_vector_tile(z: int, x: int, y: int, db: Session) -> bytes:
    cached_tile: Optional[Tiles] = db.get(Tiles, (z, x, y))
    if cached_tile is not None:
        return cached_tile.mvt
    else:
        return b""
