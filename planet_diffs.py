import gzip
import re
from asyncio import Semaphore, TaskGroup, timeout, to_thread
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import NoReturn
from xml.parsers import expat

from config import PLANET_DIFF_TIMEOUT, PLANET_REPLICA_URL
from models.aed import AED, ConflictingNodeVersionError, NodeChange, RemovedNode
from utils import HTTP


@dataclass(frozen=True, slots=True)
class ReplicationBatch:
    first_sequence: int
    last_sequence: int
    timestamp: float
    changes: tuple[NodeChange, ...]


def reduce_changes(changes: Iterable[NodeChange]) -> tuple[NodeChange, ...]:
    latest: dict[int, NodeChange] = {}
    for change in changes:
        previous = latest.get(change.id)
        if previous is None or change.version > previous.version:
            latest[change.id] = change
        elif change.version == previous.version and change != previous:
            raise ConflictingNodeVersionError(f'Conflicting node version: {change.id}/{change.version}')
    return tuple(latest.values())


async def get_planet_diffs(sequence: int) -> ReplicationBatch:
    async with timeout(PLANET_DIFF_TIMEOUT.total_seconds()):
        head, _ = await get_replication_state()
        end = min(head, sequence + 8)
        if end <= sequence:
            _, timestamp = await get_replication_state(sequence)
            return ReplicationBatch(sequence + 1, sequence, timestamp, ())
        slots = Semaphore(4)

        async def fetch(number: int) -> tuple[NodeChange, ...]:
            async with slots:
                response = await HTTP.get(f'{PLANET_REPLICA_URL}{_format_sequence_number(number)}.osc.gz')
                response.raise_for_status()
                return await to_thread(parse_diff, response.content)

        async with TaskGroup() as tasks:
            jobs = [tasks.create_task(fetch(number)) for number in range(sequence + 1, end + 1)]
        _, timestamp = await get_replication_state(end)
        changes = await to_thread(reduce_changes, (*job.result() for job in jobs))
        return ReplicationBatch(sequence + 1, end, timestamp, changes)


async def sequence_before(timestamp: float) -> int:
    """Locate a strict lower bracket for the snapshot's source timestamp."""
    high, high_time = await get_replication_state()
    if high_time < timestamp:
        raise ValueError('Replication feed is older than the snapshot')
    step = 1
    low = high
    while True:
        low = max(1, high - step)
        _, low_time = await get_replication_state(low)
        if low_time < timestamp:
            break
        if low == 1:
            raise ValueError('Snapshot precedes available replication history')
        step *= 2
    while high - low > 1:
        mid = (low + high) // 2
        _, mid_time = await get_replication_state(mid)
        if mid_time < timestamp:
            low = mid
        else:
            high = mid
    return low


async def get_replication_state(sequence: int | None = None) -> tuple[int, float]:
    path = 'state.txt' if sequence is None else f'{_format_sequence_number(sequence)}.state.txt'
    response = await HTTP.get(f'{PLANET_REPLICA_URL}{path}')
    response.raise_for_status()
    text = response.text.replace('\\:', ':')
    number = re.search(r'^sequenceNumber=(\d+)$', text, re.MULTILINE)
    date = re.search(r'^timestamp=(\S+)$', text, re.MULTILINE)
    if number is None or date is None:
        raise ValueError('Invalid replication state')
    number = int(number[1])
    if sequence is not None and number != sequence:
        raise ValueError('Replication state sequence mismatch')
    timestamp = datetime.strptime(date[1], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC).timestamp()
    return number, timestamp


def _format_sequence_number(sequence: int) -> str:
    if not 0 <= sequence <= 999999999:
        raise ValueError('Invalid replication sequence')
    value = f'{sequence:09d}'
    return '/'.join(value[i : i + 3] for i in range(0, 9, 3))


def parse_diff(content: bytes) -> tuple[NodeChange, ...]:
    """Expat validates complete XML while materializing only node state."""
    parser = expat.ParserCreate()
    depth = 0
    action = None
    node: tuple[dict[str, str], dict[str, str]] | None = None
    changes: list[NodeChange] = []
    closed = False

    def start(name: str, attrs: dict[str, str]) -> None:
        nonlocal depth, action, node
        depth += 1
        if depth == 1 and name != 'osmChange':
            raise ValueError('Expected osmChange document')
        if depth == 2:
            if name not in ('create', 'modify', 'delete'):
                raise ValueError(f'Unknown replication action: {name}')
            action = name
        elif depth == 3 and name == 'node':
            node = (attrs, {})
        elif depth == 4 and name == 'tag' and node is not None:
            _, tags = node
            key, value = attrs['k'], attrs['v']
            if key in tags:
                raise ValueError('Duplicate node tag')
            tags[key] = value

    def end(name: str) -> None:
        nonlocal depth, node, closed
        if depth == 3 and name == 'node':
            assert node is not None  # noqa: S101 - Expat balances element callbacks
            attrs, tags = node
            id, version = int(attrs['id']), int(attrs['version'])
            if not 0 < id < 2**63 or version <= 0:
                raise ValueError('Invalid node identity/version')
            if (
                action != 'delete'
                and attrs.get('visible', 'true') != 'false'
                and tags.get('emergency') == 'defibrillator'
            ):
                lon, lat = float(attrs['lon']), float(attrs['lat'])
                if not -180 <= lon <= 180 or not -90 <= lat <= 90:
                    raise ValueError('Invalid node coordinates')
                changes.append(AED(id, version, tags, lon, lat))
            else:
                changes.append(RemovedNode(id, version))
            node = None
        if depth == 1:
            closed = True
        depth -= 1

    def reject_doctype(*_) -> NoReturn:
        raise ValueError('Document types are not supported')

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = reject_doctype
    with gzip.GzipFile(fileobj=BytesIO(content)) as stream:
        while chunk := stream.read(65536):
            parser.Parse(chunk, False)
    parser.Parse(b'', True)
    if not closed:
        raise ValueError('Incomplete replication document')
    return reduce_changes(changes)
