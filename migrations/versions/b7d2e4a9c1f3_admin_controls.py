"""The admin panel: what it changed on the running app (sports and sources off, sources' allowances), and blocked accounts.

Revision ID: b7d2e4a9c1f3
Revises: e5c1f7a2d9b8
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b7d2e4a9c1f3"
down_revision = "e5c1f7a2d9b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "control",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.add_column("account", sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("account", "blocked_at")
    op.drop_table("control")
