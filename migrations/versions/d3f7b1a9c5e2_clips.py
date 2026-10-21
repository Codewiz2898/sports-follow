"""Clips: short videos published to Slam (clips.py), the publishing keys that put them up, and fans' reports.

Revision ID: d3f7b1a9c5e2
Revises: c8e1f4a7b2d9
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d3f7b1a9c5e2"
down_revision = "c8e1f4a7b2d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clip_key",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "clip",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=16), nullable=False, unique=True),
        sa.Column("key_id", sa.Integer(), sa.ForeignKey("clip_key.id", ondelete="SET NULL")),
        sa.Column("match", sa.String(length=100)),
        sa.Column("sport", sa.String(length=60)),
        sa.Column("competition", sa.String(length=200)),
        sa.Column("game_title", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("players", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("moment", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("caption", sa.String(length=280), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="uploading"),
        sa.Column("reports", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("live_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_clip_key_id", "clip", ["key_id"])
    op.create_index("ix_clip_match", "clip", ["match"])
    op.create_index("ix_clip_competition", "clip", ["competition"])
    op.create_index("ix_clip_status", "clip", ["status"])
    op.create_index("ix_clip_live_at", "clip", ["live_at"])
    op.create_index("ix_clip_players", "clip", ["players"], postgresql_using="gin")
    op.create_table(
        "clip_report",
        sa.Column("clip_id", sa.Integer(), sa.ForeignKey("clip.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("fan_id", sa.String(length=64), primary_key=True),
        sa.Column("reason", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    # The rows go; the files stay in the bucket, for a person to empty.
    op.drop_table("clip_report")
    op.drop_table("clip")
    op.drop_table("clip_key")
