from asyncio import to_thread
from datetime import UTC, datetime
from pathlib import Path

import pytest


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
    cursor = {'version': 4, 'sequence': 7123456, 'update_timestamp': 1790000000}
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
