from collections.abc import Sequence
from datetime import UTC, datetime

from aiohttp import ClientTimeout
from sentry_sdk import trace

from config import OVERPASS_API_URL
from utils import http_post, retry_exponential


@retry_exponential(None)
@trace
async def query_overpass(query: str, *, timeout: int, must_return: bool = False) -> tuple[Sequence[dict], float]:  # noqa: ASYNC109
    join = '' if query.startswith('[') else ';'
    query = f'[out:json][timeout:{timeout}]{join}{query}'

    async with http_post(
        OVERPASS_API_URL,
        data={'data': query},
        timeout=ClientTimeout(total=timeout * 2),
        allow_redirects=True,
        raise_for_status=True,
    ) as r:
        data = await r.json()

    data_timestamp = (
        datetime.strptime(
            data['osm3s']['timestamp_osm_base'],
            '%Y-%m-%dT%H:%M:%SZ',
        )
        .replace(tzinfo=UTC)
        .timestamp()
    )

    if must_return and not data['elements']:
        raise ValueError('No elements returned')

    return data['elements'], data_timestamp
