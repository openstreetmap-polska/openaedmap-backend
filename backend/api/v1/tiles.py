from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from backend.api.deps import get_db
from backend.crud.tiles import get_vector_tile

router = APIRouter()


@router.get('/tile/{z}/{x}/{y}.mvt')
async def vector_tile(z: int, x: int, y: int, db: Session = Depends(get_db)) -> Response:

    return Response(
        status_code=200,
        media_type="application/vnd.mapbox-vector-tile",
        content=get_vector_tile(z, x, y, db),
    )
