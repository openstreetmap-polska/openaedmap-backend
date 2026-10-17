from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config import PHOTOS_DIR


@dataclass(frozen=True, slots=True)
class Photo:
    id: str
    node_id: int
    user_id: int
    created_at: datetime

    @property
    def file_path(self) -> Path:
        return PHOTOS_DIR / f'{self.user_id}_{self.node_id}_{self.id}.webp'
