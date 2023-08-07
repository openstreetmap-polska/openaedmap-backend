from dataclasses import asdict
from time import time
from typing import Annotated, NoReturn, Sequence

import anyio
import orjson
from dacite import from_dict
from fastapi import Depends
from shapely.geometry import MultiPolygon, mapping, shape

from config import COUNTRY_COLLECTION, COUNTRY_UPDATE_DELAY
from models.bbox import BBox
from models.country import Country, CountryLabel
from models.lonlat import LonLat
from state_utils import get_state_doc, set_state_doc
from transaction import Transaction
from utils import as_dict, get_http_client, retry_exponential

_GEOJSON_URL = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson'


async def _should_update_db() -> bool:
    doc = await get_state_doc('country')
    if doc is None:
        return True

    update_age = time() - doc['update_timestamp']
    if update_age > COUNTRY_UPDATE_DELAY.total_seconds():
        return True

    return False


@retry_exponential(None)
async def _download_geojson() -> str:
    async with get_http_client() as http:
        r = await http.get(_GEOJSON_URL)
        r.raise_for_status()
    return r.text


def _fix_shape(shape):
    if isinstance(shape, MultiPolygon):
        return MultiPolygon([_fix_shape(p) for p in shape.geoms])

    return shape.buffer(0)


class CountryCode:
    def __init__(self):
        self.counter = 0
        self.used = set()

    def get_country_code(self, properties: dict[str, str]) -> str:
        for code in (
            properties['POSTAL'],
            properties['ISO_A2'],
            properties['ISO_A2_EH'],
            properties['ISO_A3'],
            properties['ISO_A3_EH'],
            properties['ADM0_ISO'],
            properties['ADM0_A3'],
            properties['SOV_A3'],
        ):
            if code and code[0] != '-' and code not in self.used:
                self.used.add(code)
                return code

        self.counter += 1
        return f'{self.counter:02d}'


@retry_exponential(None)
async def _update_db() -> None:
    if not await _should_update_db():
        return

    print('🗺️ Updating country database...')
    data = orjson.loads(await _download_geojson())
    country_code = CountryCode()
    countries: list[Country] = []

    for feature in data['features']:
        names = {'default': feature['properties']['NAME']}
        for k, v in feature['properties'].items():
            if len(k) == 7 and k.startswith('NAME_'):
                names[k[5:]] = v
        code = country_code.get_country_code(feature['properties'])
        geometry = _fix_shape(shape(feature['geometry']))
        label_position = LonLat(feature['properties']['LABEL_X'], feature['properties']['LABEL_Y'])
        label_min_z = feature['properties']['MIN_LABEL']
        label_max_z = feature['properties']['MAX_LABEL']
        label = CountryLabel(label_position, label_min_z, label_max_z)
        countries.append(Country(names, code, geometry, label))

    insert_many_arg = [as_dict(c) for c in countries]

    # keep transaction as short as possible: avoid doing any computation inside
    async with Transaction() as s:
        await COUNTRY_COLLECTION.delete_many({}, session=s)
        await COUNTRY_COLLECTION.insert_many(insert_many_arg, session=s)
        await set_state_doc('country', {'update_timestamp': time()}, session=s)


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

    async def get_countries_within(self, bbox: BBox) -> Sequence[Country]:
        result = []

        async for c in COUNTRY_COLLECTION.find({
            'geometry': {
                '$geoIntersects': {
                    '$geometry': mapping(bbox.extend(0.1).to_polygon())
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
