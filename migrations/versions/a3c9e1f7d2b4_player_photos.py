"""Player photos: each followed or starter athlete's Wikimedia Commons photo, its credit and its thumbnail (photos.py).

Revision ID: a3c9e1f7d2b4
Revises: 78daf1c8a9ef
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3c9e1f7d2b4"
down_revision = "78daf1c8a9ef"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "photo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file", sa.String(length=300), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("licence", sa.String(length=100), nullable=True),
        sa.Column("licence_url", sa.Text(), nullable=True),
        sa.Column("author", sa.String(length=200), nullable=True),
        sa.Column("page", sa.Text(), nullable=True),
        sa.Column("source_sha1", sa.String(length=40), nullable=True),
        sa.Column("digest", sa.String(length=32), nullable=True),
        sa.Column("media_type", sa.String(length=30), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file"),
    )
    op.create_index(op.f("ix_photo_digest"), "photo", ["digest"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_photo_digest"), table_name="photo")
    op.drop_table("photo")
