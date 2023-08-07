from fastapi import APIRouter, HTTPException

from states.aed_state import AEDStateDep

router = APIRouter()


@router.get('/node/{node_id}')
async def get_node(node_id: str, aed_state: AEDStateDep):
    aed = await aed_state.get_aed_by_id(node_id)

    if aed is None:
        raise HTTPException(404, f'Node {node_id!r} not found')

    return {
        'version': 0.6,
        'copyright': 'OpenStreetMap and contributors',
        'attribution': 'http://www.openstreetmap.org/copyright',
        'license': 'http://opendatacommons.org/licenses/odbl/1-0/',
        'elements': [{
            'type': 'node',
            'id': int(aed.id),
            'lat': aed.position.lat,
            'lon': aed.position.lon,
            'tags': aed.tags,
            'version': 0,
        }]
    }
