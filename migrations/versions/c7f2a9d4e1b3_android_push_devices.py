"""The Android app's devices beside the browsers': a device's transport (Web Push or FCM), and no Web Push
keys for an FCM token (fcm.py).

Revision ID: c7f2a9d4e1b3
Revises: b7d2e4a9c1f3
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c7f2a9d4e1b3"
down_revision = "b7d2e4a9c1f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("push_subscription", sa.Column("transport", sa.String(length=16), server_default="webpush", nullable=False))
    op.alter_column("push_subscription", "p256dh", existing_type=sa.String(length=200), nullable=True)
    op.alter_column("push_subscription", "auth", existing_type=sa.String(length=100), nullable=True)


def downgrade() -> None:
    # Without the column there's no telling an Android device from a browser: they go, and the app
    # registers them again once it's back.
    op.execute("DELETE FROM push_subscription WHERE transport <> 'webpush'")
    op.alter_column("push_subscription", "auth", existing_type=sa.String(length=100), nullable=False)
    op.alter_column("push_subscription", "p256dh", existing_type=sa.String(length=200), nullable=False)
    op.drop_column("push_subscription", "transport")
