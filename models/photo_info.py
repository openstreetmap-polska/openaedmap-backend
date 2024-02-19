from anyio import Path
from pydantic import BaseModel, ConfigDict

from config import PHOTOS_DIR


class PhotoInfo(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    id: str
    node_id: str
    user_id: str
    timestamp: float

    @property
    def path(self) -> Path:
        return PHOTOS_DIR / f'{self.user_id}_{self.node_id}_{self.id}.webp'
