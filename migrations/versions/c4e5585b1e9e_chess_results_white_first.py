"""A chess game's stored result as chess writes it, White's points first (BUG-144).

A game's result was put from the followed player's side, so Gukesh's loss with Black read "Lost 0–1", which to a
chess fan says Black, Gukesh, won. Results made since are the board's own ("Lost 1–0"); a refresh rewrites those of
a player's last few tournaments, but older ones kept the old way. This rewrites them: a single game of the player's
with Black (their line says "with Black", or its Colour) whose score reads from their side. A mini-match's result
stays from the player's side ("Won 2½–1½"), as its colours change game to game; a game with White, or a draw, reads
the same either way.

Running it again changes nothing: a result already White-first is left as it is. One whose colour isn't stored (a
result still waiting for its scorecard) is left too.

Revision ID: c4e5585b1e9e
Revises: c6e2a8f41d93
Create Date: 2026-10-23
"""

from __future__ import annotations

import re
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "c4e5585b1e9e"
down_revision = "c6e2a8f41d93"
branch_labels = None
depends_on = None

GAME = re.compile(r"(Won|Lost) ([01])–([01])((?: on armageddon)?)")
COLOUR = re.compile(r"\b(White|Black) vs ")
MATCH = re.compile(r"\bthe match |, match ")  # a mini-match's line ("Won the match 2–1 vs So", "Black vs So · game 2, match 1–0")


def colour(contribution: str | None, line: dict[str, Any] | None) -> str | None:
    """The colour the player had in a single game, as their line in it says: its words ("Lost with Black vs Dudin"),
    else its Colour; None for a mini-match's, or where it doesn't say."""
    line = line if isinstance(line, dict) else {}
    for said in (contribution, line.get("headline")):
        if isinstance(said, str) and not MATCH.search(said) and (found := COLOUR.search(said)):
            return found[1]
    stats = {s.get("label"): s.get("value") for s in line.get("stats") or [] if isinstance(s, dict)}
    return stats["Colour"] if "Match" not in stats and stats.get("Colour") in ("White", "Black") else None


def relabelled(said: str | None, colour: str | None) -> str | None:
    """A game with Black whose result reads from the player's side ("Lost 0–1") as the board's, White's points first
    ("Lost 1–0"); None where it reads so already, or isn't a single game with Black."""
    found = GAME.fullmatch(said or "")
    if colour != "Black" or not found or int(found[2]) + int(found[3]) != 1:
        return None
    board = "0–1" if found[1] == "Won" else "1–0"  # Black's win, Black's loss
    return None if f"{found[2]}–{found[3]}" == board else f"{found[1]} {board}{found[4]}"


def upgrade() -> None:
    db = op.get_bind()
    rows = db.execute(sa.text(
        "SELECT ep.event_id, ep.player_id, ep.result->>'result', ep.result->>'player_contribution', "
        "(SELECT pl.stats FROM player_line pl WHERE pl.event_id = ep.event_id AND pl.player_id = ep.player_id ORDER BY pl.as_of DESC LIMIT 1) "
        "FROM event_player ep JOIN event e ON e.id = ep.event_id "
        "WHERE lower(e.sport) = 'chess' AND ep.result->>'result' ~ '^(Won|Lost) [01]–[01]'"
    )).all()
    fixed = [{"event": event_id, "player": player_id, "said": new} for event_id, player_id, said, contribution, line in rows
             if (new := relabelled(said, colour(contribution, line)))]
    if fixed:
        db.execute(sa.text("UPDATE event_player SET result = jsonb_set(result, '{result}', to_jsonb(CAST(:said AS text))) "
                           "WHERE event_id = :event AND player_id = :player"), fixed)


def downgrade() -> None:
    pass  # the board's reading is the right one
