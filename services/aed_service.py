import json
import logging
from asyncio import Event, sleep, to_thread
from collections.abc import Iterable
from pathlib import Path
from time import time
from typing import NoReturn

from asyncpg import Connection
from sentry_sdk import start_transaction, trace

from config import AED_UPDATE_DELAY, PLANET_REPLICA_URL
from db import connection, publication
from models.aed import AED, ConflictingNodeVersionError, NodeChange
from models.map_change import MapChange
from overpass import query_overpass
from planet_diffs import ReplicationBatch, get_planet_diffs, reduce_changes, sequence_before
from services.state_service import StateService, set_state
from services.tile_service import TileService
from utils import retry_exponential

_OVERPASS_QUERY = 'node[emergency=defibrillator];out meta qt;'
_APPLY = (Path(__file__).parent.parent / 'sql/apply_aeds.sql').read_text()


class AEDService:
    @staticmethod
    async def update_db_task(started: Event) -> NoReturn:
        if await StateService.get('aed') is not None:
            started.set()
        while True:
            with start_transaction(op='db.update', name=AEDService.update_db_task.__qualname__):
                await _update_db()
            started.set()
            await sleep(AED_UPDATE_DELAY.total_seconds())

    @staticmethod
    @trace
    async def get_by_id(id: int) -> AED | None:
        async with connection() as conn:
            row = await conn.fetchrow(
                'SELECT id, version, tags::text, ST_X(position) AS lon, ST_Y(position) AS lat '
                'FROM aed WHERE id = $1 AND tags IS NOT NULL',
                id,
            )
        return AED(row['id'], row['version'], json.loads(row['tags']), row['lon'], row['lat']) if row else None

    @staticmethod
    async def publish(batch: ReplicationBatch, expected_sequence: int) -> bool:
        changes: list[MapChange] = []
        async with publication(lambda: TileService.invalidate(changes)) as conn:
            data = await conn.fetchval("SELECT data::text FROM state WHERE key = 'aed'")
            state = json.loads(data) if data else {}
            current = state.get('sequence')
            if state.get('feed') != PLANET_REPLICA_URL:
                raise ValueError('Replication feed identity changed; a new baseline is required')
            if current is not None and batch.last_sequence <= current:
                return False
            if current != expected_sequence or batch.first_sequence != expected_sequence + 1:
                raise ValueError('Replication batch does not follow the committed cursor')
            changes = await _apply_changes(conn, batch.changes)
            await set_state(
                conn,
                'aed',
                {
                    'version': 4,
                    'feed': PLANET_REPLICA_URL,
                    'sequence': batch.last_sequence,
                    'update_timestamp': batch.timestamp,
                },
            )
            if changes:
                if any(change.counts_changed for change in changes):
                    await conn.execute('SELECT refresh_map_counts()')
                await conn.execute('UPDATE map_revision SET revision = revision + 1')
        return bool(changes)


async def _apply_changes(conn: Connection, changes: Iterable[NodeChange], *, details: bool = True) -> list[MapChange]:
    records = await to_thread(
        lambda: [
            (
                (c.id, c.version, json.dumps(c.tags, ensure_ascii=False), c.lon, c.lat)
                if isinstance(c, AED)
                else (c.id, c.version, None, None, None)
            )
            for c in changes
        ]
    )
    if not records:
        return []
    await conn.execute(
        'CREATE TEMP TABLE incoming (id bigint PRIMARY KEY, version bigint, tags jsonb, lon float8, lat float8) '
        'ON COMMIT DROP'
    )
    await conn.copy_records_to_table('incoming', records=records)
    await conn.execute('ANALYZE incoming')
    conflict = await conn.fetchval("""
        SELECT incoming.id FROM incoming JOIN aed USING (id)
        WHERE incoming.version = aed.version AND (
            incoming.tags IS DISTINCT FROM aed.tags OR
            (incoming.tags IS NOT NULL AND (
                incoming.lon IS DISTINCT FROM ST_X(aed.position) OR
                incoming.lat IS DISTINCT FROM ST_Y(aed.position)
            ))
        ) LIMIT 1
    """)
    if conflict is not None:
        raise ConflictingNodeVersionError(f'Conflicting committed node version: {conflict}')
    rows = await conn.fetch(_APPLY, details)
    return [
        MapChange(
            tuple(row['before']) if row['before'] is not None else None,
            tuple(row['after']) if row['after'] is not None else None,
            row['render_changed'],
            row['counts_changed'],
        )
        for row in rows
    ]


@retry_exponential(None, start=4)
@trace
async def _update_db() -> None:
    state = await StateService.get('aed')
    if state is None or state.get('version', 1) < 4:
        await _update_db_snapshot()
        return
    if time() - state['update_timestamp'] < AED_UPDATE_DELAY.total_seconds():
        return
    batch = await get_planet_diffs(state['sequence'])
    if batch.last_sequence >= batch.first_sequence:
        await AEDService.publish(batch, state['sequence'])


@trace
async def _update_db_snapshot() -> None:
    expected_state = await StateService.get('aed')
    logging.info('Building AED baseline from Overpass')
    elements, timestamp = await query_overpass(_OVERPASS_QUERY, timeout=3600, must_return=True)
    snapshot = await to_thread(_snapshot_changes, elements)
    sequence = await sequence_before(timestamp)
    # Overpass's consistent base timestamp is assumed to describe this configured
    # minute feed. Replay the complete overlap, including non-AED/deleted events.
    overlap = []
    while True:
        batch = await get_planet_diffs(sequence)
        overlap.extend(batch.changes)
        sequence = batch.last_sequence
        if batch.timestamp > timestamp:
            timestamp = batch.timestamp
            break
        await sleep(1)
    changes = await to_thread(reduce_changes, (*snapshot, *overlap))
    replacing = False
    async with publication(lambda: TileService.invalidate_all() if replacing else None) as conn:
        current = await conn.fetchval("SELECT data::text FROM state WHERE key = 'aed'")
        if (json.loads(current) if current else None) != expected_state:
            logging.info('Discarded AED baseline superseded by committed replication')
            return
        replacing = True
        await conn.execute('DELETE FROM aed')
        await _apply_changes(conn, changes, details=False)
        await conn.execute('SELECT refresh_map_counts()')
        await conn.execute('UPDATE map_revision SET revision = revision + 1')
        await set_state(
            conn,
            'aed',
            {
                'version': 4,
                'feed': PLANET_REPLICA_URL,
                'sequence': sequence,
                'update_timestamp': timestamp,
            },
        )
        await conn.execute('ANALYZE aed')
    async with connection() as conn:
        await conn.execute('VACUUM (ANALYZE) aed')
    logging.info('Published AED baseline at sequence %d', sequence)


def _snapshot_changes(elements: Iterable[dict]) -> tuple[NodeChange, ...]:
    changes = []
    for node in elements:
        tags = node.get('tags', {})
        if not isinstance(tags, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in tags.items()):
            raise ValueError('Invalid snapshot tags')
        if node.get('type') != 'node' or tags.get('emergency') != 'defibrillator':
            raise ValueError('Unexpected snapshot element')
        id, version, lon, lat = node['id'], node['version'], node['lon'], node['lat']
        if not 0 < id < 2**63 or version <= 0 or not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise ValueError('Invalid snapshot node')
        changes.append(AED(id, version, tags, lon, lat))
    if not changes:
        raise ValueError('Empty AED snapshot')
    return reduce_changes(changes)
