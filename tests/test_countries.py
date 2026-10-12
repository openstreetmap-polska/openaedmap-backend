import json

import pytest
from shapely import Point, box


@pytest.mark.asyncio
async def test_country_names_include_counts_and_world_total(aed_database):
    from sqlalchemy import insert

    from api.v1.countries import get_names
    from models.db.aed import AED
    from models.db.country import Country

    async with aed_database.begin() as conn:
        await conn.execute(
            insert(Country),
            [
                {
                    'code': 'NAMES',
                    'names': {'default': '日本'},
                    'geometry': box(1, 1, 3, 3),
                    'label_position': Point(2, 2),
                }
            ],
        )
        await conn.execute(
            insert(AED),
            [
                {
                    'id': 201,
                    'version': 1,
                    'tags': {},
                    'position': Point(2, 2),
                    'country_codes': ['NAMES'],
                }
            ],
        )
    response = await get_names()
    assert json.loads(response.body) == [
        {
            'country_code': 'NAMES',
            'country_names': {'default': '日本'},
            'feature_count': 1,
            'data_path': '/api/v1/countries/NAMES.geojson',
        },
        {
            'country_code': 'WORLD',
            'country_names': {'default': 'World'},
            'feature_count': 1,
            'data_path': '/api/v1/countries/WORLD.geojson',
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('country_code', 'expected_ids'),
    [('WORLD', {302, 303}), ('AA', {302}), ('EMPTY', set())],
    ids=['world', 'country', 'empty'],
)
async def test_geojson_exports_preserve_membership_coordinates_and_tags(aed_database, country_code, expected_ids):
    from sqlalchemy import insert

    from api.v1.countries import get_geojson
    from models.db.aed import AED

    async with aed_database.begin() as conn:
        await conn.execute(
            insert(AED),
            [
                {
                    'id': 302,
                    'version': 7,
                    'tags': {'name': 'Défibrillateur', 'access': 'yes'},
                    'position': Point(179.25, -45.75),
                    'country_codes': ['AA'],
                },
                {
                    'id': 303,
                    'version': 4,
                    'tags': {'access': 'private'},
                    'position': Point(-12.5, 60.125),
                    'country_codes': None,
                },
            ],
        )
    response = await get_geojson(country_code)
    collection = json.loads(response.body)
    assert collection['type'] == 'FeatureCollection'
    features = {feature['properties']['@osm_id']: feature for feature in collection['features']}
    assert features.keys() == expected_ids
    if 302 in expected_ids:
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
    if 303 in expected_ids:
        assert features[303]['geometry'] == {'type': 'Point', 'coordinates': [-12.5, 60.125]}
