import json

from asyncpg import Connection

from db import connection


class StateService:
    @staticmethod
    async def get(key: str) -> dict | None:
        async with connection() as conn:
            data = await conn.fetchval('SELECT data::text FROM state WHERE key = $1', key)
        return json.loads(data) if data is not None else None

    @staticmethod
    async def set(key: str, data: dict) -> None:
        async with connection() as conn:
            await set_state(conn, key, data)


async def set_state(conn: Connection, key: str, data: dict) -> None:
    await conn.execute(
        'INSERT INTO state VALUES ($1, $2::jsonb) ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data',
        key,
        json.dumps(data),
    )
