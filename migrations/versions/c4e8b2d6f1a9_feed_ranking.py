"""The feed's ranking (BHA-301): each candidate's moment (the one it's about, so the Feed groups it there) and an
admin's word on it (approved, hidden, pinned), beside the judge's verdict and the score; and an index for the
judge's queue, the rows it hasn't read.

Revision ID: c4e8b2d6f1a9
Revises: a7d3e91c4b25
Create Date: 2026-10-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4e8b2d6f1a9"
down_revision = "a7d3e91c4b25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("feed_candidates", sa.Column("moment_id", sa.Integer(), nullable=True))
    op.add_column("feed_candidates", sa.Column("review", sa.String(length=10), nullable=True))
    op.create_foreign_key("feed_candidates_moment_id_fkey", "feed_candidates", "moment", ["moment_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_feed_candidates_unjudged", "feed_candidates", ["fetched_at"], postgresql_where=sa.text("verdict IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_feed_candidates_unjudged", table_name="feed_candidates")
    op.drop_constraint("feed_candidates_moment_id_fkey", "feed_candidates", type_="foreignkey")
    op.drop_column("feed_candidates", "review")
    op.drop_column("feed_candidates", "moment_id")
