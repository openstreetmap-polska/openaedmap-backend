from datetime import timedelta

from authlib.integrations.httpx_client import OAuth2Auth

from utils import get_http_client, retry_exponential


def osm_user_has_active_block(user: dict) -> bool:
    return user['blocks']['received']['active'] > 0


class OpenStreetMap:
    def __init__(self, oauth2_credentials: dict):
        self._http = get_http_client('https://api.openstreetmap.org/api/0.6/', auth=OAuth2Auth(oauth2_credentials))

    @retry_exponential(timedelta(seconds=10))
    async def get_authorized_user(self) -> dict | None:
        r = await self._http.get('/user/details.json')

        if r.status_code == 401:
            return None

        r.raise_for_status()

        return r.json()['user']
