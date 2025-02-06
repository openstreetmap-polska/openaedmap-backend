import gzip
import re
from asyncio import TaskGroup, timeout
from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import chain

import xmltodict
from sentry_sdk import start_span, trace

from config import AED_REBUILD_THRESHOLD, PLANET_DIFF_TIMEOUT, PLANET_REPLICA_URL
from utils import HTTP, retry_exponential
from xmltodict_postprocessor import xmltodict_postprocessor

_action_open_re = re.compile(r'<(?:create|modify|delete)>')
_action_close_re = re.compile(r'</(?:create|modify|delete)>')


@trace
async def get_planet_diffs(last_update: float) -> tuple[Sequence[dict], float]:
    async with timeout(PLANET_DIFF_TIMEOUT.total_seconds()):
        sequence_numbers = []
        sequence_timestamps = []

        while True:
            next_sequence_number = sequence_numbers[-1] - 1 if sequence_numbers else None
            sequence_number, sequence_timestamp = await _get_state(next_sequence_number)

            if sequence_timestamp <= last_update:
                break

            sequence_numbers.append(sequence_number)
            sequence_timestamps.append(sequence_timestamp)

        if not sequence_numbers:
            return (), last_update

        with start_span(description=f'Processing {len(sequence_numbers)} planet diffs'):

            @retry_exponential(AED_REBUILD_THRESHOLD)
            async def _get_planet_diff(sequence_number: int) -> tuple[int, list[dict]]:
                path = f'{_format_sequence_number(sequence_number)}.osc.gz'
                r = await HTTP.get(f'{PLANET_REPLICA_URL}{path}')
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

                return sequence_number, node_actions

            async with TaskGroup() as tg:
                tasks = [tg.create_task(_get_planet_diff(sequence_number)) for sequence_number in sequence_numbers]

        result = [t.result() for t in tasks]
        result.sort(key=lambda x: x[0])  # sort by sequence number in ascending order

        data = tuple(chain.from_iterable(data for _, data in result))
        data_timestamp = sequence_timestamps[0]
        return data, data_timestamp


@retry_exponential(AED_REBUILD_THRESHOLD)
@trace
async def _get_state(sequence_number: int | None) -> tuple[int, float]:
    path = 'state.txt' if sequence_number is None else f'{_format_sequence_number(sequence_number)}.state.txt'
    r = await HTTP.get(f'{PLANET_REPLICA_URL}{path}')
    r.raise_for_status()

    text = r.text.replace('\\:', ':')
    sequence_number = int(re.search(r'sequenceNumber=(\d+)', text).group(1))  # pyright: ignore [reportOptionalMemberAccess]
    sequence_date_str = re.search(r'timestamp=(\S+)', text).group(1)  # pyright: ignore [reportOptionalMemberAccess]
    sequence_date = datetime.strptime(sequence_date_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)
    sequence_timestamp = sequence_date.timestamp()
    return sequence_number, sequence_timestamp


def _format_sequence_number(sequence_number: int) -> str:
    result = f'{sequence_number:09d}'
    result = '/'.join(result[i : i + 3] for i in range(0, 9, 3))
    return result


def _format_actions(xml: str) -> str:
    # <create> -> <action type="create">
    # </create> -> </action>
    # etc.
    xml = _action_open_re.sub(r'<action type="\1">', xml)
    xml = _action_close_re.sub(r'</action>', xml)
    return xml
