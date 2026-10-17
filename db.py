from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg

from config import POSTGRES_URL

_pool: asyncpg.Pool | None = None


async def open_database() -> None:
    global _pool
    pool = await asyncpg.create_pool(
        POSTGRES_URL,
        min_size=1,
        max_size=6,
        command_timeout=120,
        server_settings={'application_name': 'openaedmap', 'jit': 'off'},
    )
    try:
        schema = Path(__file__).with_name('schema.sql').read_text()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(schema)
    except BaseException:
        await pool.close()
        raise
    _pool = pool


async def close_database() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def connection() -> AsyncGenerator[asyncpg.Connection]:
    if _pool is None:
        raise RuntimeError('Database has not been opened')
    async with _pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def publication(invalidate: Callable[[], None]) -> AsyncGenerator[asyncpg.Connection]:
    async with connection() as conn:
        try:
            async with conn.transaction():
                await conn.execute('SELECT id FROM map_revision FOR UPDATE')
                yield conn
        finally:
            # A cancelled commit can have reached PostgreSQL. Mark old payloads stale
            # before releasing the connection, including when the outcome is uncertain.
            invalidate()
