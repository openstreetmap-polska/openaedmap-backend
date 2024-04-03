import logging

import xmltodict
from authlib.integrations.httpx_client import OAuth2Auth
from sentry_sdk import trace

from config import CHANGESET_ID_PLACEHOLDER, DEFAULT_CHANGESET_TAGS, OPENSTREETMAP_API_URL
from utils import get_http_client, retry_exponential
from xmltodict_postprocessor import xmltodict_postprocessor


def osm_user_has_active_block(user: dict) -> bool:
    return user['blocks']['received']['active'] > 0


class OpenStreetMap:
    def __init__(self, oauth2_credentials: dict):
        self._http = get_http_client(OPENSTREETMAP_API_URL, auth=OAuth2Auth(oauth2_credentials))

    @retry_exponential(10)
    @trace
    async def get_authorized_user(self) -> dict | None:
        r = await self._http.get('/user/details.json')

        if r.status_code == 401:
            return None

        r.raise_for_status()

        return r.json()['user']

    @retry_exponential(10)
    @trace
    async def get_node_xml(self, node_id: int) -> dict | None:
        r = await self._http.get(f'/node/{node_id}')

        if r.status_code in (404, 410):
            return None

        r.raise_for_status()

        return xmltodict.parse(
            r.text,
            postprocessor=xmltodict_postprocessor,
            force_list=('tag',),
        )['osm']['node']

    @trace
    async def upload_osm_change(self, osm_change: str) -> str:
        changeset = xmltodict.unparse(
            {
                'osm': {
                    'changeset': {
                        'tag': [
                            {
                                '@k': k,
                                '@v': v,
                            }
                            for k, v in DEFAULT_CHANGESET_TAGS.items()
                        ]
                    }
                }
            }
        )

        r = await self._http.put(
            '/changeset/create',
            content=changeset,
            headers={'Content-Type': 'text/xml; charset=utf-8'},
            follow_redirects=False,
        )
        r.raise_for_status()

        changeset_id = r.text
        osm_change = osm_change.replace(CHANGESET_ID_PLACEHOLDER, changeset_id)
        logging.info('Uploading changeset %s', changeset_id)
        logging.info('https://www.openstreetmap.org/changeset/%s', changeset_id)

        upload_resp = await self._http.post(
            f'/changeset/{changeset_id}/upload',
            content=osm_change,
            headers={'Content-Type': 'text/xml; charset=utf-8'},
        )

        r = await self._http.put(f'/changeset/{changeset_id}/close')
        r.raise_for_status()

        if not upload_resp.is_success:
            raise Exception(f'Upload failed ({upload_resp.status_code}): {upload_resp.text}')

        return changeset_id
