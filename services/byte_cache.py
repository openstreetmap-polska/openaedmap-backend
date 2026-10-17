import logging
from asyncio import Task, create_task, gather, shield
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic

from fastapi import HTTPException

from config import RESPONSE_CACHE_BYTES
from models.payload import CacheKey, Payload


@dataclass(slots=True)
class _Job:
    task: Task[Payload]
    readers: int = 0
    background: bool = False


@dataclass(slots=True)
class _Entry:
    payload: Payload
    fresh_until: float
    stale_until: float
    size: int


class ByteCache:
    def __init__(
        self,
        *,
        max_bytes: int = RESPONSE_CACHE_BYTES,
        max_jobs: int = 16,
        max_readers: int = 64,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_bytes < 0 or max_jobs < 1 or max_readers < 1:
            raise ValueError('Invalid byte cache capacity')
        self.max_bytes = max_bytes
        self.max_jobs = max_jobs
        self.max_readers = max_readers
        self.clock = clock
        self.bytes = 0
        self.entries: OrderedDict[CacheKey, _Entry] = OrderedDict()
        self.jobs: dict[CacheKey, _Job] = {}
        self.active: set[Task[Payload]] = set()

    async def get(
        self, key: CacheKey, build: Callable[[], Awaitable[Payload]], *, ttl: float, stale: float = 0
    ) -> Payload:
        entry = self.entries.get(key)
        if entry is not None:
            now = self.clock()
            if now < entry.fresh_until:
                self.entries.move_to_end(key)
                return entry.payload
            if now >= entry.stale_until:
                del self.entries[key]
                self.bytes -= entry.size
                entry = None
        job = self.jobs.get(key)
        if job is None:
            if len(self.active) >= self.max_jobs:
                if entry is not None:
                    return entry.payload
                raise HTTPException(503, 'Origin capacity is busy', headers={'Retry-After': '1'})

            async def produce() -> Payload:
                return await build()

            task = create_task(produce(), name='public-payload')
            job = self.jobs[key] = _Job(task, background=entry is not None)
            self.active.add(task)

            def completed(task: Task[Payload]) -> None:
                self.active.remove(task)
                current = self.jobs.get(key) is job
                if current:
                    del self.jobs[key]
                if task.cancelled():
                    return
                error = task.exception()
                if error is None and current:
                    self._store(key, task.result(), ttl, stale)
                elif error is not None and job.background:
                    logging.error('Failed to refresh a public payload', exc_info=error)

            task.add_done_callback(completed)
        if entry is not None:
            self.entries.move_to_end(key)
            return entry.payload
        if job.readers >= self.max_readers:
            raise HTTPException(503, 'Origin capacity is busy', headers={'Retry-After': '1'})
        job.readers += 1
        try:
            return await shield(job.task)
        finally:
            job.readers -= 1
            if job.readers == 0 and not job.background and not job.task.done():
                if self.jobs.get(key) is job:
                    del self.jobs[key]
                job.task.cancel()

    def _store(self, key: CacheKey, value: Payload, ttl: float, stale: float) -> None:
        size = len(value.content) + len(repr(key).encode()) + len(value.content_type) + 256
        if size > self.max_bytes or ttl + stale <= 0:
            return
        if previous := self.entries.pop(key, None):
            self.bytes -= previous.size
        while self.entries and self.bytes + size > self.max_bytes:
            self.bytes -= self.entries.popitem(last=False)[1].size
        fresh_until = self.clock() + ttl
        self.entries[key] = _Entry(value, fresh_until, fresh_until + stale, size)
        self.bytes += size

    def invalidate(self, matches: Callable[[CacheKey], bool], *, stale: bool = False) -> None:
        for key in tuple(self.entries):
            if matches(key):
                if stale:
                    self.entries[key].fresh_until = float('-inf')
                else:
                    self.bytes -= self.entries.pop(key).size
        # Retired jobs can finish their readers, but cannot publish over a successor.
        for key in tuple(self.jobs):
            if matches(key):
                del self.jobs[key]

    async def close(self) -> None:
        self.invalidate(lambda _: True)
        for task in self.active:
            task.cancel()
        await gather(*self.active, return_exceptions=True)


byte_cache = ByteCache()
