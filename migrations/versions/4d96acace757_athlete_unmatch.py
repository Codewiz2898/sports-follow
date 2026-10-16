"""Who a registry athlete isn't: the Wikidata people an operator took a pro's match back from, for their
photo (players unmatch), which roster_wikidata.py never matches them to again.

Revision ID: 4d96acace757
Revises: a3e9d5c71f42
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "4d96acace757"
down_revision = "a3e9d5c71f42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "athlete_unmatch",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("qid", sa.String(length=20), nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("person", sa.String(length=20), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("qid", "sport", "person", postgresql_nulls_not_distinct=True),
    )


def downgrade() -> None:
    op.drop_table("athlete_unmatch")
