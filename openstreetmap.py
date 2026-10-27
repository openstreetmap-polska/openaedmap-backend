import logging
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring

from pydantic import SecretStr
from sentry_sdk import trace
from starlette import status

import http_client
from config import CREATED_BY, DEFAULT_CHANGESET_TAGS, OPENSTREETMAP_API_URL
from utils import retry_exponential


def osm_user_has_active_block(user: dict) -> bool:
    return user['blocks']['received']['active'] > 0


class OpenStreetMap:
    def __init__(self, access_token: SecretStr):
        self.access_token: SecretStr = access_token

    @retry_exponential(10)
    @trace
    async def get_authorized_user(self) -> dict | None:
        async with http_client.client().get(
            f'{OPENSTREETMAP_API_URL}user/details.json',
            headers={'Authorization': f'Bearer {self.access_token.get_secret_value()}'},
        ) as response:
            if response.status == status.HTTP_401_UNAUTHORIZED:
                return None
            response.raise_for_status()
            return (await response.json(content_type=None))['user']

    @retry_exponential(10)
    @trace
    async def get_node_xml(self, node_id: int) -> Element | None:
        async with http_client.client().get(f'{OPENSTREETMAP_API_URL}node/{node_id}') as response:
            if response.status in (status.HTTP_404_NOT_FOUND, status.HTTP_410_GONE):
                return None
            response.raise_for_status()
            content = await response.read()
        root = fromstring(content)  # noqa: S314 - OSM API XML; ElementTree does not resolve external entities
        node = root.find('node')
        if node is None:
            raise ValueError('OSM response has no node')
        return node

    @trace
    async def update_node_tags(self, node: Element, update_tags: dict[str, str]) -> str:
        tags = {tag.attrib['k']: tag.attrib['v'] for tag in node.findall('tag')}
        tags.update(update_tags)
        for tag in node.findall('tag'):
            node.remove(tag)
        for key, value in tags.items():
            SubElement(node, 'tag', k=key, v=value)
        for attribute in ('timestamp', 'user', 'uid'):
            node.attrib.pop(attribute, None)
        change = Element('osmChange', version='0.6', generator=CREATED_BY)
        SubElement(change, 'modify').append(node)

        changeset = Element('osm')
        metadata = SubElement(changeset, 'changeset')
        for key, value in DEFAULT_CHANGESET_TAGS.items():
            SubElement(metadata, 'tag', k=key, v=value)
        headers = {
            'Authorization': f'Bearer {self.access_token.get_secret_value()}',
            'Content-Type': 'text/xml; charset=utf-8',
        }

        async with http_client.client().put(
            f'{OPENSTREETMAP_API_URL}changeset/create',
            headers=headers,
            data=tostring(changeset, encoding='utf-8', xml_declaration=True),
        ) as response:
            response.raise_for_status()
            changeset_id = (await response.text()).strip()

        node.set('changeset', changeset_id)
        logging.info('Uploading changeset %s', changeset_id)
        logging.info('https://www.openstreetmap.org/changeset/%s', changeset_id)

        try:
            async with http_client.client().post(
                f'{OPENSTREETMAP_API_URL}changeset/{changeset_id}/upload',
                headers=headers,
                data=tostring(change, encoding='utf-8', xml_declaration=True),
            ) as response:
                response.raise_for_status()
        finally:
            async with http_client.client().put(
                f'{OPENSTREETMAP_API_URL}changeset/{changeset_id}/close',
                headers=headers,
            ) as response:
                response.raise_for_status()

        return changeset_id
