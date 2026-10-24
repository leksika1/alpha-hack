"""private user events

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-24 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('events', sa.Column('private_company_ids', postgresql.ARRAY(sa.String(length=32)), nullable=True), schema='core')


def downgrade() -> None:
    op.drop_column('events', 'private_company_ids', schema='core')
