from collections import defaultdict
from itertools import chain, cycle, islice, pairwise
from typing import Iterable, NamedTuple, Sequence

import networkx as nx
import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union
from tqdm import tqdm

from overpass import query_overpass

_QUERY = (
    'rel[boundary=administrative]["ISO3166-1"][name];'
    'out geom qt;'
)


class CountryFromOSM(NamedTuple):
    tags: dict[str, str]
    geometry: Polygon | MultiPolygon


def _connect_segments(segments: Sequence[tuple[tuple]]) -> Iterable[Sequence[tuple]]:
    node_count = defaultdict(int)

    for node in chain.from_iterable(segments):
        node_count[node] += 1

    # node = intersection, node_count > 1
    # edge = segment between intersections
    G = nx.DiGraph()

    # build graph
    for segment in segments:
        subsegment_start = None
        subsegment = []
        for node in segment:
            # intersection node
            if node_count[node] > 1:
                if subsegment_start:
                    if len(subsegment) == 0:
                        G.add_edge(subsegment_start, node)
                        G.add_edge(node, subsegment_start)
                    elif len(subsegment) == 1:
                        first = subsegment[0]
                        G.add_edge(subsegment_start, first)
                        G.add_edge(first, node)
                        G.add_edge(node, first)
                        G.add_edge(first, subsegment_start)
                    else:
                        first = subsegment[0]
                        last = subsegment[-1]
                        G.add_edge(subsegment_start, first)
                        G.add_edge(first, last, subsegment=subsegment)
                        G.add_edge(last, node)
                        G.add_edge(node, last)
                        G.add_edge(last, first, subsegment=subsegment[::-1])
                        G.add_edge(first, subsegment_start)
                subsegment = []
                subsegment_start = node
            # normal node
            elif subsegment_start:
                subsegment.append(node)

    connected = set()

    def connected_normalize(segment: list) -> tuple:
        min_idx = np.argmin(segment, axis=0)[0]

        # normalize starting point
        aligned = tuple(chain(
            islice(segment, min_idx, len(segment) - 1),
            islice(segment, min_idx + 1)))

        # normalize orientation
        if segment[-1] < segment[1]:
            aligned = aligned[::-1]

        return aligned

    for c in nx.simple_cycles(G):
        c = tuple(islice(cycle(c), len(c) + 1))  # close the cycle

        merged_unordered: list[list] = []

        for u, v in pairwise(c):
            if subsegment := G[u][v].get('subsegment'):
                merged_unordered.append(subsegment)
            else:
                merged_unordered.append([u, v])

        # # skip invalid cycles
        # if len({
        #         merged_unordered[0][0], merged_unordered[0][-1],
        #         merged_unordered[-1][0], merged_unordered[-1][-1]}) == 4:
        #     continue

        # # small optimization
        # if len(merged_unordered) == 1:
        #     connected.add(connected_normalize(merged_unordered[0]))
        #     continue

        first = merged_unordered[0]
        second = merged_unordered[1]

        # proper orientation of the first segment
        if first[0] in (second[0], second[-1]):
            merged = first[::-1]
        else:
            merged = first

        for segment in merged_unordered[1:]:
            if merged[-1] == segment[0]:
                merged.extend(islice(segment, 1, None))
            elif merged[-1] == segment[-1]:
                merged.extend(islice(reversed(segment), 1, None))
            else:
                print('⚠️ Invalid cycle')
                break
        else:
            if len(merged) >= 4 and merged[1] != merged[-2]:
                connected.add(connected_normalize(merged))

    return connected


async def get_countries_from_osm() -> tuple[Sequence[CountryFromOSM], float]:
    countries, data_timestamp = await query_overpass(_QUERY, timeout=3600, must_return=True)
    result = []

    for country in tqdm(countries, desc='🗺️ Processing geometry'):
        outer_segments = []
        inner_segments = []

        for member in country.get('members', []):
            if member['type'] != 'way':
                continue

            if member['role'] == 'outer':
                outer_segments.append(tuple((g['lon'], g['lat']) for g in member['geometry']))
            elif member['role'] == 'inner':
                inner_segments.append(tuple((g['lon'], g['lat']) for g in member['geometry']))

        outer_polys = (Polygon(s) for s in _connect_segments(outer_segments))
        outer_polys = tuple(p for p in outer_polys if p.is_valid)

        if not outer_polys:
            continue

        inner_polys = (Polygon(s) for s in _connect_segments(inner_segments))
        inner_polys = tuple(p for p in inner_polys if p.is_valid)

        outer_union = unary_union(outer_polys)
        inner_union = unary_union(inner_polys)
        geometry = outer_union.difference(inner_union)

        result.append(CountryFromOSM(country['tags'], geometry))

    return tuple(result), data_timestamp
