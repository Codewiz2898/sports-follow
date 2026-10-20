"""Predictions with friends (BHA-319): the questions fans call before a game or a map, their picks, and the
leagues friends call them in.

Revision ID: 9f8aaf26aaac
Revises: d5f3a8c2e7b4
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "9f8aaf26aaac"
down_revision = "d5f3a8c2e7b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prediction_question",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game", sa.String(length=100), nullable=False),
        sa.Column("ask", sa.String(length=30), nullable=False),
        sa.Column("keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("sport", sa.String(length=60), nullable=False),
        sa.Column("match", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("game_title", sa.String(length=300), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("answer", sa.String(length=64), nullable=True),
        sa.Column("locks_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game", "ask"),
    )
    op.create_index(op.f("ix_prediction_question_event_id"), "prediction_question", ["event_id"])
    op.create_index(op.f("ix_prediction_question_status"), "prediction_question", ["status"])
    op.create_index("ix_prediction_question_keys", "prediction_question", ["keys"], postgresql_using="gin")
    op.create_table(
        "prediction_pick",
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("fan_id", sa.String(length=64), nullable=False),
        sa.Column("option", sa.String(length=64), nullable=False),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["question_id"], ["prediction_question.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("question_id", "fan_id"),
    )
    op.create_index(op.f("ix_prediction_pick_fan_id"), "prediction_pick", ["fan_id"])
    op.create_table(
        "prediction_league",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=True),
        sa.Column("shared", sa.Integer(), server_default="0", nullable=False),
        sa.Column("opened", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "prediction_member",
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("fan_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["league_id"], ["prediction_league.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("league_id", "fan_id"),
    )
    op.create_index(op.f("ix_prediction_member_fan_id"), "prediction_member", ["fan_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_prediction_member_fan_id"), table_name="prediction_member")
    op.drop_table("prediction_member")
    op.drop_table("prediction_league")
    op.drop_index(op.f("ix_prediction_pick_fan_id"), table_name="prediction_pick")
    op.drop_table("prediction_pick")
    op.drop_index("ix_prediction_question_keys", table_name="prediction_question")
    op.drop_index(op.f("ix_prediction_question_status"), table_name="prediction_question")
    op.drop_index(op.f("ix_prediction_question_event_id"), table_name="prediction_question")
    op.drop_table("prediction_question")
