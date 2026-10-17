import asyncio

import pytest

from models.payload import ImageKey, Payload, TileKey
from services.byte_cache import ByteCache


@pytest.mark.asyncio
@pytest.mark.parametrize('max_bytes', [0, 4096], ids=['no-retention', 'retained'])
async def test_overlapping_readers_share_work_even_if_first_reader_cancels(max_bytes):
    cache = ByteCache(max_bytes=max_bytes)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return Payload.create(b'shared', 'text/plain')

    key = TileKey(16, 10, 12)
    first = asyncio.create_task(cache.get(key, build, ttl=60))
    await started.wait()
    others = [asyncio.create_task(cache.get(key, build, ttl=60)) for _ in range(5)]
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    results = await asyncio.gather(*others)
    assert calls == 1
    assert all(result.content == b'shared' for result in results)
    await cache.get(key, build, ttl=60)
    assert calls == (2 if max_bytes == 0 else 1)
    await cache.close()


@pytest.mark.asyncio
async def test_invalidated_fill_cannot_replace_or_remove_successor():
    cache = ByteCache()
    started, release = asyncio.Event(), asyncio.Event()
    key = TileKey(12, 7, 8)

    async def old():
        started.set()
        await release.wait()
        return Payload.create(b'old snapshot', 'text/plain')

    async def new():
        return Payload.create(b'new snapshot', 'text/plain')

    pending = asyncio.create_task(cache.get(key, old, ttl=60))
    await started.wait()
    cache.invalidate(lambda item: item == key)
    assert (await cache.get(key, new, ttl=60)).content == b'new snapshot'
    release.set()
    assert (await pending).content == b'old snapshot'
    assert (await cache.get(key, old, ttl=60)).content == b'new snapshot'
    await cache.close()


@pytest.mark.asyncio
async def test_cancelled_work_keeps_capacity_until_cleanup_finishes():
    cache = ByteCache(max_jobs=1)
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def build():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    pending = asyncio.create_task(cache.get(TileKey(10, 1, 2), build, ttl=60))
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await cleaning.wait()
    replacement_started = asyncio.Event()

    async def replacement():
        replacement_started.set()
        return Payload.create(b'replacement', 'text/plain')

    queued = asyncio.create_task(cache.get(TileKey(10, 2, 2), replacement, ttl=60))
    try:
        await asyncio.sleep(0)
        assert not replacement_started.is_set()
        assert not queued.done()
        release.set()
        assert (await queued).content == b'replacement'
    finally:
        release.set()
        queued.cancel()
        await asyncio.gather(queued, return_exceptions=True)
        await cache.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('max_bytes', [0, 4096], ids=['no-retention', 'retained'])
async def test_cold_readers_wait_for_capacity_then_share_one_fill(max_bytes):
    cache = ByteCache(max_bytes=max_bytes, max_jobs=1)
    first_started, first_release = asyncio.Event(), asyncio.Event()
    next_started, next_release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def first_build():
        first_started.set()
        await first_release.wait()
        return Payload.create(b'first', 'text/plain')

    async def next_build():
        nonlocal calls
        calls += 1
        next_started.set()
        await next_release.wait()
        return Payload.create(b'next', 'text/plain')

    try:
        async with asyncio.TaskGroup() as tasks:
            first = tasks.create_task(cache.get(TileKey(10, 1, 1), first_build, ttl=60))
            await first_started.wait()
            readers = [tasks.create_task(cache.get(TileKey(10, 2, 1), next_build, ttl=60)) for _ in range(3)]
            cancelled = tasks.create_task(cache.get(TileKey(10, 3, 1), next_build, ttl=60))
            await asyncio.sleep(0)
            assert all(not task.done() for task in readers)
            assert not next_started.is_set()
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            first_release.set()
            await next_started.wait()
            next_release.set()
        assert first.result().content == b'first'
        assert all(task.result().content == b'next' for task in readers)
        assert calls == 1
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_expiry_and_lru_evict_without_rewriting_payload_age():
    now = 100.0
    cache = ByteCache(max_bytes=800, clock=lambda: now)
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        return Payload.create(str(calls).encode(), 'text/plain')

    a, b, c = (TileKey(8, x, 3) for x in (1, 2, 3))
    first = await cache.get(a, build, ttl=60)
    await cache.get(b, build, ttl=60)
    assert await cache.get(a, build, ttl=60) is first
    await cache.get(c, build, ttl=60)
    assert await cache.get(a, build, ttl=60) is first
    now += 61
    assert (await cache.get(a, build, ttl=60)).content != first.content
    assert cache.bytes <= cache.max_bytes
    await cache.close()


@pytest.mark.asyncio
async def test_oversized_payload_is_delivered_without_retention():
    cache = ByteCache(max_bytes=512)
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        return Payload.create(b'complete image' * 100, 'image/png')

    key = ImageKey('direct', 'https://example/image.png')
    assert (await cache.get(key, build, ttl=60)).content == b'complete image' * 100
    await cache.get(key, build, ttl=60)
    assert calls == 2
    assert cache.bytes == 0
    await cache.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalidate', [False, True], ids=['expiry', 'publication'])
async def test_stale_tiles_return_immediately_while_one_shared_refresh_runs(invalidate):
    now = 200.0
    cache = ByteCache(clock=lambda: now)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        if calls > 1:
            started.set()
            await release.wait()
        return Payload.create(str(calls).encode(), 'application/vnd.mapbox-vector-tile')

    key = TileKey(14, 21, 32)
    old = await cache.get(key, build, ttl=60, stale=300)
    if invalidate:
        cache.invalidate(lambda item: item == key, stale=True)
    else:
        now += 61
    assert await cache.get(key, build, ttl=60, stale=300) is old
    await started.wait()
    for _ in range(5):
        assert await cache.get(key, build, ttl=60, stale=300) is old
    assert calls == 2
    release.set()
    await asyncio.gather(*cache.active)
    assert (await cache.get(key, build, ttl=60, stale=300)).content == b'2'
    await cache.close()


@pytest.mark.asyncio
async def test_failed_refresh_preserves_stale_body_only_within_its_age_limit(caplog):
    now = 100.0
    cache = ByteCache(clock=lambda: now)
    fail = False

    async def build():
        if fail:
            raise ConnectionError('database unavailable')
        return Payload.create(b'usable tile', 'application/vnd.mapbox-vector-tile')

    key = TileKey(8, 1, 2)
    old = await cache.get(key, build, ttl=60, stale=300)
    now += 61
    fail = True
    assert await cache.get(key, build, ttl=60, stale=300) is old
    await asyncio.gather(*cache.active, return_exceptions=True)
    assert 'Failed to refresh' in caplog.text
    now += 300
    with pytest.raises(ConnectionError):
        await cache.get(key, build, ttl=60, stale=300)
    await cache.close()
