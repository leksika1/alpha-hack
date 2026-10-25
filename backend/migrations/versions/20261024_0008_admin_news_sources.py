"""admin news sources

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-24 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('sources', sa.Column('origin', sa.String(length=16), server_default='catalog', nullable=False), schema='core')
    op.add_column('sources', sa.Column('disabled_by_admin', sa.Boolean(), server_default='false', nullable=False), schema='core')


def downgrade() -> None:
    op.drop_column('sources', 'disabled_by_admin', schema='core')
    op.drop_column('sources', 'origin', schema='core')
