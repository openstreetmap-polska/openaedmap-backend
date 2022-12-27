CREATE OR REPLACE FUNCTION mvt_clustered (IN bbox geometry, IN cluster_range double precision) RETURNS bytea
AS  $$

    WITH
        nodes as (
            SELECT
                ST_Transform(geometry, 3857) as geom,
                node_id,
                tags ->> 'access' as access
            FROM osm_nodes
            WHERE ST_Intersects(geometry, ST_Transform(bbox, 4326))
        ),
        assigned_cluster_id as (
            SELECT
                ST_ClusterDBSCAN(geom, eps := cluster_range, minpoints := 2) over () AS cluster_id,
                nodes.*
            FROM nodes
        ),
        clustered as (
            SELECT
                ST_AsMVTGeom(ST_GeometricMedian(ST_Union(geom)), bbox),
                COUNT(*) as point_count,
                abbreviated(COUNT(*)) as point_count_abbreviated,
                null::bigint as node_id,
                null::text as access
            FROM assigned_cluster_id
            WHERE cluster_id IS NOT NULL
            GROUP BY cluster_id
            UNION ALL
            SELECT
                ST_AsMVTGeom(geom, bbox),
                null,
                null,
                node_id,
                access
            FROM assigned_cluster_id
            WHERE cluster_id IS NULL
        )
        SELECT ST_AsMVT(clustered.*, 'defibrillators')
        FROM clustered

$$ LANGUAGE SQL STABLE ;
