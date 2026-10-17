"""Following a game by itself (BHA-270): a fan's match follows, and a game's own moments and pushes, which
belong to no player.

- match_follow: a fan, a game (its event, and every address it has here) and their level, from when, and
  until it lapsed (the game ended).
- moment.player_id and notification.player_id may be empty: a game's own moment, and a push led by one.
- moment.about: whom a game's own moment is about (a scorer's id in the source; "*" the whole game), so a
  fan who follows the game and a player in it hears it once.

Downgrading drops the follows and the game's own moments and pushes, which the schema before can't hold.

Revision ID: b4e7a2c91f05
Revises: d85acb8b68e0
Create Date: 2026-10-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b4e7a2c91f05"
down_revision = "d85acb8b68e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_follow",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fan_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("alert_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("keys", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fan_id", "event_id"),
    )
    op.create_index(op.f("ix_match_follow_fan_id"), "match_follow", ["fan_id"], unique=False)
    op.create_index(op.f("ix_match_follow_event_id"), "match_follow", ["event_id"], unique=False)
    op.alter_column("moment", "player_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("moment", sa.Column("about", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.alter_column("notification", "player_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM notification WHERE player_id IS NULL")
    op.alter_column("notification", "player_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("moment", "about")
    op.execute("DELETE FROM moment WHERE player_id IS NULL")
    op.alter_column("moment", "player_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index(op.f("ix_match_follow_event_id"), table_name="match_follow")
    op.drop_index(op.f("ix_match_follow_fan_id"), table_name="match_follow")
    op.drop_table("match_follow")
