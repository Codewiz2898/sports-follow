"""The Feed on a player's page (BHA-299): what a fan said about an item there.

- feed_feedback: a fan, a player, an item (feed.item_id, of its address) and what they said (not
  interested, not about the player, a report). The item leaves that fan's feed at once.

Revision ID: e2b9c4f7a1d3
Revises: 9f38e7d26f75
Create Date: 2026-10-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e2b9c4f7a1d3"
down_revision = "9f38e7d26f75"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feed_feedback",
        sa.Column("fan_id", sa.String(length=64), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(length=32), nullable=False),
        sa.Column("verdict", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("fan_id", "player_id", "item_id"),
    )
    op.create_index(op.f("ix_feed_feedback_player_id"), "feed_feedback", ["player_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_feed_feedback_player_id"), table_name="feed_feedback")
    op.drop_table("feed_feedback")
