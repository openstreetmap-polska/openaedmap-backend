import asyncio
import threading

import pytest


@pytest.mark.asyncio
async def test_repeated_cancellation_keeps_native_work_owned_until_it_finishes():
    from utils import run_blocking

    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    slot = asyncio.Semaphore(1)

    def native():
        loop.call_soon_threadsafe(started.set)
        release.wait()

    async def operation():
        async with slot:
            await run_blocking(native)

    task = asyncio.create_task(operation())
    try:
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()
        assert slot.locked()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
