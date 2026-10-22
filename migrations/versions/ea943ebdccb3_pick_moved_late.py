"""A pick brought into an account after its question closed (BUG-091, BHA-413): signing in brings every one of the
device's picks, and those on games already under way or over are marked, so no league's table counts them.

Revision ID: ea943ebdccb3
Revises: 89e07e122a1e
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "ea943ebdccb3"
down_revision = "89e07e122a1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prediction_pick", sa.Column("moved_late", sa.Boolean(), server_default=sa.text("false"), nullable=False))


def downgrade() -> None:
    op.drop_column("prediction_pick", "moved_late")
