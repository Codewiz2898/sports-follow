"""The feed's moment searches (BHA-303): one row per followed player and game whose big moment is searched for
fans' videos of it, with when its two runs ran.

Revision ID: d5f3a8c2e7b4
Revises: c4e8b2d6f1a9
Create Date: 2026-10-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d5f3a8c2e7b4"
down_revision = "c4e8b2d6f1a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feed_search",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("moment_id", sa.Integer(), nullable=False),
        sa.Column("query", sa.String(length=300), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("second_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kept", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["moment_id"], ["moment.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "event_id"),
    )
    op.create_index(op.f("ix_feed_search_moment_id"), "feed_search", ["moment_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_feed_search_moment_id"), table_name="feed_search")
    op.drop_table("feed_search")
