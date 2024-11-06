import pickle
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(kw_only=True, slots=True)
class CachedResponse:
    date: datetime
    max_age: timedelta
    stale: timedelta
    status_code: int
    headers: list[tuple[bytes, bytes]]
    content: bytes

    def to_bytes(self) -> bytes:
        return pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def from_bytes(cls, buffer: bytes) -> 'CachedResponse':
        return pickle.loads(buffer)  # noqa: S301
