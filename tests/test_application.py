import httpx
import pytest

from tests.data import seed_countries


@pytest.mark.asyncio
async def test_application_routes_serve_json_outside_the_source_directory(database, monkeypatch, tmp_path):
    await seed_countries([
        ('JP', {'default': 'Japan', 'JA': '日本'}, 'POLYGON((0 0,4 0,4 4,0 4,0 0))', 2, 2),
    ])
    monkeypatch.chdir(tmp_path)
    from main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.get('/api/v1/countries/names?language=JA&profile=1')

    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/json; charset=utf-8'
    assert response.json()[0]['country_names'] == {'JA': '日本'}
    assert response.headers['cache-control'] == 'public, max-age=3600, stale-while-revalidate=604800'
