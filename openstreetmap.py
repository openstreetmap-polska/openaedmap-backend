import logging
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring

from pydantic import SecretStr
from sentry_sdk import trace
from starlette import status

from config import CREATED_BY, DEFAULT_CHANGESET_TAGS, OPENSTREETMAP_API_URL
from utils import HTTP, retry_exponential


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
    async def get_node_xml(self, node_id: int) -> Element | None:
        r = await HTTP.get(f'{OPENSTREETMAP_API_URL}node/{node_id}')
        if r.status_code in (status.HTTP_404_NOT_FOUND, status.HTTP_410_GONE):
            return None
        r.raise_for_status()
        root = fromstring(r.content)  # noqa: S314 - OSM API XML; ElementTree does not resolve external entities
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

        r = await HTTP.put(
            f'{OPENSTREETMAP_API_URL}changeset/create',
            headers=headers,
            content=tostring(changeset, encoding='utf-8', xml_declaration=True),
        )
        r.raise_for_status()
        changeset_id = r.text.strip()

        node.set('changeset', changeset_id)
        logging.info('Uploading changeset %s', changeset_id)
        logging.info('https://www.openstreetmap.org/changeset/%s', changeset_id)

        try:
            r = await HTTP.post(
                f'{OPENSTREETMAP_API_URL}changeset/{changeset_id}/upload',
                headers=headers,
                content=tostring(change, encoding='utf-8', xml_declaration=True),
            )
            r.raise_for_status()
        finally:
            r = await HTTP.put(
                f'{OPENSTREETMAP_API_URL}changeset/{changeset_id}/close',
                headers=headers,
            )
            r.raise_for_status()

        return changeset_id
