"""Moment priorities, the push log the noise rules read, and muted games.

Revision ID: e8b2f4c61a3d
Revises: d2a6c8e41f07
Create Date: 2026-10-12 21:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'e8b2f4c61a3d'
down_revision = 'd2a6c8e41f07'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('moment', sa.Column('priority', sa.Integer(), server_default='50', nullable=False))
    # The tick's sweep for moments no sender claimed (notify.pending).
    op.create_index('ix_moment_unsent', 'moment', ['created_at'], postgresql_where=sa.text('sent_at IS NULL'))
    op.create_table('notification',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('fan_id', sa.String(length=64), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=True),
    sa.Column('player_id', sa.Integer(), nullable=False),
    sa.Column('moment_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('lines', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('loud', sa.Boolean(), nullable=False),
    sa.Column('devices', sa.Integer(), nullable=False),
    sa.Column('delivered', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('opened_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['event_id'], ['event.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['player_id'], ['player.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_notification_fan_created', 'notification', ['fan_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_notification_event_id'), 'notification', ['event_id'], unique=False)
    op.create_table('notification_mute',
    sa.Column('fan_id', sa.String(length=64), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['event.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('fan_id', 'event_id')
    )


def downgrade() -> None:
    op.drop_table('notification_mute')
    op.drop_index(op.f('ix_notification_event_id'), table_name='notification')
    op.drop_index('ix_notification_fan_created', table_name='notification')
    op.drop_table('notification')
    op.drop_index('ix_moment_unsent', table_name='moment')
    op.drop_column('moment', 'priority')
