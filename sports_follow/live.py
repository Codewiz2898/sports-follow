"""Live now: every game the running sources have on right now, league-wide, whoever follows it: the "Live
now" rail in the welcome and on an empty Today (web/src/components/LiveNow.tsx), and the home page's.

A source that can list what it has on (an adapter's live_now(): Sportmonks' livescores, Lichess's top
broadcasts, PandaScore's running matches) is read by the worker (worker.read_live), at most once every
FRESH seconds and only while fans are looking: the API serves what was last read, kept in Redis for KEEP,
and when that's older than FRESH it queues a read. So a page never waits on a source, and nothing is asked
of one while nobody looks.

Each game comes with the players a fan can follow from it, best first: those already on Sports Follow,
then those the registry knows best (by the source's id; for a game whose source names no players, its
teams' players the registry knows), then the source's own order (a starting eleven, a board's colours).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import Session

from . import bus, search, sports
from .adapters import ADAPTERS, Adapter, LiveGame, PlayerRef, SearchResult
from .models import Athlete

log = logging.getLogger(__name__)

FRESH = 60  # seconds a source's read is served before the next fan's look asks for another
KEEP = 15 * 60  # and how long it's served at all, should no newer read come (a source down)
GAMES = 6  # the most games a sport shows
PLAYERS = 4  # and the most players a game offers


def sources() -> list[Adapter]:
    """The running sources that can list what they have on (live_now)."""
    return [a for a in ADAPTERS.values() if callable(getattr(a, "live_now", None))]


def _key(system: str) -> str:
    return f"live:now:{system}"


def _dump(g: LiveGame) -> dict[str, Any]:
    snap = g.snapshot
    return {
        "source_id": g.source_id,
        "title": g.title,
        "competition": g.competition,
        "start_utc": g.start_utc.isoformat() if g.start_utc else None,
        "teams": g.teams,
        # Shaped like a card's live block (structured._apply_snapshot), so the app draws it the same way.
        "live": {"is_live": True, "event": g.title, "competition": g.competition, "score": snap.score_label, "clock": snap.clock_label,
                 "state": snap.state, "source_url": snap.source_url, "player_stats": []},
        "players": [{"athlete_id": p.athlete_id, "name": p.name, "team": (p.team_names or [""])[0], "league": p.league} for p in g.players],
    }


def read(system: str, now: datetime | None = None) -> int:
    """Ask a source what it has on now and keep it for the API (worker.read_live). Returns how many games."""
    games = cast(Any, ADAPTERS[system]).live_now()
    payload = {"as_of": (now or datetime.now(timezone.utc)).isoformat(), "games": [_dump(g) for g in games]}
    bus.sync_redis().set(_key(system), json.dumps(payload, default=str), ex=KEEP)
    return len(games)


def last_read(system: str) -> dict[str, Any] | None:
    """A source's last read ({"as_of", "games"}), or None: never read, gone stale past KEEP, or Redis can't say."""
    try:
        raw = cast(str | None, bus.sync_redis().get(_key(system)))  # a sync client: never awaitable
    except Exception:
        log.warning("live: %s's games couldn't be read", system, exc_info=True)
        return None
    return json.loads(raw) if raw else None


def _stale(read_: dict[str, Any] | None, now: datetime) -> bool:
    if read_ is None:
        return True
    as_of = datetime.fromisoformat(read_["as_of"])
    return (now - as_of).total_seconds() > FRESH


def _players(db: Session, sport: sports.Sport, system: str, game: dict[str, Any], fan: str | None) -> tuple[list[SearchResult], int]:
    """The players a fan can follow from a game, best first (see the module's notes), at most PLAYERS; and
    how well known its best known player is (Wikipedia editions)."""
    refs = [PlayerRef(system=system, athlete_id=p["athlete_id"], name=p["name"], team_ids=[], team_names=[p.get("team") or ""], league=p.get("league"))
            for p in game.get("players") or []]
    known: dict[str, int] = {}  # athlete id in this source -> Wikipedia editions
    if refs:
        rows = db.execute(select(Athlete.ids[system].astext, Athlete.sitelinks).where(Athlete.sport == sport.key, Athlete.ids[system].astext.in_([r.athlete_id for r in refs]))).all()
        known = {aid: n for aid, n in rows if aid}
    elif game.get("teams"):
        # The source names no players (an esports series): its teams' pros the registry knows by this source's id.
        athletes = db.scalars(
            select(Athlete).where(Athlete.sport == sport.key, Athlete.current, Athlete.ids.has_key(system), Athlete.teams.has_any(array(game["teams"])))
            .order_by(Athlete.sitelinks.desc(), Athlete.id).limit(PLAYERS * 3)
        ).all()
        for a in athletes:
            refs.append(PlayerRef(system=system, athlete_id=str(a.ids[system]), name=a.name, team_ids=[], team_names=[t for t in a.teams or [] if t in game["teams"]][:1], league=a.league))
            known[str(a.ids[system])] = a.sitelinks or 0
    order = {r.athlete_id: i for i, r in enumerate(refs)}
    rows = search.offered_refs(db, sport.label, refs, fan)
    rows.sort(key=lambda r: (r.player_id is None, -known.get(r.athlete_id or "", 0), order.get(r.athlete_id or "", 0)))
    return rows[:PLAYERS], max(known.values(), default=0)


def offer(db: Session, fan: str | None, now: datetime | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """The games on now, by running sport in the order the app lists sports ({"key", "label", "games"}),
    each with the players this fan can follow from it; and the sources whose last read is too old, for the
    caller to queue a read of. Games with players someone already follows, or the best known, come first."""
    now = now or datetime.now(timezone.utc)
    stale: list[str] = []
    by_sport: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for adapter in sources():
        read_ = last_read(adapter.system)
        if _stale(read_, now):
            stale.append(adapter.system)
        for game in (read_ or {}).get("games") or []:
            by_sport.setdefault(adapter.sport, []).append((adapter.system, game))
    out = []
    for sport in sports.ENABLED:
        games = []
        for system, game in by_sport.get(sport.key, []):
            players, fame = _players(db, sport, system, game, fan)
            rank = (-max((p.followers for p in players), default=0), -fame)  # most followed first, then best known
            games.append((rank, {"key": f"{system}:{game['source_id']}", "title": game["title"], "competition": game["competition"],
                                 "start_utc": game.get("start_utc"), "live": game["live"], "players": [search.public(p) for p in players]}))
        games.sort(key=lambda g: g[0])
        if games:
            out.append({"key": sport.key, "label": sport.label, "games": [g for _, g in games[:GAMES]]})
    return out, stale
