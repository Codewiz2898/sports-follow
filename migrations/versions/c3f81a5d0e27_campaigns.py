"""Campaign pages (campaigns.py): a big event's players or teams to follow in one tap, as rows; and where each
fan came from (fan_source), the first link's tags.

Revision ID: c3f81a5d0e27
Revises: 4d96acace757
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3f81a5d0e27"
down_revision = "4d96acace757"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("event", sa.String(length=120), nullable=True),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("players", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("teams", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("games", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "fan_source",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fan_id", sa.String(length=64), nullable=False),
        sa.Column("tag", sa.String(length=60), nullable=True),
        sa.Column("utm_source", sa.String(length=60), nullable=True),
        sa.Column("utm_medium", sa.String(length=60), nullable=True),
        sa.Column("utm_campaign", sa.String(length=60), nullable=True),
        sa.Column("landing", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fan_id"),
    )


def downgrade() -> None:
    op.drop_table("fan_source")
    op.drop_table("campaign")
