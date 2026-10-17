import json

import pytest
from shapely import Point, box


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('language', 'expected_names'),
    [
        (None, {'default': 'Japan', 'JA': '日本'}),
        ('JA', {'JA': '日本'}),
        ('missing', {'default': 'Japan', 'JA': '日本'}),
    ],
    ids=['all-names', 'requested-language', 'missing-language'],
)
async def test_country_names_include_counts_and_world_total(aed_database, language, expected_names):
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
                    'names': {'default': 'Japan', 'JA': '日本'},
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
    response = await get_names(language)
    assert json.loads(response.body) == [
        {
            'country_code': 'NAMES',
            'country_names': expected_names,
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
@pytest.mark.parametrize('country_count', [0, 1, 32], ids=['no-countries', 'one-country', 'many-countries'])
async def test_country_names_database_work_does_not_fan_out(aed_database, country_count):
    from sqlalchemy import event, insert

    from api.v1.countries import get_names
    from models.db.country import Country

    if country_count:
        async with aed_database.begin() as conn:
            await conn.execute(
                insert(Country),
                [
                    {
                        'code': f'C{i:02}',
                        'names': {'default': f'Country {i}'},
                        'geometry': box(1, 1, 3, 3),
                        'label_position': Point(2, 2),
                    }
                    for i in range(country_count)
                ],
            )
    statements = []
    active_connections = 0
    peak_connections = 0

    def record_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def checkout(dbapi_connection, connection_record, connection_proxy):
        nonlocal active_connections, peak_connections
        active_connections += 1
        peak_connections = max(peak_connections, active_connections)

    def checkin(dbapi_connection, connection_record):
        nonlocal active_connections
        active_connections -= 1

    listeners = [
        ('before_cursor_execute', record_statement),
        ('checkout', checkout),
        ('checkin', checkin),
    ]
    for name, listener in listeners:
        event.listen(aed_database.sync_engine, name, listener)
    try:
        response = await get_names()
    finally:
        for name, listener in listeners:
            event.remove(aed_database.sync_engine, name, listener)
    countries = json.loads(response.body)
    assert len(countries) == country_count + 1
    assert all(country['feature_count'] == 0 for country in countries)
    assert len(statements) <= 2
    assert peak_connections == 1
    assert active_connections == 0


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
