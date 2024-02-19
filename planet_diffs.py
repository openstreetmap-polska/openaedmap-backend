import gzip
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import chain

import xmltodict
from anyio import create_task_group, fail_after
from httpx import AsyncClient
from sentry_sdk import start_span, trace
from sentry_sdk.tracing import Span

from config import AED_REBUILD_THRESHOLD, PLANET_DIFF_TIMEOUT, REPLICATION_URL
from utils import get_http_client, retry_exponential
from xmltodict_postprocessor import xmltodict_postprocessor


def _format_sequence_number(sequence_number: int) -> str:
    result = f'{sequence_number:09d}'
    result = '/'.join(result[i : i + 3] for i in range(0, 9, 3))
    return result


def _format_actions(xml: str) -> str:
    # <create> -> <action type="create">
    # </create> -> </action>
    # etc.
    xml = re.sub(r'<(create|modify|delete)>', r'<action type="\1">', xml)
    xml = re.sub(r'</(create|modify|delete)>', r'</action>', xml)
    return xml


@retry_exponential(AED_REBUILD_THRESHOLD)
@trace
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


@trace
async def get_planet_diffs(last_update: float) -> tuple[Sequence[dict], float]:
    with fail_after(PLANET_DIFF_TIMEOUT.total_seconds()):
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

            result: list[tuple[int, list[dict]]] = []

            with start_span(Span(description=f'Processing {len(sequence_numbers)} planet diffs')):

                @retry_exponential(AED_REBUILD_THRESHOLD)
                async def _get_planet_diff(sequence_number: int) -> None:
                    r = await http.get(f'{_format_sequence_number(sequence_number)}.osc.gz')
                    r.raise_for_status()

                    xml = gzip.decompress(r.content).decode()
                    xml = _format_actions(xml)
                    actions: list[dict] = xmltodict.parse(
                        xml,
                        postprocessor=xmltodict_postprocessor,
                        force_list=('action', 'node', 'way', 'relation', 'member', 'tag', 'nd'),
                    )['osmChange']['action']

                    node_actions: list[dict] = []

                    for action in actions:
                        # ignore everything that is not a node
                        if 'node' in action:
                            action.pop('way', None)
                            action.pop('relation', None)
                            node_actions.append(action)

                    result.append((sequence_number, node_actions))

                async with create_task_group() as tg:
                    for sequence_number in sequence_numbers:
                        tg.start_soon(_get_planet_diff, sequence_number)

            # sort by sequence number in ascending order
            result.sort(key=lambda x: x[0])

            data = tuple(chain.from_iterable(data for _, data in result))
            data_timestamp = sequence_timestamps[0]
            return data, data_timestamp
