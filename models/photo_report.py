from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PhotoReport:
    id: str
    photo_id: str
    timestamp: float
