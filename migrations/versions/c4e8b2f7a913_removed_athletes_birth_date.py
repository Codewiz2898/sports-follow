"""A removed athlete's birth date (BHA-420): a source that had no id for them when they were removed (one switched
to or added since) names them under another, and a pick with their name and birth date is refused all the same.

Revision ID: c4e8b2f7a913
Revises: a7d2c5e9f1b3
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4e8b2f7a913"
down_revision = "a7d2c5e9f1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("athlete_override", sa.Column("birth_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("athlete_override", "birth_date")
