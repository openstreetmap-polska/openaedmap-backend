from typing import Optional

from sqlalchemy.orm import Session

from backend.models.tiles import Tiles
from backend.schemas.tiles import TilesCreate


def create_tile(db: Session, tile: TilesCreate) -> Tiles:
    tile = Tiles(**tile.dict())
    db.add(tile)
    db.commit()
    db.refresh(tile)
    return tile


def generate_tile(z: int, x: int, y: int, db: Session) -> bytes:
    return db.scalar("SELECT mvt(:z, :x, :y)", params={"z": z, "x": x, "y": y}).tobytes()


def get_vector_tile(z: int, x: int, y: int, db: Session) -> bytes:
    cached_tile: Optional[Tiles] = db.get(Tiles, (z, x, y))
    if cached_tile is not None:
        return cached_tile.mvt
    else:
        tile_data = generate_tile(z, x, y, db)
        create_tile(db, TilesCreate(z=z, x=x, y=y, mvt=tile_data))
        return tile_data
