"""Predictions' pushes (BHA-321): when a question's "Make your pick" went, and when how each pick went did.

Revision ID: 46619998e403
Revises: 9f8aaf26aaac
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "46619998e403"
down_revision = "9f8aaf26aaac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prediction_question", sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("prediction_question", sa.Column("told_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("prediction_question", "told_at")
    op.drop_column("prediction_question", "reminded_at")
