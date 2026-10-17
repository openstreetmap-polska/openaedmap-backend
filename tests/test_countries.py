import json

import pytest

from tests.asgi_helpers import body, request
from tests.data import seed_aeds, seed_countries


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('language', 'names'),
    [
        (None, {'default': 'Japan', 'JA': '日本'}),
        ('JA', {'JA': '日本'}),
        ('missing', {'default': 'Japan', 'JA': '日本'}),
    ],
)
async def test_names_language_and_exact_world_count(database, language, names):
    from api.v1.countries import get_names

    await seed_countries([
        ('AA', {'default': 'Japan', 'JA': '日本'}, 'POLYGON((0 0,4 0,4 4,0 4,0 0))', 2, 2),
        ('BB', {'default': 'Overlap'}, 'POLYGON((0 0,4 0,4 4,0 4,0 0))', 2, 2),
    ])
    await seed_aeds([
        (201, 1, {}, 2, 2, ['AA', 'AA', 'BB']),
        (202, 1, {}, 50, 50, []),
    ])
    result = json.loads((await get_names(language)).body)
    assert result[0]['country_names'] == names
    assert [c['feature_count'] for c in result] == [1, 1, 2]
    assert result[-1]['country_code'] == 'WORLD'


@pytest.mark.asyncio
@pytest.mark.parametrize(('country', 'expected'), [('WORLD', {302, 303}), ('AA', {302}), ('EMPTY', set())])
async def test_geojson_preserves_ids_tags_precision_and_membership(database, country, expected):
    from api.v1.countries import get_geojson

    await seed_aeds([
        (302, 7, {'name': 'Défibrillateur', 'access': 'yes'}, 179.25, -45.75, ['AA']),
        (303, 4, {'access': 'private'}, -12.5, 60.125, []),
    ])
    result = json.loads(body(await request(await get_geojson(country))))
    assert result['type'] == 'FeatureCollection'
    features = {f['properties']['@osm_id']: f for f in result['features']}
    assert features.keys() == expected
    if 302 in expected:
        assert features[302] == {
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [179.25, -45.75]},
            'properties': {
                '@osm_type': 'node',
                '@osm_id': 302,
                '@osm_version': 7,
                'name': 'Défibrillateur',
                'access': 'yes',
            },
        }
    if 303 in expected:
        assert features[303]['geometry']['coordinates'] == [-12.5, 60.125]


@pytest.mark.asyncio
async def test_geojson_preserves_int64_unicode_and_raw_tag_precedence(database):
    from api.v1.countries import get_geojson

    await seed_aeds([
        (
            9007199254740993,
            9,
            {'@osm_type': 'raw', 'name': '日本\n"\\', 'empty': ''},
            17.03090941234567,
            -0.00000001234567,
            [],
        ),
    ])
    feature = json.loads(body(await request(await get_geojson('WORLD'))))['features'][0]
    assert feature['properties']['@osm_id'] == 9007199254740993
    assert feature['properties']['@osm_type'] == 'raw'
    assert feature['properties']['name'] == '日本\n"\\'
    assert feature['geometry']['coordinates'] == [17.03090941234567, -0.00000001234567]
