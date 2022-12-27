CREATE OR REPLACE FUNCTION mvt_unclustered (IN bbox geometry) RETURNS bytea
AS  $$

    WITH
        nodes as (
            SELECT
                ST_AsMVTGeom(ST_Transform(geometry, 3857), bbox) as geom,
                node_id,
                tags ->> 'access' as access
            FROM osm_nodes
            WHERE ST_Intersects(geometry, ST_Transform(bbox, 4326))
        )
        SELECT ST_AsMVT(nodes.*, 'defibrillators')
        FROM nodes

$$ LANGUAGE SQL STABLE ;
