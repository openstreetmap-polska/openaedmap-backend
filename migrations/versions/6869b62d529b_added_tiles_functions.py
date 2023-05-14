"""added tiles functions

Revision ID: 6869b62d529b
Revises: e9c0b3b9c2cb
Create Date: 2022-12-12 20:49:40.290223

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6869b62d529b"
down_revision = "e9c0b3b9c2cb"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE OR REPLACE FUNCTION abbreviated (IN number bigint) RETURNS text
        AS  $$
            SELECT CASE
              WHEN number > 999999 THEN ROUND(number/1000000.0, 1)::text || 'm'
              WHEN number > 999 THEN ROUND(number/1000.0, 1)::text || 'k'
              ELSE number::text
            END
        $$ LANGUAGE SQL IMMUTABLE PARALLEL SAFE ;
    """
    )
    op.execute(
        """
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
    """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mvt_clustered (IN bbox geometry, IN cluster_range double precision) RETURNS bytea
        AS  $$
        
            WITH
                nodes as (
                    SELECT
                        ST_AsMVTGeom(ST_Transform(geometry, 3857), bbox) as geom,
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
    """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mvt_countries (IN bbox geometry) RETURNS bytea
        AS  $$
        
            WITH
                points as (
                    SELECT
                        ST_AsMVTGeom(ST_Transform(label_point, 3857), bbox),
                        country_code,
                        feature_count as point_count,
                        abbreviated(feature_count) as point_count_abbreviated
                    FROM countries
                    WHERE ST_Intersects(geometry, ST_Transform(bbox, 4326))
                    AND (
                        label_point && ST_MakeEnvelope(-180, -90, 180, -85, 4326)
                        OR
                        label_point && ST_MakeEnvelope(-180,  85, 180,  90, 4326)
                    )
                ),
                areas as (
                    SELECT
                        ST_AsMVTGeom(ST_Transform(ST_Intersection(geometry, ST_MakeEnvelope(-180, -85, 180, 85, 4326)), 3857), bbox),
                        country_code,
                        country_names ->> 'default' as country_name,
                        feature_count as point_count,
                        abbreviated(feature_count) as point_count_abbreviated
                    FROM countries
                    WHERE ST_Intersects(geometry, ST_Transform(bbox, 4326))
                ),
                layers as (
                    SELECT ST_AsMVT(points.*, 'defibrillators') as data
                    FROM points
                    UNION ALL
                    SELECT ST_AsMVT(areas.*, 'countries')
                    FROM areas
                )
                SELECT string_agg(data, null)
                FROM layers
        
        $$ LANGUAGE SQL STABLE ;
    """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mvt (IN z int, IN x int, IN y int) RETURNS bytea
        AS $$
            DECLARE
                bbox geometry;
                base double precision := 150;
            BEGIN
                bbox := ST_TileEnvelope(z, x, y);
        
                if z <= 5 then
                    RETURN mvt_countries(bbox);
                elsif z = 6 then
                    RETURN mvt_clustered(bbox, base * 2 ^ 7);
                elsif z = 7 then
                    RETURN mvt_clustered(bbox, base * 2 ^ 6);
                elsif z = 8 then
                    RETURN mvt_clustered(bbox, base * 2 ^ 5);
                elsif z = 9 then
                    RETURN mvt_clustered(bbox, base * 2 ^ 4);
                elsif z = 10 then
                    RETURN mvt_clustered(bbox, base * 2 ^ 3);
                elsif z = 11 then
                    RETURN mvt_clustered(bbox, base * 2 ^ 2);
                elsif z = 12 then
                    RETURN mvt_clustered(bbox, base * 2 ^ 1);
                elsif z >= 13 and z <= 23 then
                    RETURN mvt_unclustered(bbox);
                else
                    raise notice 'Zoom % outside valid range.', z;
                    RETURN null;
                end if;
            END;
        $$ LANGUAGE plpgsql STABLE ;
    """
    )


def downgrade():
    op.execute("DROP FUNCTION mvt;")
    op.execute("DROP FUNCTION mvt_countries;")
    op.execute("DROP FUNCTION mvt_clustered;")
    op.execute("DROP FUNCTION mvt_unclustered;")
    op.execute("DROP FUNCTION abbreviated;")
