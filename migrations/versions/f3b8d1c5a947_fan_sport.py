"""Your sports (BHA-653): the sports a fan said they watch, in their order (fansports.py), kept per fan as follows are.

Revision ID: f3b8d1c5a947
Revises: d7a3c9e1f5b8
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3b8d1c5a947"
down_revision = "d7a3c9e1f5b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fan_sport",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fan_id", sa.String(length=64), nullable=False),
        sa.Column("sport", sa.String(length=40), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fan_id", "sport"),
    )


def downgrade() -> None:
    op.drop_table("fan_sport")
