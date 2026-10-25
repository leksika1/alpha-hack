"""market rates

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-26 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('market_rates',
    sa.Column('indicator', sa.String(length=32), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('value', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('indicator', 'day', name=op.f('pk_market_rates')),
    schema='core'
    )


def downgrade() -> None:
    op.drop_table('market_rates', schema='core')
