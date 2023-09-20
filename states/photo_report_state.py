import secrets
from time import time
from typing import Annotated, Sequence

import anyio
import pymongo
from dacite import from_dict
from fastapi import Depends

from config import PHOTO_REPORT_COLLECTION
from models.photo_report import PhotoReport
from states.photo_state import get_photo_state
from utils import as_dict


class PhotoReportState:
    async def report_by_photo_id(self, photo_id: str) -> bool:
        photo_state = get_photo_state()
        photo_info = await photo_state.get_photo_by_id(photo_id)

        if photo_info is None:
            return False  # photo not found

        if await PHOTO_REPORT_COLLECTION.find_one({'photo_id': photo_id}, projection={'_id': False}):
            return False  # already reported

        await PHOTO_REPORT_COLLECTION.insert_one(as_dict(PhotoReport(
            id=secrets.token_urlsafe(16),
            photo_id=photo_id,
            timestamp=time(),
        )))

        return True

    async def get_recent_reports(self, count: int = 10) -> Sequence[PhotoReport]:
        cursor = PHOTO_REPORT_COLLECTION \
            .find(projection={'_id': False}) \
            .sort('timestamp', pymongo.DESCENDING) \
            .limit(count)

        result = []

        async for c in cursor:
            result.append(from_dict(PhotoReport, c))

        return tuple(result)


_instance=PhotoReportState()


def get_photo_report_state() -> PhotoReportState:
    return _instance


PhotoReportStateDep=Annotated[PhotoReportState, Depends(get_photo_report_state)]