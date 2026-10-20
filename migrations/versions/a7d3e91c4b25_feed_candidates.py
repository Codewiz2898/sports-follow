"""The feed's candidates (BHA-298): what the curator fetched about each followed player (videos that name them),
one row per player and item, going with the player. The verdict, score and judged_at columns stay empty until
the later stages (the judge, the ranking) fill them.

Revision ID: a7d3e91c4b25
Revises: 9f38e7d26f75
Create Date: 2026-10-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7d3e91c4b25"
down_revision = "9f38e7d26f75"
branch_labels = None
depends_on = None

JSON = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "feed_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("candidate", JSON, nullable=False),
        sa.Column("features", JSON, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("verdict", JSON, nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("judged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["player.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "external_id"),
    )
    op.create_index("ix_feed_candidates_source_external", "feed_candidates", ["source", "external_id"])


def downgrade() -> None:
    op.drop_index("ix_feed_candidates_source_external", table_name="feed_candidates")
    op.drop_table("feed_candidates")
