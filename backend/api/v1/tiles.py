from datetime import timedelta

from fastapi import APIRouter, Depends, Response, Path
from sqlalchemy.orm import Session

from backend import tiles_refresh_interval
from backend.api.deps import get_db
from backend.crud.tiles import get_vector_tile

router = APIRouter()


@router.get('/tile/{z}/{x}/{y}.mvt')
async def vector_tile(
    z: int = Path(description="Zoom level", ge=0, le=13),
    x: int = Path(description="X in XYZ tile scheme"),
    y: int = Path(description="Y in XYZ tile scheme"),
    db: Session = Depends(get_db)
) -> Response:
    """Get single vector tile.
    Usually you provide template to map library like https://host/tile/{z}/{x}/{y}.mvt and it figures out the rest."""

    max_age = tiles_refresh_interval.get(z, timedelta(seconds=60))

    return Response(
        status_code=200,
        media_type="application/vnd.mapbox-vector-tile",
        content=get_vector_tile(z, x, y, db),
        headers={
            "Cache-Control": f"max-age={int(max_age.total_seconds())}",
        }
    )
