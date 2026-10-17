"""Following a team (BHA-77): the team registry, a fan's team follows, and which of their games each team plays in.

- team: one source's team (its system and id there), with what search reads (search_text, trigram-indexed),
  what its page shows (card) and its refreshes' health. The table was made with the first schema and never
  written; its rows, if any, can't say which source's they are, so they go.
- team_override: an operator's keep or remove of a source's team, which every import respects.
- team_follow: a fan and a team, at their level.
- event_team: a followed team's games, and each one's result from the team's side.

Downgrading drops the follows and the registry, which the schema before can't hold.

Revision ID: c9e4b7a1d2f3
Revises: b4e7a2c91f05
Create Date: 2026-10-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9e4b7a1d2f3"
down_revision = "b4e7a2c91f05"
branch_labels = None
depends_on = None

JSON = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # Nothing wrote these (models.Team was a placeholder): rows without a source can't be kept.
    op.execute("UPDATE source_binding SET team_id = NULL WHERE team_id IS NOT NULL")
    op.execute("DELETE FROM membership")
    op.execute("DELETE FROM team")
    op.add_column("team", sa.Column("system", sa.String(length=60), nullable=False))
    op.add_column("team", sa.Column("external_id", sa.String(length=60), nullable=False))
    op.add_column("team", sa.Column("short", sa.String(length=20), server_default="", nullable=False))
    op.add_column("team", sa.Column("country", sa.String(length=120), nullable=True))
    op.add_column("team", sa.Column("tier", sa.Integer(), server_default="0", nullable=False))
    op.add_column("team", sa.Column("search_text", sa.Text(), server_default="", nullable=False))
    op.add_column("team", sa.Column("card", JSON, server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column("team", sa.Column("health", JSON, server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column("team", sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("team", sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("team", sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_unique_constraint("uq_team_system_external_id", "team", ["system", "external_id"])
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX ix_team_search_trgm ON team USING gin (search_text gin_trgm_ops)")

    op.create_table(
        "team_override",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("system", sa.String(length=60), nullable=False),
        sa.Column("external_id", sa.String(length=60), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("sport", sa.String(length=60), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("system", "external_id"),
    )
    op.create_table(
        "team_follow",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fan_id", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("alert_rules", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fan_id", "team_id"),
    )
    op.create_index(op.f("ix_team_follow_fan_id"), "team_follow", ["fan_id"], unique=False)
    op.create_index(op.f("ix_team_follow_team_id"), "team_follow", ["team_id"], unique=False)
    op.create_table(
        "event_team",
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("result", JSON, nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "team_id"),
    )
    op.create_index(op.f("ix_event_team_team_id"), "event_team", ["team_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_event_team_team_id"), table_name="event_team")
    op.drop_table("event_team")
    op.drop_index(op.f("ix_team_follow_team_id"), table_name="team_follow")
    op.drop_index(op.f("ix_team_follow_fan_id"), table_name="team_follow")
    op.drop_table("team_follow")
    op.drop_table("team_override")
    op.execute("DELETE FROM team")
    op.drop_index("ix_team_search_trgm", table_name="team")
    op.drop_constraint("uq_team_system_external_id", "team", type_="unique")
    for column in ("created_at", "seen_at", "fetched_at", "health", "card", "search_text", "tier", "country", "short", "external_id", "system"):
        op.drop_column("team", column)
