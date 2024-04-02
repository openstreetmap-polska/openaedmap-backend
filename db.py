from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from config import POSTGRES_LOG, POSTGRES_URL
from utils import JSON_DECODE, JSON_ENCODE

_db_engine = create_async_engine(
    POSTGRES_URL,
    echo=POSTGRES_LOG,
    echo_pool=POSTGRES_LOG,
    json_deserializer=JSON_DECODE,
    json_serializer=lambda x: JSON_ENCODE(x).decode(),  # TODO: is decode needed?
    query_cache_size=128,
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
