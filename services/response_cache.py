import logging
from asyncio import CancelledError, create_task, sleep
from collections.abc import AsyncIterator
from compression.zstd import compress, decompress
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from secrets import token_hex

from sentry_sdk import trace
from valkey.asyncio import Valkey
from valkey.exceptions import ConnectionError as CacheConnectionError
from valkey.exceptions import LockError, ResponseError
from valkey.exceptions import TimeoutError as CacheTimeoutError

from models.cached_response import CachedResponse

_UNAVAILABLE = (CacheConnectionError, CacheTimeoutError, ResponseError)
_STORE = """
if redis.call('get', KEYS[2]) ~= ARGV[1] then return 0 end
redis.call('set', KEYS[1], ARGV[2], 'PX', ARGV[3])
return 1
"""


class ResponseCacheEntry:
    """A response and its renewable, cross-worker regeneration lease."""

    def __init__(self, client: Valkey, key: str, *, lease_seconds: float = 60) -> None:
        self.client = client
        self.key = key
        self.lease_seconds = lease_seconds
        self.available = True
        self._token: str | None = None

    def _unavailable(self, exc: Exception) -> None:
        self.available = False
        logging.warning('Response cache unavailable: %s', exc)

    @trace
    async def get(self) -> CachedResponse | None:
        if not self.available:
            return None
        value = await self.client.get(self.key)
        if value is None:
            return None
        cached = CachedResponse.from_bytes(decompress(value))
        return cached if datetime.now(UTC) < cached.expires_at else None

    @asynccontextmanager
    async def regenerate(self, *, wait: bool) -> AsyncIterator[bool]:
        if not self.available:
            yield True
            return

        lock = self.client.lock('refresh:' + self.key, timeout=self.lease_seconds, thread_local=False)
        token = token_hex(16)
        try:
            acquired = await lock.acquire(blocking=wait, token=token)
        except _UNAVAILABLE as exc:
            self._unavailable(exc)
            yield True
            return
        if not acquired:
            yield False
            return

        self._token = token

        async def renew() -> None:
            try:
                while True:
                    await sleep(self.lease_seconds / 3)
                    await lock.reacquire()
            except _UNAVAILABLE as exc:
                self._unavailable(exc)
                self._token = None
            except LockError:
                self._token = None

        renewal = create_task(renew(), name='response-cache-lease')
        try:
            yield True
        finally:
            self._token = None
            renewal.cancel()
            with suppress(CancelledError):
                await renewal
            try:
                await lock.release()
            except _UNAVAILABLE as exc:
                self._unavailable(exc)
            except LockError:
                pass

    @trace
    async def store(self, cached: CachedResponse) -> None:
        if not self.available or self._token is None:
            return
        ttl_ms = int((cached.expires_at - datetime.now(UTC)).total_seconds() * 1000)
        if ttl_ms <= 0:
            return
        value = compress(cached.to_bytes(), level=1)
        # Publication must check ownership atomically: an expired or evicted
        # lease must not let an old refresh overwrite its successor.
        await self.client.eval(_STORE, 2, self.key, 'refresh:' + self.key, self._token, value, ttl_ms)
