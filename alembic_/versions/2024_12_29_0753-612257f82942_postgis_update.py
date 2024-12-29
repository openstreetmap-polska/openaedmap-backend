"""PostGIS update

Revision ID: 612257f82942
Revises: 9f60c90e8a21
Create Date: 2024-12-29 07:53:08.574574+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '612257f82942'
down_revision: str | None = '9f60c90e8a21'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('SELECT postgis_extensions_upgrade();')


def downgrade() -> None:
    pass
