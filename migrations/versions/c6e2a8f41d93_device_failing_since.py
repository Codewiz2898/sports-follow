"""When a device's run of failed pushes began (BUG-298): a device is removed for failing only once it has failed every
push for a day, so a push service's outage of minutes or hours never removes the devices it failed. None while its
last push went through.

Revision ID: c6e2a8f41d93
Revises: f3b8d1c5a947
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c6e2a8f41d93"
down_revision = "f3b8d1c5a947"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("push_subscription", sa.Column("failing_since", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("push_subscription", "failing_since")
