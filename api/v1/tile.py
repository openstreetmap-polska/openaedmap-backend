from typing import Annotated

from fastapi import APIRouter, Path, Response

from config import (
    DEFAULT_CACHE_MAX_AGE,
    TILE_AEDS_CACHE_STALE,
    TILE_COUNTRIES_CACHE_MAX_AGE,
    TILE_COUNTRIES_CACHE_STALE,
    TILE_COUNTRIES_MAX_Z,
    TILE_MAX_Z,
    TILE_MIN_Z,
)
from middlewares.cache_control_middleware import make_cache_control
from services.tile_service import TileService

router = APIRouter()


@router.get('/tile/{z}/{x}/{y}.mvt')
async def get_tile(
    z: Annotated[int, Path(ge=TILE_MIN_Z, le=TILE_MAX_Z)],
    x: Annotated[int, Path(ge=0)],
    y: Annotated[int, Path(ge=0)],
):
    content = await TileService.get_tile(z, x, y)
    if z <= TILE_COUNTRIES_MAX_Z:
        cache_control = make_cache_control(TILE_COUNTRIES_CACHE_MAX_AGE, TILE_COUNTRIES_CACHE_STALE)
    else:
        cache_control = make_cache_control(DEFAULT_CACHE_MAX_AGE, TILE_AEDS_CACHE_STALE)

    # Prevent the CDN from decompressing vector tiles.
    return Response(
        content,
        headers={'Cache-Control': cache_control + ', no-transform'},
        media_type='application/vnd.mapbox-vector-tile',
    )
