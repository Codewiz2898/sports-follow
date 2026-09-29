"""A player's results history: each finished event's result from their side, and how far back their
older results have been read.

Revision ID: d2a6c8e41f07
Revises: 9f1d5bf44898
Create Date: 2026-09-30 01:20:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'd2a6c8e41f07'
down_revision = '9f1d5bf44898'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('event_player', sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('source_binding', sa.Column('history', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('source_binding', 'history')
    op.drop_column('event_player', 'result')
