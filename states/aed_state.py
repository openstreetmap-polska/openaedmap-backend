from time import time
from typing import Annotated, Counter, Iterable, NoReturn, Sequence

import anyio
from asyncache import cached
from cachetools import TTLCache
from dacite import from_dict
from fastapi import Depends
from pymongo import DeleteOne, ReplaceOne, UpdateOne
from shapely.geometry import mapping
from sklearn.cluster import Birch
from tqdm import tqdm

from config import (AED_COLLECTION, AED_REBUILD_THRESHOLD, AED_UPDATE_DELAY,
                    VERSION_TIMESTAMP)
from models.aed import AED
from models.aed_group import AEDGroup
from models.bbox import BBox
from models.lonlat import LonLat
from overpass import query_overpass
from planet_diffs import get_planet_diffs
from state_utils import get_state_doc, set_state_doc
from transaction import Transaction
from utils import as_dict, print_run_time, retry_exponential

_QUERY = (
    'node[emergency=defibrillator];'
    'out body qt;'
)


async def _should_update_db() -> tuple[bool, float]:
    doc = await get_state_doc('aed')
    if doc is None:
        return True, 0

    update_timestamp = doc['update_timestamp']
    # if update_timestamp < VERSION_TIMESTAMP:
    #     return True, update_timestamp

    update_age = time() - update_timestamp
    if update_age > AED_UPDATE_DELAY.total_seconds():
        return True, update_timestamp

    return False, update_timestamp


def _is_defibrillator(tags: dict[str, str]) -> bool:
    return tags.get('emergency') == 'defibrillator'


async def _assign_country_codes(aeds: Sequence[AED]) -> None:
    from states.country_state import get_country_state
    country_state = get_country_state()
    bulk_write_args = []

    if len(aeds) < 100:
        for aed in aeds:
            countries = await country_state.get_countries_within(aed.position)
            country_codes = tuple(set(c.code for c in countries))
            bulk_write_args.append(
                UpdateOne(
                    {'id': aed.id},
                    {'$set': {'country_codes': country_codes}}))
    else:
        countries = await country_state.get_all_countries()
        id_codes_map = {aed.id: set() for aed in aeds}

        for country in tqdm(countries, desc='📫 Iterating over countries'):
            async for c in AED_COLLECTION.find({
                '$and': [
                    {'id': {'$in': tuple(id_codes_map)}},
                    {'position': {'$geoIntersects': {'$geometry': mapping(country.geometry)}}},
                ]
            }):
                id_codes_map[c['id']].add(country.code)

        for aed in aeds:
            bulk_write_args.append(
                UpdateOne(
                    {'id': aed.id},
                    {'$set': {'country_codes': tuple(id_codes_map[aed.id])}}))

    if bulk_write_args:
        await AED_COLLECTION.bulk_write(bulk_write_args, ordered=False)


def _process_overpass_node(node: dict) -> AED:
    tags = node.get('tags', {})
    is_valid = _is_defibrillator(tags)
    assert is_valid, 'Unexpected non-defibrillator node'
    return AED(
        id=str(node['id']),
        position=LonLat(node['lon'], node['lat']),
        country_codes=None,
        tags=tags)


async def _update_db_snapshot() -> None:
    print('🩺 Updating aed database (overpass)...')
    elements, data_timestamp = await query_overpass(_QUERY, timeout=3600, must_return=True)
    aeds = tuple(_process_overpass_node(e) for e in elements)
    insert_many_arg = tuple(as_dict(aed) for aed in aeds)

    async with Transaction() as s:
        await AED_COLLECTION.delete_many({}, session=s)
        await AED_COLLECTION.insert_many(insert_many_arg, session=s)
        await set_state_doc('aed', {'update_timestamp': data_timestamp}, session=s)

    if aeds:
        print(f'🩺 Updating country codes')
        await _assign_country_codes(aeds)

    print(f'🩺 Update complete: ={len(insert_many_arg)}')


def _parse_xml_tags(data: dict) -> dict[str, str]:
    tags = data.get('tag', [])
    return {tag['@k']: tag['@v'] for tag in tags}


def _process_action(action: dict) -> Iterable[AED | str]:
    def _process_create_or_modify(node: dict) -> AED | str:
        node_tags = _parse_xml_tags(node)
        node_valid = _is_defibrillator(node_tags)
        node_id = str(node['@id'])
        if node_valid:
            return AED(
                id=node_id,
                position=LonLat(float(node['@lon']), float(node['@lat'])),
                country_codes=None,
                tags=node_tags)
        else:
            return node_id

    def _process_delete(node: dict) -> str:
        node_id = str(node['@id'])
        return node_id

    if action['@type'] in ('create', 'modify'):
        return (_process_create_or_modify(node) for node in action['node'])
    elif action['@type'] == 'delete':
        return (_process_delete(node) for node in action['node'])
    else:
        raise NotImplementedError(f'Unknown action type: {action["@type"]}')


async def _update_db_diffs(last_update: float) -> None:
    print('🩺 Updating aed database (diff)...')
    actions, data_timestamp = await get_planet_diffs(last_update)

    if data_timestamp <= last_update:
        print('🩺 Nothing to update')
        return

    aeds = []
    remove_ids = set()

    for action in actions:
        for result in _process_action(action):
            if isinstance(result, AED):
                aeds.append(result)
            else:
                remove_ids.add(result)

    bulk_write_arg = []

    for aed in aeds:
        bulk_write_arg.append(ReplaceOne({'id': aed.id}, as_dict(aed), upsert=True))

    for remove_id in remove_ids:
        bulk_write_arg.append(DeleteOne({'id': remove_id}))

    # keep transaction as short as possible: avoid doing any computation inside
    async with Transaction() as s:
        await AED_COLLECTION.bulk_write(bulk_write_arg, ordered=True, session=s)
        await set_state_doc('aed', {'update_timestamp': data_timestamp}, session=s)

    if aeds:
        print(f'🩺 Updating country codes')
        await _assign_country_codes(aeds)

    print(f'🩺 Update complete: +{len(aeds)} -{len(remove_ids)}')


@retry_exponential(None)
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
    async def update_db_task(self, *, task_status=anyio.TASK_STATUS_IGNORED) -> NoReturn:
        started = False
        while True:
            await _update_db()
            if not started:
                task_status.started()
                started = True
            await anyio.sleep(AED_UPDATE_DELAY.total_seconds())

    async def update_country_codes(self) -> None:
        await _assign_country_codes(await self.get_all_aeds())

    @cached(TTLCache(maxsize=1024, ttl=300))
    async def count_aeds_by_country_code(self, country_code: str) -> int:
        return await AED_COLLECTION.count_documents({'country_codes': country_code})

    async def get_all_aeds(self, filter: dict | None = None) -> Sequence[AED]:
        cursor = AED_COLLECTION.find(filter, projection={'_id': False})
        result = []

        async for c in cursor:
            result.append(from_dict(AED, c))

        return tuple(result)

    async def get_aeds_by_country_code(self, country_code: str) -> Sequence[AED]:
        return await self.get_all_aeds({'country_codes': country_code})

    async def get_aeds_within(self, bbox: BBox, group_eps: float | None) -> Sequence[AED | AEDGroup]:
        return await self.get_aeds_within_geom(bbox.to_polygon(), group_eps)

    async def get_aeds_within_geom(self, geometry, group_eps: float | None) -> Sequence[AED | AEDGroup]:
        aeds = await self.get_all_aeds({
            'position': {
                '$geoIntersects': {
                    '$geometry': mapping(geometry)
                }
            }
        })

        if len(aeds) <= 1 or group_eps is None:
            return aeds

        result_positions = tuple(tuple(iter(aed.position)) for aed in aeds)
        model = Birch(threshold=group_eps, n_clusters=None, copy=False)

        with print_run_time(f'Clustering {len(aeds)} samples'):
            clusters = model.fit_predict(result_positions)

        with print_run_time(f'Processing {len(aeds)} samples'):
            result: list[AED | AEDGroup] = []
            cluster_groups: tuple[list[AED]] = tuple([] for _ in range(len(model.subcluster_centers_)))

            for aed, cluster in zip(aeds, clusters):
                cluster_groups[cluster].append(aed)

            for group, center in zip(cluster_groups, model.subcluster_centers_):
                if len(group) == 0:
                    continue

                if len(group) == 1:
                    result.append(group[0])
                    continue

                group_access_counter = Counter(aed.access for aed in group)
                group_access = group_access_counter.most_common(1)[0][0]
                result.append(AEDGroup(
                    position=LonLat(center[0], center[1]),
                    count=len(group),
                    access=group_access))

        return tuple(result)

    async def get_aed_by_id(self, aed_id: str) -> AED | None:
        doc = await AED_COLLECTION.find_one({'id': aed_id}, projection={'_id': False})
        return from_dict(AED, doc) if doc else None


_instance = AEDState()


def get_aed_state() -> AEDState:
    return _instance


AEDStateDep = Annotated[AEDState, Depends(get_aed_state)]
