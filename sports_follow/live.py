"""Live now: every game the running sources have on right now, league-wide, whoever follows it: the "Live
now" rail in the welcome and on Today (web/src/components/LiveNow.tsx), and the home page's.

A source that can list what it has on (an adapter's live_now()) is read by the worker (worker.read_live),
at most once every FRESH seconds and only while fans are looking: the API serves what was last read, from
Redis, and when that's older than FRESH it asks for a read, once a minute whatever came of the last. So a
page never waits on a source, nothing is asked of one while nobody looks, and one that's down isn't asked
on every look. A read older than SERVE isn't served: its games may be over.

The read also ranks each game's players, so the API's work stays small: the registry's best known first
(by the source's id, or by name in the sport), then the order the source puts them forward in; an athlete
an operator removed is left out. A game whose source names no players offers its teams' pros the registry
knows, in that game's title (league). The API puts those already on Sports Follow first, at most PLAYERS
a game, and the games with the most followed, then the best known, players first, at most GAMES a sport. Each game says
which of Sports Follow's events it is (event_ids, by the source's fixture ids), so Today shows a game a fan's
player is in once, as theirs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import Session

from . import bus, registry, search, sports
from .adapters import ADAPTERS, Adapter, LiveGame, PlayerRef
from .db import session
from .models import Athlete, Event

log = logging.getLogger(__name__)

FRESH = 60  # seconds a source's read is served before a fan's look asks for another
SERVE = 3 * FRESH  # a read older than this isn't served: a source down, or nobody looked for a while
KEEP = 15 * 60  # and Redis drops it after this
GAMES = 6  # the most games a sport shows
PLAYERS = 4  # and the most players a game offers
KEPT = 3 * PLAYERS  # players kept a game by a read: room for those on Sports Follow to come first
READ_GAMES = 30  # games kept a read, best known first


def sources() -> list[Adapter]:
    """The running sources that can list what they have on (live_now)."""
    return [a for a in ADAPTERS.values() if callable(getattr(a, "live_now", None))]


def _key(system: str) -> str:
    return f"live:now:{system}"


def _ranked(db: Session, sport: str, system: str, game: LiveGame, blocked: set[tuple[str, str]]) -> tuple[list[dict[str, Any]], int]:
    """A game's players, best known first, then the source's order, without anyone removed; and its best
    known player's fame (Wikipedia editions)."""
    refs = list(game.players)
    fame: dict[str, int] = {}
    if refs:
        by_id = db.execute(select(Athlete.ids[system].astext, Athlete.sitelinks).where(Athlete.sport == sport, Athlete.ids.has_key(system),
                                                                                       Athlete.ids[system].astext.in_([r.athlete_id for r in refs]))).all()
        by_name = db.execute(select(func.lower(Athlete.name), func.max(Athlete.sitelinks)).where(Athlete.sport == sport, Athlete.current,
                                                                                                 func.lower(Athlete.name).in_([r.name.lower() for r in refs])).group_by(func.lower(Athlete.name))).all()
        names = {n: s or 0 for n, s in by_name}
        ids = {aid: s or 0 for aid, s in by_id if aid}
        fame = {r.athlete_id: max(ids.get(r.athlete_id, 0), names.get(r.name.lower(), 0)) for r in refs}
    elif game.teams:
        # The source names no players (an esports series): its teams' pros the registry knows by this source's
        # id, in this game's title, so a same-named team in another title isn't taken for them.
        athletes = db.scalars(
            select(Athlete).where(Athlete.sport == sport, Athlete.current, Athlete.ids.has_key(system), Athlete.teams.has_any(array(game.teams)),
                                  *([Athlete.league == game.league] if game.league else []))
            .order_by(Athlete.sitelinks.desc(), Athlete.id).limit(KEPT)
        ).all()
        for a in athletes:
            refs.append(PlayerRef(system=system, athlete_id=str(a.ids[system]), name=a.name, team_ids=[], team_names=[t for t in a.teams or [] if t in game.teams][:1], league=a.league))
            fame[str(a.ids[system])] = a.sitelinks or 0
    order = {r.athlete_id: i for i, r in enumerate(refs)}
    kept = sorted((r for r in refs if (system, r.athlete_id) not in blocked), key=lambda r: (-fame.get(r.athlete_id, 0), order[r.athlete_id]))[:KEPT]
    players = [{"athlete_id": r.athlete_id, "name": r.name, "team": (r.team_names or [""])[0], "league": r.league or game.league, "fame": fame.get(r.athlete_id, 0)} for r in kept]
    return players, max((p["fame"] for p in players), default=0)


def _dump(game: LiveGame, players: list[dict[str, Any]], fame: int) -> dict[str, Any]:
    snap = game.snapshot
    return {
        "source_id": game.source_id,
        "fixtures": game.fixtures or [game.source_id],  # an event's key is "system:fixture" (structured.upsert_event)
        "title": game.title,
        "competition": game.competition,
        "start_utc": game.start_utc.isoformat() if game.start_utc else None,
        # Shaped like a card's live block (structured._apply_snapshot), so the app draws it the same way.
        "live": {"is_live": True, "event": game.title, "competition": game.competition, "score": snap.score_label, "clock": snap.clock_label,
                 "state": snap.state, "source_url": snap.source_url, "player_stats": []},
        "players": players,
        "fame": fame,
    }


def read(system: str, now: datetime | None = None) -> int:
    """Ask a source what it has on now, rank each game's players, and keep it for the API
    (worker.read_live). Returns how many games."""
    adapter = ADAPTERS[system]
    games = cast(Any, adapter).live_now()
    with session() as db:
        blocked = registry.blocked(db)
        dumped = [_dump(g, *_ranked(db, adapter.sport, system, g, blocked)) for g in games]
    dumped.sort(key=lambda g: -g["fame"])
    payload = {"as_of": (now or datetime.now(timezone.utc)).isoformat(), "games": dumped[:READ_GAMES]}
    bus.sync_redis().set(_key(system), json.dumps(payload, default=str), ex=KEEP)
    return len(games)


def last_read(system: str) -> dict[str, Any] | None:
    """A source's last read ({"as_of", "games"}), or None: never read, dropped, or Redis can't say."""
    try:
        raw = cast(str | None, bus.sync_redis().get(_key(system)))  # a sync client: never awaitable
    except Exception:
        log.warning("live: %s's games couldn't be read", system, exc_info=True)
        return None
    return json.loads(raw) if raw else None


def _age(read_: dict[str, Any] | None, now: datetime) -> float:
    return (now - datetime.fromisoformat(read_["as_of"])).total_seconds() if read_ else float("inf")


def _event_keys(system: str, game: dict[str, Any]) -> list[str]:
    """The keys of the events a followed player's card would have this game under (a read from before
    fixtures were kept names its source id)."""
    return [f"{system}:{f}" for f in game.get("fixtures") or [game["source_id"]]]


def offer(db: Session, fan: str | None, now: datetime | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """The games on now, by running sport in the order the app lists sports ({"key", "label", "games"}),
    each with the players this fan can follow from it and the ids of the events it is here (a followed
    player's live card names its game by one); and the sources whose last read is over FRESH old, for the
    caller to ask a read of."""
    now = now or datetime.now(timezone.utc)
    stale: list[str] = []
    by_sport: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for adapter in sources():
        read_ = last_read(adapter.system)
        age = _age(read_, now)
        if age > FRESH:
            stale.append(adapter.system)
        if age <= SERVE:
            by_sport.setdefault(adapter.sport, []).extend((adapter.system, g) for g in (read_ or {}).get("games") or [])
    blocked = registry.blocked(db) if by_sport else set()
    ranked = {sport.key: sorted(by_sport.get(sport.key, []), key=lambda sg: -sg[1].get("fame", 0))[: GAMES * 2] for sport in sports.ENABLED}
    keys = {k for candidates in ranked.values() for system, game in candidates for k in _event_keys(system, game)}
    events = {key: event_id for key, event_id in db.execute(select(Event.key, Event.id).where(Event.key.in_(keys)))} if keys else {}
    out = []
    for sport in sports.ENABLED:
        candidates = ranked[sport.key]
        refs = [(i, PlayerRef(system=system, athlete_id=p["athlete_id"], name=p["name"], team_ids=[], team_names=[p.get("team") or ""], league=p.get("league")))
                for i, (system, game) in enumerate(candidates) for p in game.get("players") or [] if (system, p["athlete_id"]) not in blocked]
        rows = search.offered_refs(db, sport.label, [r for _, r in refs], fan)  # one lookup of who's on Sports Follow for the sport
        games = []
        for i, (system, game) in enumerate(candidates):
            mine = [row for (j, _), row in zip(refs, rows) if j == i]
            mine.sort(key=lambda r: r.player_id is None)  # already on Sports Follow first; otherwise as the read ranked them
            players = mine[:PLAYERS]
            rank = (-max((p.followers for p in players), default=0), -game.get("fame", 0))  # most followed, then best known
            games.append((rank, {"key": f"{system}:{game['source_id']}", "title": game["title"], "competition": game["competition"],
                                 "start_utc": game.get("start_utc"), "live": game["live"], "players": [search.public(p) for p in players],
                                 "event_ids": sorted({events[k] for k in _event_keys(system, game) if k in events})}))
        games.sort(key=lambda g: g[0])
        if games:
            out.append({"key": sport.key, "label": sport.label, "games": [g for _, g in games[:GAMES]]})
    return out, stale
