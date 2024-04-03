from collections.abc import Sequence

from sentry_sdk import trace
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from db import db_read, db_write
from models.db.photo_report import PhotoReport
from services.photo_service import PhotoService


class PhotoReportService:
    @staticmethod
    @trace
    async def create(photo_id: str) -> bool:
        photo = await PhotoService.get_by_id(photo_id)
        if photo is None:
            return False  # photo not found

        async with db_write() as session:
            stmt = (
                insert(PhotoReport)
                .values({PhotoReport.photo_id: photo_id})
                .on_conflict_do_nothing(index_elements=(PhotoReport.photo_id,))
            )
            await session.execute(stmt)
            return True

    @staticmethod
    @trace
    async def get_recent(count: int = 10) -> Sequence[PhotoReport]:
        async with db_read() as session:
            stmt = select(PhotoReport).order_by(PhotoReport.created_at.desc()).limit(count)
            return (await session.scalars(stmt)).all()
