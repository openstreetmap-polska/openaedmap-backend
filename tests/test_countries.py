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
