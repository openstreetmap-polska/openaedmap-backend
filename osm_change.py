import xmltodict

from config import CHANGESET_ID_PLACEHOLDER, CREATED_BY


def _initialize_osm_change_structure() -> dict:
    return {
        'osmChange': {
            '@version': 0.6,
            '@generator': CREATED_BY,
            'modify': {
                'node': [],
            },
        }
    }


def update_node_tags_osm_change(node_xml: dict, update_tags: dict[str, str]) -> str:
    result = _initialize_osm_change_structure()

    node_xml.pop('@timestamp', None)
    node_xml.pop('@user', None)
    node_xml.pop('@uid', None)
    node_xml['@changeset'] = CHANGESET_ID_PLACEHOLDER

    tags = {tag['@k']: tag['@v'] for tag in node_xml['tag']}
    tags.update(update_tags)
    node_xml['tag'] = [{'@k': k, '@v': v} for k, v in tags.items()]

    result['osmChange']['modify']['node'].append(node_xml)

    return xmltodict.unparse(result)
