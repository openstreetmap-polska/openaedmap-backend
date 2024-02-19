from collections.abc import Sequence
from itertools import chain
from math import atan, degrees, pi, sinh
from typing import Annotated

import mapbox_vector_tile as mvt
import numpy as np
from anyio import create_task_group
from fastapi import APIRouter, Path, Response
from sentry_sdk import start_span, trace
from shapely import Point
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
from states.aed_state import AEDState
from states.country_state import CountryState
from utils import abbreviate

router = APIRouter()


def _tile_to_point(z: int, x: int, y: int) -> Point:
    n = 2**z
    lon_deg = x / n * 360.0 - 180.0
    lat_rad = atan(sinh(pi * (1 - 2 * y / n)))
    lat_deg = degrees(lat_rad)
    return Point(lon_deg, lat_deg)


def _tile_to_bbox(z: int, x: int, y: int) -> BBox:
    p1 = _tile_to_point(z, x, y)
    p2 = _tile_to_point(z, x + 1, y + 1)
    return BBox(p1, p2)


@router.get('/tile/{z}/{x}/{y}.mvt')
async def get_tile(
    z: Annotated[int, Path(ge=TILE_MIN_Z, le=TILE_MAX_Z)],
    x: Annotated[int, Path(ge=0)],
    y: Annotated[int, Path(ge=0)],
):
    bbox = _tile_to_bbox(z, x, y)

    if z <= TILE_COUNTRIES_MAX_Z:
        content = await _get_tile_country(z, bbox)
        headers = {'Cache-Control': make_cache_control(TILE_COUNTRIES_CACHE_MAX_AGE, TILE_COUNTRIES_CACHE_STALE)}
    else:
        content = await _get_tile_aed(z, bbox)
        headers = {'Cache-Control': make_cache_control(DEFAULT_CACHE_MAX_AGE, TILE_AEDS_CACHE_STALE)}

    return Response(content, headers=headers, media_type='application/vnd.mapbox-vector-tile')


def _mvt_rescale(x: float, y: float, x_min: float, y_min: float, x_span: float, y_span: float) -> tuple[int, int]:
    x_mvt, y_mvt = MVT_TRANSFORMER.transform(x, y)

    # subtract minimum boundary and scale to MVT extent
    x_scaled = int((x_mvt - x_min) / x_span * MVT_EXTENT)
    y_scaled = int((y_mvt - y_min) / y_span * MVT_EXTENT)
    return x_scaled, y_scaled


def _mvt_encode(bbox: BBox, data: Sequence[dict]) -> bytes:
    x_min, y_min = MVT_TRANSFORMER.transform(bbox.p1.x, bbox.p1.y)
    x_max, y_max = MVT_TRANSFORMER.transform(bbox.p2.x, bbox.p2.y)
    x_span = x_max - x_min
    y_span = y_max - y_min

    with start_span(description='Transforming MVT geometry'):
        for feature in chain.from_iterable(d['features'] for d in data):
            feature['geometry'] = transform(
                func=lambda x, y: _mvt_rescale(x, y, x_min, y_min, x_span, y_span),
                geom=feature['geometry'],
            )

    with start_span(description='Encoding MVT'):
        return mvt.encode(
            data,
            default_options={
                'extents': MVT_EXTENT,
                'check_winding_order': False,
            },
        )


@trace
async def _get_tile_country(z: int, bbox: BBox) -> bytes:
    countries = await CountryState.get_countries_within(bbox)
    country_count_map: dict[str, str] = {}

    with start_span(description='Counting AEDs'):

        async def count_task(country: Country) -> None:
            count = await AEDState.count_aeds_by_country_code(country.code)
            country_count_map[country.name] = (count, abbreviate(count))

        async with create_task_group() as tg:
            for country in countries:
                tg.start_soon(count_task, country)

    simplify_tol = 0.5 / 2**z if z < TILE_MAX_Z else None
    geometries = (country.geometry.simplify(simplify_tol, preserve_topology=False) for country in countries)

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
                        'geometry': country.label_position,
                        'properties': {
                            'country_name': country.name,
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


@trace
async def _get_tile_aed(z: int, bbox: BBox) -> bytes:
    group_eps = 9.8 / 2**z if z < TILE_MAX_Z else None
    aeds = await AEDState.get_aeds_within_bbox(bbox.extend(0.5), group_eps)

    return _mvt_encode(
        bbox,
        [
            {
                'name': 'defibrillators',
                'features': [
                    {
                        'geometry': aed.position,
                        'properties': {
                            'node_id': aed.id,
                            'access': aed.access,
                        },
                    }
                    if isinstance(aed, AED)
                    else {
                        'geometry': aed.position,
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
