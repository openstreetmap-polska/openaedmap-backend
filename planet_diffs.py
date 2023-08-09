import gzip
import re
from datetime import UTC, datetime
from heapq import heappush
from itertools import chain
from operator import itemgetter
from typing import Sequence

import anyio
import xmltodict
from anyio.streams.memory import MemoryObjectSendStream
from httpx import AsyncClient

from config import AED_REBUILD_THRESHOLD, PLANET_DIFF_TIMEOUT, REPLICATION_URL
from utils import get_http_client, retry_exponential
from xmltodict_postprocessor import xmltodict_postprocessor


def _format_sequence_number(sequence_number: int) -> str:
    result = f'{sequence_number:09d}'
    result = '/'.join(result[i:i + 3] for i in range(0, 9, 3))
    return result


def _format_actions(xml: str) -> str:
    # <create> -> <action type="create">
    # </create> -> </action>
    # etc.
    xml = re.sub(r'<(create|modify|delete)>', r'<action type="\1">', xml)
    xml = re.sub(r'</(create|modify|delete)>', r'</action>', xml)
    return xml


@retry_exponential(AED_REBUILD_THRESHOLD)
async def _get_state(http: AsyncClient, sequence_number: int | None) -> tuple[int, float]:
    if sequence_number is None:
        r = await http.get('state.txt')
    else:
        r = await http.get(f'{_format_sequence_number(sequence_number)}.state.txt')

    r.raise_for_status()

    text = r.text
    text = text.replace('\\:', ':')

    sequence_number = int(re.search(r'sequenceNumber=(\d+)', text).group(1))
    sequence_date_str = re.search(r'timestamp=(\S+)', text).group(1)
    sequence_date = datetime.strptime(sequence_date_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)
    sequence_timestamp = sequence_date.timestamp()

    return sequence_number, sequence_timestamp


@retry_exponential(AED_REBUILD_THRESHOLD)
async def _get_planet_diff(http: AsyncClient, sequence_number: int, send_stream: MemoryObjectSendStream) -> None:
    r = await http.get(f'{_format_sequence_number(sequence_number)}.osc.gz')
    r.raise_for_status()

    xml_gz = r.content
    xml = gzip.decompress(xml_gz).decode()
    xml = _format_actions(xml)
    actions: list[dict] = xmltodict.parse(xml,
                                          postprocessor=xmltodict_postprocessor,
                                          force_list=('action', 'node', 'way', 'relation', 'member', 'tag', 'nd'))['osmChange']['action']

    node_actions = []

    for action in actions:
        if 'node' in action:
            action.pop('way', None)
            action.pop('relation', None)
            node_actions.append(action)

    await send_stream.send((sequence_number, tuple(node_actions)))


async def get_planet_diffs(last_update: float) -> tuple[Sequence[dict], float]:
    with anyio.fail_after(PLANET_DIFF_TIMEOUT.total_seconds()):
        async with get_http_client(REPLICATION_URL) as http:
            sequence_numbers = []
            sequence_timestamps = []

            while True:
                next_sequence_number = sequence_numbers[-1] - 1 if sequence_numbers else None
                sequence_number, sequence_timestamp = await _get_state(http, next_sequence_number)

                if sequence_timestamp <= last_update:
                    break

                sequence_numbers.append(sequence_number)
                sequence_timestamps.append(sequence_timestamp)

            if not sequence_numbers:
                return (), last_update

            send_stream, receive_stream = anyio.create_memory_object_stream()
            result: list[tuple[int, dict]] = []

            async with anyio.create_task_group() as tg, send_stream, receive_stream:
                for sequence_number in sequence_numbers:
                    tg.start_soon(_get_planet_diff, http, sequence_number, send_stream)

                for _ in range(len(sequence_numbers)):
                    sequence_number, data = await receive_stream.receive()
                    result.append((sequence_number, data))

            result.sort(key=itemgetter(0))
            data = tuple(chain.from_iterable(data for _, data in result))
            data_timestamp = sequence_timestamps[0]

            return data, data_timestamp
