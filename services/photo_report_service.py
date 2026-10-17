from secrets import token_urlsafe

from sentry_sdk import trace

from db import connection
from models.photo import Photo
from models.photo_report import PhotoReport
from services.photo_service import PhotoService


class PhotoReportService:
    @staticmethod
    @trace
    async def create(photo_id: str) -> bool:
        if await PhotoService.get_by_id(photo_id) is None:
            return False
        async with connection() as conn:
            await conn.execute(
                'INSERT INTO photo_report (id, photo_id) VALUES ($1, $2) ON CONFLICT (photo_id) DO NOTHING',
                token_urlsafe(16),
                photo_id,
            )
        return True

    @staticmethod
    @trace
    async def get_recent(count: int = 10) -> list[PhotoReport]:
        async with connection() as conn:
            rows = await conn.fetch(
                """
                SELECT r.id, r.photo_id, r.created_at, p.node_id, p.user_id, p.created_at AS photo_created_at
                FROM photo_report r JOIN photo p ON p.id = r.photo_id
                ORDER BY r.created_at DESC LIMIT $1
            """,
                count,
            )
        return [
            PhotoReport(
                r['id'],
                r['photo_id'],
                r['created_at'],
                Photo(r['photo_id'], r['node_id'], r['user_id'], r['photo_created_at']),
            )
            for r in rows
        ]
