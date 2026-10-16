"""The width each kept photo's thumbnail was asked at (photos.WIDTH), so a photo kept at another is fetched again.

Revision ID: 723d839b685f
Revises: 4d96acace757
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "723d839b685f"
down_revision = "4d96acace757"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Photos already kept have none: the photos job fetches each again at WIDTH on its next runs (PER_RUN a
    # run), and they show at their old size meanwhile.
    op.add_column("photo", sa.Column("asked_width", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("photo", "asked_width")
