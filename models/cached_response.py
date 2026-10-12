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

    @property
    def fresh_until(self) -> datetime:
        return self.date + self.max_age

    @property
    def expires_at(self) -> datetime:
        return self.fresh_until + self.stale

    def to_bytes(self) -> bytes:
        return pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def from_bytes(cls, buffer: bytes) -> CachedResponse:
        return pickle.loads(buffer)  # noqa: S301
