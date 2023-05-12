"""fixed mvt_clustered function

Revision ID: c738991beb3c
Revises: 656be64c79a3
Create Date: 2022-12-17 19:00:33.101828

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c738991beb3c"
down_revision = "656be64c79a3"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
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
                    AND NOT (
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


def downgrade():
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
