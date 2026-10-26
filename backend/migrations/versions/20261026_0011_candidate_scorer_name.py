"""Allow full ML scorer names on candidate matches.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The ML endpoint returns names such as "ml-ranker@1:recalibrated-baseline" (33 characters).
    op.alter_column(
        "candidate_matches",
        "scorer",
        schema="core",
        existing_type=sa.String(32),
        type_=sa.String(255),
        existing_nullable=False,
        existing_server_default="baseline@1",
    )


def downgrade() -> None:
    # PostgreSQL refuses a downgrade while longer names exist instead of silently truncating their identity.
    op.alter_column(
        "candidate_matches",
        "scorer",
        schema="core",
        existing_type=sa.String(255),
        type_=sa.String(32),
        existing_nullable=False,
        existing_server_default="baseline@1",
    )
