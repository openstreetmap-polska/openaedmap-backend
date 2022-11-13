from fastapi import APIRouter

from backend.api.v1 import osm_nodes, metadata, countries, tiles

api_router = APIRouter()
api_router.include_router(osm_nodes.router, prefix='', tags=['osm'])
api_router.include_router(metadata.router, prefix='', tags=['osm'])
api_router.include_router(countries.router, prefix='', tags=['osm'])
api_router.include_router(tiles.router, prefix='', tags=['osm'])
