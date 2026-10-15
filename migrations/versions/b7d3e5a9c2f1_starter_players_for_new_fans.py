"""Starter players: registry athletes offered to a new fan in their sport's starter picker (starters.py).

Revision ID: b7d3e5a9c2f1
Revises: 780ca4a81e4f
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7d3e5a9c2f1"
down_revision = "780ca4a81e4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "starter",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("qid", sa.String(length=20), nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("qid", "sport"),
    )


def downgrade() -> None:
    op.drop_table("starter")
