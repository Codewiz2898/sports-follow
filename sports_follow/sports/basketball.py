"""Basketball: points milestones, double- and triple-doubles, and form in per-game averages."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .base import Found, Line, Poster, Seen, Sport, crossed, num, number, stat, values

if TYPE_CHECKING:
    from ..models import Athlete


def counts(line: dict[str, Any] | None) -> dict[str, int]:
    """A line's counting stats: points, rebounds and assists from the headline ("15 PTS · 3 REB · 10 AST"),
    steals and blocks from the stats."""
    head = (line or {}).get("headline") or ""
    got = {key: int(n) for n, key in re.findall(r"(\d+)\s*(PTS|REB|AST)", head)}
    got["STL"], got["BLK"] = number(stat(line, "Steals")), number(stat(line, "Blocks"))
    return got


def _made(value: str | None) -> tuple[int, int]:
    """ "15-19" -> (15, 19)."""
    made, _, tried = (value or "").partition("-")
    return int(num(made)), int(num(tried))


class Basketball(Sport):
    key = "basketball"
    label = "Basketball"
    kinds = ("basketball",)
    team = True
    wikidata = {"espn_basketball": "P3685", "nba": "P3647", "wnba": "P3588", "bref_nba": "P2685", "bref_wnba": "P4561"}
    athletes = 9_612
    no_line = "Not on the game's roster"
    leagues = ("nba", "wnba")
    colour = {"sport": "#ff7a1a", "on": "#170a00", "on_soft": "rgba(23, 10, 0, 0.8)", "ink": "#ff7a1a"}
    pattern = r"basketball"
    alerts = {
        "key": ("Big games and results", "30 points, triple-doubles, tip-off, the result, injuries and transfers"),
        "more": ("20 points and double-doubles", "20 points, double-doubles and milestones"),
    }

    def team_ok(self, label: str) -> bool:
        return not label.lower().endswith(" football")  # a club's football side (Real Madrid's)

    def league_of(self, ids: dict[str, str]) -> str | None:
        return "wnba" if ("wnba" in ids or "bref_wnba" in ids) else "nba"

    def search_detail(self, athlete: Athlete) -> list[str]:
        return [*list(athlete.teams or [])[:1], (athlete.league or "").upper() if athlete.current else ""]

    def left_out(self, now: Seen) -> bool:
        return now.lineups and now.line is None

    def during(self, who: str, athlete_id: str, before: Seen, now: Seen) -> list[Found]:
        if now.line is None:
            return []
        found = []
        was, got = counts(before.line), counts(now.line)
        where = " · ".join(x for x in ((now.line or {}).get("headline"), now.score, now.clock) if x)
        for mark in crossed(was.get("PTS", 0), got.get("PTS", 0), (20, 30, 40, 50, 60)):
            found.append(Found("points", "key" if mark >= 30 else "minor", f"{who}: {mark} points", where, str(mark), {20: 30, 30: 65, 40: 85}.get(mark, 95)))
        tens_was = sum(1 for k in ("PTS", "REB", "AST", "STL", "BLK") if was.get(k, 0) >= 10)
        tens_now = sum(1 for k in ("PTS", "REB", "AST", "STL", "BLK") if got.get(k, 0) >= 10)
        if tens_was < 2 <= tens_now:
            found.append(Found("double", "minor", f"{who}: double-double", where))
        if tens_was < 3 <= tens_now:
            found.append(Found("triple", "key", f"{who}: triple-double", where))
        return found

    def high(self, f: Found, versus: str) -> Poster | None:
        """30 points or more ("40 points v Utah Jazz"), or a triple-double with its line ("22 PTS · 11 REB ·
        10 AST", the start of the body)."""
        if m := re.search(r": (\d+) points$", f.title):
            return Poster(f"{m[1]} points", versus) if int(m[1]) >= 30 else None
        if f.title.endswith(": triple-double"):
            line = re.match(r"\d+ PTS · \d+ REB · \d+ AST", f.body)
            return Poster("Triple-double", line[0] if line else versus)
        return None

    def form(self, items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
        played = [ln for ln in lines if not str(ln.get("headline") or "").startswith("Did not play")]
        if not played:
            return []
        n = len(played)
        heads = [{k: int(v) for v, k in re.findall(r"(\d+)\s*(PTS|REB|AST)", ln.get("headline") or "")} for ln in played]
        avg = lambda xs: f"{sum(xs) / n:.1f}"  # noqa: E731
        stats = [{"label": "Games", "value": str(n)}] + [{"label": key, "value": avg([h.get(key, 0) for h in heads])} for key in ("PTS", "REB", "AST")]
        for label, name in (("FG", "FG%"), ("3PT", "3P%"), ("FT", "FT%")):
            made = [_made(values(ln).get(label)) for ln in played]
            tried = sum(t for _, t in made)
            if tried:
                stats.append({"label": name, "value": f"{100 * sum(m for m, _ in made) / tried:.1f}"})
        minutes = [num(values(ln).get("Minutes")) for ln in played]
        if any(minutes):
            stats.append({"label": "MIN", "value": avg(minutes)})
        return stats


SPORT = Basketball()
