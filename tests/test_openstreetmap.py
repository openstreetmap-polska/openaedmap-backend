from xml.etree.ElementTree import fromstring

import httpx
import pytest
from pydantic import SecretStr


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
async def test_node_tag_updates_preserve_source_coordinates_versions_and_opaque_values(monkeypatch, tags):
    import openstreetmap

    source = (
        '<osm><node id="9007199254740993" version="17" lat="52.1000000" lon="-0.0000001" '
        f'timestamp="2026-10-27T08:00:00Z" user="Mapper" uid="456">{tags}</node></osm>'
    )
    writes = []

    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, text=source)
        assert request.headers['authorization'] == 'Bearer example-token'
        writes.append(request)
        return httpx.Response(200, text='987654321\n' if request.url.path.endswith('/create') else '')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        monkeypatch.setattr(openstreetmap, 'HTTP', upstream)
        osm = openstreetmap.OpenStreetMap(SecretStr('example-token'))
        node = await osm.get_node_xml(9007199254740993)
        assert node is not None
        changeset = await osm.update_node_tags(node, {'image': 'https://new.example/a?b=1&c=2', 'image:license': 'CC0'})

    assert changeset == '987654321'
    assert [(r.method, r.url.path) for r in writes] == [
        ('PUT', '/api/0.6/changeset/create'),
        ('POST', '/api/0.6/changeset/987654321/upload'),
        ('PUT', '/api/0.6/changeset/987654321/close'),
    ]
    change = fromstring(writes[1].content)  # noqa: S314 - XML produced by the application
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
async def test_missing_remote_node_returns_none(monkeypatch, status):
    import openstreetmap

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as upstream:
        monkeypatch.setattr(openstreetmap, 'HTTP', upstream)
        assert await openstreetmap.OpenStreetMap(SecretStr('example-token')).get_node_xml(123) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['conflict', 'lost-response'])
async def test_failed_edit_closes_changeset_without_replaying_upload(monkeypatch, failure):
    import openstreetmap

    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith('/create'):
            return httpx.Response(200, text='901')
        if request.url.path.endswith('/upload'):
            if failure == 'lost-response':
                raise httpx.ReadTimeout('Response was lost')
            return httpx.Response(409, text='Version conflict')
        return httpx.Response(200)

    node = fromstring('<node id="123" version="4" lat="51.2" lon="21.3"/>')  # noqa: S314 - literal fixture
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        monkeypatch.setattr(openstreetmap, 'HTTP', upstream)
        with pytest.raises(httpx.ReadTimeout if failure == 'lost-response' else httpx.HTTPStatusError):
            await openstreetmap.OpenStreetMap(SecretStr('example-token')).update_node_tags(node, {'image': 'new'})

    assert requests == [
        '/api/0.6/changeset/create',
        '/api/0.6/changeset/901/upload',
        '/api/0.6/changeset/901/close',
    ]
