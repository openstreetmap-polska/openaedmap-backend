from asyncio import Semaphore
from collections.abc import Iterable, Sequence
from math import asinh, floor, pi, radians, tan
from pathlib import Path
from typing import NamedTuple

import numpy as np
from asyncpg import Record
from sentry_sdk import trace
from sklearn.cluster import Birch

from config import TILE_COUNTRIES_MAX_Z, TILE_MAX_Z, TILE_MIN_Z
from db import connection
from models.map_change import MapChange
from models.payload import TileKey
from services.byte_cache import byte_cache
from utils import run_blocking

_SQL = Path(__file__).parent.parent / 'sql'
_AEDS = (_SQL / 'tile_aeds.sql').read_text()
_COUNTRIES = (_SQL / 'tile_countries.sql').read_text()
_EMPTY_AEDS = bytes.fromhex('1a150a0e646566696272696c6c61746f72732880207802')
_EMPTY_COUNTRIES = bytes.fromhex('1a100a09636f756e74726965732880207802')
_cluster_slot = Semaphore(1)
_ACCESS_TIERS = {'yes': 0, 'permissive': 1, 'customers': 2, '': 3, 'unknown': 3, 'private': 4, 'no': 5}


class _Location(NamedTuple):
    id: int
    access: str
    lon: float
    lat: float


class _Feature(NamedTuple):
    node_id: int | None
    point_count: int | None
    access: str
    lon: float
    lat: float


class TileService:
    @staticmethod
    def invalidate_all() -> None:
        byte_cache.invalidate(lambda key: isinstance(key, TileKey), stale=True)

    @staticmethod
    def invalidate(changes: Iterable[MapChange]) -> None:
        affected: set[TileKey] = set()
        counts_changed = False
        for change in changes:
            counts_changed |= change.counts_changed
            if not change.render_changed:
                continue
            for position in (change.before, change.after):
                if position is None:
                    continue
                lon, lat = position
                lat = max(-85.0511287798066, min(85.0511287798066, lat))
                for z in range(TILE_COUNTRIES_MAX_Z + 1, TILE_MAX_Z + 1):
                    n = 1 << z
                    x = floor((lon + 180) / 360 * n) % n
                    y = max(0, min(n - 1, floor((1 - asinh(tan(radians(lat))) / pi) / 2 * n)))
                    affected.update(
                        TileKey(z, (x + dx) % n, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if 0 <= y + dy < n
                    )
        if affected or counts_changed:
            byte_cache.invalidate(
                lambda key: (
                    isinstance(key, TileKey) and (key in affected or (counts_changed and key.z <= TILE_COUNTRIES_MAX_Z))
                ),
                stale=True,
            )

    @staticmethod
    @trace
    async def get_tile(z: int, x: int, y: int) -> bytes:
        if not TILE_MIN_Z <= z <= TILE_MAX_Z or not 0 <= x < 2**z or not 0 <= y < 2**z:
            raise ValueError('Tile coordinates are outside the map')
        if z <= TILE_COUNTRIES_MAX_Z:
            async with connection() as conn:
                row = await conn.fetchrow(_COUNTRIES, z, x, y)
                return (row['countries'] or _EMPTY_COUNTRIES) + (row['labels'] or _EMPTY_AEDS)
        if z == TILE_MAX_Z:
            async with connection() as conn:
                return await conn.fetchval(_AEDS, z, x, y, None, None, None, None, None) or _EMPTY_AEDS
        async with _cluster_slot:
            async with connection() as conn:
                rows = await conn.fetch('SELECT * FROM tile_locations($1, $2, $3)', z, x, y)
            features = await run_blocking(_cluster_locations, rows, z)
        if not features:
            return _EMPTY_AEDS
        async with connection() as conn:
            return await conn.fetchval(_AEDS, z, x, y, *zip(*features, strict=True)) or _EMPTY_AEDS


def _cluster_locations(rows: Sequence[Record], z: int) -> list[_Feature]:
    locations = [_Location(*row) for row in rows]
    if not locations:
        return []
    if len(locations) == 1:
        item = locations[0]
        return [_Feature(item.id, None, item.access, item.lon, item.lat)]

    positions = np.asarray([(item.lon, item.lat) for item in locations])
    fit_positions = positions
    if len(positions) > 7000:
        fit_positions = positions[np.linspace(0, len(positions), 7000, endpoint=False, dtype=int)]
    # Fixed cells visibly grid the map; cluster centers must follow the AED distribution.
    model = Birch(threshold=9.6 / 2**z, n_clusters=None, compute_labels=False)
    model.fit(fit_positions)
    groups: list[list[int]] = [[] for _ in model.subcluster_centers_]
    for index, cluster in enumerate(model.predict(positions)):
        groups[cluster].append(index)

    result = []
    for group, center in zip(groups, model.subcluster_centers_, strict=True):
        if not group:
            continue
        if len(group) == 1:
            item = locations[group[0]]
            result.append(_Feature(item.id, None, item.access, item.lon, item.lat))
        else:
            access = ''
            tier = float('inf')
            for index in group:
                candidate = locations[index].access
                candidate_tier = _ACCESS_TIERS.get(candidate)
                if candidate_tier is not None and candidate_tier < tier:
                    access, tier = candidate, candidate_tier
                    if tier == 0:
                        break
            result.append(_Feature(None, len(group), access, float(center[0]), float(center[1])))
    return result
