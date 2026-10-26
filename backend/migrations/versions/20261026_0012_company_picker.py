"""company picker

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-26 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('user_company_access', sa.Column('added_by_user', sa.Boolean(), server_default='false', nullable=False), schema='core')


def downgrade() -> None:
    op.drop_column('user_company_access', 'added_by_user', schema='core')
