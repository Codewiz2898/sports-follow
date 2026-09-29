"""Trigram index on player aliases, for search as you type and "did you mean".

Revision ID: a7c3e1f09b2d
Revises: 5d5bab6ded76
Create Date: 2026-09-29
"""

from __future__ import annotations

from alembic import op

revision = "a7c3e1f09b2d"
down_revision = "5d5bab6ded76"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX IF NOT EXISTS ix_player_alias_trgm ON player_alias USING gin (alias gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_player_alias_trgm")
