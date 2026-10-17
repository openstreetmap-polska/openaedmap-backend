from dataclasses import dataclass
from datetime import datetime

from models.photo import Photo


@dataclass(frozen=True, slots=True)
class PhotoReport:
    id: str
    photo_id: str
    created_at: datetime
    photo: Photo
