"""Corrections: a moment that takes another back (a goal VAR rules out) names it.

A correction goes only to the fans who were sent the moment it takes back.

Revision ID: 78daf1c8a9ef
Revises: b7d3e5a9c2f1
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "78daf1c8a9ef"
down_revision = "b7d3e5a9c2f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("moment", sa.Column("corrects", sa.Integer(), nullable=True))
    # Indexed: deleting old moments (retention.py) looks up the corrections of each one.
    op.create_index(op.f("ix_moment_corrects"), "moment", ["corrects"])
    op.create_foreign_key("moment_corrects_fkey", "moment", "moment", ["corrects"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    op.drop_constraint("moment_corrects_fkey", "moment", type_="foreignkey")
    op.drop_index(op.f("ix_moment_corrects"), table_name="moment")
    op.drop_column("moment", "corrects")
