"""adjusted mvt and mvt_countries functions

Revision ID: bfc8c7757aa3
Revises: c738991beb3c
Create Date: 2022-12-17 23:39:22.379598

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "bfc8c7757aa3"
down_revision = "c738991beb3c"
branch_labels = None
depends_on = None


def upgrade():
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
                    RETURN mvt_clustered(bbox, 1000 + base * 2 ^ 6);
                elsif z = 7 then
                    RETURN mvt_clustered(bbox, base * 2 ^ 6);
                elsif z = 8 then
                    RETURN mvt_clustered(bbox, base * 2 ^ 5);
                elsif z = 9 then
                    RETURN mvt_clustered(bbox, base * 2 ^ 4);
                elsif z = 10 then
                    RETURN mvt_clustered(bbox, base * 2 ^ 3);
                elsif z = 11 then
                    RETURN mvt_clustered(bbox, 150 + base * 2 ^ 2);
                elsif z = 12 then
                    RETURN mvt_clustered(bbox, 100 + base * 2 ^ 1);
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
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mvt_countries (IN bbox geometry) RETURNS bytea
        AS  $$

            WITH
                points as (
                    SELECT
                        ST_AsMVTGeom(ST_Transform(label_point, 3857), bbox),
                        country_code,
                        country_names ->> 'default' as country_name,
                        feature_count as point_count,
                        abbreviated(feature_count) as point_count_abbreviated
                    FROM countries
                    WHERE ST_Intersects(ST_Transform(label_point, 3857), bbox)
                    AND NOT (
                        label_point && ST_MakeEnvelope(-180, -90, 180, -85, 4326)
                        OR
                        label_point && ST_MakeEnvelope(-180,  85, 180,  90, 4326)
                    )
                ),
                areas as (
                    SELECT
                        ST_AsMVTGeom(ST_SimplifyPreserveTopology(ST_Transform(ST_Intersection(geometry, ST_MakeEnvelope(-180, -85, 180, 85, 4326)), 3857), 5000), bbox),
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
