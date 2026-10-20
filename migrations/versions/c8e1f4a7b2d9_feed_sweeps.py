"""The feed's sweeps (BHA-336): one row per search for videos about a followed player that no moment asked for, a gap
search or the famous sweep, with what it found and kept.

Revision ID: c8e1f4a7b2d9
Revises: 46619998e403
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8e1f4a7b2d9"
down_revision = "46619998e403"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feed_sweep",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("stream", sa.String(length=10), nullable=False),
        sa.Column("query", sa.String(length=300), nullable=False),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("found", sa.Integer(), server_default="0", nullable=False),
        sa.Column("kept", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feed_sweep_player_stream_ran", "feed_sweep", ["player_id", "stream", "ran_at"])


def downgrade() -> None:
    op.drop_index("ix_feed_sweep_player_stream_ran", table_name="feed_sweep")
    op.drop_table("feed_sweep")
