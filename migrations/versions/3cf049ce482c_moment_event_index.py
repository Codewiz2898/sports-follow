"""Indexes on two foreign keys read often: moment.event_id, which every live game's poll, its match page and the
team and game activity lists look moments up by (BUG-266), and feed_candidates.moment_id, which retention's daily
delete of old moments looks each one's candidates up by (BUG-219). Without them each look read every moment, or
every candidate once per moment deleted.

Built CONCURRENTLY, so the site and the worker keep reading and writing both tables while they're made.

Revision ID: 3cf049ce482c
Revises: a7d2c5e9f1b3
Create Date: 2026-10-22
"""

from __future__ import annotations

from alembic import op

revision = "3cf049ce482c"
down_revision = "a7d2c5e9f1b3"
branch_labels = None
depends_on = None

INDEXES = [("ix_moment_event_id", "moment", "event_id"), ("ix_feed_candidates_moment_id", "feed_candidates", "moment_id")]


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY can't run in a transaction: the autocommit block commits the migrations before this
    # one, then runs these by themselves. A build that stopped part way leaves an invalid index of the same name,
    # which is dropped first; until this revision is recorded, nothing else made one.
    with op.get_context().autocommit_block():
        for name, table, column in INDEXES:
            op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
            op.create_index(name, table, [column], postgresql_concurrently=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, _ in INDEXES:
            op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
