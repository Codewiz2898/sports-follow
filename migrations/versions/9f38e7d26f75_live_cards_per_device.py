"""The Android app's live cards per device: whether the phone shows them (its Live scores switch) and the games
whose card it stopped, so the server sends none it would throw away (livecards.android). Every device has them on,
as it had before.

Revision ID: 9f38e7d26f75
Revises: c9e4b7a1d2f3
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "9f38e7d26f75"
down_revision = "c9e4b7a1d2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("push_subscription", sa.Column("live_cards", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.add_column("push_subscription", sa.Column("live_stopped", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))


def downgrade() -> None:
    op.drop_column("push_subscription", "live_stopped")
    op.drop_column("push_subscription", "live_cards")
