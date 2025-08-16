import logging

import xmltodict
from pydantic import SecretStr
from sentry_sdk import trace
from starlette import status

from config import CHANGESET_ID_PLACEHOLDER, DEFAULT_CHANGESET_TAGS, OPENSTREETMAP_API_URL
from utils import HTTP, retry_exponential
from xmltodict_postprocessor import xmltodict_postprocessor


def osm_user_has_active_block(user: dict) -> bool:
    return user['blocks']['received']['active'] > 0


class OpenStreetMap:
    def __init__(self, access_token: SecretStr):
        self.access_token: SecretStr = access_token

    @retry_exponential(10)
    @trace
    async def get_authorized_user(self) -> dict | None:
        r = await HTTP.get(
            f'{OPENSTREETMAP_API_URL}user/details.json',
            headers={'Authorization': f'Bearer {self.access_token.get_secret_value()}'},
        )
        if r.status_code == status.HTTP_401_UNAUTHORIZED:
            return None
        r.raise_for_status()
        return r.json()['user']

    @retry_exponential(10)
    @trace
    async def get_node_xml(self, node_id: int) -> dict | None:
        r = await HTTP.get(f'{OPENSTREETMAP_API_URL}node/{node_id}')
        if r.status_code in (status.HTTP_404_NOT_FOUND, status.HTTP_410_GONE):
            return None
        r.raise_for_status()
        return xmltodict.parse(
            r.content,
            postprocessor=xmltodict_postprocessor,
            force_list=('tag',),
        )['osm']['node']

    @trace
    async def upload_osm_change(self, osm_change: str) -> str:
        changeset = xmltodict.unparse({
            'osm': {'changeset': {'tag': [{'@k': k, '@v': v} for k, v in DEFAULT_CHANGESET_TAGS.items()]}}
        })

        r = await HTTP.put(
            f'{OPENSTREETMAP_API_URL}changeset/create',
            headers={
                'Authorization': f'Bearer {self.access_token.get_secret_value()}',
                'Content-Type': 'text/xml; charset=utf-8',
            },
            content=changeset,
        )
        r.raise_for_status()
        changeset_id = r.text

        osm_change = osm_change.replace(CHANGESET_ID_PLACEHOLDER, changeset_id)
        logging.info('Uploading changeset %s', changeset_id)
        logging.info('https://www.openstreetmap.org/changeset/%s', changeset_id)

        r = await HTTP.post(
            f'{OPENSTREETMAP_API_URL}changeset/{changeset_id}/upload',
            headers={
                'Authorization': f'Bearer {self.access_token.get_secret_value()}',
                'Content-Type': 'text/xml; charset=utf-8',
            },
            content=osm_change,
        )
        r.raise_for_status()

        r = await HTTP.put(
            f'{OPENSTREETMAP_API_URL}changeset/{changeset_id}/close',
            headers={'Authorization': f'Bearer {self.access_token.get_secret_value()}'},
        )
        r.raise_for_status()

        return changeset_id
