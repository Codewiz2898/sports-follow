"""The player registry: athletes imported from Wikidata, searchable by trigram.

Revision ID: c41d0e8f5a17
Revises: a7c3e1f09b2d
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c41d0e8f5a17"
down_revision = "a7c3e1f09b2d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "athlete",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("qid", sa.String(length=20), nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("country", sa.String(length=120), nullable=True),
        sa.Column("teams", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("league", sa.String(length=20), nullable=True),
        sa.Column("title", sa.String(length=10), nullable=True),
        sa.Column("ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("image", sa.String(length=300), nullable=True),
        sa.Column("sitelinks", sa.Integer(), nullable=False),
        sa.Column("current", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("qid", "sport"),
    )
    op.create_index(op.f("ix_athlete_qid"), "athlete", ["qid"], unique=False)
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX ix_athlete_search_trgm ON athlete USING gin (search_text gin_trgm_ops)")
    # Source ids are looked up both ways: registry -> live source when following, and back when linking.
    op.execute("CREATE INDEX ix_athlete_ids ON athlete USING gin (ids jsonb_path_ops)")


def downgrade() -> None:
    op.drop_table("athlete")
