import pytest
from shapely import Point


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('codes', 'expected'),
    [
        ((), {}),
        (('AA', 'BB', 'EMPTY', 'AA'), {'AA': 2, 'BB': 1, 'EMPTY': 0}),
    ],
    ids=['no-countries', 'overlapping-duplicate-and-missing-memberships'],
)
async def test_country_counts_preserve_array_membership(aed_database, codes, expected):
    from sqlalchemy import insert

    from models.db.aed import AED
    from services.aed_service import AEDService

    async with aed_database.begin() as conn:
        await conn.execute(
            insert(AED),
            [
                {'id': id, 'version': 1, 'tags': {}, 'position': Point(1, 2), 'country_codes': memberships}
                for id, memberships in [
                    (401, ['AA', 'AA', 'BB']),
                    (402, ['AA']),
                    (403, None),
                    (404, []),
                    (405, ['OTHER']),
                ]
            ],
        )
    assert await AEDService.count_by_country_codes(iter(codes)) == expected


@pytest.mark.asyncio
async def test_country_counts_reuse_cached_positive_and_zero_counts(aed_database):
    from sqlalchemy import event, insert

    from models.db.aed import AED
    from services.aed_service import AEDService

    async with aed_database.begin() as conn:
        await conn.execute(
            insert(AED),
            [{'id': 501, 'version': 1, 'tags': {}, 'position': Point(3, 4), 'country_codes': ['CACHED']}],
        )
    assert await AEDService.count_by_country_codes(['CACHED', 'EMPTY']) == {'CACHED': 1, 'EMPTY': 0}
    statements = []

    def record_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(aed_database.sync_engine, 'before_cursor_execute', record_statement)
    try:
        cached = await AEDService.count_by_country_codes(['CACHED', 'EMPTY'])
        assert cached == {'CACHED': 1, 'EMPTY': 0}
        assert statements == []
        counts = await AEDService.count_by_country_codes(['CACHED', 'EMPTY', 'NEW'])
    finally:
        event.remove(aed_database.sync_engine, 'before_cursor_execute', record_statement)
    assert counts == {'CACHED': 1, 'EMPTY': 0, 'NEW': 0}
    assert len(statements) == 1
