"""Calling games fairly (BUG-105, BUG-106): whether a pick counts in its question's split and points (one from a
fan the request itself made up doesn't), and an open question's split as fans see it, refreshed only once enough
more have picked that a friend's one pick isn't told by it. Picks made before count, as they did; open questions
show their split again from their next pick.

Revision ID: 89e07e122a1e
Revises: c4e8b2f7a913
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "89e07e122a1e"
down_revision = "c4e8b2f7a913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prediction_pick", sa.Column("counted", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.add_column("prediction_question", sa.Column("split", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("prediction_question", "split")
    op.drop_column("prediction_pick", "counted")
