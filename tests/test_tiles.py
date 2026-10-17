import asyncio
from threading import Event

import mapbox_vector_tile as mvt
import pytest
from shapely import Point, box


async def seed_aeds(engine, values):
    from sqlalchemy import insert

    from models.db.aed import AED

    async with engine.begin() as conn:
        await conn.execute(insert(AED), values)


@pytest.mark.asyncio
async def test_individual_tiles_preserve_ids_access_and_positions(aed_database):
    from services.tile_service import TileService

    await seed_aeds(
        aed_database,
        [
            {
                'id': 9007199254740993,
                'version': 3,
                'tags': {'access': 'customers', 'name': 'unused'},
                'position': Point(0.001, -0.001),
            },
            {'id': 27, 'version': 1, 'tags': {}, 'position': Point(0.002, -0.002)},
            {'id': 99, 'version': 1, 'tags': {'access': 'yes'}, 'position': Point(30, 30)},
        ],
    )
    tile = mvt.decode(await TileService.get_tile(16, 32768, 32768))['defibrillators']
    features = {f['properties']['node_id']: f for f in tile['features']}
    assert set(features) == {9007199254740993, 27}
    assert features[9007199254740993]['properties'] == {'node_id': 9007199254740993, 'access': 'customers'}
    assert features[27]['properties'] == {'node_id': 27, 'access': ''}
    assert features[9007199254740993]['geometry']['coordinates'] == [746, 3350]
    assert features[27]['geometry']['coordinates'] == [1491, 2605]


@pytest.mark.asyncio
async def test_clusters_preserve_counts_access_and_singletons(aed_database):
    from services.tile_service import TileService

    await seed_aeds(
        aed_database,
        [
            {'id': 11, 'version': 1, 'tags': {'access': 'no'}, 'position': Point(0.01, -0.01)},
            {'id': 12, 'version': 1, 'tags': {'access': 'yes'}, 'position': Point(0.02, -0.02)},
            {'id': 13, 'version': 1, 'tags': {'access': 'customers'}, 'position': Point(1, -1)},
        ],
    )
    features = mvt.decode(await TileService.get_tile(6, 32, 32))['defibrillators']['features']
    assert len(features) == 2
    group = next(f for f in features if 'point_count' in f['properties'])
    individual = next(f for f in features if 'node_id' in f['properties'])
    assert group['properties'] == {'point_count': 2, 'point_count_abbreviated': '2', 'access': 'yes'}
    assert individual['properties'] == {'node_id': 13, 'access': 'customers'}


@pytest.mark.asyncio
@pytest.mark.parametrize(('z', 'x', 'y'), [(6, 0, 0), (16, 0, 0)])
async def test_empty_tiles(aed_database, z, x, y):
    from services.tile_service import TileService

    assert mvt.decode(await TileService.get_tile(z, x, y))['defibrillators']['features'] == []


@pytest.mark.asyncio
async def test_country_tiles_keep_names_and_country_counts(aed_database):
    from sqlalchemy import insert

    from models.db.country import Country
    from services.tile_service import TileService

    async with aed_database.begin() as conn:
        await conn.execute(
            insert(Country),
            [
                {
                    'code': 'TEST',
                    'names': {'default': 'Example'},
                    'geometry': box(1, -3, 3, -1),
                    'label_position': Point(2, -2),
                },
                {
                    'code': 'OTHER',
                    'names': {'default': 'Example'},
                    'geometry': box(4, -3, 6, -1),
                    'label_position': Point(5, -2),
                },
            ],
        )
    await seed_aeds(
        aed_database,
        [
            {'id': 71, 'version': 1, 'tags': {}, 'position': Point(2, -2), 'country_codes': ['TEST']},
            {'id': 72, 'version': 1, 'tags': {}, 'position': Point(5, -2), 'country_codes': ['OTHER']},
            {'id': 73, 'version': 1, 'tags': {}, 'position': Point(5, -2), 'country_codes': ['OTHER']},
        ],
    )
    tile = mvt.decode(await TileService.get_tile(3, 4, 4))
    assert len(tile['countries']['features']) == 2
    properties = {
        feature['properties']['country_code']: feature['properties'] for feature in tile['defibrillators']['features']
    }
    assert properties == {
        'TEST': {
            'country_name': 'Example',
            'country_code': 'TEST',
            'point_count': 1,
            'point_count_abbreviated': '1',
        },
        'OTHER': {
            'country_name': 'Example',
            'country_code': 'OTHER',
            'point_count': 2,
            'point_count_abbreviated': '2',
        },
    }


@pytest.mark.asyncio
async def test_cancelled_render_keeps_its_slot_until_thread_finishes(aed_database, monkeypatch):
    # The encoder is the external native/CPU boundary; holding it models work
    # that cannot be interrupted when its awaiting request is cancelled.
    import services.tile_service as tiles

    started = Event()
    release = Event()
    real_encode = mvt.encode
    calls = []

    def encode(*args, **kwargs):
        calls.append(1)
        started.set()
        if not release.wait(timeout=10):
            raise TimeoutError('Test encoder was not released')
        return real_encode(*args, **kwargs)

    monkeypatch.setattr(mvt, 'encode', encode)
    first = asyncio.create_task(tiles.TileService.get_tile(16, 0, 0))
    second = None
    try:
        assert await asyncio.to_thread(started.wait, 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(tiles.TileService.get_tile(16, 0, 0))
        await asyncio.sleep(0)
        assert len(calls) == 1
    finally:
        release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        if second is not None:
            await asyncio.wait_for(second, 5)
    assert len(calls) == 2
