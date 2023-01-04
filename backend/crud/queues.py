from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.models import TilesQueue, CountriesQueue


@dataclass(frozen=True)
class TilesQueueInfo:
    count: int


@dataclass(frozen=True)
class CountriesQueueInfo:
    count: int


def get_tiles_queue_count(db: Session) -> TilesQueueInfo:
    return TilesQueueInfo(count=db.query(TilesQueue).count())


def get_countries_queue_count(db: Session) -> CountriesQueueInfo:
    return CountriesQueueInfo(count=db.query(CountriesQueue).count())
