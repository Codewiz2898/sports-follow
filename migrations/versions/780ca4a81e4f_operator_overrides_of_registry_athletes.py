"""Operator overrides of registry athletes: kept (added by hand, or pinned) or removed (players.py).

Revision ID: 780ca4a81e4f
Revises: f4c8a1e93d27
Create Date: 2026-10-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "780ca4a81e4f"
down_revision = "f4c8a1e93d27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "athlete_override",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("qid", sa.String(length=20), nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("qid", "sport"),
    )


def downgrade() -> None:
    op.drop_table("athlete_override")
