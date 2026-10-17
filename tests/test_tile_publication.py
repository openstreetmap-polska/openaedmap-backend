import asyncio

import httpx
import mapbox_vector_tile as mvt
import pytest
from fastapi import FastAPI

from tests.data import seed_aeds


@pytest.mark.asyncio
async def test_committed_moves_access_edits_and_removal_refresh_buffered_high_zoom_tiles(database):
    from api.v1.tile import router
    from config import PLANET_REPLICA_URL
    from models.aed import AED, RemovedNode
    from planet_diffs import ReplicationBatch
    from services.aed_service import AEDService
    from services.byte_cache import byte_cache
    from services.state_service import StateService

    await seed_aeds([(8701, 1, {'access': 'yes'}, -0.001, -0.001, [])])
    await StateService.set('aed', {'version': 4, 'feed': PLANET_REPLICA_URL, 'sequence': 50, 'update_timestamp': 1})
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        own = '/api/v1/tile/16/32767/32768.mvt'
        adjacent = '/api/v1/tile/16/32768/32768.mvt'
        old = await client.get(own)
        buffered = await client.get(adjacent)
        assert mvt.decode(buffered.content)['defibrillators']['features'][0]['properties']['access'] == 'yes'
        unchanged = await client.get(own, headers={'If-None-Match': old.headers['etag']})
        assert unchanged.status_code == 304
        await AEDService.publish(
            ReplicationBatch(51, 51, 2, (AED(8701, 2, {'access': 'private'}, -0.001, -0.001),)), 50
        )
        stale = await client.get(adjacent)
        assert stale.content == buffered.content
        await asyncio.gather(*byte_cache.active)
        edited = await client.get(adjacent, headers={'If-None-Match': buffered.headers['etag']})
        assert edited.status_code == 200
        assert mvt.decode(edited.content)['defibrillators']['features'][0]['properties']['access'] == 'private'
        await AEDService.publish(ReplicationBatch(52, 52, 3, (AED(8701, 3, {'access': 'private'}, 0.01, -0.001),)), 51)
        await client.get(own)
        await asyncio.gather(*byte_cache.active)
        departed = await client.get(own, headers={'If-None-Match': old.headers['etag']})
        assert departed.status_code == 200
        assert mvt.decode(departed.content)['defibrillators']['features'] == []
        destination = '/api/v1/tile/16/32769/32768.mvt'
        moved = await client.get(destination)
        assert mvt.decode(moved.content)['defibrillators']['features'][0]['properties']['node_id'] == 8701
        await AEDService.publish(ReplicationBatch(53, 53, 4, (RemovedNode(8701, 4),)), 52)
        assert (await client.get(destination)).content == moved.content
        await asyncio.gather(*byte_cache.active)
        removed = await client.get(destination, headers={'If-None-Match': moved.headers['etag']})
        assert removed.status_code == 200
        assert mvt.decode(removed.content)['defibrillators']['features'] == []


@pytest.mark.asyncio
async def test_non_render_edits_preserve_tile_payload_and_validator(database):
    from api.v1.tile import router
    from config import PLANET_REPLICA_URL
    from models.aed import AED
    from models.payload import TileKey
    from planet_diffs import ReplicationBatch
    from services.aed_service import AEDService
    from services.byte_cache import byte_cache
    from services.state_service import StateService

    await seed_aeds([(8702, 1, {'name': 'First'}, 0.001, -0.001, [])])
    await StateService.set('aed', {'version': 4, 'feed': PLANET_REPLICA_URL, 'sequence': 60, 'update_timestamp': 1})
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        path = '/api/v1/tile/16/32768/32768.mvt'
        before = await client.get(path)
        entry = byte_cache.entries[TileKey(16, 32768, 32768)]
        await AEDService.publish(ReplicationBatch(61, 61, 2, (AED(8702, 2, {'name': 'Second'}, 0.001, -0.001),)), 60)
        after = await client.get(path, headers={'If-None-Match': before.headers['etag']})
    assert after.status_code == 304
    assert byte_cache.entries[TileKey(16, 32768, 32768)] is entry
