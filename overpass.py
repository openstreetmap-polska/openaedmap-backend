from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from config import OVERPASS_API_URL
from utils import get_http_client, retry_exponential


def _extract_center(elements: Iterable[dict]) -> None:
    for e in elements:
        if 'center' in e:
            e |= e['center']
            del e['center']


def _split_by_count(elements: Iterable[dict]) -> list[list[dict]]:
    result = []
    current_split = []

    for e in elements:
        if e['type'] == 'count':
            result.append(current_split)
            current_split = []
        else:
            current_split.append(e)

    assert not current_split, 'Last element must be count type'
    return result


@retry_exponential(None)
async def query_overpass(query: str, *, timeout: int, must_return: bool = False) -> tuple[Sequence[dict], float]:
    join = '' if query.startswith('[') else ';'
    query = f'[out:json][timeout:{timeout}]{join}{query}'

    async with get_http_client() as http:
        r = await http.post(OVERPASS_API_URL, data={'data': query}, timeout=timeout * 2)
        r.raise_for_status()

    data = r.json()
    data_timestamp = (
        datetime.strptime(
            data['osm3s']['timestamp_osm_base'],
            '%Y-%m-%dT%H:%M:%SZ',
        )
        .replace(tzinfo=UTC)
        .timestamp()
    )

    if must_return and not data['elements']:
        raise Exception('No elements returned')

    return data['elements'], data_timestamp
