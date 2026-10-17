from asyncio import to_thread
from datetime import UTC, datetime
from pathlib import Path

import pytest
from asyncpg import CheckViolationError


@pytest.mark.asyncio
async def test_schema_reinitialization_preserves_valuable_data_and_cursor(database):
    from config import PHOTOS_DIR
    from db import connection
    from services.state_service import StateService

    PHOTOS_DIR.mkdir(exist_ok=True)
    path = PHOTOS_DIR / '456_123_preserved.webp'
    path.write_bytes(b'original uploaded bytes')
    created = datetime(2025, 3, 4, 12, 30, tzinfo=UTC)
    async with connection() as conn:
        await conn.execute('INSERT INTO photo VALUES ($1,$2,$3,$4)', 'preserved', 123, 456, created)
        await conn.execute('INSERT INTO photo_report VALUES ($1,$2,$3)', 'report-id', 'preserved', created)
    cursor = {'sequence': 7123456, 'update_timestamp': 1790000000}
    await StateService.set('aed', cursor)
    schema = await to_thread(Path('schema.sql').read_text)
    async with connection() as conn:
        await conn.execute(schema)
        await conn.execute(schema)
        assert tuple(await conn.fetchrow("SELECT * FROM photo WHERE id='preserved'")) == (
            'preserved',
            123,
            456,
            created,
        )
        assert await conn.fetchval("SELECT photo_id FROM photo_report WHERE id='report-id'") == 'preserved'
    assert path.read_bytes() == b'original uploaded bytes'
    assert await StateService.get('aed') == cursor


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('tags', 'position'),
    [
        pytest.param('{}', None, id='live-aed-without-position'),
        pytest.param(None, 'SRID=4326;POINT(2 3)', id='tombstone-with-position'),
    ],
)
async def test_schema_rejects_inconsistent_live_state(database, tags, position):
    from db import connection

    async with connection() as conn:
        with pytest.raises(CheckViolationError):
            await conn.execute(
                'INSERT INTO aed (id, version, tags, position) VALUES (321, 8, $1::jsonb, $2::geometry)',
                tags,
                position,
            )
