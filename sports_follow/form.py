"""A player's form over their last few results (the Stats tab's Last 5 and Last 10), from the results
history and the lines stored with it. Each sport sums or averages what its lines carry; every sport
with "Won/Drew/Lost" results gets a record.
"""

from __future__ import annotations

import re
from typing import Any

from .adapters.espn_cricket import batting_bowling

Line = dict[str, Any]  # {"headline": "38 PTS · 16 REB · 2 AST", "stats": [{"label", "value"}]}


def summarize(sport: str, results: list[tuple[dict[str, Any], Line | None]], teams: list[str]) -> tuple[list[dict[str, str]], str]:
    """(stats, note) over these results (newest first), each with the player's line if it's stored."""
    if not results:
        return [], ""
    sport = (sport or "").lower()
    lines = [line for _, line in results if line]
    stats = [s for s in [_record(sport, [r for r, _ in results], teams)] if s]
    per_sport = {"football": _football, "soccer": _football, "basketball": _basketball, "cricket": _cricket, "tennis": _tennis, "chess": _chess}.get(sport)
    if per_sport:
        stats += per_sport([r for r, _ in results], lines)
    n = len(results)
    one, many = ("match", "matches") if sport in ("tennis", "cricket") else ("game", "games")
    note = f"Last {n} {one if n == 1 else many}"
    loading = sum(1 for r, _ in results if r.get("scorecard") is False)
    if loading:
        note += f" ({loading} still loading)"
    return stats, note


# ---------------------------------------------------------------- reading results and lines


def _values(line: Line) -> dict[str, str]:
    return {s.get("label"): str(s.get("value")) for s in line.get("stats") or []}


def _num(value: str | None) -> float:
    match = re.match(r"\s*([+-]?\d+(?:\.\d+)?)", value or "")
    return float(match.group(1)) if match else 0.0


def _made(value: str | None) -> tuple[int, int]:
    """ "15-19" -> (15, 19)."""
    made, _, tried = (value or "").partition("-")
    return int(_num(made)), int(_num(tried))


def _outcome(result: str) -> str | None:
    word = (result or "").split(" ", 1)[0]
    return {"Won": "W", "Drew": "D", "Lost": "L"}.get(word)


def _record(sport: str, items: list[dict[str, Any]], teams: list[str]) -> dict[str, str] | None:
    outcomes = [_outcome(r.get("result", "")) for r in items]
    if sport == "cricket":
        # "India won by 5 wickets": a win for an India player, a loss for their opponents'.
        outcomes = [_cricket_outcome(r.get("result", ""), teams) for r in items]
    known = [o for o in outcomes if o]
    if not known:
        return None
    w, d, l = (known.count(x) for x in "WDL")
    if d or sport in ("football", "soccer", "chess", "cricket"):
        return {"label": "W–D–L", "value": f"{w}–{d}–{l}"}
    return {"label": "W–L", "value": f"{w}–{l}"}


def _cricket_outcome(summary: str, teams: list[str]) -> str | None:
    text = summary.lower()
    if " won " not in text:
        return "D" if any(w in text for w in ("drawn", "tied", "no result")) else None
    winner = text.split(" won ", 1)[0]
    if any(t and t.lower() in winner for t in teams):
        return "W"
    return "L" if teams else None


# ---------------------------------------------------------------- per sport


def _football(items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
    played = [ln for ln in lines if ln.get("headline") not in (None, "On the bench")]
    total = lambda label: int(sum(_num(_values(ln).get(label)) for ln in played))  # noqa: E731
    stats = [{"label": "Apps", "value": str(len(played))}]
    if played:
        stats += [{"label": "Goals", "value": str(total("Goals"))}, {"label": "Assists", "value": str(total("Assists"))}]
        shots, on_target = total("Shots"), total("On target")
        if shots:
            stats.append({"label": "Shots (on target)", "value": f"{shots} ({on_target})"})
    return stats


def _basketball(items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
    played = [ln for ln in lines if not str(ln.get("headline") or "").startswith("Did not play")]
    if not played:
        return []
    n = len(played)
    heads = [{k: int(v) for v, k in re.findall(r"(\d+)\s*(PTS|REB|AST)", ln.get("headline") or "")} for ln in played]
    avg = lambda xs: f"{sum(xs) / n:.1f}"  # noqa: E731
    stats = [{"label": "Games", "value": str(n)}] + [{"label": key, "value": avg([h.get(key, 0) for h in heads])} for key in ("PTS", "REB", "AST")]
    for label, name in (("FG", "FG%"), ("3PT", "3P%"), ("FT", "FT%")):
        made = [_made(_values(ln).get(label)) for ln in played]
        tried = sum(t for _, t in made)
        if tried:
            stats.append({"label": name, "value": f"{100 * sum(m for m, _ in made) / tried:.1f}"})
    minutes = [_num(_values(ln).get("Minutes")) for ln in played]
    if any(minutes):
        stats.append({"label": "MIN", "value": avg(minutes)})
    return stats


def _cricket(items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
    return batting_bowling([ln.get("stats") or [] for ln in lines])


def _tennis(items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
    won = lost = 0
    for ln in lines:
        mine, _, theirs = (_values(ln).get("Sets") or "").partition("–")
        won, lost = won + int(_num(mine)), lost + int(_num(theirs))
    return [{"label": "Sets", "value": f"{won}–{lost}"}] if won or lost else []


def _points(result: str) -> tuple[float, float] | None:
    """ "Won 3½–2½" -> (3.5, 2.5)."""
    match = re.search(r"(\d*½?)–(\d*½?)", result or "")
    if not match:
        return None
    value = lambda s: (int(s.rstrip("½")) if s.rstrip("½") else 0) + (0.5 if s.endswith("½") else 0)  # noqa: E731
    return value(match.group(1)), value(match.group(2))


def _chess(items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
    scored = [p for p in (_points(r.get("result", "")) for r in items) if p]
    if not scored:
        return []
    mine, games = sum(p for p, _ in scored), sum(p + q for p, q in scored)
    fmt = lambda x: (str(int(x)) if x == int(x) else f"{int(x)}½" if x > 1 else "½")  # noqa: E731
    return [{"label": "Score", "value": f"{fmt(mine)}/{fmt(games)}"}, {"label": "Score %", "value": f"{100 * mine / games:.0f}%"}]
