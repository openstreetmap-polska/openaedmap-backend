from xml.etree.ElementTree import fromstring

import aiohttp
import pytest
from pydantic import SecretStr
from yarl import URL

from tests.http import mock_http


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'tags',
    [
        '',
        (
            '<tag k="description" v="__CHANGESET_ID_PLACEHOLDER__ &amp; café&#10;second line"/>'
            '<tag k="image" v="https://old.example/image.jpg"/>'
        ),
    ],
    ids=('tagless-node', 'existing-tags'),
)
async def test_node_tag_updates_preserve_source_coordinates_versions_and_opaque_values(tags):
    import openstreetmap
    from config import OPENSTREETMAP_API_URL

    source = (
        '<osm><node id="9007199254740993" version="17" lat="52.1000000" lon="-0.0000001" '
        f'timestamp="2026-10-27T08:00:00Z" user="Mapper" uid="456">{tags}</node></osm>'
    )
    upload_url = f'{OPENSTREETMAP_API_URL}changeset/987654321/upload'
    async with mock_http() as upstream:
        upstream.get(f'{OPENSTREETMAP_API_URL}node/9007199254740993', body=source)
        upstream.put(f'{OPENSTREETMAP_API_URL}changeset/create', body='987654321\n')
        upstream.post(upload_url)
        upstream.put(f'{OPENSTREETMAP_API_URL}changeset/987654321/close')
        osm = openstreetmap.OpenStreetMap(SecretStr('example-token'))
        node = await osm.get_node_xml(9007199254740993)
        assert node is not None
        changeset = await osm.update_node_tags(node, {'image': 'https://new.example/a?b=1&c=2', 'image:license': 'CC0'})
        requests = dict(upstream.requests)

    assert changeset == '987654321'
    writes = [(method, url.path) for method, url in requests if method != 'GET']
    assert writes == [
        ('PUT', '/api/0.6/changeset/create'),
        ('POST', '/api/0.6/changeset/987654321/upload'),
        ('PUT', '/api/0.6/changeset/987654321/close'),
    ]
    for (method, _), calls in requests.items():
        assert len(calls) == 1
        if method != 'GET':
            assert calls[0].headers['Authorization'] == 'Bearer example-token'
    content = requests[('POST', URL(upload_url))][0].captured_body
    change = fromstring(content)  # noqa: S314 - XML produced by the application
    modified = change.find('modify/node')
    assert modified is not None
    assert modified.attrib == {
        'id': '9007199254740993',
        'version': '17',
        'lat': '52.1000000',
        'lon': '-0.0000001',
        'changeset': '987654321',
    }
    result = {tag.attrib['k']: tag.attrib['v'] for tag in modified.findall('tag')}
    expected = {'image': 'https://new.example/a?b=1&c=2', 'image:license': 'CC0'}
    if tags:
        expected['description'] = '__CHANGESET_ID_PLACEHOLDER__ & café\nsecond line'
    assert result == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [404, 410])
async def test_missing_remote_node_returns_none(status):
    import openstreetmap
    from config import OPENSTREETMAP_API_URL

    async with mock_http() as upstream:
        upstream.get(f'{OPENSTREETMAP_API_URL}node/123', status=status)
        assert await openstreetmap.OpenStreetMap(SecretStr('example-token')).get_node_xml(123) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['conflict', 'lost-response'])
async def test_failed_edit_closes_changeset_without_replaying_upload(failure):
    import openstreetmap
    from config import OPENSTREETMAP_API_URL

    node = fromstring('<node id="123" version="4" lat="51.2" lon="21.3"/>')  # noqa: S314 - literal fixture
    async with mock_http() as upstream:
        upstream.put(f'{OPENSTREETMAP_API_URL}changeset/create', body='901')
        if failure == 'lost-response':
            upstream.post(f'{OPENSTREETMAP_API_URL}changeset/901/upload', exception=True)
        else:
            upstream.post(f'{OPENSTREETMAP_API_URL}changeset/901/upload', status=409)
        upstream.put(f'{OPENSTREETMAP_API_URL}changeset/901/close')
        with pytest.raises(
            aiohttp.ClientConnectionError if failure == 'lost-response' else aiohttp.ClientResponseError
        ):
            await openstreetmap.OpenStreetMap(SecretStr('example-token')).update_node_tags(node, {'image': 'new'})
        requests = dict(upstream.requests)

    assert [url.path for _, url in requests] == [
        '/api/0.6/changeset/create',
        '/api/0.6/changeset/901/upload',
        '/api/0.6/changeset/901/close',
    ]
    assert all(len(calls) == 1 for calls in requests.values())
