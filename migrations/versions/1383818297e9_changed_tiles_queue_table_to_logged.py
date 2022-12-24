"""changed tiles queue table to logged

Revision ID: 1383818297e9
Revises: bfc8c7757aa3
Create Date: 2022-12-22 23:52:14.756125

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '1383818297e9'
down_revision = 'bfc8c7757aa3'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE tiles_queue SET LOGGED;")


def downgrade():
    op.execute("ALTER TABLE tiles_queue SET UNLOGGED;")
