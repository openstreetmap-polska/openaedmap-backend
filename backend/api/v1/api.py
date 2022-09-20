from fastapi import APIRouter

from backend.api.v1 import buildings


api_router = APIRouter()
api_router.include_router(
    buildings.router,
    prefix='/buildings',
    tags=['buildings']
)
