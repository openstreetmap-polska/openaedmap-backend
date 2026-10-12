import asyncio

import pytest
from pyinstrument import Profiler

from tests.asgi_helpers import body, headers, request, request_scope, respond


@pytest.fixture
def profilers(monkeypatch):
    instances = []
    start = Profiler.start

    def record_start(self, *args, **kwargs):
        instances.append(self)
        return start(self, *args, **kwargs)

    monkeypatch.setattr(Profiler, 'start', record_start)
    try:
        yield instances
    finally:
        for profiler in instances:
            if profiler.is_running:
                profiler.stop()


@pytest.mark.asyncio
async def test_profile_covers_the_complete_response(profilers):
    from middlewares.profiler_middleware import ProfilerMiddleware

    headers_sent = asyncio.Event()
    release = asyncio.Event()
    sent = asyncio.Event()

    async def endpoint(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        headers_sent.set()
        await release.wait()
        await send({'type': 'http.response.body', 'body': b'application content'})

    task = asyncio.create_task(
        request(
            ProfilerMiddleware(endpoint),
            {**request_scope(), 'query_string': b'profile=1'},
            sent=sent,
        )
    )
    try:
        await asyncio.wait_for(headers_sent.wait(), 5)
        assert not sent.is_set()
        assert profilers[0].is_running
    finally:
        release.set()
    response = await asyncio.wait_for(task, 5)
    assert headers(response)[b'content-type'].startswith(b'text/html')
    assert body(response)
    assert body(response) != b'application content'
    assert not profilers[0].is_running


@pytest.mark.asyncio
async def test_profile_stops_on_application_failure(profilers):
    from middlewares.profiler_middleware import ProfilerMiddleware

    async def endpoint(scope, receive, send):
        raise ValueError('Application failed before sending headers')

    with pytest.raises(ValueError, match='Application failed'):
        await request(ProfilerMiddleware(endpoint), {**request_scope(), 'query_string': b'profile=1'})
    assert len(profilers) == 1
    assert not profilers[0].is_running


@pytest.mark.asyncio
async def test_profile_stops_on_cancellation(profilers):
    from middlewares.profiler_middleware import ProfilerMiddleware

    started = asyncio.Event()

    async def endpoint(scope, receive, send):
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(request(ProfilerMiddleware(endpoint), {**request_scope(), 'query_string': b'profile=1'}))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(profilers) == 1
    assert not profilers[0].is_running


@pytest.mark.asyncio
async def test_normal_requests_pass_through_without_profiling(profilers):
    from middlewares.profiler_middleware import ProfilerMiddleware

    async def endpoint(scope, receive, send):
        await respond(send, b'ordinary response')

    response = await request(ProfilerMiddleware(endpoint))
    assert body(response) == b'ordinary response'
    assert not profilers
