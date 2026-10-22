"""A clip's own token for its hidden files' names (BUG-126's review): kept on its row, so a change of the clips
bucket's keys can't lose where a hidden clip's files are. None until it's first hidden.

Revision ID: d7a3c9e1f5b8
Revises: 3cf049ce482c
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d7a3c9e1f5b8"
down_revision = "3cf049ce482c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clip", sa.Column("hidden_as", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("clip", "hidden_as")
