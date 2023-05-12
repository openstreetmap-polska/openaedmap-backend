from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.models.countries import Countries
from backend.models.osm_nodes import OsmNodes
from backend.schemas.osm_nodes import OsmNodesCreate


def create_osm_node(db: Session, osm_node: OsmNodesCreate):
    osm_node = OsmNodes(**osm_node.dict())
    if (
        matching_country := db.query(Countries)
        .filter(func.ST_Contains(Countries.geometry, osm_node.geometry))
        .first()
    ):
        osm_node.country_code = matching_country.country_code
    else:
        osm_node.country_code = None
    db.add(osm_node)
    db.commit()
    db.refresh(osm_node)

    return osm_node
