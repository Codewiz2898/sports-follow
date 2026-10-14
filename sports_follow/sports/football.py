"""Football, as every football source shares it: a result from the player's side."""

from __future__ import annotations

from typing import Any

from ..adapters.base import Fixture


def pens(a: int | None, b: int | None) -> str:
    """ " (4–2 pens)" after a shootout, else ""."""
    return f" ({a}–{b} pens)" if a is not None and b is not None else ""


def _tally(side: dict[str, Any]) -> tuple[int, int] | None:
    """A side's goals, then its shootout penalties, to compare; None if the score isn't a number."""
    score = str(side.get("score") or "")
    return (int(score), side.get("pens") or 0) if score.isdigit() else None


def result_label(fixture: Fixture, team_ids: list[str]) -> str:
    """ "Won 2–1" / "Lost 0–1" / "Drew 1–1" from the player's team's side, else the plain score; a
    shootout adds its score ("Lost 0–0 (2–4 pens)")."""
    r = fixture.result
    if not r:
        return ""
    home, away = r["home"], r["away"]
    ours, theirs = (home, away) if str(home["id"]) in team_ids else (away, home) if str(away["id"]) in team_ids else (None, None)
    if ours is None:
        return f"{home['name']} {home['score']}–{away['score']} {away['name']}{pens(home.get('pens'), away.get('pens'))}"
    score = f"{ours['score']}–{theirs['score']}{pens(ours.get('pens'), theirs.get('pens'))}"
    if ours.get("winner") or theirs.get("winner"):
        return f"{'Won' if ours.get('winner') else 'Lost'} {score}"
    # No winner flag: the score decides.
    mine, other = _tally(ours), _tally(theirs)
    if mine and other and mine != other:
        return f"{'Won' if mine > other else 'Lost'} {score}"
    return f"Drew {score}"
