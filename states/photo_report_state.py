import secrets
from collections.abc import Sequence
from time import time

import pymongo
from dacite import from_dict
from sentry_sdk import trace

from config import PHOTO_REPORT_COLLECTION
from models.photo_report import PhotoReport
from states.photo_state import PhotoState
from utils import as_dict


class PhotoReportState:
    @staticmethod
    @trace
    async def report_by_photo_id(photo_id: str) -> bool:
        photo_info = await PhotoState.get_photo_by_id(photo_id)

        if photo_info is None:
            return False  # photo not found

        if await PHOTO_REPORT_COLLECTION.find_one({'photo_id': photo_id}, projection={'_id': False}):
            return False  # already reported

        await PHOTO_REPORT_COLLECTION.insert_one(
            as_dict(
                PhotoReport(
                    id=secrets.token_urlsafe(16),
                    photo_id=photo_id,
                    timestamp=time(),
                )
            )
        )

        return True

    @staticmethod
    @trace
    async def get_recent_reports(count: int = 10) -> Sequence[PhotoReport]:
        cursor = (
            PHOTO_REPORT_COLLECTION.find(projection={'_id': False}).sort('timestamp', pymongo.DESCENDING).limit(count)
        )

        result = []

        async for c in cursor:
            result.append(from_dict(PhotoReport, c))  # noqa: PERF401

        return tuple(result)
