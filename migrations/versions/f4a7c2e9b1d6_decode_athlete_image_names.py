"""Commons file names stored percent-encoded, decoded in place (BUG-077).

The import kept each photo's file name from its Special:FilePath URL with only "%20" decoded, so
29,253 of 65,597 names read like "Zlatan Ibrahimovi%C4%87 nyc.jpg". The import decodes them now;
this fixes the rows already stored without waiting for the weekly run.

Running it again changes nothing: MediaWiki never allows "%" and two hex digits in a title, so a name
with one is still encoded, and a decoded one ("100% Pure.jpg", "Ibrahimović") is left as it is. A
name the old 300-character limit cut in the middle of a character is left for the next import.

Revision ID: f4a7c2e9b1d6
Revises: b7d41e9c2a58
Create Date: 2026-10-13
"""

from __future__ import annotations

from urllib.parse import unquote

import sqlalchemy as sa
from alembic import op

revision = "f4a7c2e9b1d6"
down_revision = "b7d41e9c2a58"
branch_labels = None
depends_on = None


def decoded(name: str) -> str:
    try:
        return unquote(name, errors="strict")
    except UnicodeDecodeError:
        return name


def upgrade() -> None:
    db = op.get_bind()
    rows = db.execute(sa.text("SELECT id, image FROM athlete WHERE image ~ '%[0-9A-Fa-f]{2}'")).all()
    fixed = [{"id": athlete_id, "image": decoded(name)} for athlete_id, name in rows if decoded(name) != name]
    if fixed:
        db.execute(sa.text("UPDATE athlete SET image = :image WHERE id = :id"), fixed)


def downgrade() -> None:
    pass  # the decoded names are the right ones
