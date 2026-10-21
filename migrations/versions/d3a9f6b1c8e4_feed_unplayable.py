"""Feed videos a fan's app said won't play inside it (BHA-380): when, so the Feed stops serving them.

Revision ID: d3a9f6b1c8e4
Revises: d3f7b1a9c5e2
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d3a9f6b1c8e4"
down_revision = "d3f7b1a9c5e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("feed_candidates", sa.Column("unplayable_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("feed_candidates", "unplayable_at")
