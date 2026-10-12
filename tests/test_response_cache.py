import asyncio
from compression.zstd import compress
from datetime import UTC, datetime, timedelta

import pytest

from tests.cache_helpers import body, headers, request, request_scope, respond, seed_cache


@pytest.mark.asyncio
@pytest.mark.parametrize('workers', [1, 3])
async def test_cold_requests_share_regeneration(cache_connection, workers):
    from middlewares.cache_response_middleware import CacheResponseMiddleware

    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def endpoint(scope, receive, send):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        await respond(send)

    apps = [CacheResponseMiddleware(endpoint, cache_connection=cache_connection) for _ in range(workers)]
    first = asyncio.create_task(request(apps[0]))
    await asyncio.wait_for(started.wait(), 5)
    others = [asyncio.create_task(request(apps[i % workers])) for i in range(1, 9)]
    release.set()
    results = await asyncio.wait_for(asyncio.gather(first, *others), 10)
    assert calls == 1
    assert all(body(result) == b'new response' for result in results)


@pytest.mark.asyncio
async def test_stale_followers_do_not_wait_for_refresh(cache_client, cache_connection):
    from middlewares.cache_response_middleware import CacheResponseMiddleware

    await seed_cache(cache_client, stale=True)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def endpoint(scope, receive, send):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        await respond(send)

    first_app = CacheResponseMiddleware(endpoint, cache_connection=cache_connection)
    second_app = CacheResponseMiddleware(endpoint, cache_connection=cache_connection)
    sent = asyncio.Event()
    first = asyncio.create_task(request(first_app, sent=sent))
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert sent.is_set()
        local, other_worker = await asyncio.wait_for(
            asyncio.gather(request(first_app), request(second_app)),
            5,
        )
        assert body(local) == body(other_worker) == b'old'
        assert headers(local)[b'x-cache'] == headers(other_worker)[b'x-cache'] == b'STALE'
        assert calls == 1
    finally:
        release.set()
        await asyncio.wait_for(first, 5)
    assert body(await request(second_app)) == b'new response'
    assert calls == 1


@pytest.mark.asyncio
async def test_stale_followers_wait_once_the_response_expires(cache_client, cache_connection, monkeypatch):
    import middlewares.cache_response_middleware as middleware
    import services.response_cache as cache

    await seed_cache(cache_client, stale=True)
    started = asyncio.Event()
    release = asyncio.Event()

    async def endpoint(scope, receive, send):
        started.set()
        await release.wait()
        await respond(send)

    app = middleware.CacheResponseMiddleware(endpoint, cache_connection=cache_connection)
    leader = asyncio.create_task(request(app))
    await asyncio.wait_for(started.wait(), 5)

    class Clock:
        @staticmethod
        def now(tz):
            return datetime.now(tz) + timedelta(minutes=2)

    monkeypatch.setattr(middleware, 'datetime', Clock)
    monkeypatch.setattr(cache, 'datetime', Clock)
    follower = asyncio.create_task(request(app))
    try:
        await asyncio.sleep(0)
        assert not follower.done()
    finally:
        release.set()
    first, second = await asyncio.wait_for(asyncio.gather(leader, follower), 5)
    assert body(first) == b'old'
    assert body(second) == b'new response'


@pytest.mark.asyncio
async def test_cancelled_follower_does_not_cancel_shared_work(cache_connection):
    from middlewares.cache_response_middleware import CacheResponseMiddleware

    started = asyncio.Event()
    release = asyncio.Event()

    async def endpoint(scope, receive, send):
        started.set()
        await release.wait()
        await respond(send)

    app = CacheResponseMiddleware(endpoint, cache_connection=cache_connection)
    leader = asyncio.create_task(request(app))
    await asyncio.wait_for(started.wait(), 5)
    follower = asyncio.create_task(request(app))
    await asyncio.sleep(0)
    follower.cancel()
    with pytest.raises(asyncio.CancelledError):
        await follower
    release.set()
    assert body(await asyncio.wait_for(leader, 5)) == b'new response'
    assert body(await request(app)) == b'new response'


@pytest.mark.asyncio
async def test_cancelled_leader_releases_lease(cache_connection):
    from middlewares.cache_response_middleware import CacheResponseMiddleware

    started = asyncio.Event()
    calls = 0

    async def endpoint(scope, receive, send):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await asyncio.Event().wait()
        await respond(send)

    app = CacheResponseMiddleware(endpoint, cache_connection=cache_connection)
    leader = asyncio.create_task(request(app))
    await asyncio.wait_for(started.wait(), 5)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    other_worker = CacheResponseMiddleware(endpoint, cache_connection=cache_connection)
    assert body(await asyncio.wait_for(request(other_worker), 5)) == b'new response'
    assert calls == 2


@pytest.mark.asyncio
async def test_cancelled_leader_followers_elect_one_replacement(cache_connection):
    from middlewares.cache_response_middleware import CacheResponseMiddleware

    first_started = asyncio.Event()
    next_started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def endpoint(scope, receive, send):
        nonlocal calls
        calls += 1
        (first_started if calls == 1 else next_started).set()
        await release.wait()
        await respond(send)

    app = CacheResponseMiddleware(endpoint, cache_connection=cache_connection)
    leader = asyncio.create_task(request(app))
    await asyncio.wait_for(first_started.wait(), 5)
    followers = [asyncio.create_task(request(app)) for _ in range(6)]
    await asyncio.sleep(0)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    try:
        await asyncio.wait_for(next_started.wait(), 5)
        assert calls == 2
    finally:
        release.set()
    results = await asyncio.wait_for(asyncio.gather(*followers), 5)
    assert all(body(result) == b'new response' for result in results)
    assert calls == 2


@pytest.mark.asyncio
async def test_lost_lease_cannot_overwrite_successor(cache_client):
    from models.cached_response import CachedResponse
    from services.response_cache import ResponseCacheEntry

    def response(content):
        return CachedResponse(
            date=datetime.now(UTC),
            max_age=timedelta(minutes=1),
            stale=timedelta(),
            status_code=200,
            headers=[],
            content=content,
        )

    old = ResponseCacheEntry(cache_client, 'shared')
    new = ResponseCacheEntry(cache_client, 'shared')
    async with old.regenerate(wait=True) as acquired:
        assert acquired
        await cache_client.delete('refresh:shared')
        async with new.regenerate(wait=True) as acquired:
            assert acquired
            await new.store(response(b'new'))
            await old.store(response(b'old'))
            assert (await new.get()).content == b'new'
        await old.store(response(b'old again'))
    assert (await new.get()).content == b'new'


@pytest.mark.asyncio
async def test_regeneration_renews_its_lease(cache_client, monkeypatch):
    import services.response_cache as caching

    sleeping = asyncio.Queue()
    advance = asyncio.Event()

    async def sleep_until_advanced(delay):
        await sleeping.put(None)
        await advance.wait()
        advance.clear()

    monkeypatch.setattr(caching, 'sleep', sleep_until_advanced)
    entry = caching.ResponseCacheEntry(cache_client, 'renewed', lease_seconds=60)
    async with entry.regenerate(wait=True) as acquired:
        assert acquired
        await asyncio.wait_for(sleeping.get(), 5)
        await cache_client.pexpire('refresh:renewed', 1000)
        advance.set()
        await asyncio.wait_for(sleeping.get(), 5)
        assert await cache_client.pttl('refresh:renewed') > 1000
    assert not await cache_client.exists('refresh:renewed')


@pytest.mark.asyncio
async def test_head_hit_has_no_body_and_head_miss_does_not_poison_get(cache_connection):
    from middlewares.cache_response_middleware import CacheResponseMiddleware

    calls = []

    async def endpoint(scope, receive, send):
        calls.append(scope['method'])
        await respond(send, b'' if scope['method'] == 'HEAD' else b'get body')

    app = CacheResponseMiddleware(endpoint, cache_connection=cache_connection)
    assert body(await request(app, request_scope(method='HEAD'))) == b''
    assert body(await request(app)) == b'get body'
    hit = await request(app, request_scope(method='HEAD'))
    assert body(hit) == b''
    assert headers(hit)[b'content-length'] == b'8'
    assert calls == ['HEAD', 'GET']


@pytest.mark.asyncio
async def test_encoding_variants_are_not_coalesced(cache_connection):
    from middlewares.cache_response_middleware import CacheResponseMiddleware

    async def endpoint(scope, receive, send):
        await respond(send, dict(scope['headers']).get(b'accept-encoding', b'identity'))

    app = CacheResponseMiddleware(endpoint, cache_connection=cache_connection)
    results = await asyncio.gather(request(app), request(app, request_scope(encoding='gzip')))
    assert [body(result) for result in results] == [b'identity', b'gzip']
    assert body(await request(app)) == b'identity'
    assert body(await request(app, request_scope(encoding='gzip'))) == b'gzip'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'policy',
    [
        b'no-store',
        b'private, max-age=60, stale-while-revalidate=60',
        b'public, max-age=0, stale-while-revalidate=0',
    ],
)
async def test_uncacheable_responses_are_not_reused(cache_connection, policy):
    from middlewares.cache_response_middleware import CacheResponseMiddleware

    calls = 0

    async def endpoint(scope, receive, send):
        nonlocal calls
        calls += 1
        await respond(send, str(calls).encode(), cache_control=policy)

    app = CacheResponseMiddleware(endpoint, cache_connection=cache_connection)
    assert body(await request(app)) == b'1'
    assert body(await request(app)) == b'2'


@pytest.mark.asyncio
async def test_refresh_failure_keeps_stale_response_and_allows_retry(cache_client, cache_connection):
    from middlewares.cache_response_middleware import CacheResponseMiddleware

    await seed_cache(cache_client, stale=True)
    calls = 0

    async def endpoint(scope, receive, send):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('Failed regeneration')
        await respond(send)

    app = CacheResponseMiddleware(endpoint, cache_connection=cache_connection)
    assert body(await request(app)) == b'old'
    assert body(await request(app)) == b'old'
    assert body(await request(app)) == b'new response'
    assert calls == 2


@pytest.mark.asyncio
async def test_compressed_cache_preserves_encoding_and_per_request_cors(cache_connection):
    import httpx
    from starlette.applications import Starlette
    from starlette.middleware.cors import CORSMiddleware
    from starlette.responses import Response
    from starlette.routing import Route
    from starlette_compress import CompressMiddleware

    from middlewares.cache_response_middleware import CacheResponseMiddleware

    calls = 0
    content = b'encoded response ' * 1000

    async def endpoint(request):
        nonlocal calls
        calls += 1
        return Response(
            content,
            media_type='text/plain',
            headers={'Cache-Control': 'public, max-age=60, stale-while-revalidate=60'},
        )

    app = Starlette(routes=[Route('/example', endpoint)])
    app.add_middleware(CompressMiddleware)
    app.add_middleware(CacheResponseMiddleware, cache_connection=cache_connection)
    app.add_middleware(CORSMiddleware, allow_origins=['https://allowed.example'])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        first = await client.get('/example', headers={'Accept-Encoding': 'gzip', 'Origin': 'https://allowed.example'})
        hit = await client.get('/example', headers={'Accept-Encoding': 'gzip', 'Origin': 'https://other.example'})
        identity = await client.get('/example', headers={'Accept-Encoding': 'identity'})
    assert first.content == hit.content == identity.content == content
    assert first.headers['content-encoding'] == hit.headers['content-encoding'] == 'gzip'
    assert first.headers['access-control-allow-origin'] == 'https://allowed.example'
    assert 'access-control-allow-origin' not in hit.headers
    assert 'content-encoding' not in identity.headers
    assert hit.headers['x-cache'] == 'HIT'
    assert calls == 2


@pytest.mark.asyncio
async def test_stale_tile_keeps_no_transform(cache_client, cache_connection):
    from middlewares.cache_response_middleware import CacheResponseMiddleware
    from models.cached_response import CachedResponse

    cached = CachedResponse(
        date=datetime.now(UTC) - timedelta(seconds=65),
        max_age=timedelta(seconds=60),
        stale=timedelta(seconds=60),
        status_code=200,
        headers=[(b'cache-control', b'public, max-age=60, stale-while-revalidate=60, no-transform')],
        content=b'tile',
    )
    await cache_client.set('cache3:identity:/example:', compress(cached.to_bytes()), ex=60)

    async def endpoint(scope, receive, send):
        await respond(send)

    result = await request(CacheResponseMiddleware(endpoint, cache_connection=cache_connection))
    assert body(result) == b'tile'
    assert b'no-transform' in headers(result)[b'cache-control']
    assert b'max-age=0' in headers(result)[b'cache-control']


@pytest.mark.asyncio
async def test_pathsend_responses_are_not_copied_into_cache(cache_connection, tmp_path):
    from starlette.responses import FileResponse

    from middlewares.cache_response_middleware import CacheResponseMiddleware

    path = tmp_path / 'photo.txt'
    path.write_bytes(b'file response')
    calls = 0

    async def endpoint(scope, receive, send):
        nonlocal calls
        calls += 1
        await FileResponse(path, headers={'Cache-Control': 'public, max-age=60, stale-while-revalidate=60'})(
            scope,
            receive,
            send,
        )

    app = CacheResponseMiddleware(endpoint, cache_connection=cache_connection)
    scope = {**request_scope(), 'extensions': {'http.response.pathsend': {}}}
    for _ in range(2):
        messages = await request(app, scope)
        assert messages[-1] == {'type': 'http.response.pathsend', 'path': str(path)}
    assert calls == 2
