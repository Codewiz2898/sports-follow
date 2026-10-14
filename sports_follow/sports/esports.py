"""Esports, as every esports source shares it. Battle royale lobbies (BGMI, Free Fire, PUBG) rank
every team in one result: {"teams": 16, "standings": [{"id", "name", "place", "points", …}]}."""

from __future__ import annotations

from typing import Callable

from ..adapters.base import Fixture


def ordinal(n: int | None) -> str:
    if not n:
        return "?"
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def points(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def lobby_result(fixture: Fixture, team_ids: list[str], key: Callable[[str], str] = str) -> str:
    """ "3rd of 16 · 87 pts" from the player's team's side; key is how the source compares team ids."""
    r = fixture.result or {}
    mine = {key(t) for t in team_ids}
    ours = next((s for s in r.get("standings") or [] if key(s["id"]) in mine), None)
    if ours is None:
        return ""
    return f"{ordinal(ours['place'])} of {r.get('teams')} · {points(ours['points'])} pts"
