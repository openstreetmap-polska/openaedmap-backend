from dataclasses import dataclass

from anyio import Path

from config import PHOTOS_DIR


@dataclass(frozen=True, slots=True)
class PhotoInfo:
    id: str
    node_id: str
    user_id: str
    timestamp: float

    @property
    def path(self) -> Path:
        return PHOTOS_DIR / f'{self.user_id}_{self.node_id}_{self.id}.webp'
