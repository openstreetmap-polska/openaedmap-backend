from collections.abc import Iterable, Sequence
from time import time
from typing import NoReturn

import anyio
import numpy as np
from asyncache import cached
from cachetools import TTLCache
from pymongo import DeleteOne, ReplaceOne, UpdateOne
from sentry_sdk import start_span, start_transaction, trace
from shapely import Point
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from sklearn.cluster import Birch
from tqdm import tqdm

from config import AED_COLLECTION, AED_REBUILD_THRESHOLD, AED_UPDATE_DELAY
from models.aed import AED
from models.aed_group import AEDGroup
from models.bbox import BBox
from overpass import query_overpass
from planet_diffs import get_planet_diffs
from state_utils import get_state_doc, set_state_doc
from transaction import Transaction
from utils import retry_exponential
from validators.geometry import geometry_validator

_AED_QUERY = 'node[emergency=defibrillator];out meta qt;'


@trace
async def _should_update_db() -> tuple[bool, float]:
    doc = await get_state_doc('aed')
    if doc is None or doc.get('version', 1) < 3:
        return True, 0

    update_timestamp: float = doc['update_timestamp']
    update_age = time() - update_timestamp
    if update_age > AED_UPDATE_DELAY.total_seconds():
        return True, update_timestamp

    return False, update_timestamp


@trace
async def _assign_country_codes(aeds: Sequence[AED]) -> None:
    from states.country_state import CountryState

    if len(aeds) < 100:
        bulk_write_args = []
        for aed in aeds:
            countries = await CountryState.get_countries_within(aed.position)
            country_codes = tuple({c.code for c in countries})
            bulk_write_args.append(UpdateOne({'id': aed.id}, {'$set': {'country_codes': country_codes}}))
    else:
        countries = await CountryState.get_all_countries()
        id_codes_map = {aed.id: set() for aed in aeds}

        for country in tqdm(countries, desc='📫 Iterating over countries'):
            async for doc in AED_COLLECTION.find(
                {
                    '$and': [
                        {'id': {'$in': tuple(id_codes_map)}},
                        {'position': {'$geoIntersects': {'$geometry': mapping(country.geometry)}}},
                    ]
                }
            ):
                id_codes_map[doc['id']].add(country.code)

        bulk_write_args = [
            UpdateOne(
                {'id': aed.id},
                {'$set': {'country_codes': tuple(id_codes_map[aed.id])}},
            )
            for aed in aeds
        ]

    if bulk_write_args:
        await AED_COLLECTION.bulk_write(bulk_write_args, ordered=False)


def _is_defibrillator(tags: dict[str, str]) -> bool:
    return tags.get('emergency') == 'defibrillator'


def _process_overpass_node(node: dict) -> AED:
    tags = node.get('tags', {})
    is_valid = _is_defibrillator(tags)
    assert is_valid, 'Unexpected non-defibrillator node'
    return AED(
        id=node['id'],
        position=Point(node['lon'], node['lat']),
        country_codes=None,
        tags=tags,
        version=node['version'],
    )


@trace
async def _update_db_snapshot() -> None:
    print('🩺 Updating aed database (overpass)...')
    elements, data_timestamp = await query_overpass(_AED_QUERY, timeout=3600, must_return=True)
    aeds = tuple(_process_overpass_node(e) for e in elements)
    insert_many_arg = tuple(aed.model_dump() for aed in aeds)

    async with Transaction() as s:
        await AED_COLLECTION.delete_many({}, session=s)
        await AED_COLLECTION.insert_many(insert_many_arg, session=s)
        await set_state_doc('aed', {'update_timestamp': data_timestamp, 'version': 3}, session=s)

    if aeds:
        print('🩺 Updating country codes')
        await _assign_country_codes(aeds)

    print(f'🩺 Update complete: ={len(insert_many_arg)}')


def _parse_xml_tags(data: dict) -> dict[str, str]:
    tags = data.get('tag', [])
    return {tag['@k']: tag['@v'] for tag in tags}


def _process_action(action: dict) -> Iterable[AED | int]:
    def _process_create_or_modify(node: dict) -> AED | int:
        node_tags = _parse_xml_tags(node)
        node_valid = _is_defibrillator(node_tags)
        if node_valid:
            return AED(
                id=node['@id'],
                position=Point(node['@lon'], node['@lat']),
                country_codes=None,
                tags=node_tags,
                version=node['@version'],
            )
        else:
            return node['@id']

    def _process_delete(node: dict) -> str:
        return node['@id']

    if action['@type'] in ('create', 'modify'):
        return (_process_create_or_modify(node) for node in action['node'])
    elif action['@type'] == 'delete':
        return (_process_delete(node) for node in action['node'])
    else:
        raise NotImplementedError(f'Unknown action type: {action["@type"]}')


@trace
async def _update_db_diffs(last_update: float) -> None:
    print('🩺 Updating aed database (diff)...')
    actions, data_timestamp = await get_planet_diffs(last_update)

    if data_timestamp <= last_update:
        print('🩺 Nothing to update')
        return

    aeds: list[AED] = []
    remove_ids: set[int] = set()

    for action in actions:
        for result in _process_action(action):
            if isinstance(result, AED):
                aeds.append(result)
            else:
                remove_ids.add(result)

    bulk_write_arg = [ReplaceOne({'id': aed.id}, aed.model_dump(), upsert=True) for aed in aeds]
    bulk_write_arg.extend(DeleteOne({'id': remove_id}) for remove_id in remove_ids)

    # keep transaction as short as possible: avoid doing any computation inside
    async with Transaction() as s:
        await AED_COLLECTION.bulk_write(bulk_write_arg, ordered=True, session=s)
        await set_state_doc('aed', {'update_timestamp': data_timestamp, 'version': 3}, session=s)

    if aeds:
        print('🩺 Updating country codes')
        await _assign_country_codes(aeds)

    print(f'🩺 Update complete: +{len(aeds)} -{len(remove_ids)}')


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


class AEDState:
    @staticmethod
    async def update_db_task(*, task_status=anyio.TASK_STATUS_IGNORED) -> NoReturn:
        if (await _should_update_db())[1] > 0:
            task_status.started()
            started = True
        else:
            started = False

        while True:
            with start_transaction(op='db.update', name=AEDState.update_db_task.__qualname__):
                await _update_db()
            if not started:
                task_status.started()
                started = True
            await anyio.sleep(AED_UPDATE_DELAY.total_seconds())

    @classmethod
    @trace
    async def update_country_codes(cls) -> None:
        await _assign_country_codes(await cls.get_all_aeds())

    @staticmethod
    @cached(TTLCache(maxsize=1024, ttl=3600))
    @trace
    async def count_aeds_by_country_code(country_code: str) -> int:
        return await AED_COLLECTION.count_documents({'country_codes': country_code})

    @staticmethod
    @trace
    async def get_aed_by_id(aed_id: int) -> AED | None:
        doc = await AED_COLLECTION.find_one({'id': aed_id}, projection={'_id': False})
        if doc is None:
            return None

        doc['position'] = geometry_validator(doc['position'])
        aed = AED.model_construct(**doc)
        return aed

    @staticmethod
    @trace
    async def get_all_aeds(filter: dict | None = None) -> Sequence[AED]:
        cursor = AED_COLLECTION.find(filter, projection={'_id': False})
        result = []

        async for doc in cursor:
            doc['position'] = geometry_validator(doc['position'])
            aed = AED.model_construct(**doc)
            result.append(aed)

        return result

    @classmethod
    async def get_aeds_by_country_code(cls, country_code: str) -> Sequence[AED]:
        return await cls.get_all_aeds({'country_codes': country_code})

    @classmethod
    async def get_aeds_within_geom(cls, geometry: BaseGeometry, group_eps: float | None) -> Sequence[AED | AEDGroup]:
        aeds = await cls.get_all_aeds({'position': {'$geoIntersects': {'$geometry': mapping(geometry)}}})

        if len(aeds) <= 1 or group_eps is None:
            return aeds

        positions = tuple((aed.position.x, aed.position.y) for aed in aeds)

        # deterministic sampling
        max_fit_samples = 7000
        if len(positions) > max_fit_samples:
            indices = np.linspace(0, len(positions), max_fit_samples, endpoint=False, dtype=int)
            fit_positions = np.array(positions)[indices]
        else:
            fit_positions = positions

        with start_span(description=f'Fitting model with {len(fit_positions)} samples'):
            model = Birch(threshold=group_eps, n_clusters=None, compute_labels=False, copy=False)
            model.fit(fit_positions)

        with start_span(description=f'Processing {len(aeds)} samples'):
            cluster_groups: tuple[list[AED]] = tuple([] for _ in range(len(model.subcluster_centers_)))
            result: list[AED | AEDGroup] = []

            with start_span(description='Clustering'):
                clusters = model.predict(positions)

            for aed, cluster in zip(aeds, clusters, strict=True):
                cluster_groups[cluster].append(aed)

            for group, center in zip(cluster_groups, model.subcluster_centers_, strict=True):
                if len(group) == 0:
                    continue
                if len(group) == 1:
                    result.append(group[0])
                    continue

                result.append(
                    AEDGroup(
                        position=Point(center[0], center[1]),
                        count=len(group),
                        access=AEDGroup.decide_access(aed.access for aed in group),
                    )
                )

        return result

    @classmethod
    async def get_aeds_within_bbox(cls, bbox: BBox, group_eps: float | None) -> Sequence[AED | AEDGroup]:
        return await cls.get_aeds_within_geom(bbox.to_polygon(), group_eps)
