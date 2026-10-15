"""Where the face is in each player photo, so the profile shows it (faces.py): the thumbnail's size and the face's box.

Revision ID: e5c1f7a2d9b8
Revises: a3c9e1f7d2b4
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e5c1f7a2d9b8"
down_revision = "a3c9e1f7d2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Photos already kept have none yet: the photos job looks at them on its next run.
    op.add_column("photo", sa.Column("width", sa.Integer(), nullable=True))
    op.add_column("photo", sa.Column("height", sa.Integer(), nullable=True))
    op.add_column("photo", sa.Column("face", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("photo", "face")
    op.drop_column("photo", "height")
    op.drop_column("photo", "width")
