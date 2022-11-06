from fastapi import APIRouter

from backend.api.v1 import osm_nodes, metadata


api_router = APIRouter()
api_router.include_router(
    osm_nodes.router,
    prefix='',
    tags=['osm']
)
api_router.include_router(
    metadata.router,
    prefix='',
    tags=['osm']
)
