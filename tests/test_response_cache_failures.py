import asyncio
from contextlib import asynccontextmanager

import pytest
from valkey.asyncio import Valkey

from tests.cache_helpers import body, headers, request, respond


@pytest.mark.asyncio
async def test_cache_outage_coalesces_local_work_and_preserves_response(tmp_path):
    from middlewares.cache_response_middleware import CacheResponseMiddleware

    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()
    async with Valkey(unix_socket_path=str(tmp_path / 'absent.sock')) as client:

        @asynccontextmanager
        async def connection():
            yield client

        async def endpoint(scope, receive, send):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            await respond(send)

        app = CacheResponseMiddleware(endpoint, cache_connection=connection)
        leader = asyncio.create_task(request(app))
        await asyncio.wait_for(started.wait(), 5)
        followers = [asyncio.create_task(request(app)) for _ in range(5)]
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(leader, *followers), 5)
    assert calls == 1
    assert all(body(result) == b'new response' for result in results)


@pytest.mark.asyncio
async def test_cache_write_failure_does_not_fail_delivered_response(cache_client, cache_connection):
    from middlewares.cache_response_middleware import CacheResponseMiddleware

    async def endpoint(scope, receive, send):
        # Deny future cache allocations after the lease was acquired.
        await cache_client.config_set('maxmemory-policy', 'noeviction')
        await cache_client.config_set('maxmemory', 1)
        await respond(send)

    try:
        result = await request(CacheResponseMiddleware(endpoint, cache_connection=cache_connection))
        assert body(result) == b'new response'
        assert result[0]['status'] == 200
    finally:
        await cache_client.config_set('maxmemory', 0)


@pytest.mark.asyncio
async def test_invalid_cache_entry_is_regenerated(cache_client, cache_connection):
    from middlewares.cache_response_middleware import CacheResponseMiddleware

    await cache_client.set('cache3:identity:/example:', b'not a compressed response')

    async def endpoint(scope, receive, send):
        await respond(send)

    app = CacheResponseMiddleware(endpoint, cache_connection=cache_connection)
    assert body(await request(app)) == b'new response'
    assert headers(await request(app))[b'x-cache'] == b'HIT'
