"""A player's own social accounts (BHA-297): player.socials, a list of {platform, handle}, which the Feed's
content judge (curate.py) reads to know a share is the player's own. Every player starts with none.

Revision ID: e2a7c4f19b36
Revises: 9f38e7d26f75
Create Date: 2026-10-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e2a7c4f19b36"
down_revision = "9f38e7d26f75"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("player", sa.Column("socials", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))


def downgrade() -> None:
    op.drop_column("player", "socials")
