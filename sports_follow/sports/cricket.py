"""Cricket: a line's innings, fifties, hundreds and wicket hauls in each, the XI before the start, results
named by the side that won, and form over a run of matches."""

from __future__ import annotations

import re
from typing import Any

from ..naming import team_key
from .base import ORDINAL, Found, Line, Seen, Sport, crossed, joined, number, stat


def _int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


_BAT = re.compile(r"(\d+)(\*?) \((\d+)\)")
_BOWL = re.compile(r"(\d+)/(\d+) \(([\d.]+) ov\)")


def innings(headline: str | None) -> tuple[list[tuple[int, bool, int]], list[tuple[int, int, str]]]:
    """Every innings in a cricket line's headline ("103 (152) & 48* (60) · 3/45 (20 ov)"): batting as
    (runs, out, balls), bowling as (wickets, runs conceded, overs)."""
    bats, bowls = [], []
    for part in re.split(r" & | · ", headline or ""):
        if m := _BAT.fullmatch(part):
            bats.append((int(m[1]), not m[2], int(m[3])))
        elif m := _BOWL.fullmatch(part):
            bowls.append((int(m[1]), int(m[2]), m[3]))
    return bats, bowls


def batting_bowling(lines: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Runs, average, strike rate, highest, 100s/50s, wickets and economy over some matches' lines
    ({"headline", "stats"}), every innings counted: a line's stats are only its latest innings, so a
    Test's two come from the headline."""
    runs = balls = outs = batted = hundreds = fifties = 0
    best = None
    wickets = conceded = 0
    balls_bowled = 0
    for line in lines:
        bats, bowls = innings(line.get("headline"))
        values = {s["label"]: s["value"] for s in line.get("stats") or []}
        if not bats and "Runs" in values:
            bats = [(_int(values["Runs"].rstrip("*")), not values["Runs"].endswith("*"), _int(values.get("Balls")))]
        if not bowls and "Wickets" in values:
            bowls = [(_int(values["Wickets"]), _int(values.get("Runs conceded")), str(values.get("Overs", "0")))]
        for r, out, b in bats:
            batted += 1
            runs += r
            balls += b
            outs += out
            hundreds += r >= 100
            fifties += 50 <= r < 100
            best = max(best or 0, r)
        for w, c, overs in bowls:
            wickets += w
            conceded += c
            whole, _, part = overs.partition(".")
            balls_bowled += _int(whole) * 6 + _int(part or 0)
    stats = []
    if batted:
        stats += [
            {"label": "Runs", "value": str(runs)},
            {"label": "Average", "value": f"{runs / outs:.2f}" if outs else "–"},
            {"label": "Strike rate", "value": f"{runs * 100 / balls:.1f}" if balls else "–"},
            {"label": "Highest", "value": str(best)},
            {"label": "100s / 50s", "value": f"{hundreds} / {fifties}"},
        ]
    if balls_bowled:
        stats += [
            {"label": "Wickets", "value": str(wickets)},
            {"label": "Economy", "value": f"{conceded * 6 / balls_bowled:.2f}"},
        ]
    return stats


# ---------------------------------------------------------------- the sport


def _another(was: list[Any], now: list[Any]) -> bool:
    """Whether the player has started another innings since the last poll: the line has more innings,
    and the ones it had are unchanged. A last line that read otherwise (one holding only the latest
    innings) doesn't count, so milestones already passed aren't told late."""
    return len(now) > len(was) and now[:len(was)] == was


def _nth(n: int) -> tuple[str, str]:
    """The player's innings in a cricket moment: the detail's prefix ("i2:"; none for their first, as
    before) and its name in the body ("2nd innings")."""
    if n < 2:
        return "", ""
    return f"i{n}:", f"{ORDINAL.get(n, f'{n}th')} innings"


def winner_outcome(summary: str, teams: list[str]) -> str | None:
    """The winner a result names ("India won by 8 wkts", "Match tied (India won the Super Over)"), as
    one of the player's teams by whole name: "India A" and "Mumbai Indians" aren't India. The app's
    outcome() in web/src/format.ts reads results the same way."""
    winner = re.search(r"(?:^|\()([^()]+?) won\b", summary, re.I)
    if not winner:
        return "D" if any(w in summary.lower() for w in ("drawn", "tied", "no result")) else None
    named = team_key(winner.group(1))
    if named and any(team_key(t) == named for t in teams):
        return "W"
    return "L" if teams else None


class Cricket(Sport):
    key = "cricket"
    label = "Cricket"
    kinds = ("cricket",)
    team = True
    wikidata = {"espn_cricket": "P2697"}
    athletes = 19_745
    no_line = "Did not bat or bowl"
    nation_first = True  # India before Royal Challengers Bengaluru
    games = ("match", "matches")
    draws = True

    def lineup_title(self, who: str, starting: bool) -> str:
        return f"{who} is in the XI" if starting else f"{who} is named a substitute"

    def result_text(self, score: str, clock: str) -> str:
        return clock  # "India won by 57 runs"

    def during(self, who: str, athlete_id: str, before: Seen, now: Seen) -> list[Found]:
        """Fifties, hundreds, out and wicket hauls in the player's latest innings: a line's stats are its
        latest batting and bowling innings, and its headline lists every innings ("103 (152) & 48* (60)").
        In a Test the second innings starts again from nothing, and its moments say which innings they
        are in the detail ("i2:100"), so they aren't taken for the first's ("100")."""
        if now.line is None:
            return []
        was_line = before.line
        found = []
        (bats_was, bowls_was), (bats_now, bowls_now) = (innings((line or {}).get("headline")) for line in (was_line, now.line))
        runs_was, runs_now = stat(was_line, "Runs"), stat(now.line, "Runs")
        if _another(bats_was, bats_now):
            runs_was = None  # the last poll's runs were the innings before
        if runs_now is not None:
            balls = stat(now.line, "Balls")
            batting = f"{runs_now} ({balls})" if balls else runs_now
            pre, nth = _nth(len(bats_now))
            for mark in crossed(number(runs_was), number(runs_now), (50, 100, 150, 200)):
                found.append(Found("hundred" if mark >= 100 else "fifty", "key", f"{who} reaches {mark}", joined(batting, nth, now.score), f"{pre}{mark}", 95 if mark >= 150 else 0))
            out_now = not runs_now.endswith("*") and stat(now.line, "Out") is not None
            out_was = runs_was is not None and not runs_was.endswith("*") and stat(was_line, "Out") is not None
            if out_now and not out_was:
                runs = number(runs_now)
                found.append(Found("out", "key", f"{who} out for {runs}", joined(batting, nth, stat(now.line, "Out")), f"{pre}out", 75 if runs >= 50 else 65 if runs == 0 else 60))
        wickets_was, wickets_now = number(stat(was_line, "Wickets")), number(stat(now.line, "Wickets"))
        if _another(bowls_was, bowls_now):
            wickets_was = 0
        pre, nth = _nth(len(bowls_now))
        for mark in crossed(wickets_was, wickets_now, (3, 4, 5, 6, 7, 8, 9, 10)):
            figures = f"{wickets_now}/{stat(now.line, 'Runs conceded') or '?'} ({stat(now.line, 'Overs') or '?'} ov)"
            found.append(Found("wickets", "key", f"{who} takes {mark} wickets", joined(figures, nth, now.score), f"{pre}w{mark}", 90 if mark >= 5 else 75 if mark == 4 else 70))
        return found

    def outcome(self, result: str, teams: list[str]) -> str | None:
        # "India won by 5 wickets": a win for an India player, a loss for their opponents'.
        return winner_outcome(result, teams)

    def form(self, items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
        return batting_bowling(lines)


SPORT = Cricket()
