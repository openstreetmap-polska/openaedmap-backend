"""added tiles cache table

Revision ID: e9c0b3b9c2cb
Revises: 283357f19e40
Create Date: 2022-12-03 22:12:15.805185

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "e9c0b3b9c2cb"
down_revision = "283357f19e40"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "tiles",
        sa.Column("z", sa.SMALLINT(), nullable=False),
        sa.Column("x", sa.Integer(), nullable=False),
        sa.Column("y", sa.Integer(), nullable=False),
        sa.Column("mvt", postgresql.BYTEA(), nullable=False),
        sa.PrimaryKeyConstraint("z", "x", "y"),
    )
    op.execute(
        "CREATE INDEX idx_tiles_bbox ON tiles USING GIST (ST_TileEnvelope(z, x, y))"
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("tiles")
    # ### end Alembic commands ###
