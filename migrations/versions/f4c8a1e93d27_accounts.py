"""Accounts: a signed-in fan's own fan id, their sessions, and which session turned a device on.

Revision ID: f4c8a1e93d27
Revises: f4a7c2e9b1d6
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f4c8a1e93d27"
down_revision = "f4a7c2e9b1d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fan_id", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("google_sub", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_sign_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("fan_id"),
        sa.UniqueConstraint("google_sub"),
    )
    op.create_table(
        "account_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_account_session_account_id"), "account_session", ["account_id"])
    op.create_index(op.f("ix_account_session_expires_at"), "account_session", ["expires_at"])
    op.add_column("push_subscription", sa.Column("session_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_push_subscription_session_id"), "push_subscription", ["session_id"])
    op.create_foreign_key("push_subscription_session_id_fkey", "push_subscription", "account_session", ["session_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    op.drop_constraint("push_subscription_session_id_fkey", "push_subscription", type_="foreignkey")
    op.drop_index(op.f("ix_push_subscription_session_id"), table_name="push_subscription")
    op.drop_column("push_subscription", "session_id")
    op.drop_index(op.f("ix_account_session_expires_at"), table_name="account_session")
    op.drop_index(op.f("ix_account_session_account_id"), table_name="account_session")
    op.drop_table("account_session")
    op.drop_table("account")
