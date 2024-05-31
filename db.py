from contextlib import asynccontextmanager

from redis.asyncio import ConnectionPool, Redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from config import POSTGRES_URL, VALKEY_URL
from utils import JSON_DECODE, JSON_ENCODE

_db_engine = create_async_engine(
    POSTGRES_URL,
    json_deserializer=JSON_DECODE,
    json_serializer=lambda x: JSON_ENCODE(x).decode(),
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


_redis_pool = ConnectionPool().from_url(VALKEY_URL)


@asynccontextmanager
async def valkey():
    async with Redis(connection_pool=_redis_pool) as r:
        yield r
