"""Feed candidates the judge couldn't judge (BHA-440): how many times the model declined one or answered off the
schema, the last failure, and when it may be asked again, so it isn't asked at every run forever.

Revision ID: a7d2c5e9f1b3
Revises: d3a9f6b1c8e4
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7d2c5e9f1b3"
down_revision = "d3a9f6b1c8e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("feed_candidates", sa.Column("judge_tries", sa.Integer(), server_default="0", nullable=False))
    op.add_column("feed_candidates", sa.Column("judge_error", sa.String(20), nullable=True))
    op.add_column("feed_candidates", sa.Column("judge_after", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("feed_candidates", "judge_after")
    op.drop_column("feed_candidates", "judge_error")
    op.drop_column("feed_candidates", "judge_tries")
