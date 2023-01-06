"""refactor update process

Revision ID: 0efac400a003
Revises: a3688824e614
Create Date: 2023-01-06 19:31:08.414264

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0efac400a003'
down_revision = 'a3688824e614'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('temp_node_changes',
        sa.Column('change_type', sa.String(), nullable=False),
        sa.Column('node_id', sa.BigInteger(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('uid', sa.Integer(), nullable=True),
        sa.Column('user', sa.String(), nullable=True),
        sa.Column('changeset', sa.Integer(), nullable=True),
        sa.Column('latitude', postgresql.DOUBLE_PRECISION(), nullable=True),
        sa.Column('longitude', postgresql.DOUBLE_PRECISION(), nullable=True),
        sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('version_timestamp', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('node_id', 'version'),
        prefixes=['UNLOGGED']
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION get_tile_names (IN min_zoom int, IN max_zoom int) RETURNS TABLE (
            z smallint,
            x int,
            y int
        )
        AS  $$
            WITH
            zoom_levels(zoom) as (
                SELECT zoom FROM generate_series(min_zoom, max_zoom) as zoom
            )
            SELECT zoom as z, x, y
            FROM zoom_levels, generate_series(0, (2^zoom-1)::int) as x, generate_series(0, (2^zoom-1)::int) as y;
        $$ LANGUAGE SQL IMMUTABLE PARALLEL SAFE ;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION get_tile_names_for_non_empty() RETURNS TABLE (
            z smallint,
            x int,
            y int
        )
        AS  $$
            WITH
            tiles_with_nodes(z, x, y) as (
                SELECT DISTINCT z, lon2tile(ST_X(geometry), z) as x, lat2tile(ST_Y(geometry), z) as y
                FROM osm_nodes n
                CROSS JOIN generate_series(6, 13) as z
            ),
            low_zoom_tile_names(z, x, y, geometry) as (
                SELECT z, x, y, ST_Transform(ST_TileEnvelope(z, x, y), 4326) as geometry
                FROM get_tile_names(0, 5)
            ),
            tiles_with_countries(z, x, y) as (
                SELECT DISTINCT z, x, y
                FROM low_zoom_tile_names n
                JOIN countries c ON ST_Intersects(n.geometry, c.geometry)
            )
            SELECT z, x, y FROM tiles_with_countries
            UNION ALL
            SELECT z, x, y FROM tiles_with_nodes
            ;
        $$ LANGUAGE SQL IMMUTABLE PARALLEL SAFE ;
    """)
    op.execute("DROP PROCEDURE IF EXISTS queue_reload_of_all_tiles(min_zoom int, max_zoom int)")
    op.execute("""
        CREATE OR REPLACE PROCEDURE queue_reload_of_all_tiles()
        LANGUAGE SQL
        BEGIN ATOMIC
            INSERT INTO tiles_queue(z, x, y, inserted_at)
                SELECT z, x, y, CURRENT_TIMESTAMP - '1 day'::interval as ts
                FROM get_tile_names_for_non_empty()
            ON CONFLICT DO NOTHING
            ;
        END;
    """)
    op.execute("TRUNCATE TABLE tiles")
    op.execute("TRUNCATE TABLE tiles_queue")


def downgrade():
    op.drop_table('temp_node_changes')
    op.execute("""DROP FUNCTION IF EXISTS queue_reload_of_all_tiles()""")
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
    op.execute("DROP FUNCTION IF EXISTS get_tile_names (IN min_zoom smallint, IN max_zoom smallint)")
    op.execute("DROP FUNCTION IF EXISTS get_tile_names_for_non_empty()")
