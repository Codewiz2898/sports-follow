"""League games: the big results and the big games coming up in the competitions the running sources cover,
whoever follows them. Today's Results and Coming up rails end in them (web/src/components/League.tsx), and
a fan who follows nobody sees only them: GET /api/league, the same for every fan.

A source that can list its competitions' games between two days (an adapter's schedule()) is read by the
worker (worker.read_league), at most once every FRESH seconds and only while fans are looking: the API
serves what was last read, from Redis, and when that's older than FRESH it asks for a read, once every
ASK seconds whatever came of the last. A read covers BACK days before today to AHEAD days after (UTC),
and each game in it is ranked by its sport (Sport.notable), which leaves out the ones that aren't big. A
sport turned off isn't read: its sources don't run. Of a sport's sources, the first that lists a schedule
is read, or every one where they sit side by side (Sport.side_by_side: each covers its own games).

The API serves the finished games since yesterday, or of the latest day that has some (days where the fan
is: the app says its UTC offset), and those to come in the next AHEAD days, each list biggest first of
every sport together, at most RESULTS and COMING.
Each game says which of Sports Follow's events it is (event_ids, by the source's fixture id), so Today
shows a game a fan's player is in once, as theirs.
"""

from __future__ import annotations

import json
import logging
import time
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
        theirs = [a for a in ADAPTERS.values() if a.sport == sport.key and callable(getattr(a, "schedule", None))]
        out += theirs if sport.side_by_side else theirs[:1]
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
    many it kept."""
    adapter = ADAPTERS[system]
    sport = sports.of(adapter.sport)
    now = now or datetime.now(timezone.utc)
    today = now.date()
    fixtures = cast(Any, adapter).schedule(today - timedelta(days=BACK), today + timedelta(days=AHEAD))
    games = []
    for f in fixtures:
        if f.status in ("final", "scheduled") and f.start_utc and (n := sport.notable(f)) is not None:
            games.append(_dump(system, adapter.sport, f, n))
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
    (None for since yesterday), the days as a fan tz minutes east of UTC has them; biggest first, then newest."""
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
    return shown[:RESULTS], day


def _coming(games: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """The games still to start in the next AHEAD days, biggest first, then soonest."""
    ahead = [g for g in games if g["status"] == "scheduled" and now < _start(g) <= now + timedelta(days=AHEAD)]
    ahead.sort(key=lambda g: (-g["rank"], _start(g).timestamp()))
    return ahead[:COMING]


def _shown(game: dict[str, Any], events: dict[str, int]) -> dict[str, Any]:
    return {**{k: v for k, v in game.items() if k != "status"}, "event_ids": [events[game["key"]]] if game["key"] in events else []}


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
            games += (read_ or {}).get("games") or []
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
