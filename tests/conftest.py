# Fixture commands use the project development tools and fixture-owned paths.
# ruff: noqa: S603, S607

import subprocess
from contextlib import asynccontextmanager
from time import monotonic, sleep

import pytest
import pytest_asyncio
from valkey import Valkey as SyncValkey
from valkey.asyncio import Valkey
from valkey.exceptions import ConnectionError as CacheConnectionError


@pytest.fixture(scope='session', autouse=True)
def application_config(tmp_path_factory):
    root = tmp_path_factory.mktemp('backend')
    pgdata = root / 'postgres'
    pgsocket = root / 'pgsocket'
    pgsocket.mkdir()
    subprocess.run(
        ['initdb', '-D', str(pgdata), '--auth=trust', '--no-locale', '--encoding=UTF8', '--username=postgres'],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ['pg_ctl', '-D', str(pgdata), '-l', str(root / 'postgres.log'), '-o', f"-k {pgsocket} -h ''", '-w', 'start'],
        check=True,
        capture_output=True,
    )
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.delenv('SENTRY_DSN', raising=False)
            import config

            patch.setattr(config, 'POSTGRES_URL', f'postgresql+asyncpg://postgres@/postgres?host={pgsocket}')
            yield
    finally:
        subprocess.run(['pg_ctl', '-D', str(pgdata), '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def database(application_config):
    from sqlalchemy import text

    from db import _db_engine
    from models.db.aed import AED
    from models.db.country import Country

    try:
        async with _db_engine.begin() as conn:
            await conn.execute(text('CREATE EXTENSION postgis'))
            await conn.run_sync(AED.__table__.create)
            await conn.run_sync(Country.__table__.create)
        yield _db_engine
    finally:
        await _db_engine.dispose()


@pytest_asyncio.fixture(loop_scope='session')
async def aed_database(database):
    from sqlalchemy import text

    async with database.begin() as conn:
        await conn.execute(text('TRUNCATE aed, country'))
    return database


@pytest.fixture(scope='session')
def cache_url(tmp_path_factory):
    root = tmp_path_factory.mktemp('valkey')
    socket = root / 'cache.sock'
    with (root / 'valkey.log').open('wb') as log:
        process = subprocess.Popen(
            ['valkey-server', '--port', '0', '--unixsocket', str(socket), '--save', '', '--appendonly', 'no'],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            client = SyncValkey(unix_socket_path=str(socket), socket_timeout=1, socket_connect_timeout=1)
            try:
                deadline = monotonic() + 10
                while True:
                    try:
                        client.ping()
                        break
                    except CacheConnectionError:
                        if process.poll() is not None or monotonic() >= deadline:
                            raise
                        sleep(0.01)
            finally:
                client.close()
            yield f'unix://{socket}'
        finally:
            process.terminate()
            process.wait(timeout=10)


@pytest_asyncio.fixture(loop_scope='session')
async def cache_client(cache_url):
    async with Valkey.from_url(cache_url) as client:
        await client.flushdb()
        yield client


@pytest.fixture
def cache_connection(cache_client):
    @asynccontextmanager
    async def connection():
        yield cache_client

    return connection
