import asyncio
import json
import os

import pytest

from tests.data import seed_aeds


@pytest.mark.asyncio
async def test_export_readers_share_one_artifact_and_last_reader_closes_it(database):
    from services.export_service import ExportService

    await seed_aeds([(3101, 5, {'name': 'Shared'}, 12.5, -32.125, [])])
    service = ExportService()
    entered = asyncio.Event()
    release = asyncio.Event()
    async with service.open('WORLD') as first:

        async def follower():
            async with service.open('WORLD') as second:
                assert os.fstat(first.file.fileno()).st_ino == os.fstat(second.file.fileno()).st_ino
                entered.set()
                await release.wait()

        task = asyncio.create_task(follower())
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not first.file.closed
        features = json.loads(os.pread(first.file.fileno(), 65536, 0))['features']
        assert features[0]['properties']['@osm_id'] == 3101
    assert first.file.closed


@pytest.mark.asyncio
async def test_export_releases_database_snapshot_before_transfer(database):
    from db import connection
    from services.export_service import ExportService

    await seed_aeds([(3102, 3, {}, 2.5, -1.25, [])])
    async with ExportService().open('WORLD'), connection() as conn:
        assert (
            await conn.fetchval("""
                SELECT count(*) FROM pg_stat_activity
                WHERE application_name='openaedmap' AND state='idle in transaction'
            """)
            == 0
        )


@pytest.mark.asyncio
async def test_slow_reader_does_not_block_preparing_another_country(database):
    from services.export_service import ExportService

    await seed_aeds([
        (3121, 3, {'name': 'First'}, 2.5, -1.25, ['AA']),
        (3122, 5, {'name': 'Second'}, 3.5, -2.25, ['BB']),
    ])
    service = ExportService()
    async with service.open('AA') as first, asyncio.timeout(5), service.open('BB') as second:
        assert not first.file.closed
        features = json.loads(os.pread(second.file.fileno(), second.size, 0))['features']
        assert features[0]['properties']['@osm_id'] == 3122
    assert first.file.closed


@pytest.mark.asyncio
async def test_cancelling_last_export_reader_stops_database_work_and_releases_slot(database):
    from db import connection
    from services.export_service import ExportService

    service = ExportService()
    async with connection() as locker, locker.transaction():
        await locker.execute('LOCK TABLE aed IN ACCESS EXCLUSIVE MODE')

        async def read():
            async with service.open('WORLD'):
                pytest.fail('Export must wait for the table lock')

        pending = asyncio.create_task(read())
        try:
            async with asyncio.timeout(5), connection() as observer:
                blocked = """
                    SELECT EXISTS(SELECT FROM pg_stat_activity
                      WHERE application_name='openaedmap' AND wait_event_type='Lock')
                """
                while not await observer.fetchval(blocked):  # noqa: ASYNC110 - server state has no notification API
                    await asyncio.sleep(0)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    async with asyncio.timeout(5), service.open('WORLD') as prepared:
        assert json.loads(os.pread(prepared.file.fileno(), 65536, 0))['features'] == []


@pytest.mark.asyncio
async def test_unchanged_export_validates_without_reading_the_locked_aed_table(database):
    import httpx
    from fastapi import FastAPI

    from api.v1.countries import router
    from db import connection
    from middlewares.cache_control_middleware import CacheControlMiddleware

    await seed_aeds([(3103, 7, {'name': 'Conditional'}, 23.5, -14.25, [])])
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.add_middleware(CacheControlMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        first = await client.get('/api/v1/countries/WORLD.geojson')
        async with connection() as locker, locker.transaction():
            await locker.execute('LOCK TABLE aed IN ACCESS EXCLUSIVE MODE')
            async with asyncio.timeout(5):
                unchanged = await client.get(
                    '/api/v1/countries/WORLD.geojson', headers={'If-None-Match': first.headers['etag']}
                )
    assert first.json()['features'][0]['properties']['@osm_id'] == 3103
    assert unchanged.status_code == 304
    assert unchanged.content == b''
    assert unchanged.headers['cache-control'] == first.headers['cache-control']
