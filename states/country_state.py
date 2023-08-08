from time import time
from typing import Annotated, NoReturn, Sequence

import anyio
from dacite import from_dict
from fastapi import Depends
from shapely.geometry import Point, mapping, shape

from config import COUNTRY_COLLECTION, COUNTRY_UPDATE_DELAY, VERSION_TIMESTAMP
from country_from_osm import get_countries_from_osm
from models.bbox import BBox
from models.country import Country, CountryLabel
from models.lonlat import LonLat
from state_utils import get_state_doc, set_state_doc
from transaction import Transaction
from utils import as_dict, retry_exponential


class CountryCode:
    def __init__(self):
        self.counter = 0
        self.used = set()

    def get_unique(self, tags: dict[str, str]) -> str:
        for code in (
            tags.get('ISO3166-1'),
            tags.get('ISO3166-1:alpha2'),
            tags.get('ISO3166-1:alpha3'),
        ):
            if code and len(code) >= 2 and code not in self.used:
                self.used.add(code)
                return code

        self.counter += 1
        return f'{self.counter:02d}'


def _get_names(tags: dict[str, str]) -> dict[str, str]:
    names = {}

    for default in (
        tags.get('name:en'),
        tags.get('int_name'),
        tags.get('name'),
    ):
        if default:
            names['default'] = default
            break

    for k, v in tags.items():
        if k.startswith('name:'):
            names[k[5:].upper()] = v

    return names


async def _should_update_db() -> tuple[bool, float]:
    doc = await get_state_doc('country')
    if doc is None:
        return True, 0

    update_timestamp = doc['update_timestamp']
    if update_timestamp < VERSION_TIMESTAMP:
        return True, update_timestamp

    update_age = time() - update_timestamp
    if update_age > COUNTRY_UPDATE_DELAY.total_seconds():
        return True, update_timestamp

    return False, update_timestamp


@retry_exponential(None)
async def _update_db() -> None:
    update_required, update_timestamp = await _should_update_db()
    if not update_required:
        return

    print('🗺️ Updating country database...')
    countries_from_osm, data_timestamp = await get_countries_from_osm()

    if data_timestamp <= update_timestamp:
        print('🗺️ Nothing to update')
        return

    country_code = CountryCode()
    countries: list[Country] = []

    for c in countries_from_osm:
        names = _get_names(c.tags)
        code = country_code.get_unique(c.tags)
        label_position = c.geometry.representative_point()

        if label_position.is_empty:
            label_position = c.geometry.centroid

        label_position = LonLat(label_position.x, label_position.y)
        label = CountryLabel(label_position)
        countries.append(Country(names, code, c.geometry, label))

    insert_many_arg = tuple(as_dict(c) for c in countries)

    # keep transaction as short as possible: avoid doing any computation inside
    async with Transaction() as s:
        await COUNTRY_COLLECTION.delete_many({}, session=s)
        await COUNTRY_COLLECTION.insert_many(insert_many_arg, session=s)
        await set_state_doc('country', {'update_timestamp': data_timestamp}, session=s)

    print('🗺️ Updating country codes')
    from states.aed_state import get_aed_state
    aed_state = get_aed_state()
    await aed_state.update_country_codes()

    print('🗺️ Update complete')


class CountryState:
    async def update_db_task(self, *, task_status=anyio.TASK_STATUS_IGNORED) -> NoReturn:
        started = False
        while True:
            await _update_db()
            if not started:
                task_status.started()
                started = True
            await anyio.sleep(COUNTRY_UPDATE_DELAY.total_seconds())

    async def get_all_countries(self) -> Sequence[Country]:
        result = []

        async for c in COUNTRY_COLLECTION.find():
            c.pop('_id')
            c['geometry'] = shape(c['geometry'])
            result.append(from_dict(Country, c))

        return tuple(result)

    async def get_countries_within(self, bbox_or_pos: BBox | LonLat) -> Sequence[Country]:
        result = []

        async for c in COUNTRY_COLLECTION.find({
            'geometry': {
                '$geoIntersects': {
                    '$geometry': mapping(
                        bbox_or_pos.extend(0.1).to_polygon()
                        if isinstance(bbox_or_pos, BBox) else
                        Point(bbox_or_pos.lon, bbox_or_pos.lat))
                }
            }
        }):
            c.pop('_id')
            c['geometry'] = shape(c['geometry'])
            result.append(from_dict(Country, c))

        return tuple(result)

    async def get_country_by_code(self, code: str) -> Country | None:
        doc = await COUNTRY_COLLECTION.find_one({'code': code})
        if doc is None:
            return None
        doc.pop('_id')
        doc['geometry'] = shape(doc['geometry'])
        return from_dict(Country, doc)


_instance = CountryState()


def get_country_state() -> CountryState:
    return _instance


CountryStateDep = Annotated[CountryState, Depends(get_country_state)]
