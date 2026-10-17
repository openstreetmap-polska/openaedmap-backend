"""Exercise the installed image, database startup and real HTTP server."""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


async def main() -> None:
    from config import PLANET_REPLICA_URL
    from db import close_database, connection, open_database
    from services.state_service import StateService

    await open_database()
    try:
        async with connection() as conn:
            await conn.execute("""
                INSERT INTO aed VALUES (9007199254740993, 7, '{"emergency":"defibrillator","name":"Image smoke"}',
                    ST_SetSRID(ST_MakePoint(0.001,-0.001),4326), '{}');
                INSERT INTO aed VALUES (9007199254741003, 3, '{"emergency":"defibrillator","access":"yes"}',
                    ST_SetSRID(ST_MakePoint(0.002,-0.002),4326), '{}');
                SELECT refresh_map_counts()
            """)
        await StateService.set('country', {'checked_at': time.time(), 'update_timestamp': time.time()})
        await StateService.set(
            'aed',
            {
                'feed': PLANET_REPLICA_URL,
                'sequence': 1,
                'update_timestamp': time.time(),
            },
        )
    finally:
        await close_database()
    process = await asyncio.create_subprocess_exec('h2corn', 'main:app')
    try:
        async with asyncio.timeout(20):
            while True:
                try:
                    await asyncio.to_thread(check_node)
                    break
                except urllib.error.URLError, ConnectionError:
                    if process.returncode is not None:
                        raise RuntimeError('Image server exited during startup') from None
                    await asyncio.sleep(0.05)
        await asyncio.to_thread(check_outputs)
    finally:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 8)
        except TimeoutError:
            process.kill()
            await process.wait()
    if process.returncode != 0:
        raise RuntimeError(f'Image server failed to shut down cleanly: {process.returncode}')


def get(path: str) -> tuple[bytes, str | None]:
    with urllib.request.urlopen(f'http://127.0.0.1:8000/api/v1/{path}', timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f'Image returned {response.status} for {path}')
        return response.read(), response.headers.get('X-Version')


def check_node() -> None:
    content, version = get('node/9007199254740993')
    node = json.loads(content)['elements'][0]
    if node['id'] != 9007199254740993 or node['tags']['name'] != 'Image smoke':
        raise RuntimeError('Image node data differs from the committed seed')
    if version != os.environ['SENTRY_RELEASE']:
        raise RuntimeError('Image release identity differs from the built revision')


def check_outputs() -> None:
    content, _ = get('countries/WORLD.geojson')
    features = json.loads(content)['features']
    if {feature['properties']['@osm_id'] for feature in features} != {9007199254740993, 9007199254741003}:
        raise RuntimeError('Image export differs from the committed seed')
    tile, _ = get('tile/16/32768/32768.mvt')
    if not tile.startswith(b'\x1a'):
        raise RuntimeError('Image did not return a vector tile')
    clustered, _ = get('tile/6/32/32.mvt')
    if not clustered.startswith(b'\x1a'):
        raise RuntimeError('Image did not return a clustered vector tile')
    for package in ('alembic', 'mapbox_vector_tile', 'pyproj', 'pytest', 'shapely', 'sqlalchemy', 'valkey'):
        if importlib.util.find_spec(package) is not None:
            raise RuntimeError(f'Unexpected package in runtime image: {package}')


if __name__ == '__main__':
    sys.path.insert(0, str(Path.cwd()))
    subprocess.run(['dev-start'], check=True)  # noqa: S607
    asyncio.run(main())
