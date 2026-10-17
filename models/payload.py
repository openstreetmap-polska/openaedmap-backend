from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import blake2b


@dataclass(frozen=True, slots=True)
class TileKey:
    z: int
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class ImageKey:
    operation: str
    url: str


type CacheKey = TileKey | ImageKey


@dataclass(frozen=True, slots=True)
class Payload:
    content: bytes
    content_type: str
    date: datetime
    etag: str

    @staticmethod
    def create(content: bytes, content_type: str, date: datetime | None = None) -> Payload:
        identity = blake2b(content_type.encode() + b'\0', digest_size=16)
        identity.update(content)
        digest = identity.hexdigest()
        return Payload(content, content_type, date or datetime.now(UTC), f'W/"{digest}"')
