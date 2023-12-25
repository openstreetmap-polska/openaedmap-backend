import math
from collections.abc import Sequence
from itertools import chain
from typing import Annotated

import anyio
import mapbox_vector_tile as mvt
import numpy as np
from anyio.streams.memory import MemoryObjectSendStream
from fastapi import APIRouter, Path, Response
from numba import njit
from shapely.ops import transform

from config import (
    DEFAULT_CACHE_MAX_AGE,
    MVT_EXTENT,
    MVT_TRANSFORMER,
    TILE_AEDS_CACHE_STALE,
    TILE_COUNTRIES_CACHE_MAX_AGE,
    TILE_COUNTRIES_CACHE_STALE,
    TILE_COUNTRIES_MAX_Z,
    TILE_MAX_Z,
    TILE_MIN_Z,
)
from middlewares.cache_middleware import make_cache_control
from models.aed import AED
from models.bbox import BBox
from models.country import Country
from models.lonlat import LonLat
from states.aed_state import AEDState, AEDStateDep
from states.country_state import CountryState, CountryStateDep
from utils import abbreviate, print_run_time

router = APIRouter()


@njit(fastmath=True)
def _tile_to_lonlat(z: int, x: int, y: int) -> tuple[float, float]:
    n = 2.0**z
    lon_deg = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat_deg = math.degrees(lat_rad)
    return lon_deg, lat_deg


def _tile_to_bbox(z: int, x: int, y: int) -> BBox:
    p1_lon, p1_lat = _tile_to_lonlat(z, x, y)
    p2_lon, p2_lat = _tile_to_lonlat(z, x + 1, y + 1)
    return BBox(LonLat(p1_lon, p2_lat), LonLat(p2_lon, p1_lat))


async def _count_aed_in_country(country: Country, aed_state: AEDState, send_stream: MemoryObjectSendStream) -> None:
    count = await aed_state.count_aeds_by_country_code(country.code)
    await send_stream.send((country, count))


def _mvt_rescale(x, y, x_min: float, y_min: float, x_span: float, y_span: float) -> tuple:
    x_mvt, y_mvt = MVT_TRANSFORMER.transform(np.array(x), np.array(y))

    # subtract minimum boundary and scale to MVT extent
    x_scaled = np.rint((x_mvt - x_min) / x_span * MVT_EXTENT).astype(int)
    y_scaled = np.rint((y_mvt - y_min) / y_span * MVT_EXTENT).astype(int)

    return x_scaled, y_scaled


def _mvt_encode(bbox: BBox, data: Sequence[dict]) -> bytes:
    x_min, y_min = MVT_TRANSFORMER.transform(*bbox.p1)
    x_max, y_max = MVT_TRANSFORMER.transform(*bbox.p2)
    x_span = x_max - x_min
    y_span = y_max - y_min

    with print_run_time('Transforming MVT geometry'):
        for feature in chain.from_iterable(d['features'] for d in data):
            feature['geometry'] = transform(
                func=lambda x, y: _mvt_rescale(x, y, x_min, y_min, x_span, y_span),
                geom=feature['geometry'],
            )

    with print_run_time('Encoding MVT'):
        return mvt.encode(data, default_options={'extents': MVT_EXTENT})


async def _get_tile_country(z: int, bbox: BBox, country_state: CountryState, aed_state: AEDState) -> bytes:
    with print_run_time('Querying countries'):
        countries = await country_state.get_countries_within(bbox)

    simplify_tol = 0.5 / 2**z if z < TILE_MAX_Z else None
    geometries = (country.geometry.simplify(simplify_tol, preserve_topology=False) for country in countries)

    send_stream, receive_stream = anyio.create_memory_object_stream()
    country_count_map = {}

    with print_run_time('Counting AEDs'):
        async with anyio.create_task_group() as tg, send_stream, receive_stream:
            for country in countries:
                tg.start_soon(_count_aed_in_country, country, aed_state, send_stream)

            for _ in range(len(countries)):
                country, count = await receive_stream.receive()
                country_count_map[country.name] = (count, abbreviate(count))

    return _mvt_encode(
        bbox,
        [
            {
                'name': 'countries',
                'features': [
                    {
                        'geometry': geometry,
                        'properties': {},
                    }
                    for geometry in geometries
                ],
            },
            {
                'name': 'defibrillators',
                'features': [
                    {
                        'geometry': country.label.position.shapely,
                        'properties': {
                            'country_name': country.name,
                            'country_names': country.names,
                            'country_code': country.code,
                            'point_count': country_count_map[country.name][0],
                            'point_count_abbreviated': country_count_map[country.name][1],
                        },
                    }
                    for country in countries
                ],
            },
        ],
    )


async def _get_tile_aed(z: int, bbox: BBox, aed_state: AEDState) -> bytes:
    group_eps = 9.8 / 2**z if z < TILE_MAX_Z else None
    aeds = await aed_state.get_aeds_within(bbox.extend(0.5), group_eps)

    return _mvt_encode(
        bbox,
        [
            {
                'name': 'defibrillators',
                'features': [
                    {
                        'geometry': aed.position.shapely,
                        'properties': {
                            'node_id': int(aed.id),
                            'access': aed.access,
                        },
                    }
                    if isinstance(aed, AED)
                    else {
                        'geometry': aed.position.shapely,
                        'properties': {
                            'point_count': aed.count,
                            'point_count_abbreviated': abbreviate(aed.count),
                            'access': aed.access,
                        },
                    }
                    for aed in aeds
                ],
            }
        ],
    )


@router.get('/tile/{z}/{x}/{y}.mvt')
async def get_tile(
    z: Annotated[int, Path(ge=TILE_MIN_Z, le=TILE_MAX_Z)],
    x: Annotated[int, Path(ge=0)],
    y: Annotated[int, Path(ge=0)],
    country_state: CountryStateDep,
    aed_state: AEDStateDep,
):
    bbox = _tile_to_bbox(z, x, y)
    assert bbox.p1.lon <= bbox.p2.lon, f'{bbox.p1.lon=} <= {bbox.p2.lon=}'
    assert bbox.p1.lat <= bbox.p2.lat, f'{bbox.p1.lat=} <= {bbox.p2.lat=}'

    if z <= TILE_COUNTRIES_MAX_Z:
        content = await _get_tile_country(z, bbox, country_state, aed_state)
        headers = {'Cache-Control': make_cache_control(TILE_COUNTRIES_CACHE_MAX_AGE, TILE_COUNTRIES_CACHE_STALE)}
    else:
        content = await _get_tile_aed(z, bbox, aed_state)
        headers = {'Cache-Control': make_cache_control(DEFAULT_CACHE_MAX_AGE, TILE_AEDS_CACHE_STALE)}

    return Response(content, headers=headers, media_type='application/vnd.mapbox-vector-tile')
