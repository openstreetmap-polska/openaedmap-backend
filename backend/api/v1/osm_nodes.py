from fastapi import APIRouter, Depends, Query, Response, Path
from geoalchemy2 import func
from sqlalchemy.orm import Session

from datetime import datetime

from backend.crud.osm_nodes_views import create_osm_node_view
from backend.models.osm_nodes import OsmNodes
from backend.schemas.osm_node_views import OsmNodesViewsCreate
from backend.api.deps import get_db


router = APIRouter()


def _parse_image_tags(tags: dict) -> list[dict]:
    images = []

    def check_image_url(url: str) -> bool:
        return url.startswith("http://") or url.startswith("https://")

    for image_tag in filter(lambda tag: tag.startswith("image"), tags.keys()):
        for image_url in tags[image_tag].split(";"):
            if check_image_url(image_url):
                images.append(dict(url=image_url))
    if "wikimedia_commons" in tags:
        wikimedia_commons_tag = tags["wikimedia_commons"]
        if wikimedia_commons_tag.startswith("File:"):
            wikimedia_commons_url = (
                "https://commons.wikimedia.org/wiki/" + wikimedia_commons_tag
            )
            if check_image_url(wikimedia_commons_url):
                images.append(dict(url=wikimedia_commons_url))
    return images


@router.get("/node/{node_id}")
async def osm_node(
    node_id: int = Path(description="Id of OSM Node", gt=0),
    db: Session = Depends(get_db),
) -> Response | dict:
    request_receive_dt = datetime.utcnow()

    node_data: OsmNodes | None = db.get(entity=OsmNodes, ident=node_id)

    if node_data is None:
        return Response(status_code=404)
    else:
        create_osm_node_view(
            db, OsmNodesViewsCreate(node_id=node_id, seen_at=request_receive_dt)
        )
        response_data = {
            "version": "0.6",
            "copyright": "OpenStreetMap and contributors",
            "attribution": "http://www.openstreetmap.org/copyright",
            "license": "http://opendatacommons.org/licenses/odbl/1-0/",
            "elements": [
                {
                    "type": "node",
                    "id": node_data.node_id,
                    "lat": db.scalar(func.ST_Y(node_data.geometry)),
                    "lon": db.scalar(func.ST_X(node_data.geometry)),
                    "timestamp": node_data.version_last_ts,
                    "version": node_data.version,
                    "changeset": None,
                    "user": None,
                    "uid": None,
                    "tags": node_data.tags,
                    "images": _parse_image_tags(node_data.tags),
                }
            ],
        }
        return response_data
