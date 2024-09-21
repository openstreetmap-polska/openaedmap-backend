import logging
from collections.abc import Collection, Iterable, Sequence
from operator import attrgetter, itemgetter
from time import time
from typing import NoReturn, cast

import anyio
import numpy as np
from asyncache import cached
from cachetools import TTLCache
from sentry_sdk import start_span, start_transaction, trace
from shapely import Point, get_coordinates, points
from shapely.geometry.base import BaseGeometry
from sklearn.cluster import Birch
from sqlalchemy import any_, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import array_agg, insert

from config import AED_REBUILD_THRESHOLD, AED_UPDATE_DELAY
from db import db_read, db_write
from models.aed_group import AEDGroup
from models.bbox import BBox
from models.db.aed import AED
from models.db.country import Country
from overpass import query_overpass
from planet_diffs import get_planet_diffs
from services.state_service import StateService
from utils import retry_exponential

OVERPASS_QUERY = 'node[emergency=defibrillator];out meta qt;'


class AEDService:
    @staticmethod
    async def update_db_task(*, task_status=anyio.TASK_STATUS_IGNORED) -> NoReturn:
        if (await _should_update_db())[1] > 0:
            task_status.started()
            started = True
        else:
            started = False

        while True:
            with start_transaction(op='db.update', name=AEDService.update_db_task.__qualname__):
                await _update_db()
            if not started:
                task_status.started()
                started = True
            await anyio.sleep(AED_UPDATE_DELAY.total_seconds())

    @classmethod
    @trace
    async def update_country_codes(cls) -> None:
        await _assign_country_codes(await cls.get_all())

    @staticmethod
    @cached(TTLCache(maxsize=1024, ttl=3600))
    @trace
    async def count_by_country_code(country_code: str) -> int:
        async with db_read() as session:
            stmt = select(func.count()).select_from(
                select(text('1'))  #
                .where(any_(AED.country_codes) == country_code)
                .subquery()
            )
            return (await session.execute(stmt)).scalar_one()

    @staticmethod
    @trace
    async def get_by_id(id: int) -> AED | None:
        async with db_read() as session:
            return await session.get(AED, id)

    @staticmethod
    @trace
    async def get_all() -> Sequence[AED]:
        async with db_read() as session:
            stmt = select(AED)
            return (await session.scalars(stmt)).all()

    @classmethod
    @trace
    async def get_by_country_code(cls, country_code: str) -> Sequence[AED]:
        async with db_read() as session:
            stmt = select(AED).where(any_(AED.country_codes) == country_code)
            return (await session.scalars(stmt)).all()

    @classmethod
    @trace
    async def get_intersecting(
        cls, bbox_or_geom: BBox | BaseGeometry, group_eps: float | None
    ) -> Sequence[AED | AEDGroup]:
        geometry = bbox_or_geom.to_polygon() if isinstance(bbox_or_geom, BBox) else bbox_or_geom
        geometry_wkt = geometry.wkt

        async with db_read() as session:
            stmt = select(AED).where(func.ST_Intersects(AED.position, func.ST_GeomFromText(geometry_wkt, 4326)))
            aeds = (await session.scalars(stmt)).all()

        if len(aeds) <= 1 or group_eps is None:
            return aeds

        positions = tuple(get_coordinates(aed.position)[0] for aed in aeds)

        # deterministic sampling
        max_fit_samples = 7000
        if len(positions) > max_fit_samples:
            indices = np.linspace(0, len(positions), max_fit_samples, endpoint=False, dtype=int)
            fit_positions = np.asarray(positions)[indices]
        else:
            fit_positions = positions

        with start_span(description=f'Fitting model with {len(fit_positions)} samples'):
            model = Birch(threshold=group_eps, n_clusters=None, compute_labels=False, copy=False)
            model.fit(fit_positions)
            center_points = cast(Collection[Point], points(model.subcluster_centers_))

        with start_span(description=f'Processing {len(aeds)} samples'):
            cluster_groups: tuple[list[AED], ...] = tuple([] for _ in range(len(center_points)))
            result: list[AED | AEDGroup] = []

            with start_span(description='Clustering'):
                clusters = model.predict(positions)

            cluster: int
            for aed, cluster in zip(aeds, clusters, strict=True):
                cluster_groups[cluster].append(aed)

            for group, center_point in zip(cluster_groups, center_points, strict=True):
                if len(group) == 0:
                    continue
                if len(group) == 1:
                    result.append(group[0])
                    continue

                result.append(
                    AEDGroup(
                        position=center_point,
                        count=len(group),
                        access=AEDGroup.decide_access(aed.access for aed in group),
                    )
                )

        return result


@trace
async def _assign_country_codes(aeds: Collection[AED]) -> None:
    if not aeds:
        return

    ids: set[int] = set(map(attrgetter('id'), aeds))

    async with db_write() as session:
        stmt = (
            update(AED)
            .where(AED.id.in_(text(','.join(str(id) for id in ids))))
            .values(
                {
                    AED.country_codes: select(array_agg(Country.code))
                    .where(func.ST_Intersects(Country.geometry, AED.position))
                    .scalar_subquery()
                }
            )
        )
        await session.execute(stmt)


@trace
async def _should_update_db() -> tuple[bool, float]:
    doc = await StateService.get('aed')
    if doc is None or doc.get('version', 1) < 3:
        return True, 0

    update_timestamp: float = doc['update_timestamp']
    update_age = time() - update_timestamp
    if update_age > AED_UPDATE_DELAY.total_seconds():
        return True, update_timestamp

    return False, update_timestamp


@retry_exponential(None, start=4)
@trace
async def _update_db() -> None:
    update_required, update_timestamp = await _should_update_db()
    if not update_required:
        return

    update_age = time() - update_timestamp

    if update_age > AED_REBUILD_THRESHOLD.total_seconds():
        await _update_db_snapshot()
    else:
        await _update_db_diffs(update_timestamp)


@trace
async def _update_db_snapshot() -> None:
    logging.info('Updating aed database (overpass)...')
    elements, data_timestamp = await query_overpass(OVERPASS_QUERY, timeout=3600, must_return=True)
    aeds = tuple(_process_overpass_node(e) for e in elements)

    async with db_write() as session:
        await session.execute(text(f'TRUNCATE "{AED.__tablename__}" CASCADE'))
        session.add_all(aeds)

    await StateService.set('aed', {'update_timestamp': data_timestamp, 'version': 3})

    if aeds:
        logging.info('Updating country codes')
        await _assign_country_codes(aeds)

        logging.info('Updating statistics')
        async with db_write() as session:
            await session.connection(execution_options={'isolation_level': 'AUTOCOMMIT'})
            await session.execute(text(f'ANALYZE "{AED.__tablename__}"'))

    logging.info('AED update finished (=%d)', len(aeds))


@trace
async def _update_db_diffs(last_update: float) -> None:
    logging.info('Updating aed database (diff)...')
    actions, data_timestamp = await get_planet_diffs(last_update)

    if data_timestamp <= last_update:
        logging.info('Nothing to update')
        return

    # aeds need to be deduplicated to use ON CONFLICT
    id_aed_map: dict[int, AED] = {}
    remove_ids: set[int] = set()

    for action in actions:
        for result in _process_action(action):
            if isinstance(result, AED):
                prev = id_aed_map.get(result.id)
                if prev is None or prev.version < result.version:
                    id_aed_map[result.id] = result
            else:
                remove_ids.add(result)

    aeds = id_aed_map.values()

    async with db_write() as session:
        if aeds:
            stmt = insert(AED).values(
                [
                    {
                        'id': aed.id,
                        'version': aed.version,
                        'tags': aed.tags,
                        'position': aed.position,
                        'country_codes': None,
                    }
                    for aed in aeds
                ]
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=(AED.id,),
                set_={
                    'version': stmt.excluded.version,
                    'tags': stmt.excluded.tags,
                    'position': stmt.excluded.position,
                    'country_codes': None,
                },
            )
            await session.execute(stmt)

        if remove_ids:
            stmt = delete(AED).where(AED.id.in_(text(','.join(str(id) for id in remove_ids))))
            await session.execute(stmt)

    await StateService.set('aed', {'update_timestamp': data_timestamp, 'version': 3})

    if aeds:
        logging.info('Updating country codes')
        await _assign_country_codes(aeds)

    logging.info('AED update finished (+%d, -%d)', len(aeds), len(remove_ids))


def _process_action(action: dict) -> Iterable[AED | int]:
    if action['@type'] in ('create', 'modify'):
        return (_process_action_create_or_modify(node) for node in action['node'])
    elif action['@type'] == 'delete':
        return map(itemgetter('@id'), action['node'])
    else:
        raise NotImplementedError(f'Unknown action type: {action["@type"]}')


def _process_action_create_or_modify(node: dict) -> AED | int:
    tags = _parse_xml_tags(node)
    if _is_defibrillator(tags):
        return AED(
            id=node['@id'],
            version=node['@version'],
            tags=tags,
            position=Point(node['@lon'], node['@lat']),
            country_codes=None,
        )
    else:
        return node['@id']


def _parse_xml_tags(data: dict) -> dict[str, str]:
    tags = data.get('tag', [])
    return {tag['@k']: tag['@v'] for tag in tags}


def _process_overpass_node(node: dict) -> AED:
    tags = node.get('tags', {})
    if not _is_defibrillator(tags):
        raise AssertionError('Unexpected non-defibrillator node')
    return AED(
        id=node['id'],
        version=node['version'],
        tags=tags,
        position=Point(node['lon'], node['lat']),
        country_codes=None,
    )


def _is_defibrillator(tags: dict[str, str]) -> bool:
    return tags.get('emergency') == 'defibrillator'
