"""Broaden country geometry type

Revision ID: 2f0c5a9d3f13
Revises: 612257f82942
Create Date: 2026-04-22 09:55:00.000000+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2f0c5a9d3f13'
down_revision: str | None = '612257f82942'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        '''
        ALTER TABLE "country"
        ALTER COLUMN "geometry"
        TYPE geometry(Geometry, 4326)
        USING "geometry"::geometry(Geometry, 4326)
        '''
    )


def downgrade() -> None:
    pass
