from collections.abc import Sequence
from time import time
from typing import NoReturn

import anyio
from sentry_sdk import start_transaction, trace
from shapely.geometry import Point, mapping
from shapely.geometry.base import BaseGeometry

from config import COUNTRY_COLLECTION, COUNTRY_UPDATE_DELAY
from models.bbox import BBox
from models.country import Country, CountryLabel
from osm_countries import get_osm_countries
from state_utils import get_state_doc, set_state_doc
from transaction import Transaction
from utils import retry_exponential
from validators.geometry import geometry_validator


class CountryCodeAssigner:
    def __init__(self):
        self.used = set()

    def get_unique(self, tags: dict[str, str]) -> str:
        for check_used in (True, False):
            for code in (
                tags.get('ISO3166-2'),
                tags.get('ISO3166-1'),
                tags.get('ISO3166-1:alpha2'),
                tags.get('ISO3166-1:alpha3'),
            ):
                if code and len(code) >= 2 and (not check_used or code not in self.used):
                    self.used.add(code)
                    return code

        return 'XX'


shape_cache: dict[str, BaseGeometry] = {}


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


@trace
async def _should_update_db() -> tuple[bool, float]:
    doc = await get_state_doc('country')
    if doc is None:
        return True, 0

    update_timestamp: float = doc['update_timestamp']
    update_age = time() - update_timestamp
    if update_age > COUNTRY_UPDATE_DELAY.total_seconds():
        return True, update_timestamp

    return False, update_timestamp


@retry_exponential(None, start=4)
@trace
async def _update_db() -> None:
    update_required, update_timestamp = await _should_update_db()
    if not update_required:
        return

    print('🗺️ Updating country database...')
    osm_countries = await get_osm_countries()
    data_timestamp = osm_countries[0].timestamp if osm_countries else float('-inf')

    if data_timestamp <= update_timestamp:
        print('🗺️ Nothing to update')
        return

    if len(osm_countries) < 210:
        # suspiciously low number of countries
        print(f'🗺️ Not enough countries found: {len(osm_countries)})')
        return

    country_code_assigner = CountryCodeAssigner()
    insert_many_arg = []

    for c in osm_countries:
        country = Country(
            names=_get_names(c.tags),
            code=country_code_assigner.get_unique(c.tags),
            geometry=c.geometry,
            label=CountryLabel(position=c.representative_point),
        )
        insert_many_arg.append(country.model_dump())

    # keep transaction as short as possible: avoid doing any computation inside
    async with Transaction() as s:
        await COUNTRY_COLLECTION.delete_many({}, session=s)
        await COUNTRY_COLLECTION.insert_many(insert_many_arg, session=s)
        await set_state_doc('country', {'update_timestamp': data_timestamp}, session=s)

    shape_cache.clear()

    print('🗺️ Updating country codes')
    from states.aed_state import AEDState

    await AEDState.update_country_codes()

    print('🗺️ Update complete')


class CountryState:
    @staticmethod
    async def update_db_task(*, task_status=anyio.TASK_STATUS_IGNORED) -> NoReturn:
        if (await _should_update_db())[1] > 0:
            task_status.started()
            started = True
        else:
            started = False

        while True:
            with start_transaction(op='update_db', name=CountryState.update_db_task.__qualname__, sampled=True):
                await _update_db()
            if not started:
                task_status.started()
                started = True
            await anyio.sleep(COUNTRY_UPDATE_DELAY.total_seconds())

    @staticmethod
    @trace
    async def get_all_countries(filter: dict | None = None) -> Sequence[Country]:
        cursor = COUNTRY_COLLECTION.find(filter, projection={'_id': False})
        result = []

        async for doc in cursor:
            geometry = shape_cache.get(doc['code'])
            if geometry is None:
                geometry = geometry_validator(doc['geometry'])
                shape_cache[doc['code']] = geometry

            doc['geometry'] = geometry
            result.append(Country.model_construct(doc))

        return result

    @classmethod
    async def get_countries_within(cls, bbox_or_pos: BBox | Point) -> Sequence[Country]:
        return await cls.get_all_countries(
            {
                'geometry': {
                    '$geoIntersects': {
                        '$geometry': mapping(
                            bbox_or_pos.to_polygon(nodes_per_edge=8)
                            if isinstance(bbox_or_pos, BBox)  #
                            else bbox_or_pos
                        )
                    }
                }
            }
        )
