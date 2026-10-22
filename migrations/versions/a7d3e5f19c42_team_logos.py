"""Team logos (BHA-666): where each team's source keeps its logo, the two colours an operator set for it, and our own
copy of the logo with the colours read from it (logos.py), served from our origin.

Revision ID: a7d3e5f19c42
Revises: c6e2a8f41d93
Create Date: 2026-10-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7d3e5f19c42"
down_revision = "c6e2a8f41d93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("team", sa.Column("logo_url", sa.Text(), nullable=True))
    op.add_column("team", sa.Column("colours", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table(
        "team_logo",
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("digest", sa.String(length=32), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("palette", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id"),
    )
    op.create_index(op.f("ix_team_logo_digest"), "team_logo", ["digest"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_team_logo_digest"), table_name="team_logo")
    op.drop_table("team_logo")
    op.drop_column("team", "colours")
    op.drop_column("team", "logo_url")
