"""How precisely the registry knew a removed athlete's birth date (BUG-292): a pick with their name and birth date is
them only as far as that date goes, so a real New Year's Day birthday no longer counts as the year alone.

Rows already written keep NULL, and their 1 January compares by year only, as before (registry.removed).

Revision ID: 932250dbb09f
Revises: c6e2a8f41d93
Create Date: 2026-10-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "932250dbb09f"
down_revision = "c6e2a8f41d93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("athlete_override", sa.Column("birth_precision", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("athlete_override", "birth_precision")
