import json
from asyncio import to_thread
from datetime import UTC, datetime

from aiohttp import ClientTimeout
from sentry_sdk import trace

import http_client
from config import OVERPASS_API_URL
from utils import retry_exponential


@retry_exponential(None)
@trace
async def query_overpass(query: str, *, timeout: int, must_return: bool = False) -> tuple[list[dict], float]:  # noqa: ASYNC109
    join = '' if query.startswith('[') else ';'
    query = f'[out:json][timeout:{timeout}]{join}{query}'

    async with http_client.client().post(
        OVERPASS_API_URL,
        data={'data': query},
        timeout=ClientTimeout(total=None, connect=timeout * 2, sock_connect=timeout * 2, sock_read=timeout * 2),
    ) as response:
        response.raise_for_status()
        content = await response.read()
    data = await to_thread(json.loads, content)

    data_timestamp = (
        datetime
        .strptime(
            data['osm3s']['timestamp_osm_base'],
            '%Y-%m-%dT%H:%M:%SZ',
        )
        .replace(tzinfo=UTC)
        .timestamp()
    )

    if must_return and not data['elements']:
        raise ValueError('No elements returned')
    if data.get('remark'):
        raise ValueError(f'Incomplete Overpass response: {data["remark"]}')

    return data['elements'], data_timestamp
