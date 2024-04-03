from datetime import datetime, timedelta

from msgspec import Struct

from utils import MSGPACK_ENCODE, typed_msgpack_decoder


class CachedResponse(Struct, forbid_unknown_fields=True, array_like=True):
    date: datetime
    max_age: timedelta
    stale: timedelta
    status_code: int
    headers: list[tuple[bytes, bytes]]
    content: bytes

    def to_bytes(self) -> bytes:
        return MSGPACK_ENCODE(self)

    @classmethod
    def from_bytes(cls, buffer: bytes) -> 'CachedResponse':
        return _decode(buffer)


_decode = typed_msgpack_decoder(CachedResponse).decode
