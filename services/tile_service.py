from asyncio import AbstractEventLoop, Semaphore, get_running_loop, wrap_future
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from functools import partial
from math import atan, degrees, pi, sinh
from weakref import WeakKeyDictionary

import mapbox_vector_tile as mvt
import numpy as np
from sentry_sdk import start_span, trace
from shapely import get_coordinates, points, set_coordinates, simplify
from sklearn.cluster import Birch
from sqlalchemy import func, select

from config import MVT_EXTENT, MVT_TRANSFORMER, TILE_COUNTRIES_MAX_Z, TILE_MAX_Z
from db import db_read
from models.bbox import BBox
from models.db.aed import AED
from models.db.country import Country
from services.aed_service import AEDService
from services.country_service import CountryService
from utils import abbreviate

type AEDLocation = tuple[int, str, float, float]

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tiles')
_slots: WeakKeyDictionary[AbstractEventLoop, Semaphore] = WeakKeyDictionary()
_ACCESS_TIERS = {'yes': 0, 'permissive': 1, 'customers': 2, '': 3, 'unknown': 3, 'private': 4, 'no': 5}


class TileService:
    @staticmethod
    @trace
    async def get_tile(z: int, x: int, y: int) -> bytes:
        loop = get_running_loop()
        slot = _slots.setdefault(loop, Semaphore(1))
        await slot.acquire()
        try:
            bbox = _tile_to_bbox(z, x, y)
            if z <= TILE_COUNTRIES_MAX_Z:
                countries = await CountryService.get_intersecting(bbox)
                counts = await AEDService.count_by_country_codes(country.code for country in countries)
                render = partial(_encode_country_tile, z, bbox, countries, counts)
            else:
                async with db_read() as session:
                    stmt = select(
                        AED.id,
                        func.coalesce(AED.tags['access'].astext, ''),
                        func.ST_X(AED.position),
                        func.ST_Y(AED.position),
                    ).where(
                        func.ST_Intersects(AED.position, func.ST_GeomFromText(bbox.extend(0.5).to_polygon().wkt, 4326))
                    )
                    locations = (await session.execute(stmt)).tuples().all()
                render = partial(_encode_aed_tile, z, bbox, locations)
            work = _executor.submit(copy_context().run, render)
        except BaseException:
            slot.release()
            raise

        # Cancelling the request cannot stop a running thread. Keep its slot until
        # the computation actually ends, including after a client disconnects.
        work.add_done_callback(lambda _: loop.call_soon_threadsafe(slot.release) if not loop.is_closed() else None)
        return await wrap_future(work)


def _tile_to_bbox(z: int, x: int, y: int) -> BBox:
    n = 2**z

    def point(x: int, y: int) -> tuple[float, float]:
        return x / n * 360.0 - 180.0, degrees(atan(sinh(pi * (1 - 2 * y / n))))

    p1, p2 = points((point(x, y + 1), point(x + 1, y)))
    return BBox(p1, p2)


def _encode_aed_tile(z: int, bbox: BBox, locations: Sequence[AEDLocation]) -> bytes:
    group_eps = 9.6 / 2**z if z < TILE_MAX_Z else None
    return _mvt_encode(bbox, [{'name': 'defibrillators', 'features': _aed_features(locations, group_eps)}])


def _aed_features(locations: Sequence[AEDLocation], group_eps: float | None) -> list[dict]:
    if not locations:
        return []

    positions = np.asarray([(lon, lat) for _, _, lon, lat in locations])
    if len(locations) <= 1 or group_eps is None:
        coordinates = positions
        properties = [{'node_id': id, 'access': access} for id, access, _, _ in locations]
    else:
        max_fit_samples = 7000
        if len(positions) > max_fit_samples:
            indices = np.linspace(0, len(positions), max_fit_samples, endpoint=False, dtype=int)
            fit_positions = positions[indices]
        else:
            fit_positions = positions

        with start_span(name=f'Fitting model with {len(fit_positions)} samples'):
            model = Birch(threshold=group_eps, n_clusters=None, compute_labels=False)
            model.fit(fit_positions)

        with start_span(name=f'Processing {len(locations)} samples'):
            groups: list[list[int]] = [[] for _ in model.subcluster_centers_]
            with start_span(name='Clustering'):
                clusters = model.predict(positions)
            for i, cluster in enumerate(clusters):
                groups[cluster].append(i)

            coordinates = []
            properties = []
            for group, center in zip(groups, model.subcluster_centers_, strict=True):
                if not group:
                    continue
                if len(group) == 1:
                    id, access, lon, lat = locations[group[0]]
                    coordinates.append((lon, lat))
                    properties.append({'node_id': id, 'access': access})
                else:
                    coordinates.append(center)
                    properties.append({
                        'point_count': len(group),
                        'point_count_abbreviated': abbreviate(len(group)),
                        'access': _group_access(locations[i][1] for i in group),
                    })

    return [
        {'geometry': point, 'properties': props} for point, props in zip(points(coordinates), properties, strict=True)
    ]


def _group_access(accesses: Iterable[str]) -> str:
    access = ''
    tier = float('inf')
    for candidate in accesses:
        candidate_tier = _ACCESS_TIERS.get(candidate)
        if candidate_tier is not None and candidate_tier < tier:
            access, tier = candidate, candidate_tier
            if tier == 0:
                break
    return access


def _encode_country_tile(z: int, bbox: BBox, countries: Sequence[Country], counts: dict[str, int]) -> bytes:
    simplify_tol = 0.5 / 2**z
    return _mvt_encode(
        bbox,
        [
            {
                'name': 'countries',
                'features': [
                    {'geometry': simplify(country.geometry, simplify_tol, preserve_topology=False), 'properties': {}}
                    for country in countries
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
                            'point_count': counts[country.code],
                            'point_count_abbreviated': abbreviate(counts[country.code]),
                        },
                    }
                    for country in countries
                ],
            },
        ],
    )


def _mvt_encode(bbox: BBox, layers: Sequence[dict]) -> bytes:
    with start_span(name='Transforming MVT geometry'):
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
                    start, end = coords_range[i]
                    feature['geometry'] = set_coordinates(feature['geometry'], coords[start:end])
                    i += 1

    with start_span(name='Encoding MVT'):
        return mvt.encode(layers, default_options={'extents': MVT_EXTENT, 'check_winding_order': False})
