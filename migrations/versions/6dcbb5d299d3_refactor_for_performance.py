"""refactor for performance

Revision ID: 6dcbb5d299d3
Revises: cfe13af7230d
Create Date: 2023-01-04 00:27:49.356723

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '6dcbb5d299d3'
down_revision = 'cfe13af7230d'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE TABLE countries_queue (countries_queue_id bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY, country_code text NOT NULL)")
    op.execute("""
        CREATE OR REPLACE FUNCTION get_tile_names (IN zoom smallint) RETURNS TABLE (
            z smallint,
            x int,
            y int
        )
        AS  $$
            SELECT zoom as z, x, y FROM generate_series(0, (2^zoom-1)::int) as x, generate_series(0, (2^zoom-1)::int) as y;
        $$ LANGUAGE SQL IMMUTABLE PARALLEL SAFE ;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION lon2tile(lon DOUBLE PRECISION, zoom INTEGER) RETURNS INTEGER AS $$
            SELECT FLOOR( (lon + 180) / 360 * (1 << zoom) )::INTEGER;
        $$ LANGUAGE SQL IMMUTABLE PARALLEL SAFE;

        CREATE OR REPLACE FUNCTION lat2tile(lat double precision, zoom integer) RETURNS integer AS $$
            SELECT floor( (1.0 - ln(tan(radians(lat)) + 1.0 / cos(radians(lat))) / pi()) / 2.0 * (1 << zoom) )::integer;
        $$ LANGUAGE SQL IMMUTABLE PARALLEL SAFE;
        
        CREATE OR REPLACE FUNCTION tile2lat(y integer, zoom integer) RETURNS double precision AS $$
        DECLARE
            n float;
            sinh float;
            E float = 2.7182818284;
        BEGIN
            -- This code returns the coordinate of the _upper left_ (northwest-most)-point of the tile.
            n = pi() - (2.0 * pi() * y) / power(2.0, zoom);
            sinh = (1 - power(E, -2*n)) / (2 * power(E, -n));
            return degrees(atan(sinh));
        END;
        $$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;
        
        CREATE OR REPLACE FUNCTION tile2lon(x integer, zoom integer) RETURNS double precision AS $$
            -- This code returns the coordinate of the _upper left_ (northwest-most)-point of the tile.
            SELECT CAST(x * 1.0 / (1 << zoom) * 360.0 - 180.0 AS double precision);
        $$ LANGUAGE SQL IMMUTABLE PARALLEL SAFE;
    """)
    op.execute("""
        CREATE OR REPLACE PROCEDURE queue_reload_of_all_tiles(min_zoom int, max_zoom int)
        LANGUAGE SQL
        SET work_mem TO '512MB'
        BEGIN ATOMIC
            WITH
            tiles_to_refresh as (
                SELECT DISTINCT z, lon2tile(ST_X(geometry), z) as x, lat2tile(ST_Y(geometry), z) as y
                FROM osm_nodes n
                CROSS JOIN generate_series(min_zoom, max_zoom) as z
            ),
            tiles_to_refresh_with_offset as (
                SELECT z, x, y, NTILE(10) OVER(PARTITION BY z ORDER BY x, y) as bin
                FROM tiles_to_refresh
            )
            INSERT INTO tiles_queue
                SELECT z, x, y, (CURRENT_TIMESTAMP + (bin || ' minute')::interval) as ts
                FROM tiles_to_refresh_with_offset
            ON CONFLICT DO NOTHING
            ;
        END;
    """)
    op.execute("DROP INDEX idx_tiles_bbox")
    op.execute("TRUNCATE TABLE tiles")


def downgrade():
    op.execute("DROP TABLE countries_queue")
    op.execute("DROP PROCEDURE queue_reload_of_all_tiles")
    op.execute("DROP FUNCTION get_tile_names")
    op.execute("DROP FUNCTION lon2tile")
    op.execute("DROP FUNCTION lat2tile")
    op.execute("DROP FUNCTION tile2lat")
    op.execute("DROP FUNCTION tile2lon")
    op.execute("CREATE INDEX idx_tiles_bbox ON tiles USING GIST (ST_TileEnvelope(z, x, y))")
