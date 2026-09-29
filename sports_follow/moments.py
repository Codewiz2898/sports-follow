"""Moments: what's worth telling a player's fans, found by comparing one live poll with the last.

detect() is pure: it takes the event and the player's line as they were and as they are, and says
what happened in between (a goal, a fifty, a set, the result). Each moment carries a stable key, so
however many polls see the same fifty, fans hear about it once. Its level decides who hears:

    key    goals, assists, red cards, fifties and hundreds, 3+ wickets, out, 30/40/50 points,
           triple-doubles, chess games starting, every result, upsets, injuries, transfers
    minor  coming on, yellow cards, 20 points, double-doubles, each set, milestones in the news

Fans choose per player (follow.alert_rules["level"]): everything, key moments (the default),
results only, or off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

LEVELS = ("everything", "key", "results", "off")
DEFAULT_LEVEL = "key"
NEWS_KINDS = {"injury": "key", "transfer": "key", "retirement": "key", "milestone": "minor"}
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}


@dataclass
class Seen:
    """An event and one player's line in it, at one poll."""

    status: str | None  # scheduled | armed | live | final | postponed
    state: dict[str, Any] = field(default_factory=dict)
    line: dict[str, Any] | None = None  # {"headline": "52* (41)", "stats": [{"label", "value"}]}
    score: str = ""
    clock: str = ""
    lineups: bool = False  # the source lists who's playing (any player has a line)


@dataclass
class Found:
    kind: str
    level: str  # key | minor
    title: str
    body: str
    detail: str = ""  # tells two moments of one kind apart in the key ("50", "goal2", "set3")


def wanted(level: str, moment_level: str, kind: str) -> bool:
    """Whether a fan who chose `level` for a player hears about this moment."""
    if level == "everything":
        return True
    if level == "results":
        return kind == "final"
    if level == "off":
        return False
    return moment_level == "key"


def news_kind(value: str | None) -> str | None:
    """The agent's free-text kind as one of NEWS_KINDS ("Loan" is a transfer), or None."""
    text = (value or "").strip().lower()
    for kind, words in (("injury", ("injur", "fitness", "ruled out")), ("transfer", ("transfer", "loan", "sign", "contract", "move")), ("retirement", ("retire",)), ("milestone", ("milestone", "record", "award"))):
        if text.startswith(kind) or any(w in text for w in words):
            return kind
    return None


# ---------------------------------------------------------------- reading a line


def stat(line: dict[str, Any] | None, label: str) -> str | None:
    for s in (line or {}).get("stats") or []:
        if s.get("label") == label:
            return str(s.get("value"))
    return None


def number(value: str | None) -> int:
    match = re.match(r"\s*(\d+)", value or "")
    return int(match.group(1)) if match else 0


def counts(line: dict[str, Any] | None) -> dict[str, int]:
    """A basketball line's counting stats: points, rebounds and assists from the headline
    ("15 PTS · 3 REB · 10 AST"), steals and blocks from the stats."""
    head = (line or {}).get("headline") or ""
    got = {key: int(n) for n, key in re.findall(r"(\d+)\s*(PTS|REB|AST)", head)}
    got["STL"], got["BLK"] = number(stat(line, "Steals")), number(stat(line, "Blocks"))
    return got


def crossed(before: int, now: int, marks: tuple[int, ...]) -> list[int]:
    return [m for m in marks if before < m <= now]


# ---------------------------------------------------------------- per sport


def detect(sport: str, who: str, athlete_id: str, before: Seen | None, now: Seen) -> list[Found]:
    """What happened to this player between two polls.

    before=None is a first look at a game already under way (the worker just started, or the fan
    followed mid-innings): only a result is reported from it, so nobody hears "reaches 50" for runs
    scored an hour ago.
    """
    sport = (sport or "").lower()
    found: list[Found] = []
    was = before.status if before else None
    head = (now.line or {}).get("headline") or ""
    in_game = now.line is not None and head != "On the bench" and not head.startswith("Did not play")

    if before is not None and now.status == "live" and was in ("scheduled", "armed", "postponed") and (in_game or sport in ("tennis", "chess")):
        found.append(_start(sport, who, now))
    if now.status == "final" and was != "final" and not _left_out(sport, now):
        found.append(_final(sport, who, now))
        found += _upset(sport, who, athlete_id, now)
    if before is None:
        return found
    rules = {"football": _football, "soccer": _football, "cricket": _cricket, "basketball": _basketball, "tennis": _tennis}.get(sport)
    if rules and now.line is not None:
        found += rules(who, before.line, now)
    return found


def _left_out(sport: str, now: Seen) -> bool:
    """Known not to be in the game: the line-ups are out and the player isn't in them (a national
    team game they weren't called up for). The card says the same ("Not in the matchday squad").
    In cricket no line only means they haven't batted or bowled, so it doesn't count."""
    return sport in ("football", "soccer", "basketball") and now.lineups and now.line is None


def _start(sport: str, who: str, now: Seen) -> Found:
    if sport == "chess":
        rnd = now.state.get("round") or "Their game"
        opp = stat(now.line, "Opponent")
        return Found("start", "key", f"{who} is playing", f"{rnd}{f' · vs {opp}' if opp else ''}")
    if sport == "tennis":
        return Found("start", "key", f"{who}'s match has started", " · ".join(x for x in (now.state.get("round"), now.state.get("tournament")) if x))
    return Found("start", "key", f"{who}'s game has started", " · ".join(x for x in (now.score, now.clock) if x))


def _final(sport: str, who: str, now: Seen) -> Found:
    head = (now.line or {}).get("headline") or ""
    if sport in ("tennis", "chess"):
        return Found("final", "key", f"{who}: {head or 'result'}", now.score)
    result = now.clock if sport == "cricket" else now.score
    return Found("final", "key", f"{who}: {result or 'full time'}", head)


def _upset(sport: str, who: str, athlete_id: str, now: Seen) -> list[Found]:
    """A tennis win over a better-seeded player (or any seed, for an unseeded one)."""
    if sport != "tennis":
        return []
    players = now.state.get("players") or []
    me = next((p for p in players if str(p.get("id")) == athlete_id), None)
    other = next((p for p in players if p is not me), None)
    if not (me and me.get("winner") and other and other.get("seed")) or (me.get("seed") and me["seed"] < other["seed"]):
        return []
    return [Found("upset", "key", f"Upset: {who} beats [{other['seed']}] {other.get('name')}", now.score)]


def _football(who: str, before: dict[str, Any] | None, now: Seen) -> list[Found]:
    found = []
    where = " · ".join(x for x in (now.score, now.clock) if x)
    for label, kind, level, title in (("Goals", "goal", "key", "Goal: {who}"), ("Assists", "assist", "key", "Assist: {who}"), ("Red", "red", "key", "Red card: {who}"), ("Yellow", "yellow", "minor", "Yellow card: {who}")):
        was, got = number(stat(before, label)), number(stat(now.line, label))
        for n in range(was + 1, got + 1):
            found.append(Found(kind, level, title.format(who=who) + (f" ({n})" if n > 1 and kind in ("goal", "assist") else ""), where, f"{kind}{n}"))
    if (before or {}).get("headline") == "On the bench" and (now.line or {}).get("headline") not in (None, "On the bench"):
        found.append(Found("on", "minor", f"{who} comes on", where))
    return found


def _cricket(who: str, before: dict[str, Any] | None, now: Seen) -> list[Found]:
    found = []
    where = " · ".join(x for x in (now.score,) if x)
    runs_was, runs_now = stat(before, "Runs"), stat(now.line, "Runs")
    if runs_now is not None:
        balls = stat(now.line, "Balls")
        innings = f"{runs_now} ({balls})" if balls else runs_now
        for mark in crossed(number(runs_was), number(runs_now), (50, 100, 150, 200)):
            found.append(Found("hundred" if mark >= 100 else "fifty", "key", f"{who} reaches {mark}", f"{innings} · {where}".strip(" ·"), str(mark)))
        out_now = not runs_now.endswith("*") and stat(now.line, "Out") is not None
        out_was = runs_was is not None and not runs_was.endswith("*") and stat(before, "Out") is not None
        if out_now and not out_was:
            found.append(Found("out", "key", f"{who} out for {number(runs_now)}", f"{innings} · {stat(now.line, 'Out')}", "out"))
    wickets_was, wickets_now = number(stat(before, "Wickets")), number(stat(now.line, "Wickets"))
    for mark in crossed(wickets_was, wickets_now, (3, 4, 5, 6, 7, 8, 9, 10)):
        figures = f"{wickets_now}/{stat(now.line, 'Runs conceded') or '?'} ({stat(now.line, 'Overs') or '?'} ov)"
        found.append(Found("wickets", "key", f"{who} takes {mark} wickets", f"{figures} · {where}".strip(" ·"), f"w{mark}"))
    return found


def _basketball(who: str, before: dict[str, Any] | None, now: Seen) -> list[Found]:
    found = []
    was, got = counts(before), counts(now.line)
    where = " · ".join(x for x in ((now.line or {}).get("headline"), now.score, now.clock) if x)
    for mark in crossed(was.get("PTS", 0), got.get("PTS", 0), (20, 30, 40, 50, 60)):
        found.append(Found("points", "key" if mark >= 30 else "minor", f"{who}: {mark} points", where, str(mark)))
    tens_was = sum(1 for k in ("PTS", "REB", "AST", "STL", "BLK") if was.get(k, 0) >= 10)
    tens_now = sum(1 for k in ("PTS", "REB", "AST", "STL", "BLK") if got.get(k, 0) >= 10)
    if tens_was < 2 <= tens_now:
        found.append(Found("double", "minor", f"{who}: double-double", where))
    if tens_was < 3 <= tens_now:
        found.append(Found("triple", "key", f"{who}: triple-double", where))
    return found


def _tennis(who: str, before: dict[str, Any] | None, now: Seen) -> list[Found]:
    def sets(line: dict[str, Any] | None) -> tuple[int, int]:
        won, _, lost = (stat(line, "Sets") or "0–0").partition("–")
        return number(won), number(lost)

    (won_was, lost_was), (won_now, lost_now) = sets(before), sets(now.line)
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
