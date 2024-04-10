import logging
from collections.abc import Sequence
from time import time
from typing import NoReturn

import anyio
from sentry_sdk import start_transaction, trace
from shapely.geometry import Point
from sqlalchemy import func, select, text

from config import COUNTRY_UPDATE_DELAY
from country_code_assigner import CountryCodeAssigner
from db import db_read, db_write
from models.bbox import BBox
from models.db.aed import AED
from models.db.country import Country
from osm_countries import get_osm_countries
from services.state_service import StateService
from utils import retry_exponential


class CountryService:
    @staticmethod
    async def update_db_task(*, task_status=anyio.TASK_STATUS_IGNORED) -> NoReturn:
        if (await _should_update_db())[1] > 0:
            task_status.started()
            started = True
        else:
            started = False

        while True:
            with start_transaction(op='db.update', name=CountryService.update_db_task.__qualname__, sampled=True):
                await _update_db()
            if not started:
                task_status.started()
                started = True
            await anyio.sleep(COUNTRY_UPDATE_DELAY.total_seconds())

    @staticmethod
    @trace
    async def get_all() -> Sequence[Country]:
        async with db_read() as session:
            stmt = select(Country)
            return (await session.scalars(stmt)).all()

    @classmethod
    @trace
    async def get_intersecting(cls, bbox_or_geom: BBox | Point) -> Sequence[Country]:
        geometry = bbox_or_geom.to_polygon() if isinstance(bbox_or_geom, BBox) else bbox_or_geom
        geometry_wkt = geometry.wkt

        async with db_read() as session:
            stmt = select(Country).where(func.ST_Intersects(Country.geometry, func.ST_GeomFromText(geometry_wkt, 4326)))
            return (await session.scalars(stmt)).all()


@trace
async def _should_update_db() -> tuple[bool, float]:
    data = await StateService.get('country')
    if data is None or data.get('version', 1) < 2:
        return True, 0

    update_timestamp: float = data['update_timestamp']
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

    logging.info('Updating country database...')
    osm_countries = await get_osm_countries()
    data_timestamp = osm_countries[0].timestamp if osm_countries else float('-inf')

    if data_timestamp <= update_timestamp:
        logging.info('Nothing to update')
        return

    if len(osm_countries) < 210:
        # suspiciously low number of countries
        logging.warning('Not enough countries found: %d', len(osm_countries))
        return

    code_assigner = CountryCodeAssigner()
    countries = tuple(
        Country(
            code=code_assigner.get_unique(c.tags),
            names=_get_names(c.tags),
            geometry=c.geometry,
            label_position=c.representative_point,
        )
        for c in osm_countries
    )

    async with db_write() as session:
        await session.execute(text(f'TRUNCATE "{Country.__tablename__}" CASCADE'))
        session.add_all(countries)

    await StateService.set('country', {'update_timestamp': data_timestamp, 'version': 2})

    logging.info('Updating country codes')
    from services.aed_service import AEDService

    await AEDService.update_country_codes()

    logging.info('Updating statistics')
    async with db_write() as session:
        await session.connection(execution_options={'isolation_level': 'AUTOCOMMIT'})
        await session.execute(text(f'ANALYZE "{AED.__tablename__}", "{Country.__tablename__}"'))

    logging.info('Country update finished')


def _get_names(tags: dict[str, str]) -> dict[str, str]:
    names = {}

    for key in ('name:en', 'int_name', 'name'):
        default = tags.get(key)
        if default:
            names['default'] = default
            break

    for k, v in tags.items():
        if k.startswith('name:'):
            names[k[5:].upper()] = v

    return names
