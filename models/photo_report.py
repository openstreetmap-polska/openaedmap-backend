from pydantic import BaseModel, ConfigDict


class PhotoReport(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    id: str
    photo_id: str
    timestamp: float
