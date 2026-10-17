import mapbox_vector_tile as mvt
import pytest

from tests.data import seed_aeds, seed_countries


@pytest.mark.asyncio
async def test_individual_tiles_preserve_ids_access_and_positions(database):
    from services.tile_service import TileService

    await seed_aeds([
        (9007199254740993, 3, {'access': 'customers', 'name': 'unused'}, 0.001, -0.001, []),
        (27, 1, {}, 0.002, -0.002, []),
        (99, 1, {'access': 'yes'}, 30, 30, []),
    ])
    tile = mvt.decode(await TileService.get_tile(16, 32768, 32768))['defibrillators']
    features = {f['properties']['node_id']: f for f in tile['features']}
    assert set(features) == {9007199254740993, 27}
    assert features[9007199254740993]['properties'] == {'node_id': 9007199254740993, 'access': 'customers'}
    assert features[27]['properties'] == {'node_id': 27, 'access': ''}
    assert features[9007199254740993]['geometry']['coordinates'] == [746, 3350]
    assert features[27]['geometry']['coordinates'] == [1491, 2605]


@pytest.mark.asyncio
async def test_clusters_preserve_counts_access_and_singletons(database):
    from services.tile_service import TileService

    await seed_aeds([
        (11, 1, {'access': 'no'}, 0.01, -0.01, []),
        (12, 1, {'access': 'yes'}, 0.02, -0.02, []),
        (13, 1, {'access': 'customers'}, 1, -1, []),
    ])
    features = mvt.decode(await TileService.get_tile(6, 32, 32))['defibrillators']['features']
    assert len(features) == 2
    group = next(f for f in features if 'point_count' in f['properties'])
    individual = next(f for f in features if 'node_id' in f['properties'])
    assert group['properties'] == {'point_count': 2, 'point_count_abbreviated': '2', 'access': 'yes'}
    assert individual['properties'] == {'node_id': 13, 'access': 'customers'}


@pytest.mark.asyncio
async def test_nearby_points_form_a_natural_cluster_across_former_grid_boundaries(database):
    from services.tile_service import TileService

    await seed_aeds([
        (21, 1, {'access': 'yes'}, 1.405, -1.4, []),
        (22, 1, {'access': 'no'}, 1.407, -1.4, []),
    ])
    features = mvt.decode(await TileService.get_tile(6, 32, 32))['defibrillators']['features']
    assert len(features) == 1
    assert features[0]['properties']['point_count'] == 2
    assert features[0]['geometry']['coordinates'][0] == 1024


@pytest.mark.asyncio
async def test_native_encoding_preserves_natural_feature_draw_order(database):
    from db import connection
    from services.tile_service import _AEDS

    async with connection() as conn:
        content = await conn.fetchval(
            _AEDS,
            6,
            32,
            32,
            (None, 31, 11),
            (2, None, None),
            ('yes', '', 'yes'),
            (0.02, 1.0, 2.0),
            (-0.01, -1.0, -2.0),
        )
    features = mvt.decode(content)['defibrillators']['features']
    assert [feature['properties'].get('node_id') for feature in features] == [None, 31, 11]


@pytest.mark.asyncio
async def test_dateline_buffers_preserve_each_source_node_once(database):
    from services.tile_service import TileService

    await seed_aeds([
        (31, 1, {}, 179.99, -0.01, []),
        (32, 1, {}, 179.98, -0.02, []),
        (33, 1, {}, -179.99, -0.01, []),
        (34, 1, {}, -179.98, -0.02, []),
    ])
    for x in (0, 63):
        features = mvt.decode(await TileService.get_tile(6, x, 32))['defibrillators']['features']
        assert len(features) == 1
        assert sum(f['properties']['point_count'] for f in features) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(('z', 'x', 'y'), [(3, 0, 0), (6, 0, 0), (16, 0, 0)])
async def test_empty_tiles_keep_named_layers(database, z, x, y):
    from services.tile_service import TileService

    tile = mvt.decode(await TileService.get_tile(z, x, y))
    assert tile['defibrillators']['features'] == []
    if z == 3:
        assert tile['countries']['features'] == []


@pytest.mark.asyncio
async def test_country_tiles_keep_names_and_country_counts(database):
    from services.tile_service import TileService

    await seed_countries([
        ('TEST', {'default': 'Example'}, 'POLYGON((1 -3,3 -3,3 -1,1 -1,1 -3))', 2, -2),
        ('OTHER', {'default': 'Example'}, 'POLYGON((4 -3,6 -3,6 -1,4 -1,4 -3))', 5, -2),
    ])
    await seed_aeds([
        (71, 1, {}, 2, -2, ['TEST']),
        (72, 1, {}, 5, -2, ['OTHER']),
        (73, 1, {}, 5, -2, ['OTHER']),
    ])
    tile = mvt.decode(await TileService.get_tile(3, 4, 4))
    assert len(tile['countries']['features']) == 2
    properties = {f['properties']['country_code']: f['properties'] for f in tile['defibrillators']['features']}
    assert properties['TEST']['point_count'] == 1
    assert properties['OTHER']['point_count'] == 2
    assert properties['OTHER']['country_name'] == 'Example'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('n', 'expected'), [(999, '999'), (1050, '1.1k'), (1150, '1.1k'), (1250, '1.2k'), (1000000, '1.0m')]
)
async def test_native_count_labels_preserve_binary_float_rounding(database, n, expected):
    from db import connection

    async with connection() as conn:
        assert await conn.fetchval('SELECT abbreviate_count($1)', n) == expected
