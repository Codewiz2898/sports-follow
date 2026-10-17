"""Tennis: one player a side, each set, upsets of a higher seed, a tour (ATP, WTA), form in sets."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .base import ORDINAL, Found, Line, Poster, Seen, Sport, num, number, stat, values

if TYPE_CHECKING:
    from ..models import Athlete


class Tennis(Sport):
    key = "tennis"
    label = "Tennis"
    kinds = ("tennis",)
    wikidata = {"espn_tennis": "P11585", "atp": "P536", "wta": "P597"}
    athletes = 10_781
    retire_age = 40
    leagues = ("atp", "wta")
    source_line_first = True
    colour = {"sport": "#d7f03c", "on": "#141600", "on_soft": "rgba(20, 22, 0, 0.8)", "ink": "#d7f03c"}
    pattern = r"tennis"
    exclude = r"table tennis|padel|beach tennis"
    alerts = {
        "key": ("Matches, upsets and results", "The match starting, beating a higher seed, the result and injuries"),
        "more": ("every set", "each set and milestones"),
    }
    stats_title = "Overview"
    games = ("match", "matches")

    def league_of(self, ids: dict[str, str]) -> str | None:
        return "wta" if "wta" in ids else "atp"

    def search_detail(self, athlete: Athlete) -> list[str]:
        return [(athlete.league or "").upper(), athlete.country or ""]

    def starts_unlisted(self, now: Seen) -> bool:
        return True

    def start(self, who: str, now: Seen) -> Found:
        return Found("start", "key", f"{who}'s match has started", " · ".join(x for x in (now.state.get("round"), now.state.get("tournament")) if x))

    def final(self, who: str, now: Seen) -> Found:
        head = (now.line or {}).get("headline") or ""
        return Found("final", "key", f"{who}: {head or 'result'}", now.score)

    def verdict(self, state: dict[str, Any], score: str, clock: str) -> str:
        winner = next((p for p in state.get("players") or [] if p.get("winner")), None)
        return f"{winner['name']} won" if winner and winner.get("name") else ""

    def after_final(self, who: str, athlete_id: str, now: Seen) -> list[Found]:
        """A win over a better-seeded player (or any seed, for an unseeded one), in a match played to the
        end: a walkover or a retirement isn't an upset."""
        if now.state.get("ending"):
            return []
        players = now.state.get("players") or []
        me = next((p for p in players if str(p.get("id")) == athlete_id), None)
        other = next((p for p in players if p is not me), None)
        if not (me and me.get("winner") and other and other.get("seed")) or (me.get("seed") and me["seed"] < other["seed"]):
            return []
        return [Found("upset", "key", f"Upset: {who} beats [{other['seed']}] {other.get('name')}", now.score)]

    def high(self, f: Found, versus: str) -> Poster | None:
        """An upset: "beat Sinner, the No. 2 seed"."""
        if m := re.match(r"Upset: .+ beats \[(\d+)\] (.+)$", f.title):
            return Poster("Upset", f"beat {m[2]}, the No. {m[1]} seed")
        return None

    def during(self, who: str, athlete_id: str, before: Seen, now: Seen) -> list[Found]:
        if now.line is None:
            return []

        def sets(line: dict[str, Any] | None) -> tuple[int, int]:
            won, _, lost = (stat(line, "Sets") or "0–0").partition("–")
            return number(won), number(lost)

        (won_was, lost_was), (won_now, lost_now) = sets(before.line), sets(now.line)
        if now.status == "final":
            return []  # the result says it
        found, n = [], won_was + lost_was
        # Polls come every ten seconds, so this is almost always one set; if two finished in between,
        # which came first isn't known and wins are listed first.
        for won in [True] * max(0, won_now - won_was) + [False] * max(0, lost_now - lost_was):
            n += 1
            title = f"{who} {'wins' if won else 'loses'} the {ORDINAL.get(n, f'{n}th')} set"
            found.append(Found("set", "minor", title, f"Sets {won_now}–{lost_now} · {now.score}".strip(" ·"), f"set{n}"))
        return found

    def form(self, items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
        won = lost = 0
        for ln in lines:
            mine, _, theirs = (values(ln).get("Sets") or "").partition("–")
            won, lost = won + int(num(mine)), lost + int(num(theirs))
        return [{"label": "Sets", "value": f"{won}–{lost}"}] if won or lost else []


SPORT = Tennis()
