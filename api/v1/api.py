from fastapi import APIRouter

import api.v1.countries as countries
import api.v1.node as node
import api.v1.photos as photos
import api.v1.tile as tile

router = APIRouter()
router.include_router(countries.router)
router.include_router(node.router)
router.include_router(photos.router)
router.include_router(tile.router)
