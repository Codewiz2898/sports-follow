"""Starters: the players a new fan, who follows nobody yet, is offered in each sport, in the welcome's
picker and on an empty Today (web/src/components/Starters.tsx), and as the search page's "Try" names.

Every running sport offers a mix of four pools, worked out the same way for each sport:

- pinned: the operator's own picks, first and in their order, set with `python -m sports_follow.players
  starter add|remove|list` (players.py). Adding one checks their source has them and pins them, so no
  import drops them; removing the athlete takes their starter row with them.
- playing: who's on now or soon, worked out as asked: players on Sports Follow whose card, kept current
  by the worker while anyone follows them, is live or starts within SOON; and the top players whose next
  game, read with the top pool, is that close by the clock.
- followed: the players on Sports Follow the most fans follow, counted as asked.
- top: the sport's best-known current athletes (Wikipedia editions; the home country's, a setting, count
  double), found in the source they're followed through and kept only if it has them playing lately or
  soon (a game in the last RECENT_DAYS or the next TOP_DAYS), so none is an empty card: a star the plan
  has no games for is left out. Finding them asks the sources, so the worker builds this pool once a day
  (refresh) and keeps it in Redis, with each one's next game.

A sport takes from the pools in turn, after its pinned players, up to PER_SPORT. Only athletes a fan can
follow without AI research are offered (search.offered: one already on Sports Follow, or one a running
source knows by id), and a sport that's turned off offers none.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, cast

from sqlalchemy import case, delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import bus, registry, search, shutdown, sports
from .adapters import AdapterError, Fixture, SearchResult
from .adapters.budget import RateLimited
from .config import STARTERS_HOME_COUNTRY
from .db import session
from .models import Athlete, Follow, Player, Starter

log = logging.getLogger(__name__)

PER_SPORT = 8  # the most a sport offers; the picker shows them as tiles, two to a row
POOL = 12  # each pool's length before they're mixed
SOON = timedelta(hours=18)  # a game starting this soon counts as playing now
STARTED = timedelta(hours=3)  # and one that started this long ago, by the clock alone, may still be on
TOP_DAYS = 21  # a top player is kept only with a game this soon
RECENT_DAYS = 45  # or one this recent: chess has weeks between events
TOP_CANDIDATES = 30  # the best-known athletes a sport's refresh looks at
TOP_SECONDS = 300  # and the longest it spends on them: a source taking one request at a time (Lichess) is shared with live games
TOP_TTL = 3 * 86400  # a pool outlasts a day or two with no refresh (one that can't ask the sources keeps it)
LIMITED_PAUSE = 65  # seconds a sport's refresh waits, once, when a source's allowance is spent: a minute's comes back
FOLLOWED_LOOKED_AT = 50  # the most-followed players of a sport whose live cards are read for "playing"
CACHE_SECONDS = 60  # what a sport offers, the same for every fan, is worked out at most this often (a process)


# ---------------------------------------------------------------- pinned: the operator's picks


def add(db: Session, athlete: Athlete) -> bool:
    """Pin a registry athlete as a starter, after the sport's other pins. False if they already were."""
    last = db.scalar(select(func.max(Starter.position)).where(Starter.sport == athlete.sport))
    stmt = insert(Starter).values(qid=athlete.qid, sport=athlete.sport, position=(last or 0) + 1)
    return bool(db.execute(stmt.on_conflict_do_nothing(index_elements=["qid", "sport"]).returning(Starter.id)).first())


def remove(db: Session, athletes: list[Athlete]) -> int:
    """Unpin these athletes as starters. Returns how many starters that took out."""
    if not athletes:
        return 0
    pairs = [(a.qid, a.sport) for a in athletes]
    return len(db.execute(delete(Starter).where(tuple_(Starter.qid, Starter.sport).in_(pairs)).returning(Starter.id)).all())


def listed(db: Session) -> list[tuple[Starter, Athlete | None]]:
    """Every pinned starter by sport and order, with their registry row (None if it has gone)."""
    on_row = (Athlete.qid == Starter.qid) & (Athlete.sport == Starter.sport)
    return [(s, a) for s, a in db.execute(select(Starter, Athlete).outerjoin(Athlete, on_row).order_by(Starter.sport, Starter.position)).all()]


def _pinned(db: Session, sport: str) -> list[Athlete]:
    on_row = (Athlete.qid == Starter.qid) & (Athlete.sport == Starter.sport)
    return list(db.scalars(select(Athlete).join(Starter, on_row).where(Starter.sport == sport).order_by(Starter.position)))


# ---------------------------------------------------------------- top: built once a day


def _top_key(sport: str) -> str:
    return f"starters:top:{sport}"


def store_top(sport: str, entries: list[dict[str, Any]]) -> None:
    bus.sync_redis().set(_top_key(sport), json.dumps(entries), ex=TOP_TTL)


def top_pool(sport: str) -> list[dict[str, Any]]:
    """A sport's top players as the last refresh left them ({"qid"} and their next game's "start_utc",
    "title" and "competition", or None), best known first; none when Redis can't say."""
    try:
        raw = cast(str | None, bus.sync_redis().get(_top_key(sport)))  # a sync client: never awaitable
    except Exception:
        log.warning("starters: the top pool of %s couldn't be read", sport, exc_info=True)
        return []
    return json.loads(raw) if raw else []


def known() -> Any:
    """How well known an athlete is: Wikipedia editions, doubled for the home country's."""
    if not STARTERS_HOME_COUNTRY:
        return Athlete.sitelinks
    return case((Athlete.country == STARTERS_HOME_COUNTRY, Athlete.sitelinks * 2), else_=Athlete.sitelinks)


def _playing_lately(fixtures: Iterable[Fixture], now: datetime) -> tuple[bool, Fixture | None]:
    """Whether the source has them playing lately or soon (a game in the last RECENT_DAYS or the next
    TOP_DAYS), and their first game not yet over: started within STARTED, or starting within TOP_DAYS."""
    near = [f for f in fixtures if f.start_utc and now - timedelta(days=RECENT_DAYS) <= f.start_utc <= now + timedelta(days=TOP_DAYS)]
    ahead = [f for f in near if f.status in ("scheduled", "live") and f.start_utc and f.start_utc >= now - STARTED]
    return bool(near), min(ahead, key=lambda f: f.start_utc or now) if ahead else None


def refresh(now: datetime | None = None, keep: int = POOL) -> dict[str, int]:
    """Build every running sport's top pool: its best-known current athletes, found in the source they're
    followed through (registry.find_in_source, which writes a found id back), with their next game. Asks
    the sources, a few calls an athlete: the worker runs it once a day (worker.refresh_starters), and
    `players starter refresh` by hand. Returns how many each sport kept.

    An athlete it can't look up (their source failing, or its allowance or this refresh's time running out
    first) stays as the last refresh found them: a source's outage at 04:40 used to leave its sport with no
    best-known starters for a day. And a worker stopping ends it at its next step, storing nothing more: its
    thread used to ask on about each athlete while the interpreter shut down, and store the few it found
    (BUG-172)."""
    now = now or datetime.now(timezone.utc)
    kept: dict[str, int] = {}
    for sport in sports.ENABLED:
        with session() as db:
            # Only athletes some Wikipedia knows: a sport's roster-imported pros (esports) all have none, and
            # among ties the order would be the import's, and the checks would spend a scarce allowance.
            rows = db.scalars(select(Athlete).where(Athlete.sport == sport.key, Athlete.current, Athlete.sitelinks > 0).order_by(known().desc(), Athlete.id).limit(TOP_CANDIDATES)).all()
            people = [registry.Person.of(a) for a in rows]
        last = {e["qid"]: e for e in top_pool(sport.key)}  # for whoever this refresh can't look up
        entries: list[dict[str, Any]] = []
        as_before = 0
        waited = False
        queue = list(people)
        until = time.monotonic() + TOP_SECONDS
        while queue and len(entries) < keep and not shutdown.stopping.is_set():
            if time.monotonic() > until:
                log.info("starters: %s's top pool stops at %d after %ds", sport.key, len(entries), TOP_SECONDS)
                break
            person = queue[0]
            if registry.follow_system(person.sport, person.ids) is None:
                queue.pop(0)
                continue  # no running source follows them: following would be AI research
            try:
                found = registry.find_in_source(person)
                playing, game = _playing_lately(found[0].fixtures(found[1]), now) if found else (False, None)
            except RateLimited as exc:
                if waited:
                    log.info("starters: %s's top pool stops at %d: %s", sport.key, len(entries), exc)
                    break
                waited = True  # a minute's allowance comes back; an hour's or a day's won't, and the next try says so
                shutdown.sleep(LIMITED_PAUSE)
                continue
            except Exception as exc:
                if isinstance(exc, AdapterError):
                    log.info("starters: %s (%s) couldn't be looked up: %s", person.name, sport.key, exc)
                else:
                    log.exception("starters: %s (%s) couldn't be looked up", person.name, sport.key)
                queue.pop(0)
                if person.qid in last:
                    entries.append(last[person.qid])
                    as_before += 1
                continue
            queue.pop(0)
            if playing:
                entries.append({"qid": person.qid, "start_utc": game.start_utc.isoformat() if game and game.start_utc else None,
                                "title": game.title if game else None, "competition": game.competition if game else None})
        if shutdown.stopping.is_set():
            log.info("starters: the worker is stopping; %s's top pool and the rest are left as they were", sport.key)
            return kept
        if queue and len(entries) < keep:  # it stopped short: those it didn't get to stay as they were too
            unasked = [last[p.qid] for p in queue if p.qid in last][:keep - len(entries)]
            entries += unasked
            as_before += len(unasked)
        store_top(sport.key, entries)
        kept[sport.key] = len(entries)
        log.info("starters: %d top %s players playing lately or soon, %d of them as the last refresh found them", len(entries), sport.key, as_before)
    return kept


# ---------------------------------------------------------------- playing and followed: as asked


def hot_cards(player_ids: list[int]) -> dict[int, dict[str, Any]]:
    """The live copies of these players' cards the worker keeps (bus.card_key); none when Redis can't say."""
    if not player_ids:
        return {}
    try:
        raw = cast(list[str | None], bus.sync_redis().mget([bus.card_key(pid) for pid in player_ids]))
    except Exception:
        log.warning("starters: live cards couldn't be read", exc_info=True)
        return {}
    return {pid: json.loads(r) for pid, r in zip(player_ids, raw) if r}


def _when(value: Any) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when


def _on(start: datetime | None, now: datetime) -> str | None:
    """Whether a game starting then counts as playing now by the clock: "started", "soon", or None."""
    if start is None or not now - STARTED <= start <= now + SOON:
        return None
    return "started" if start <= now else "soon"


def _most_followed(db: Session, sport: sports.Sport) -> list[Player]:
    """The sport's players on Sports Follow by how many fans follow them, most first."""
    names = sports.names_of(sport)
    rows = db.execute(
        select(Player, func.count(Follow.id).label("n"))
        .join(Follow, Follow.player_id == Player.id)
        .where(func.lower(Player.sport).in_(names))
        .group_by(Player.id)
        .order_by(func.count(Follow.id).desc(), Player.id)
        .limit(FOLLOWED_LOOKED_AT)
    ).all()
    return [p for p, _ in rows]


Offered = tuple[SearchResult, str]  # a row, and why it's offered: pinned | live | started | soon | followed | top
PLAYING = ("live", "started", "soon")


def _playing(db: Session, followed: list[Player], top: list[Offered], fan: str | None, now: datetime) -> list[Offered]:
    """Who's on now or soon: followed players whose live card says so, then top players by the clock;
    live first, then by start."""
    cards = hot_cards([p.id for p in followed])
    on: list[tuple[Player, dict[str, Any], str]] = []
    for p in followed:
        nxt = search.next_up(cards.get(p.id) or {})
        if nxt and nxt.get("live"):
            on.append((p, nxt, "live"))
        elif nxt and (why := _on(_when(nxt.get("start_utc")), now)) == "soon":
            on.append((p, nxt, why))  # a followed player's card knows whether a started game is live
    on.sort(key=lambda o: (o[2] != "live", _when(o[1].get("start_utc")) or now))
    on = on[:POOL]
    rows = {r.player_id: r for r in search.offered_players(db, [p for p, _, _ in on], fan)}
    playing: list[Offered] = []
    for p, nxt, why in on:
        if (r := rows.get(p.id)) is not None:
            r.next = nxt
            playing.append((r, why))
    for r, _ in top:
        why = "live" if (r.next or {}).get("live") else _on(_when((r.next or {}).get("start_utc")), now)
        if why is not None:
            playing.append((r, why))
    order = {"live": 0, "started": 1, "soon": 2}
    return sorted(playing, key=lambda o: (order[o[1]], _when((o[0].next or {}).get("start_utc")) or now))


def _top(db: Session, sport: str, fan: str | None, now: datetime) -> list[Offered]:
    entries = top_pool(sport)
    if not entries:
        return []
    by_qid = {a.qid: a for a in db.scalars(select(Athlete).where(Athlete.sport == sport, Athlete.qid.in_([e["qid"] for e in entries])))}
    games = {e["qid"]: e for e in entries}
    out: list[Offered] = []
    for a, r in search.offered_pairs(db, [by_qid[e["qid"]] for e in entries if e["qid"] in by_qid], fan):
        game = games[a.qid]
        start = _when(game.get("start_utc"))
        # The morning's next game, unless their own card says what's next, or it's over by now.
        if r.next is None and start is not None and start >= now - STARTED:
            r.next = {"title": game["title"], "start_utc": game["start_utc"], "competition": game["competition"]}
        out.append((r, "top"))
    return out


def _taking_turns(pools: list[list[Offered]]) -> Iterable[Offered]:
    for i in range(max((len(p) for p in pools), default=0)):
        for pool in pools:
            if i < len(pool):
                yield pool[i]


_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _sport_offer(db: Session, sport: sports.Sport, now: datetime) -> list[dict[str, Any]]:
    """What a sport offers anyone, in order: pinned, then the pools in turn, up to PER_SPORT."""
    pinned = [(r, "pinned") for r in search.offered(db, _pinned(db, sport.key), None)]
    followed_players = _most_followed(db, sport)
    followed = [(r, "followed") for r in search.offered_players(db, followed_players[:POOL], None)]
    top = _top(db, sport.key, None, now)
    playing = _playing(db, followed_players, top, None, now)
    at: dict[Any, int] = {}  # where each player already is in the list
    players: list[dict[str, Any]] = []
    for r, why in [*pinned, *_taking_turns([playing[:POOL], followed, top[:POOL]])]:
        who = r.player_id or r.key
        if who in at:
            # Offered twice: playing now says more than being followed or known, wherever they came first.
            if why in PLAYING and players[at[who]]["why"] in ("followed", "top"):
                players[at[who]] |= {"why": why, "next": r.next}
            continue
        if len(players) < PER_SPORT:
            at[who] = len(players)
            players.append({**search.public(r), "why": why})
    return players


def offer(db: Session, fan: str | None, now: datetime | None = None) -> list[dict[str, Any]]:
    """What a new fan is offered: each running sport with anyone to offer, in the order the app lists
    sports, with its starters as result rows ({"key", "label", "players"}), each saying why ("why") and
    whether this fan follows them. What a sport offers is the same for everyone, so it's worked out at
    most every CACHE_SECONDS; the fan's own follows are filled in each time."""
    now = now or datetime.now(timezone.utc)
    out = []
    for sport in sports.ENABLED:
        cached = _cache.get(sport.key)
        if cached is None or time.monotonic() - cached[0] > CACHE_SECONDS:
            cached = (time.monotonic(), _sport_offer(db, sport, now))
            _cache[sport.key] = cached
        if cached[1]:
            out.append({"key": sport.key, "label": sport.label, "players": [dict(p) for p in cached[1]]})
    ids = [p["player_id"] for s in out for p in s["players"] if p.get("player_id")]
    mine = set(db.scalars(select(Follow.player_id).where(Follow.fan_id == fan, Follow.player_id.in_(ids)))) if fan and ids else set()
    for s in out:
        for p in s["players"]:
            p["following"] = p.get("player_id") in mine
    return out
