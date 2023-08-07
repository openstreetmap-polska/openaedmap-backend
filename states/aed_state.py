from time import time
from typing import Annotated, Counter, Iterable, NoReturn, Sequence

import anyio
import numpy as np
from dacite import from_dict
from fastapi import Depends
from pymongo import DeleteOne, ReplaceOne
from shapely.geometry import mapping
from sklearn.cluster import DBSCAN

from config import AED_COLLECTION, AED_REBUILD_THRESHOLD, AED_UPDATE_DELAY
from models.aed import AED
from models.aed_group import AEDGroup
from models.bbox import BBox
from models.lonlat import LonLat
from overpass import query_overpass
from planet_diffs import get_planet_diffs
from state_utils import get_state_doc, set_state_doc
from transaction import Transaction
from utils import as_dict, retry_exponential

_QUERY = (
    'node[emergency=defibrillator];'
    'out body qt;'
)


async def _should_update_db() -> tuple[bool, float]:
    doc = await get_state_doc('aed')
    if doc is None:
        return True, 0

    update_age = time() - doc['update_timestamp']
    if update_age > AED_UPDATE_DELAY.total_seconds():
        return True, doc['update_timestamp']

    return False, doc['update_timestamp']


def _is_defibrillator(tags: dict[str, str]) -> bool:
    return tags.get('emergency') == 'defibrillator'


def _process_overpass_node(node: dict) -> AED:
    tags = node.get('tags', {})
    is_valid = _is_defibrillator(tags)
    assert is_valid, 'Unexpected non-defibrillator node'
    return AED(
        id=str(node['id']),
        position=LonLat(node['lon'], node['lat']),
        tags=tags)


async def _update_db_snapshot() -> None:
    print('🩺 Updating aed database (overpass)...')
    elements, data_timestamp = await query_overpass(_QUERY, timeout=3600, must_return=True)
    insert_many_arg = tuple(as_dict(_process_overpass_node(e)) for e in elements)

    async with Transaction() as s:
        await AED_COLLECTION.delete_many({}, session=s)
        await AED_COLLECTION.insert_many(insert_many_arg, session=s)
        await set_state_doc('aed', {'update_timestamp': data_timestamp}, session=s)

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

    bulk_write_arg = []
    add_counter = 0
    remove_counter = 0

    for action in actions:
        for result in _process_action(action):
            if isinstance(result, AED):
                bulk_write_arg.append(ReplaceOne({'id': result.id}, as_dict(result), upsert=True))
                add_counter += 1
            else:
                bulk_write_arg.append(DeleteOne({'id': result}))
                remove_counter += 1

    # keep transaction as short as possible: avoid doing any computation inside
    async with Transaction() as s:
        await AED_COLLECTION.bulk_write(bulk_write_arg, ordered=True, session=s)
        await set_state_doc('aed', {'update_timestamp': data_timestamp}, session=s)

    print(f'🩺 Update complete: +{add_counter} -{remove_counter}')


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

    async def count_aeds(self, geometry) -> int:
        return await AED_COLLECTION.count_documents({
            'position': {
                '$geoIntersects': {
                    '$geometry': mapping(geometry)
                }
            }
        })

    async def get_all_aeds(self) -> Sequence[AED]:
        result_aed: list[AED] = []

        async for c in AED_COLLECTION.find():
            c.pop('_id')
            result_aed.append(from_dict(AED, c))

        return tuple(result_aed)

    async def get_aeds_within(self, bbox: BBox, group_eps: float | None) -> Sequence[AED | AEDGroup]:
        return await self.get_aeds_within_geom(bbox.to_polygon(), group_eps)

    async def get_aeds_within_geom(self, geometry, group_eps: float | None) -> Sequence[AED | AEDGroup]:
        result_aed: list[AED] = []

        async for c in AED_COLLECTION.find({
            'position': {
                '$geoIntersects': {
                    '$geometry': mapping(geometry)
                }
            }
        }):
            c.pop('_id')
            result_aed.append(from_dict(AED, c))

        if len(result_aed) <= 1 or group_eps is None:
            return tuple(result_aed)

        result_positions = tuple(tuple(iter(aed.position)) for aed in result_aed)
        model = DBSCAN(eps=group_eps, min_samples=2, n_jobs=-1)
        clusters = model.fit_predict(result_positions)

        result_grouped: list[AED | AEDGroup] = []
        cluster_groups: tuple[list[AED]] = tuple([] for _ in range(clusters.max() + 1))

        for aed, cluster in zip(result_aed, clusters):
            if cluster == -1:
                result_grouped.append(aed)
            else:
                cluster_groups[cluster].append(aed)

        for group in cluster_groups:
            group_center = np.mean(tuple(tuple(iter(aed.position)) for aed in group), axis=0)
            group_access_counter = Counter(aed.access for aed in group)
            group_access = group_access_counter.most_common(1)[0][0]
            result_grouped.append(AEDGroup(
                position=LonLat(group_center[0], group_center[1]),
                count=len(group),
                access=group_access))

        return tuple(result_grouped)

    async def get_aed_by_id(self, aed_id: str) -> AED | None:
        doc = await AED_COLLECTION.find_one({'id': aed_id})
        if doc is None:
            return None
        doc.pop('_id')
        return from_dict(AED, doc)


_instance = AEDState()


def get_aed_state() -> AEDState:
    return _instance


AEDStateDep = Annotated[AEDState, Depends(get_aed_state)]
