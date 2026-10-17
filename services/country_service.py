import json
import logging
from asyncio import Event, sleep, to_thread
from collections.abc import Iterable
from hashlib import blake2b
from time import time
from typing import NoReturn

from sentry_sdk import start_transaction, trace

from config import COUNTRY_UPDATE_DELAY
from country_code_assigner import CountryCodeAssigner
from db import connection, publication
from models.country import CountrySummary
from models.osm_country import OSMCountry
from osm_countries import get_osm_countries
from services.state_service import StateService, set_state
from services.tile_service import TileService
from utils import retry_exponential


class CountryService:
    @staticmethod
    async def update_db_task(started: Event) -> NoReturn:
        if await StateService.get('country') is not None:
            started.set()
        while True:
            with start_transaction(op='db.update', name=CountryService.update_db_task.__qualname__):
                await _update_db()
            started.set()
            await sleep(COUNTRY_UPDATE_DELAY.total_seconds())

    @staticmethod
    @trace
    async def get_names() -> list[CountrySummary]:
        async with connection() as conn:
            rows = await conn.fetch("""
                SELECT code, names::text, coalesce(feature_count, 0) AS feature_count, false AS world
                FROM country LEFT JOIN country_count USING (code)
                UNION ALL
                SELECT 'WORLD', '{"default":"World"}', feature_count, true FROM map_revision
                ORDER BY world, code
            """)
        return [CountrySummary(r['code'], json.loads(r['names']), r['feature_count']) for r in rows]


@retry_exponential(None, start=4)
@trace
async def _update_db() -> None:
    state = await StateService.get('country')
    if (
        state is not None
        and time() - state.get('checked_at', state['update_timestamp']) < COUNTRY_UPDATE_DELAY.total_seconds()
    ):
        return
    countries = await get_osm_countries()
    if len(countries) < 210:
        raise ValueError(f'Incomplete country artifact: {len(countries)} countries')
    records = await to_thread(_country_records, countries)
    fingerprint = await to_thread(lambda: blake2b(json.dumps(records).encode(), digest_size=16).hexdigest())
    new_state = {
        'version': 3,
        'update_timestamp': max(c.timestamp for c in countries),
        'checked_at': time(),
        'fingerprint': fingerprint,
    }
    replacing = False
    async with publication(lambda: TileService.invalidate_all() if replacing else None) as conn:
        current = await conn.fetchval("SELECT data::text FROM state WHERE key = 'country'")
        if (json.loads(current) if current else None) != state:
            return
        if state is not None and state.get('fingerprint') == fingerprint:
            await set_state(conn, 'country', new_state)
            return
        await conn.execute(
            'CREATE TEMP TABLE incoming_country (code varchar(8) PRIMARY KEY, names jsonb, geometry jsonb, label jsonb) '
            'ON COMMIT DROP'
        )
        await conn.copy_records_to_table('incoming_country', records=records)
        invalid = await conn.fetchval("""
            SELECT code FROM incoming_country
            WHERE NOT ST_IsValid(ST_GeomFromGeoJSON(geometry)) OR ST_IsEmpty(ST_GeomFromGeoJSON(geometry))
              OR GeometryType(ST_GeomFromGeoJSON(geometry)) NOT IN ('POLYGON', 'MULTIPOLYGON')
              OR GeometryType(ST_GeomFromGeoJSON(label)) <> 'POINT'
            LIMIT 1
        """)
        if invalid is not None:
            raise ValueError(f'Invalid country geometry: {invalid}')
        replacing = True
        await conn.execute('DELETE FROM country')
        await conn.execute("""
            INSERT INTO country SELECT code, names, ST_GeomFromGeoJSON(geometry), ST_GeomFromGeoJSON(label)
            FROM incoming_country
        """)
        await conn.execute('ANALYZE country')
        await conn.execute('SELECT assign_country_codes()')
        await conn.execute('SELECT refresh_map_counts()')
        await conn.execute('SELECT prepare_country_tiles()')
        await conn.execute('UPDATE map_revision SET revision = revision + 1, country_revision = country_revision + 1')
        await set_state(conn, 'country', new_state)
        await conn.execute('ANALYZE aed; ANALYZE country_tile; ANALYZE country_count')
    logging.info('Published %d countries and complete AED memberships', len(records))


def _country_records(countries: Iterable[OSMCountry]) -> list[tuple[str, str, str, str]]:
    assigner = CountryCodeAssigner()
    records = []
    for country in countries:
        code = assigner.get_unique(country.tags)
        names = _get_names(country.tags)
        if not 2 <= len(code) <= 8 or code == 'WORLD' or 'default' not in names:
            raise ValueError(f'Invalid country identity: {code}')
        records.append((
            code,
            json.dumps(names, ensure_ascii=False, sort_keys=True),
            json.dumps(country.geometry, sort_keys=True),
            json.dumps(country.representative_point, sort_keys=True),
        ))
    return sorted(records)


def _get_names(tags: dict[str, str]) -> dict[str, str]:
    names = {}
    for key in ('name:en', 'int_name', 'name'):
        if default := tags.get(key):
            names['default'] = default
            break
    names.update((k[5:].upper(), v) for k, v in tags.items() if k.startswith('name:'))
    return names
