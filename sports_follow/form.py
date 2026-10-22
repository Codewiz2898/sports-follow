"""A player's form over their last few results (the Stats tab's Last 5 and Last 10), from the results
history and the lines stored with it. Each sport sums or averages what its lines carry (sports/: form);
every sport with "Won/Drew/Lost" results gets a record.
"""

from __future__ import annotations

from typing import Any

from . import sports
from .sports.base import Line


def summarize(sport: str, results: list[tuple[dict[str, Any], Line | None]], teams: list[str]) -> tuple[list[dict[str, str]], str]:
    """(stats, note) over these results (newest first), each with the player's line if it's stored."""
    if not results:
        return [], ""
    rules = sports.of(sport)
    items = [r for r, _ in results]
    lines = [line for _, line in results if line]
    stats = [s for s in [_record(rules, items, teams)] if s]
    stats += rules.form(items, lines)
    n = len(results)
    one, many = rules.form_games(items)
    note = f"Last {n} {one if n == 1 else many}"
    loading = sum(1 for r, _ in results if r.get("scorecard") is False)
    if loading:
        note += f" ({loading} still loading)"
    return stats, note


def _record(rules: sports.Sport, items: list[dict[str, Any]], teams: list[str]) -> dict[str, str] | None:
    # Each result's outcome as it was kept with it (results._judged), the one its row shows; else read from its words.
    known = [o for o in (r["outcome"] if "outcome" in r else rules.outcome(r.get("result", ""), teams) for r in items) if o]
    if not known:
        return None
    w, d, l = (known.count(x) for x in "WDL")
    if d or rules.draws:
        return {"label": "W–D–L", "value": f"{w}–{d}–{l}"}
    return {"label": "W–L", "value": f"{w}–{l}"}
