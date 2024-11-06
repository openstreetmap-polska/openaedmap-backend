from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from valkey.asyncio import ConnectionPool, Valkey

from config import POSTGRES_URL, VALKEY_URL

_db_engine = create_async_engine(
    POSTGRES_URL,
    query_cache_size=128,
    pool_size=10,
    max_overflow=-1,
)


@asynccontextmanager
async def db_read():
    """
    Get a database session for reading.
    """
    async with AsyncSession(
        _db_engine,
        expire_on_commit=False,
        close_resets_only=False,
    ) as session:
        yield session


@asynccontextmanager
async def db_write():
    """
    Get a database session for writing, automatically committing on exit.
    """
    async with AsyncSession(
        _db_engine,
        expire_on_commit=False,
        close_resets_only=False,
    ) as session:
        yield session
        await session.commit()


_valkey_pool = ConnectionPool().from_url(VALKEY_URL)


@asynccontextmanager
async def valkey():
    async with Valkey(connection_pool=_valkey_pool) as r:
        yield r
