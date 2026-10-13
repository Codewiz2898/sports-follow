"""How precisely Wikidata knows each athlete's birth date (BUG-071).

Rows already imported get it on the next registry import; until then it is NULL, and a stored
1 January compares by year only (registry.same_day).

Revision ID: b7d41e9c2a58
Revises: e8b2f4c61a3d
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7d41e9c2a58"
down_revision = "e8b2f4c61a3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("athlete", sa.Column("birth_precision", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("athlete", "birth_precision")
