from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.deps import get_db
from backend.crud.queues import get_tiles_queue_count, get_countries_queue_count, TilesQueueInfo, CountriesQueueInfo

router = APIRouter()


@router.get('/queue/tiles/info')
async def queue_tiles_info(db: Session = Depends(get_db)) -> TilesQueueInfo:
    return get_tiles_queue_count(db)


@router.get('/queue/countries/info')
async def queue_countries_info(db: Session = Depends(get_db)) -> CountriesQueueInfo:
    return get_countries_queue_count(db)
