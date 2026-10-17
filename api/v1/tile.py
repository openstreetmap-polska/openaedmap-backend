from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request

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
from models.payload import Payload, TileKey
from payload_response import payload_response
from services.byte_cache import byte_cache
from services.tile_service import TileService

router = APIRouter()


@router.get('/tile/{z}/{x}/{y}.mvt')
async def get_tile(
    request: Request,
    z: Annotated[int, Path(ge=TILE_MIN_Z, le=TILE_MAX_Z)],
    x: Annotated[int, Path(ge=0)],
    y: Annotated[int, Path(ge=0)],
):
    if x >= 2**z or y >= 2**z:
        raise HTTPException(422, 'Tile coordinates are outside the map')
    if z <= TILE_COUNTRIES_MAX_Z:
        ttl = TILE_COUNTRIES_CACHE_MAX_AGE
        stale = TILE_COUNTRIES_CACHE_STALE
        cache_control = make_cache_control(TILE_COUNTRIES_CACHE_MAX_AGE, TILE_COUNTRIES_CACHE_STALE)
    else:
        ttl = DEFAULT_CACHE_MAX_AGE
        stale = TILE_AEDS_CACHE_STALE
        cache_control = make_cache_control(DEFAULT_CACHE_MAX_AGE, TILE_AEDS_CACHE_STALE)

    async def build() -> Payload:
        return Payload.create(await TileService.get_tile(z, x, y), 'application/vnd.mapbox-vector-tile')

    payload = await byte_cache.get(TileKey(z, x, y), build, ttl=ttl.total_seconds(), stale=stale.total_seconds())
    # Prevent the CDN from decompressing vector tiles.
    response = payload_response(request, payload)
    response.headers['Cache-Control'] = cache_control + ', no-transform'
    return response
