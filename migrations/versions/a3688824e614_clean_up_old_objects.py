"""clean up old objects

Revision ID: a3688824e614
Revises: 6dcbb5d299d3
Create Date: 2023-01-05 00:44:40.913211

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3688824e614'
down_revision = '6dcbb5d299d3'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP PROCEDURE IF EXISTS queue_reload_of_all_tiles();")


def downgrade():
    pass
