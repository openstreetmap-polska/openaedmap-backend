import gzip
from asyncio import timeout

import httpx
import pytest

from tests.data import seed_aeds, seed_countries


@pytest.mark.asyncio
async def test_replication_uses_latest_terminal_state_and_atomic_memberships(database):
    from config import PLANET_REPLICA_URL
    from db import connection
    from planet_diffs import ReplicationBatch, parse_diff
    from services.aed_service import AEDService
    from services.state_service import StateService

    await seed_countries([('AA', {'default': 'First'}, 'POLYGON((0 0,4 0,4 4,0 4,0 0))', 2, 2)])
    await StateService.set('aed', {'feed': PLANET_REPLICA_URL, 'sequence': 10, 'update_timestamp': 1})
    changes = parse_diff(
        gzip.compress(b"""<osmChange version="0.6">
      <modify><node id="42" version="1" lon="2" lat="2"/></modify>
      <modify><node id="42" version="2" lon="2" lat="2">
        <tag k="emergency" v="defibrillator"/><tag k="name" v="New AED"/>
      </node></modify></osmChange>""")
    )
    assert await AEDService.publish(ReplicationBatch(11, 11, 2, changes), 10)
    aed = await AEDService.get_by_id(42)
    assert aed is not None
    assert aed.version == 2
    assert aed.tags['name'] == 'New AED'
    async with connection() as conn:
        assert await conn.fetchval('SELECT country_codes FROM aed WHERE id=42') == ['AA']
        assert await conn.fetchval("SELECT feature_count FROM country_count WHERE code='AA'") == 1
        assert await conn.fetchval('SELECT feature_count FROM map_revision') == 1
    state = await StateService.get('aed')
    assert state is not None
    assert state['sequence'] == 11


@pytest.mark.asyncio
async def test_replication_deletion_retains_version_and_replay_is_idempotent(database):
    from config import PLANET_REPLICA_URL
    from db import connection
    from models.aed import AED, RemovedNode
    from planet_diffs import ReplicationBatch
    from services.aed_service import AEDService
    from services.state_service import StateService

    await seed_aeds([(81, 5, {'emergency': 'defibrillator'}, 2, 3, [])])
    await StateService.set('aed', {'feed': PLANET_REPLICA_URL, 'sequence': 20, 'update_timestamp': 1})
    deleted = ReplicationBatch(21, 21, 2, (RemovedNode(81, 6),))
    assert await AEDService.publish(deleted, 20)
    assert await AEDService.get_by_id(81) is None
    assert not await AEDService.publish(deleted, 20)
    await AEDService.publish(ReplicationBatch(22, 22, 3, (AED(81, 5, {'emergency': 'defibrillator'}, 2, 3),)), 21)
    assert await AEDService.get_by_id(81) is None
    async with connection() as conn:
        assert await conn.fetchval('SELECT version FROM aed WHERE id=81') == 6
        assert await conn.fetchval('SELECT feature_count FROM map_revision') == 0


@pytest.mark.asyncio
async def test_conflicting_version_rolls_back_cursor_payload_and_counts(database):
    from config import PLANET_REPLICA_URL
    from db import connection
    from models.aed import AED, ConflictingNodeVersionError
    from planet_diffs import ReplicationBatch
    from services.aed_service import AEDService
    from services.state_service import StateService

    await seed_aeds([(91, 7, {'name': 'Original'}, 3, 4, [])])
    state = {'feed': PLANET_REPLICA_URL, 'sequence': 30, 'update_timestamp': 1}
    await StateService.set('aed', state)
    with pytest.raises(ConflictingNodeVersionError):
        await AEDService.publish(ReplicationBatch(31, 31, 2, (AED(91, 7, {'name': 'Different'}, 3, 4),)), 30)
    aed = await AEDService.get_by_id(91)
    assert aed is not None
    assert aed.tags == {'name': 'Original'}
    assert await StateService.get('aed') == state
    async with connection() as conn:
        assert await conn.fetchval('SELECT feature_count FROM map_revision') == 1


@pytest.mark.parametrize(
    'xml',
    [
        b'<osmChange><modify><node id="1" version="1"/></modify>',
        b'<osm><node id="1" version="1"/></osm>',
        b'<!DOCTYPE osmChange [<!ENTITY sample "x">]><osmChange/>',
    ],
)
def test_incomplete_or_wrong_source_document_is_rejected(xml):
    from xml.parsers.expat import ExpatError

    from planet_diffs import parse_diff

    with pytest.raises((ValueError, ExpatError)):
        parse_diff(gzip.compress(xml))


@pytest.mark.asyncio
async def test_irrelevant_replication_advances_cursor_without_changing_content_revision(database):
    from config import PLANET_REPLICA_URL
    from db import connection
    from models.aed import RemovedNode
    from planet_diffs import ReplicationBatch
    from services.aed_service import AEDService
    from services.state_service import StateService

    await StateService.set('aed', {'feed': PLANET_REPLICA_URL, 'sequence': 40, 'update_timestamp': 1})
    assert not await AEDService.publish(ReplicationBatch(41, 41, 2, (RemovedNode(999, 2),)), 40)
    state = await StateService.get('aed')
    assert state is not None
    assert state['sequence'] == 41
    async with connection() as conn:
        assert await conn.fetchval('SELECT revision FROM map_revision') == 0


@pytest.mark.asyncio
async def test_updater_resumes_committed_cursor_without_rebuilding_snapshot(database, monkeypatch):
    import overpass
    import planet_diffs
    from config import PLANET_REPLICA_URL
    from services.aed_service import AEDService, _update_db
    from services.state_service import StateService

    await seed_aeds([(92, 3, {'name': 'Retained'}, 3, 4, [])])
    await StateService.set('aed', {'feed': PLANET_REPLICA_URL, 'sequence': 41, 'update_timestamp': 1})
    responses = {
        f'{PLANET_REPLICA_URL}state.txt': b'sequenceNumber=42\ntimestamp=2026-10-17T09:00:00Z\n',
        f'{PLANET_REPLICA_URL}000/000/042.state.txt': b'sequenceNumber=42\ntimestamp=2026-10-17T09:00:00Z\n',
        f'{PLANET_REPLICA_URL}000/000/042.osc.gz': gzip.compress(b"""<osmChange><create>
            <node id="93" version="2" lon="5" lat="6">
                <tag k="emergency" v="defibrillator"/>
            </node></create></osmChange>"""),
    }

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=responses[str(request.url)])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream:
        monkeypatch.setattr(planet_diffs, 'HTTP', upstream)
        monkeypatch.setattr(overpass, 'HTTP', upstream)
        async with timeout(10):
            await _update_db()
    state = await StateService.get('aed')
    assert state == {'feed': PLANET_REPLICA_URL, 'sequence': 42, 'update_timestamp': 1792227600}
    retained = await AEDService.get_by_id(92)
    assert retained is not None
    assert retained.tags == {'name': 'Retained'}
    added = await AEDService.get_by_id(93)
    assert added is not None
    assert added.version == 2
