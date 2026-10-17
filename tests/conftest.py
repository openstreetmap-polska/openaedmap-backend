# Fixture commands use the project development tools and fixture-owned paths.
# ruff: noqa: S603, S607
import subprocess

import pytest
import pytest_asyncio


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

            patch.setattr(config, 'POSTGRES_URL', f'postgresql://postgres@/postgres?host={pgsocket}')
            patch.setattr(config, 'PHOTOS_DIR', root / 'photos')
            yield
    finally:
        subprocess.run(['pg_ctl', '-D', str(pgdata), '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def postgres_pool(application_config):
    from db import close_database, open_database

    await open_database()
    try:
        yield
    finally:
        await close_database()


@pytest_asyncio.fixture(loop_scope='session')
async def database(postgres_pool):
    from db import connection
    from services.byte_cache import byte_cache

    byte_cache.invalidate(lambda _: True)
    async with connection() as conn:
        await conn.execute("""
            TRUNCATE aed, country, country_tile, country_count, photo_report, photo;
            DELETE FROM state WHERE key <> 'native_schema';
            UPDATE map_revision SET revision = 0, country_revision = 0, feature_count = 0
        """)
    yield
