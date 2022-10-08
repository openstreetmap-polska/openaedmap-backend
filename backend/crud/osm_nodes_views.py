from sqlalchemy.orm import Session

from backend.models.osm_nodes_views import OsmNodesViews
from backend.schemas.osm_node_views import OsmNodesViewsCreate


def create_osm_node_view(db: Session, osm_node_view: OsmNodesViewsCreate):
    osm_node_view = OsmNodesViews(**osm_node_view.dict())
    db.add(osm_node_view)
    db.commit()
    db.refresh(osm_node_view)

    return osm_node_view
