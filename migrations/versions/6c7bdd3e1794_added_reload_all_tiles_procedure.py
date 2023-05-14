"""added_reload_all_tiles_procedure

Revision ID: 6c7bdd3e1794
Revises: 9e2034191715
Create Date: 2022-12-25 23:26:27.284214

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "6c7bdd3e1794"
down_revision = "9e2034191715"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE OR REPLACE PROCEDURE queue_reload_of_all_tiles()
        LANGUAGE SQL
        SET work_mem TO '512MB'
        BEGIN ATOMIC
            WITH
            tiles_to_refresh as (
                SELECT z, x, y, NTILE(1000) OVER(PARTITION BY z ORDER BY x, y) as bin
                FROM tiles
            )
            INSERT INTO tiles_queue
                SELECT z, x, y, (CURRENT_TIMESTAMP + (bin || ' minute')::interval) as ts
                FROM tiles_to_refresh
            ON CONFLICT DO NOTHING
            ;
        END;
    """
    )


def downgrade():
    op.execute("DROP PROCEDURE queue_reload_of_all_tiles;")
