"""Feed candidates whose verdict was cleared to JSON's null (a paid promotion declared after they were judged,
before BUG-118's review fix): SQL's NULL instead, so the judge's queue (verdict IS NULL) reads them again.

Revision ID: b5d1e8a2c7f4
Revises: ea943ebdccb3
Create Date: 2026-10-22
"""

from __future__ import annotations

from alembic import op

revision = "b5d1e8a2c7f4"
down_revision = "ea943ebdccb3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE feed_candidates SET verdict = NULL WHERE verdict = 'null'::jsonb")


def downgrade() -> None:
    pass  # nothing to put back: a JSON null was never a verdict
