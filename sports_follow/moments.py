"""Moments: what's worth telling a player's fans, found by comparing one live poll with the last.

detect() is pure: it takes the event and the player's line as they were and as they are, and says
what happened in between (a goal, a fifty, a set, the result). Each moment carries a stable key, so
however many polls see the same fifty, fans hear about it once. Its level decides who hears:

    key    goals, assists, red cards, fifties and hundreds, 3+ wickets, out, 30/40/50 points,
           triple-doubles, chess games starting, a chess player winning, in trouble or turning a
           game around, the line-up (starting or on the bench), esports series and lobbies
           starting, a chicken dinner (Booyah), going top of a lobby, every result, upsets,
           injuries, transfers, a goal ruled out
    minor  coming on, yellow cards, 20 points, double-doubles, each set, each esports map (with a Dota
           player's kills, deaths and assists once OpenDota has them), each
           battle royale game's placement, a chess player better, worse or level again, milestones
           in the news

Fans choose per player (follow.alert_rules["level"]): everything, key moments (the default),
results only, or off.

What counts as a moment in each sport is that sport's (sports/: start, final, during, left_out); this
module runs them in order and keeps what every sport shares: line-ups, levels, priorities.

A source can take a moment back: VAR rules out a goal fans were told about. The sport says so
(taken_back) with a correction, which replaces the game's notification for the fans who were sent the
moment it takes back, and reaches no one else (alerts.py).

Each moment also has a priority, 0 to 100: how much it's worth interrupting someone for. A hundred
outranks a fifty, a late equaliser outranks a fourth goal in a rout, a duck outranks being out for 20.
alerts.py uses it to pick which moment leads a notification and which may still buzz once a fan's
phone has buzzed enough.
"""

from __future__ import annotations

from . import sports
from .sports.base import Found, Seen, crossed, number, stat

__all__ = ["DEFAULT_LEVEL", "LEVELS", "Found", "Seen", "crossed", "detect", "news_kind", "number", "stat", "wanted"]

LEVELS = ("everything", "key", "results", "off")
DEFAULT_LEVEL = "key"
NEWS_KINDS = {"injury": "key", "transfer": "key", "retirement": "key", "milestone": "minor"}
NEWS_PRIORITY = {"injury": 75, "transfer": 70, "retirement": 90, "milestone": 40}
# A moment's priority unless its rule sets one (a hundred over a fifty, a late equaliser over a goal).
PRIORITY = {
    "final": 80, "start": 40, "lineup": 50, "goal": 85, "assist": 65, "red": 80, "yellow": 20, "on": 30,
    "fifty": 70, "hundred": 90, "out": 60, "wickets": 70, "points": 30, "double": 30, "triple": 85,
    "set": 30, "map": 30, "kda": 30, "game": 30, "dinner": 90, "lead": 75, "upset": 90, "swing": 30, "news": 60,
    "correction": 85,
}


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
    """The agent's kind if it is one of NEWS_KINDS ("Injury" is), else None. Matched whole: by substring,
    "contract extension" was a transfer and "record" a milestone."""
    text = (value or "").strip().lower()
    return text if text in NEWS_KINDS else None


def detect(sport: str, who: str, athlete_id: str, before: Seen | None, now: Seen, told: list[tuple[str, str]] | None = None) -> list[Found]:
    """What happened to this player between two polls.

    before=None is a first look at a game already under way (the worker just started, or the fan
    followed mid-innings): only a result is reported from it, so nobody hears "reaches 50" for runs
    scored an hour ago. A line-up is still news on a first look, as long as the game hasn't started.

    `told` is what was found for the player in this game before (each moment's kind and detail, oldest
    first, as notify.told reads them back): what the sport may take back.
    """
    rules = sports.of(sport)
    found: list[Found] = []
    was = before.status if before else None
    head = (now.line or {}).get("headline") or ""
    in_game = now.line is not None and head != "On the bench" and not head.startswith("Did not play")

    found += _lineup(rules, who, now)
    if before is not None and now.status == "live" and was in ("scheduled", "armed", "postponed") and (in_game or rules.starts_unlisted(now)):
        found.append(rules.start(who, now))
    if now.status == "final" and was != "final" and not rules.left_out(now):
        found.append(rules.final(who, now))
        found += rules.after_final(who, athlete_id, now)
    if before is not None:
        found += rules.during(who, athlete_id, before, now)
        found += rules.taken_back(who, athlete_id, before, now, told or [])
    for f in found:
        f.priority = f.priority or PRIORITY.get(f.kind, 50)
    return found


def _lineup(rules: sports.Sport, who: str, now: Seen) -> list[Found]:
    """The team sheet is out, before the start: the player starts, or is on the bench. Its key is the
    role, so a late change ("now on the bench") is news too. Not being on the sheet isn't told: a
    national team game shows up for every player of that country, called up or not."""
    if now.status not in ("scheduled", "armed") or now.role not in ("starting", "bench"):
        return []
    starting = now.role == "starting"
    body = " · ".join(x for x in (rules.matchup(now.state), "line-ups are out") if x)
    return [Found("lineup", "key", rules.lineup_title(who, starting), body[:1].upper() + body[1:], now.role, 50 if starting else 45)]
