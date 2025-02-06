from asyncio import TaskGroup
from collections.abc import Sequence
from math import atan, degrees, pi, sinh
from typing import Annotated

import mapbox_vector_tile as mvt
import numpy as np
from fastapi import APIRouter, Path, Response
from sentry_sdk import start_span, trace
from shapely import get_coordinates, points, set_coordinates, simplify

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
from middlewares.cache_control_middleware import make_cache_control
from models.bbox import BBox
from models.db.aed import AED
from models.db.country import Country
from services.aed_service import AEDService
from services.country_service import CountryService
from utils import abbreviate

router = APIRouter()


def _tile_to_point(z: int, x: int, y: int) -> tuple[float, float]:
    n = 2**z
    lon_deg = x / n * 360.0 - 180.0
    lat_rad = atan(sinh(pi * (1 - 2 * y / n)))
    lat_deg = degrees(lat_rad)
    return lon_deg, lat_deg


def _tile_to_bbox(z: int, x: int, y: int) -> BBox:
    p1_coords = _tile_to_point(z, x, y + 1)
    p2_coords = _tile_to_point(z, x + 1, y)
    p1, p2 = points((p1_coords, p2_coords))
    return BBox(p1, p2)


@router.get('/tile/{z}/{x}/{y}.mvt')
async def get_tile(
    z: Annotated[int, Path(ge=TILE_MIN_Z, le=TILE_MAX_Z)],
    x: Annotated[int, Path(ge=0)],
    y: Annotated[int, Path(ge=0)],
):
    bbox = _tile_to_bbox(z, x, y)

    # no-transform:
    # https://community.cloudflare.com/t/cloudflare-is-decompressing-my-mapbox-vector-tiles/278031/2

    if z <= TILE_COUNTRIES_MAX_Z:
        content = await _get_tile_country(z, bbox)
        cache_control = make_cache_control(TILE_COUNTRIES_CACHE_MAX_AGE, TILE_COUNTRIES_CACHE_STALE) + ', no-transform'
    else:
        content = await _get_tile_aed(z, bbox)
        cache_control = make_cache_control(DEFAULT_CACHE_MAX_AGE, TILE_AEDS_CACHE_STALE) + ', no-transform'

    return Response(content, headers={'Cache-Control': cache_control}, media_type='application/vnd.mapbox-vector-tile')


def _mvt_encode(bbox: BBox, layers: Sequence[dict]) -> bytes:
    with start_span(description='Transforming MVT geometry'):
        coords_range = []
        coords = []

        for layer in layers:
            for feature in layer['features']:
                feature_coords = get_coordinates(feature['geometry'])
                coords_len = len(coords)
                coords_range.append((coords_len, coords_len + len(feature_coords)))
                coords.extend(feature_coords)

        if coords:
            bbox_coords = np.asarray((get_coordinates(bbox.p1)[0], get_coordinates(bbox.p2)[0]))
            bbox_coords = np.asarray(MVT_TRANSFORMER.transform(bbox_coords[:, 0], bbox_coords[:, 1])).T
            span = bbox_coords[1] - bbox_coords[0]

            coords = np.asarray(coords)
            coords = np.asarray(MVT_TRANSFORMER.transform(coords[:, 0], coords[:, 1])).T
            coords = np.rint((coords - bbox_coords[0]) / span * MVT_EXTENT).astype(int)

            i = 0
            for layer in layers:
                for feature in layer['features']:
                    feature_coords_range = coords_range[i]
                    feature_coords = coords[feature_coords_range[0] : feature_coords_range[1]]
                    feature['geometry'] = set_coordinates(feature['geometry'], feature_coords)
                    i += 1

    with start_span(description='Encoding MVT'):
        return mvt.encode(
            layers,
            default_options={
                'extents': MVT_EXTENT,
                'check_winding_order': False,
            },
        )


@trace
async def _get_tile_country(z: int, bbox: BBox) -> bytes:
    countries = await CountryService.get_intersecting(bbox)
    country_count_map: dict[str, tuple[int, str]] = {}

    with start_span(description='Counting AEDs'):

        async def count_task(country: Country) -> None:
            count = await AEDService.count_by_country_code(country.code)
            country_count_map[country.name] = (count, abbreviate(count))

        async with TaskGroup() as tg:
            for country in countries:
                tg.create_task(count_task(country))

    simplify_tol = 0.5 / 2**z
    geometries = (simplify(country.geometry, simplify_tol, preserve_topology=False) for country in countries)

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
    group_eps = 9.6 / 2**z if z < TILE_MAX_Z else None
    aeds = await AEDService.get_intersecting(bbox.extend(0.5), group_eps)

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
