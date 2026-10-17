"""Who's out: the injuries and bans a source lists league-wide, each read compared with the last, for the
facts on Today's Wire (sidelined.py).

Revision ID: a3e9d5c71f42
Revises: c7f2a9d4e1b3
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3e9d5c71f42"
down_revision = "c7f2a9d4e1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sidelined",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("system", sa.String(length=60), nullable=False),
        sa.Column("source_id", sa.String(length=60), nullable=False),
        sa.Column("athlete_id", sa.String(length=60), nullable=False),
        sa.Column("team_id", sa.String(length=60), nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("team", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("what", sa.String(length=200), nullable=False),
        sa.Column("since", sa.Date(), nullable=True),
        sa.Column("until", sa.Date(), nullable=True),
        sa.Column("fame", sa.Integer(), nullable=False),
        sa.Column("out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("system", "source_id"),
    )
    op.create_index("ix_sidelined_athlete", "sidelined", ["system", "athlete_id"])


def downgrade() -> None:
    op.drop_table("sidelined")
