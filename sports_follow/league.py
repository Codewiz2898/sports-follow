"""League games: the big results and the big games coming up in the competitions the running sources cover,
whoever follows them. Today's Results and Coming up rails end in them (web/src/components/League.tsx), and
a fan who follows nobody sees only them: GET /api/league, the same for every fan.

A source that can list its competitions' games between two days (an adapter's schedule()) is read by the
worker (worker.read_league), at most once every FRESH seconds and only while fans are looking: the API
serves what was last read, from Redis, and when that's older than FRESH it asks for a read, once every
ASK seconds whatever came of the last. A read covers BACK days before today to AHEAD days after (UTC),
and each game in it is ranked by its sport (Sport.notable), which leaves out the ones that aren't big. A
sport turned off isn't read: its sources don't run, and a source of several sports (esports games) reads
only those that run. Of a sport's sources, the first that lists a schedule is read, or every one where they
sit side by side (Sport.side_by_side: each covers its own games); a source of several sports is read once.

The API serves the finished games since yesterday, or of the latest day that has some (days where the fan
is: the app says its UTC offset), and those to come in the next AHEAD days, at most RESULTS and COMING. In
each list the sports take turns, so one sport's busy week never fills it: each sport's biggest game, the
biggest of those first, then each one's next biggest, and so on; a group of sports (Sport.group: every
esports game) takes one turn, and its sports take turns within it the same way, so a busy esports week of
many games doesn't fill it either. Today shows Coming up's first few soonest first (web/src/today.ts).
Each game says which of Sports Follow's events it is (event_ids, by the source's fixture id), so Today
shows a game a fan's player is in once, as theirs; and its match page's address (match), where it has one: a
game its source can read on its own (an adapter's match()), or one of those events. A game listed here counts
as named for its match page (match.py), so opening it may cost its source a read, once. A big game coming
up within a day, in a sport that asks fans to call it, is read ahead for its questions (predictions.prepare),
so a fan who follows nobody can call it from its card.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import bus, sports
from .adapters import ADAPTERS, Adapter, Fixture
from .models import Event
from .sports import Notable

log = logging.getLogger(__name__)

FRESH = 3 * 3600  # seconds a source's read is served before a fan's look asks for another: results come in all day
ASK = 3600  # and at most this often, so a source that's down isn't asked on every look
SERVE = 2 * 86400  # a read older than this isn't served: a source down for days
KEEP = 3 * 86400  # and Redis drops it after this
BACK, AHEAD = 3, 14  # days a read covers before today (the latest day with results) and after it (Coming up's)
RESULTS, COMING = 40, 40  # the most games each list serves
CACHE = 60  # seconds the API keeps what it served (cached)


def sources() -> list[Adapter]:
    """The running sources whose schedule is read: each running sport's first that lists one, or every one
    for a sport whose sources sit side by side."""
    out: list[Adapter] = []
    for sport in sports.ENABLED:
        theirs = [a for a in ADAPTERS.values() if sport.key in a.sports and callable(getattr(a, "schedule", None))]
        out += [a for a in (theirs if sport.side_by_side else theirs[:1]) if a not in out]
    return out


def _key(system: str) -> str:
    return f"league:games:{system}"


def _dump(system: str, sport: str, f: Fixture, n: Notable) -> dict[str, Any]:
    return {
        "key": f"{system}:{f.source_id}",  # an event's key (structured.upsert_event)
        "sport": sport,
        "title": f.title,
        "competition": f.competition,
        "detail": f.detail,
        "start_utc": f.start_utc.isoformat() if f.start_utc else None,
        "status": f.status,
        "rank": n.rank,
        "word": n.word,
        "line": n.line,
        "why": n.why,
    }


def read(system: str, now: datetime | None = None) -> int:
    """Ask a source for its competitions' games from BACK days ago to AHEAD days on, keep those its sport
    ranks, finished or to come (a game on now is Live now's), for the API (worker.read_league). Returns how
    many it kept: none from a source turned off since the read was asked for (the admin panel)."""
    adapter = ADAPTERS.get(system)
    if adapter is None:
        return 0
    now = now or datetime.now(timezone.utc)
    today = now.date()
    fixtures = cast(Any, adapter).schedule(today - timedelta(days=BACK), today + timedelta(days=AHEAD))
    one = adapter.sports[0] if len(adapter.sports) == 1 else ""  # a source of several names each game's sport
    games = []
    for f in fixtures:
        key = f.sport or one
        if key and f.status in ("final", "scheduled") and f.start_utc and (n := sports.of(key).notable(f)) is not None:
            games.append(_dump(system, key, f, n))
    bus.sync_redis().set(_key(system), json.dumps({"as_of": now.isoformat(), "games": games}, default=str), ex=KEEP)
    return len(games)


def last_read(system: str) -> dict[str, Any] | None:
    """A source's last read ({"as_of", "games"}), or None: never read, dropped, or Redis can't say."""
    try:
        raw = cast(str | None, bus.sync_redis().get(_key(system)))  # a sync client: never awaitable
    except Exception:
        log.warning("league: %s's games couldn't be read", system, exc_info=True)
        return None
    return json.loads(raw) if raw else None


def _age(read_: dict[str, Any] | None, now: datetime) -> float:
    return (now - datetime.fromisoformat(read_["as_of"])).total_seconds() if read_ else float("inf")


def _start(game: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(game["start_utc"])


def _results(games: list[dict[str, Any]], now: datetime, tz: int = 0) -> tuple[list[dict[str, Any]], date | None]:
    """The finished games since yesterday, or failing that of the latest day that had some, and that day
    (None for since yesterday), the days as a fan tz minutes east of UTC has them; in turns, biggest first,
    then newest."""
    def day_of(at: datetime) -> date:
        return (at + timedelta(minutes=tz)).date()

    finished = [g for g in games if g["status"] == "final" and _start(g) <= now]
    since = day_of(now) - timedelta(days=1)
    shown = [g for g in finished if day_of(_start(g)) >= since]
    day = None
    if not shown and finished:
        day = max(day_of(_start(g)) for g in finished)
        shown = [g for g in finished if day_of(_start(g)) == day]
    shown.sort(key=lambda g: (-g["rank"], -_start(g).timestamp()))
    return _turns(shown)[:RESULTS], day


def _coming(games: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """The games still to start in the next AHEAD days, in turns, biggest first, then soonest."""
    ahead = [g for g in games if g["status"] == "scheduled" and now < _start(g) <= now + timedelta(days=AHEAD)]
    ahead.sort(key=lambda g: (-g["rank"], _start(g).timestamp()))
    return _turns(ahead)[:COMING]


def _turns(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Games, best first, as their sports take turns: every sport's best, then every sport's next best, and
    so on, a round in the order given; a group of sports (every esports game) takes one turn, which goes
    round its sports the same way."""
    return _rounds(games, lambda g: sports.of(g["sport"]).group or g["sport"], lambda g: g["sport"])


def _rounds(items: list[dict[str, Any]], *by: Callable[[dict[str, Any]], str]) -> list[dict[str, Any]]:
    if not by:
        return items
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(by[0](item), []).append(item)
    queues = [_rounds(group, *by[1:]) for group in groups.values()]
    at = {id(item): i for i, item in enumerate(items)}
    out: list[dict[str, Any]] = []
    for n in range(max(map(len, queues), default=0)):
        out += sorted((q[n] for q in queues if n < len(q)), key=lambda item: at[id(item)])
    return out


def _address(key: str, event: bool) -> str | None:
    """A game's match page address (after /match/), where its page has more than the card says: its source
    can read the game (match()), or it's an event here, with what was stored of it."""
    system, _, game = key.partition(":")
    return f"{system}/{game}" if event or callable(getattr(ADAPTERS.get(system), "match", None)) else None


def _shown(game: dict[str, Any], events: dict[str, int]) -> dict[str, Any]:
    return {**{k: v for k, v in game.items() if k not in ("status", "group")}, "event_ids": [events[game["key"]]] if game["key"] in events else [],
            "match": _address(game["key"], game["key"] in events)}


def _gather(now: datetime) -> tuple[list[dict[str, Any]], list[str], tuple[str | None, ...]]:
    """Every source's last read still served, those over FRESH old, and when each was read."""
    stale: list[str] = []
    games: list[dict[str, Any]] = []
    seen: list[str | None] = []
    for adapter in sources():
        read_ = last_read(adapter.system)
        age = _age(read_, now)
        seen.append((read_ or {}).get("as_of"))
        if age > FRESH:
            stale.append(adapter.system)
        if age <= SERVE:
            games += [g for g in (read_ or {}).get("games") or [] if sports.on(g.get("sport"))]  # a game turned off since: not served
    return games, stale, tuple(seen)


def _ranked(games: list[dict[str, Any]], now: datetime, tz: int) -> dict[str, Any]:
    results, day = _results(games, now, tz)
    return {"day": day.isoformat() if day else None, "results": results, "coming": _coming(games, now)}


def _with_events(db: Session, ranked: dict[str, Any]) -> dict[str, Any]:
    """The answer, each game with the ids of the event it is here: looked up afresh every time, so a game a
    fan's player is in from a moment ago (a follow from Today) is theirs."""
    keys = {g["key"] for g in (*ranked["results"], *ranked["coming"])}
    events = {key: event_id for key, event_id in db.execute(select(Event.key, Event.id).where(Event.key.in_(keys)))} if keys else {}
    return {"results": {"day": ranked["day"], "games": [_shown(g, events) for g in ranked["results"]]},
            "coming": {"games": [_shown(g, events) for g in ranked["coming"]]}}


def coming(now: datetime | None = None) -> list[dict[str, Any]]:
    """The big games coming up as the API serves them, without their events: the games whose questions are made
    ahead (predictions.prepare) and that Today's calls cover (predictions.today), whoever follows them."""
    now = now or datetime.now(timezone.utc)
    return _coming(_gather(now)[0], now)


def offer(db: Session, now: datetime | None = None, tz: int = 0) -> tuple[dict[str, Any], list[str]]:
    """The big results ({"day", "games"}: day is null for since yesterday, days as a fan tz minutes east of
    UTC has them) and the big games coming up ({"games"}), each game with the ids of the event it is here;
    and the sources whose last read is over FRESH old, for the caller to ask a read of."""
    now = now or datetime.now(timezone.utc)
    games, stale, _ = _gather(now)
    return _with_events(db, _ranked(games, now, tz)), stale


_served: dict[int, dict[str, Any]] = {}  # by UTC offset: when, of which reads, the ranked games


def cached(db: Session, tz: int = 0) -> tuple[dict[str, Any], list[str]]:
    """offer(), its games ranked for every fan in a time zone once a CACHE (and anew as soon as a source's
    read is), their events looked up every time."""
    now = datetime.now(timezone.utc)
    games, stale, seen = _gather(now)
    kept = _served.get(tz)
    if kept is None or kept["seen"] != seen or time.monotonic() - kept["at"] > CACHE:
        kept = _served[tz] = {"at": time.monotonic(), "seen": seen, "value": _ranked(games, now, tz)}
    return _with_events(db, cast(dict[str, Any], kept["value"])), stale
