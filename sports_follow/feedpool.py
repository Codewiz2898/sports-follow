"""The feed's pool of top players (BHA-382): players nobody need follow whose videos the curator fetches too, so For
you has more than other fans' followed players to mix in (feed.others). Followed players are always fetched; the
pool is each running sport's best known besides them.

For each sport the registry keeps (football, cricket, …; esports for every game), its best known current athletes,
FEED_POOL_SIZE for a sport and that many for each running game of a group: Wikipedia editions first (the starters'
measure, starters.known: the home country's count double), then how big their team's source rates it (Team.tier),
which is all an esports pro has, Wikipedia knowing none of them. An athlete neither rates isn't best known and is
left out, and so is one an operator removed (registry.blocked).

A pool player is a player here like any other: once a day (refresh, run by the curator) each one without a player
yet is found in their sport's live source and made one, as a fan's follow from search would (registry.resolve,
pipeline.start_from_pick), up to FEED_POOL_ADDS a day in all, the best known of each sport first, in turns. Nobody
follows them, so the worker reads nothing of theirs until a fan does (pipeline.due_work): then their page is built
and their games read as for any follow. One no live source has (no source for the sport, or not found in it) is
left out, and isn't looked for again for MISSING_FOR.

The pool, as player ids in order (the sports taking turns down their lists), is kept in Redis (POOL_KEY) for the
channel poll and the sweeps (members); without it (Redis down, or no refresh yet) the pool is empty and only
followed players are fetched.

    .venv/bin/python -m sports_follow.curator pool [--run]
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import bus, db, naming, pipeline, registry, shutdown, sports, starters
from .adapters import AdapterError
from .adapters.budget import RateLimited
from .config import FEED_POOL_ADDS, FEED_POOL_SIZE
from .models import Athlete, Player, Team

log = logging.getLogger("sports_follow.feedpool")

POOL_KEY = "feed:pool"
POOL_TTL = 3 * 86400  # a refresh's pool outlasts a day or two of failed refreshes
MISSING_FOR = 7 * 86400  # seconds an athlete no live source found isn't looked for again
LOOKED_AT = 2  # registry athletes read for each place: some are removed, or no source has them


@dataclass
class Ranked:
    """A registry athlete in their sport's pool order, and their player here, if they have one."""

    person: registry.Person
    player_id: int | None


def size(key: str) -> int:
    """How many a registry sport's pool holds: FEED_POOL_SIZE, for a group that many for each of its running sports."""
    group = sports.members(sports.BY_KEY[key]) if key in sports.BY_KEY else ()
    running = [s for s in group if s in sports.ENABLED]
    return FEED_POOL_SIZE * max(1, len(running))


def ranked(session: Session, key: str, limit: int) -> list[Athlete]:
    """A registry sport's best known current athletes, best first, limit at most: Wikipedia editions (starters.known),
    then their first team's tier (Team.tier, the best its source gives a team of that name in the sport or its
    group's sports). Neither: left out."""
    tiers: dict[str, int] = {}
    for name, best in session.execute(select(Team.name, func.max(Team.tier)).where(Team.sport.in_(sports.within(key)), Team.tier > 0).group_by(Team.name)):
        tiers[naming.team_key(name)] = max(tiers.get(naming.team_key(name), 0), best)

    def tier(a: Athlete) -> int:
        teams = [t for t in a.teams or [] if isinstance(t, str)]
        return tiers.get(naming.team_key(teams[0]), 0) if teams else 0

    fame = starters.known()
    famous = [(a, n) for a, n in session.execute(select(Athlete, fame).where(Athlete.sport == key, Athlete.current, Athlete.sitelinks > 0)
                                                 .order_by(fame.desc(), Athlete.id).limit(limit))]  # fmt: skip
    rated: list[tuple[Athlete, int]] = []
    if tiers:
        unknown = session.scalars(select(Athlete).where(Athlete.sport == key, Athlete.current, Athlete.sitelinks == 0, func.jsonb_array_length(Athlete.teams) > 0))
        rated = [(a, 0) for a in unknown if tier(a) > 0]
    both = sorted([*famous, *rated], key=lambda an: (-an[1], -tier(an[0]), an[0].id))
    return [a for a, _ in both[:limit]]


def _missing_key(qid: str, sport: str) -> str:
    return f"feed:pool:missing:{sport}:{qid}"


def _missing(people: list[registry.Person]) -> set[str]:
    """The qids of these athletes no live source found lately."""
    if not people:
        return set()
    try:
        seen = cast(list[Any], bus.sync_redis().mget([_missing_key(p.qid, p.sport) for p in people]))
    except Exception:  # noqa: BLE001 — without Redis they're looked for again: a request or a few each
        return set()
    return {p.qid for p, s in zip(people, seen) if s}


def _mark_missing(person: registry.Person) -> None:
    try:
        bus.sync_redis().set(_missing_key(person.qid, person.sport), "1", ex=MISSING_FOR)
    except Exception:  # noqa: BLE001
        pass


def plan(session: Session) -> dict[str, list[Ranked]]:
    """Each running registry sport's pool as it should be, in order: its best known (ranked), less those an operator
    removed and those no live source found lately, each with their player here if they have one."""
    removed = registry.blocked(session)
    out: dict[str, list[Ranked]] = {}
    for key in sports.running_keys():
        places = size(key)
        rows = ranked(session, key, places * LOOKED_AT)
        people = [registry.Person.of(a) for a in rows if not any((s, str(x)) in removed for s, x in (a.ids or {}).items())]
        gone = _missing(people)
        kept: list[Ranked] = []
        for person in people:
            if len(kept) >= places:
                break
            holder = pipeline.registry_holder(session, person.qid, person.sport)
            if holder is None and person.qid in gone:
                continue
            kept.append(Ranked(person, holder))
        if kept:
            out[key] = kept
    return out


def _turns(lists: list[list[Any]]) -> list[Any]:
    """Lists taking turns: each one's first, then each one's second, …"""
    out = []
    for i in range(max((len(x) for x in lists), default=0)):
        out += [x[i] for x in lists if i < len(x)]
    return out


def make(person: registry.Person) -> int | None:
    """A pool athlete made a player, found in their sport's live source as a fan's follow from search would: their
    player's id, or None when no live source has them (remembered for MISSING_FOR) or the one found was removed.
    RateLimited and AdapterError go to the caller: tried again another day."""
    found = registry.resolve(person)
    if found is None:
        _mark_missing(person)
        return None
    adapter, ref = found
    with db.session() as session:
        if (ref.system, str(ref.athlete_id)) in registry.blocked(session):
            return None
    player_id, _ = pipeline.start_from_pick(adapter, ref)
    with db.session() as session:
        registry.link(session, player_id, person.qid)
    return player_id


def refresh(adds: int | None = None) -> dict[str, int]:
    """Work the pool out again, make players of up to adds (FEED_POOL_ADDS) of its athletes who have none, the
    sports taking turns, and keep it for members. Asks the live sources: the curator runs it once a day. Returns
    what it did: the pool's players, those made, and those no source has."""
    adds = FEED_POOL_ADDS if adds is None else adds
    counts = {"players": 0, "made": 0, "missing": 0}
    if FEED_POOL_SIZE <= 0:
        _store({})
        return counts
    with db.session() as session:
        planned = plan(session)
    stop = False
    for r in _turns(list(planned.values())):
        if r.player_id is not None or stop or adds <= 0:
            continue
        if shutdown.stopping.is_set():
            break
        try:
            r.player_id = make(r.person)
        except RateLimited as exc:
            log.info("feed pool: %s; the rest are made another day", exc)
            stop = True
            continue
        except AdapterError as exc:
            log.info("feed pool: %s (%s) not made: %s", r.person.name, r.person.sport, exc)
            continue
        except Exception:
            log.exception("feed pool: %s (%s) not made", r.person.name, r.person.sport)
            continue
        adds -= 1
        counts["made" if r.player_id is not None else "missing"] += 1
    pool = {key: held for key, rs in planned.items() if (held := [r.player_id for r in rs if r.player_id is not None])}
    _store(pool)
    counts["players"] = sum(len(ids) for ids in pool.values())
    return counts


def _store(pool: dict[str, list[int]]) -> None:
    try:
        bus.sync_redis().set(POOL_KEY, json.dumps({"at": datetime.now(timezone.utc).isoformat(), "players": pool}), ex=POOL_TTL)
    except Exception:  # noqa: BLE001 — the last pool stands until it expires
        log.warning("feed pool: couldn't be kept", exc_info=True)


def stored() -> dict[str, list[int]]:
    """Each registry sport's pool as the last refresh kept it, player ids best first; {} when Redis can't say."""
    try:
        raw = cast(str | None, bus.sync_redis().get(POOL_KEY))
        players = (json.loads(raw) if raw else {}).get("players") or {}
    except Exception:  # noqa: BLE001
        log.warning("feed pool: couldn't be read; only followed players are fetched", exc_info=True)
        return {}
    return {str(k): [i for i in v if isinstance(i, int) and not isinstance(i, bool)] for k, v in players.items() if isinstance(v, list)}


def ids() -> list[int]:
    """The pool's player ids in its order: the sports taking turns down their lists."""
    return list(dict.fromkeys(_turns(list(stored().values()))))


def report(session: Session) -> list[dict[str, Any]]:
    """The pool as kept, for the curator's command: each player's registry sport, place in it, id, name and sport."""
    pool = stored()
    wanted = [i for v in pool.values() for i in v]
    names = {pid: (name, sport) for pid, name, sport in session.execute(select(Player.id, Player.name, Player.sport).where(Player.id.in_(wanted)))} if wanted else {}
    return [{"key": key, "place": n + 1, "player_id": pid, "name": names.get(pid, ("(gone)", ""))[0], "sport": names.get(pid, ("", ""))[1]}
            for key, v in pool.items() for n, pid in enumerate(v)]  # fmt: skip
