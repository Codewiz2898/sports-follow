"""Tennis: one player a side, each set, upsets of a higher seed, a tour (ATP, WTA), form in sets."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .base import ORDINAL, Found, Glance, Line, Played, Poster, Seen, Sport, num, number, short, stat, values

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
    game_alerts = {
        "key": ("The start and the result", "The match starting and the result"),
        "more": ("every set", "each set"),
    }
    kind_words = {"set": "Set"}
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

    def glance(self, state: dict[str, Any], score: str, clock: str, status: str | None, events: list[dict[str, Any]]) -> Glance:
        """Each player's short name and their games in each set ("6 2"), and the set being played ("Set 2"), or
        "Final" once it's over."""
        players = [p for p in state.get("players") or [] if isinstance(p, dict)]
        sides = [(short(p), " ".join(str(s["games"]) for s in p.get("sets") or [] if isinstance(s, dict) and s.get("games") is not None)) for p in players]
        where = "Final" if status == "final" else f"Set {state['set']}" if state.get("set") else clock or ""
        return Glance(sides if len(sides) == 2 else [], where)

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
        return self._sets(who, before, now, losses=True)

    def game_during(self, name: str, before: Played, now: Played, told: list[tuple[str, str]]) -> list[Found]:
        """Each set, once, from the side that won it: "Sinner v Alcaraz: Sinner wins the 2nd set"."""
        found = []
        for athlete_id, who in now.names.items():
            for f in self._sets(who, before.seen(athlete_id), now.seen(athlete_id), losses=False):
                f.title, f.about = f"{name}: {f.title}", ("*",)
                found.append(f)
        return found

    def _sets(self, who: str, before: Seen, now: Seen, losses: bool) -> list[Found]:
        """The sets a player won (and lost, with losses) since the last poll."""
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
        for won in [True] * max(0, won_now - won_was) + ([False] * max(0, lost_now - lost_was) if losses else []):
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

    # ---- the Feed's content judge (curate.py)

    def content_hints(self) -> str:
        return """\
A milestone: a title, a Grand Slam, a first, a ranking landmark (career high, No. 1), a win streak. A match \
moment: a match point, a tweener, a long rally, a comeback from a set down. Full-match uploads are restreams; \
tournament and tour channels' highlights are official."""

    # ---- the feed (curator.py): channels trusted for videos about its players (each feed answered, 20 Oct 2026)

    video_channels = (
        "UCbcxFkd6B9xUU54InHv4Tig",  # Tennis TV: the ATP's highlights, interviews
        "UCaBIVVpHjq6j3tSyxwTE-8Q",  # WTA: highlights, interviews
        "UCDitdIjOjS9Myza9I21IqzQ",  # Tennis Channel: analysis
    )


SPORT = Tennis()
