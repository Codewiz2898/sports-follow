"""Following a team (BHA-77): a fan follows Arsenal, the Lakers, India's cricket side or T1, not only its players.

**The registry.** A team is one source's team, by the source's id for it (Team), imported weekly from the list
each running source keeps of the teams it covers (Adapter.teams), for the sports whose teams fans can follow
(Sport.follow_teams): from the sport's first source with a list, or from each, for a sport whose sources sit
side by side (esports). An operator adds, removes and restores teams with the command below. Removing one takes
its follows, its games' links and its results with it, and leaves a decision (TeamOverride) no import
overrides; adding one keeps it, so no import drops it. A team no import has listed for STALE that nobody
follows goes.

    .venv/bin/python -m sports_follow.teams import [system]          # read the sources' lists now (weekly anyway)
    .venv/bin/python -m sports_follow.teams list [name]              # the registry's teams, with their ids
    .venv/bin/python -m sports_follow.teams add <system> <team id>   # a team the lists don't have, kept
    .venv/bin/python -m sports_follow.teams remove <id>              # by its id here, or system:team id
    .venv/bin/python -m sports_follow.teams restore <system> <team id>

**A follow** (TeamFollow) is a fan, a team and their level. A followed team's games are read from its source
(refresh: Adapter.team_fixtures, as often as a followed player's near a game, every QUIET otherwise: stale) and
linked to it (EventTeam), and from then on each is followed as a game followed by itself is (matches.py): armed
by its sport's lead, polled in the live lane by the same allowances, its own moments from both sides
(Sport.game_moments) reaching the team's fans by their level, and its result told from the team's side
(Sport.side_final) in place of the game's own (alerts.plan). A fan who follows a player in it as well hears each
moment once; one who mutes a game of it hears nothing more of that game. The refresh keeps each result from the
team's side (EventTeam.result), and the team's page (its card): its games to come, its last results and its squad
(Adapter.team, read daily), each player followable as a search result is. A fan follows at most MAX teams.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import ColumnElement, Select, Text, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.orm import Session, aliased

from . import bus, logs, matches, models, moments, naming, registry, search, sports, structured
from .adapters import ADAPTERS, KNOWN, Adapter, AdapterError, Fixture, PlayerRef, SourceTeam, budget, serving, source_name
from .db import session
from .models import Event, EventState, EventTeam, Moment, NotificationMute, Team, TeamFollow, TeamOverride
from .sports.base import Found
from .sports.base import sides as sports_sides

log = logging.getLogger("sports_follow.teams")

MAX = 30  # teams a fan follows at once
STALE = timedelta(days=21)  # a team no import has listed this long, that nobody follows, goes
SQUAD_EVERY = timedelta(days=1)  # how often a followed team's squad is read again
SQUAD_MOST = 40  # players a squad lists: a club's first team with its youngsters on the bench
UPCOMING = 5  # games to come a team's page shows
RECENT = 5  # and results
LIMIT = 6  # teams a search answers with
NAMESAKES = 20  # and teams with exactly the name typed, however many past LIMIT: Team Liquid in each of its titles
REFRESH_LOCK = 20 * 60  # as a player's refresh (structured.REFRESH_LOCK)
LOOK_AGAIN = 10 * 60  # a page nobody follows the team of asks its source again at most this often
SOON = timedelta(hours=48)  # a team with a game on or this soon has its games read as often as a followed player's
QUIET = timedelta(hours=6)  # and one without, this often: a schedule days off doesn't change by the half hour
READS = 6  # and pages ask at most this many reads a minute of one source's allowance, whichever teams (as match.READS)
STREAM_AHEAD = timedelta(hours=36)  # a fan's teams' games this soon are on their stream (event_ids)
NO_SOURCE = "This team's games can't be followed now: its source isn't running."
TAG = "team:"  # a game's own moment from one team's side is about TAG + the team's id here (Moment.about)


class Refused(Exception):
    """A follow that can't be made: its HTTP status, and what the fan is told."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def followable(sport: str | None) -> bool:
    """Whether fans can follow teams of a sport now: one whose teams can be (Sport.follow_teams) and that runs."""
    found = sports.get(sport)
    return found is not None and found.follow_teams and sports.on(found.key)


def readable(adapter: Adapter | None) -> bool:
    """Whether a source can say a team's games (Adapter.team_fixtures)."""
    return adapter is not None and callable(getattr(adapter, "team_fixtures", None))


def tag(team_id: int) -> str:
    return f"{TAG}{team_id}"


# ---------------------------------------------------------------- the registry


def importers() -> list[Adapter]:
    """The running sources a team import reads: for each sport running whose teams fans can follow, its first
    source with a list of teams, or every one, for a sport whose sources sit side by side (esports' games)."""
    out: list[Adapter] = []
    for sport in sports.ENABLED:
        if not sport.follow_teams:
            continue
        theirs = [a for a in serving(sport) if callable(getattr(a, "teams", None))]
        for adapter in theirs if sport.side_by_side else theirs[:1]:
            if adapter not in out:
                out.append(adapter)
    return out


def search_text(name: str, short: str, aliases: Sequence[str] = ()) -> str:
    """What search reads of a team: its name, its code ("ARS") and any other names, as an athlete's (registry.search_text)."""
    return registry.search_text(name, [short, *aliases])


def _merged(listed: list[SourceTeam]) -> list[SourceTeam]:
    """A source's list with a team listed under several competitions (a cricket side in a league and a cup) once,
    with each competition it's in and its best tier."""
    by_id: dict[str, SourceTeam] = {}
    seen: dict[str, list[str]] = {}
    for t in listed:
        if not t.team_id or not (t.name or "").strip():
            continue
        first = by_id.setdefault(t.team_id, t)
        first.tier = max(first.tier, t.tier)
        seen.setdefault(t.team_id, [])
        if t.competition and t.competition not in seen[t.team_id]:
            seen[t.team_id].append(t.competition)
    for team_id, t in by_id.items():
        t.competition = " · ".join(seen[team_id])
    return list(by_id.values())


def _sport_of(adapter: Adapter, t: SourceTeam) -> str:
    return t.sport or adapter.sports[0]


def upsert(db: Session, system: str, t: SourceTeam, sport: str, now: datetime) -> Team:
    """A source's team as the registry keeps it: written anew from what the source says of it now."""
    competitions = [c for c in (t.competition or "").split(" · ") if c]
    values = {"name": t.name.strip()[:200], "sport": sport, "competitions": competitions, "short": (t.short or "")[:20], "country": (t.country or None) and t.country[:120],
              "tier": t.tier, "search_text": search_text(t.name, t.short or ""), "seen_at": now}
    db.execute(insert(Team).values(system=system, external_id=t.team_id, **values).on_conflict_do_update(index_elements=["system", "external_id"], set_=values))
    return db.scalars(select(Team).where(Team.system == system, Team.external_id == t.team_id)).one()


def import_teams(adapter: Adapter, now: datetime | None = None) -> int:
    """Read one source's list of teams into the registry (the weekly import, or `teams import`), leaving out those
    an operator removed and those of a sport not running; then the source's teams no import has listed for STALE,
    that nobody follows and nobody kept, go. Returns the teams it listed. A list that can't be read changes nothing."""
    now = now or _now()
    listed = _merged(adapter.teams())  # pyright: ignore[reportAttributeAccessIssue]: importers() picks sources with a list; outside a transaction: it asks the source
    with session() as db:
        removed = set(db.scalars(select(TeamOverride.external_id).where(TeamOverride.system == adapter.system, TeamOverride.action == "remove")))
        running = set(adapter.running())
        kept = 0
        for t in listed:
            sport = _sport_of(adapter, t)
            if t.team_id in removed or sport not in running or not followable(sport):
                continue
            upsert(db, adapter.system, t, sport, now)
            kept += 1
        if kept:  # an empty list is the source's failure, not every team gone
            pinned = select(TeamOverride.external_id).where(TeamOverride.system == adapter.system, TeamOverride.action == "keep")
            db.execute(delete(Team).where(Team.system == adapter.system, or_(Team.seen_at.is_(None), Team.seen_at < now - STALE),
                                          Team.id.not_in(select(TeamFollow.team_id)), Team.external_id.not_in(pinned)))
    log.info("teams: %s listed %d teams, %d kept", adapter.system, len(listed), kept)
    return kept


def import_all() -> dict[str, int]:
    """Every running source's list (the worker, weekly): each source's teams, by its system. One that fails is
    logged and the rest go on."""
    done = {}
    for adapter in importers():
        try:
            done[adapter.system] = import_teams(adapter)
        except AdapterError as exc:
            log.warning("teams: couldn't read %s's teams: %s", adapter.system, exc)
    return done


def get(db: Session, ref: str) -> Team | None:
    """A team by its id here ("12") or its source's ("sportmonks_football:19")."""
    system, sep, external = ref.partition(":")
    if sep:
        return db.scalar(select(Team).where(Team.system == system, Team.external_id == external))
    return db.get(Team, int(ref)) if ref.isdigit() and len(ref) <= 12 else None


def add(db: Session, system: str, team_id: str, note: str | None = None) -> Team:
    """An operator's team (`teams add`): read from its source now and kept, so no import drops it."""
    adapter = KNOWN.get(system)
    if adapter is None or system not in ADAPTERS:
        raise Refused(404, f"{system} isn't a running source")
    if not getattr(adapter, "team_id_pattern", re.compile(r"[0-9]{1,12}")).fullmatch(team_id) or not callable(getattr(adapter, "team", None)):
        raise Refused(404, f"{team_id!r} isn't one of {system}'s team ids, or it can't read teams")
    found = adapter.team(team_id)  # pyright: ignore[reportAttributeAccessIssue]: callable() said it has one
    if found is None:
        raise Refused(404, f"{system} has no team {team_id}")
    sport = _sport_of(adapter, found)
    if not followable(sport):
        raise Refused(409, f"{found.name} plays {sport}, whose teams can't be followed here")
    db.execute(delete(TeamOverride).where(TeamOverride.system == system, TeamOverride.external_id == team_id))
    db.add(TeamOverride(system=system, external_id=team_id, action="keep", name=found.name, sport=sport, note=note))
    return upsert(db, system, found, sport, _now())


def remove(db: Session, team: Team, note: str | None = None) -> int:
    """Take a team out (`teams remove`), with everything hanging off it (follows, its games' links and results: the
    database cascades), and keep it out: no import writes it again. Returns the follows it took."""
    follows = db.scalar(select(func.count()).select_from(TeamFollow).where(TeamFollow.team_id == team.id)) or 0
    db.execute(delete(TeamOverride).where(TeamOverride.system == team.system, TeamOverride.external_id == team.external_id))
    db.add(TeamOverride(system=team.system, external_id=team.external_id, action="remove", name=team.name, sport=team.sport, note=note))
    db.delete(team)
    return follows


def restore(db: Session, system: str, team_id: str) -> bool:
    """Take back what was decided about a source's team: the next import may write it again (or drop it). Whether there was a decision."""
    return bool(db.execute(delete(TeamOverride).where(TeamOverride.system == system, TeamOverride.external_id == team_id).returning(TeamOverride.id)).first())


# ---------------------------------------------------------------- search


def find(db: Session, query: str, sport: str | None = None, fan: str | None = None, limit: int = LIMIT) -> list[dict[str, Any]]:
    """Teams whose name or code has a word starting with every typed word (search as you type, beside players):
    a whole name typed first (every team with it, up to NAMESAKES), then the fan's own, then the most followed by
    signed-in fans, the biggest, the most followed by anyone (models.followers), the shortest name, `limit` in all
    unless more have the whole name. Only teams of sports running whose
    source runs. `sport` narrows to one sport, or a group's (esports); so does a sport's name typed with a team's
    ("india cricket", "liquid valorant"), when the words together find no team."""
    typed = registry.words(query)
    if not typed or len(" ".join(typed)) < 2:
        return []
    found = _find(db, typed, sport, fan, limit)
    for i, word in enumerate(typed if not found else []):
        named, rest = sports.get(word), typed[:i] + typed[i + 1:]
        if named is not None and rest and (not sport or named.key in sports.within(sport)):
            found = _find(db, rest, named.key, fan, limit)
            if found:
                break
    return found


def _find(db: Session, typed: list[str], sport: str | None, fan: str | None, limit: int) -> list[dict[str, Any]]:
    keys = [s.key for s in sports.ENABLED if s.follow_teams]
    if sport:
        keys = [k for k in keys if k in sports.within(sport)]
    if not keys:
        return []
    fans = models.followers(TeamFollow.team_id, TeamFollow.fan_id).subquery()
    accounts, follows = func.coalesce(fans.c.accounts, 0), func.coalesce(fans.c.follows, 0)
    whole = " ".join(typed)
    exact = or_(Team.search_text.like(f" {whole} |%"), Team.search_text.like(f"%| {whole} |%"))
    mine = Team.id.in_(select(TeamFollow.team_id).where(TeamFollow.fan_id == fan)) if fan else None
    order = [exact.desc(), *([mine.desc()] if mine is not None else []), accounts.desc(), Team.tier.desc(), follows.desc(), func.length(Team.name), Team.id]
    rows = db.execute(select(Team, accounts, exact).outerjoin(fans, fans.c.id == Team.id)
                      .where(*[Team.search_text.like(f"% {w}%") for w in typed], Team.sport.in_(keys), Team.system.in_(list(ADAPTERS)))
                      .order_by(*order).limit(max(limit, NAMESAKES))).all()
    rows = [(t, n) for i, (t, n, named) in enumerate(rows) if i < limit or named]
    theirs = _following(db, fan, [t.id for t, _ in rows])
    return [summary(t, n, t.id in theirs) for t, n in rows]


def _following(db: Session, fan: str | None, ids: list[int]) -> dict[int, str]:
    """Of these teams, the ones this fan follows, with their level."""
    if not fan or not ids:
        return {}
    return {tid: (rules or {}).get("level", moments.DEFAULT_LEVEL) for tid, rules in db.execute(select(TeamFollow.team_id, TeamFollow.alert_rules).where(TeamFollow.fan_id == fan, TeamFollow.team_id.in_(ids)))}


def summary(team: Team, followers: int = 0, following: bool = False) -> dict[str, Any]:
    """A team as the app lists it (search, Following): who, what it plays in, and its next game as last read."""
    card = team.card or {}
    nxt = next(iter(card.get("upcoming") or []), None)
    return {
        "team_id": team.id,
        "name": team.name,
        "short": team.short,
        "sport": team.sport,
        "detail": " · ".join(x for x in [*(team.competitions or [])[:1], team.country or ""] if x),
        "followers": followers,
        "following": following,
        "next": {k: nxt.get(k) for k in ("title", "start_utc", "status", "match", "versus")} if nxt else None,
    }


# ---------------------------------------------------------------- following


def follow(db: Session, fan: str, team_id: int) -> Team:
    """Follow a team (its id here). Refused for no such team, one of a sport not running, one whose source can't say
    its games now, or a fan already following MAX teams. Following one already followed changes nothing."""
    team = db.get(Team, team_id)
    if team is None or not followable(team.sport):
        raise Refused(404, "No such team.")
    if not readable(ADAPTERS.get(team.system)):
        raise Refused(409, NO_SOURCE)
    # One follow at a time per fan, so two at once can't both pass the count.
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"team-follow:{fan}", 0))))
    if db.scalar(select(TeamFollow.id).where(TeamFollow.fan_id == fan, TeamFollow.team_id == team.id)) is not None:
        return team
    if counted(db, fan) >= MAX:
        raise Refused(429, f"You follow {MAX} teams already: unfollow one to follow this.")
    db.execute(insert(TeamFollow).values(fan_id=fan, team_id=team.id, alert_rules={}).on_conflict_do_nothing())
    return team


def counted(db: Session, fan: str) -> int:
    """The teams a fan follows that count toward MAX: those of the sports that run. A turned-off sport's stay
    followed, out of sight (mine), and the fan can't unfollow them to make room."""
    return db.scalar(select(func.count()).select_from(TeamFollow).join(Team, Team.id == TeamFollow.team_id)
                     .where(TeamFollow.fan_id == fan, Team.sport.in_(sports.running_keys()))) or 0


def unfollow(db: Session, fan: str, team_id: int) -> None:
    db.execute(delete(TeamFollow).where(TeamFollow.fan_id == fan, TeamFollow.team_id == team_id))


def set_level(db: Session, fan: str, team_id: int, level: str) -> bool:
    """Which of the team's games' moments reach this fan (moments.LEVELS). False if they don't follow it."""
    row = db.scalar(select(TeamFollow).where(TeamFollow.fan_id == fan, TeamFollow.team_id == team_id))
    if row is None:
        return False
    row.alert_rules = {**(row.alert_rules or {}), "level": level}
    return True


def stale(team: Team, now: datetime | None = None) -> bool:
    """Whether a team's games are due to be read again: as often as a followed player's (FIXTURES_MAX_AGE) while
    it has a game on or within SOON, as its last read had them, else every QUIET. A game of it that ends has them
    read at the next tick (ended)."""
    from .config import FIXTURES_MAX_AGE

    now = now or _now()
    if team.fetched_at is None:
        return True
    soon = any(g.get("status") in ("armed", "live") or (g.get("start_utc") and datetime.fromisoformat(g["start_utc"]) <= now + SOON)
               for g in (team.card or {}).get("upcoming") or [])
    return team.fetched_at < now - (timedelta(seconds=FIXTURES_MAX_AGE) if soon else QUIET)


def due(db: Session, now: datetime) -> list[int]:
    """The followed teams whose games are due to be read again (the tick, pipeline.due_work: stale): of a sport
    running, from a source running that can say them and isn't down to what its live games need (structured.spare),
    not failing lately (structured.retry_at), and not being read now."""
    from .config import FIXTURES_MAX_AGE

    running = dict(ADAPTERS.items())
    room: dict[str, bool] = {}
    out = []
    for team in db.scalars(select(Team).where(Team.id.in_(select(TeamFollow.team_id)), or_(Team.fetched_at.is_(None), Team.fetched_at < now - timedelta(seconds=FIXTURES_MAX_AGE)))):
        adapter = running.get(team.system)
        if not stale(team, now) or not readable(adapter) or not followable(team.sport):
            continue
        retry = structured.retry_at(team.health)
        if retry and now < retry:
            continue
        if team.system not in room:
            room[team.system] = structured.spare(adapter)  # pyright: ignore[reportArgumentType]: readable() said it's there
        if room[team.system] and not bus.is_locked(f"team:{team.id}"):
            out.append(team.id)
    return out


# ---------------------------------------------------------------- its games, from its source


def refresh(team_id: int) -> None:
    """Read a team's games again from its source (the worker, for a followed team, or a page nobody follows it
    from): each one's event made or brought up to date and linked to the team, each result kept from its side,
    and its page. A source down to what its live games need isn't read (structured.spare), nor one whose share for this
    lane is spent for now (budget.RateLimited): both are tried at the next tick, and neither is a failure. A failure is
    noted in the team's health, which the tick waits on before the next try (structured.retry_at)."""
    lock = f"team:{team_id}"
    token = bus.try_lock(lock, REFRESH_LOCK)
    if not token:
        return
    try:
        with session() as db:
            team = db.get(Team, team_id)
            if team is None:
                return
            system, external, card = team.system, team.external_id, dict(team.card or {})
        adapter = ADAPTERS.get(system)
        if adapter is None or not readable(adapter):
            return
        if not structured.spare(adapter):
            log.info("team %s: %s is down to what live games need; next tick", team_id, system)
            return
        _refresh(team_id, adapter, external, card)
    except budget.RateLimited as exc:
        log.info("team %s: %s; next tick", team_id, exc)
    except AdapterError as exc:
        log.warning("team %s refresh failed: %s", team_id, exc)
        _health(team_id, ok=False, error=str(exc))
    except Exception as exc:
        log.exception("team %s refresh crashed", team_id)
        _health(team_id, ok=False, error=f"crashed: {exc}"[:300])
    finally:
        bus.release(lock, token)


def _refresh(team_id: int, adapter: Adapter, external: str, card: dict[str, Any]) -> None:
    now = _now()
    fixtures: list[Fixture] = adapter.team_fixtures(external)  # pyright: ignore[reportAttributeAccessIssue]: readable() said it has one
    keys = {f.source_id: f"{adapter.system}:{f.source_id}" for f in fixtures}
    with session() as db:
        here = {key: (event_id, status) for event_id, key, status in db.execute(select(Event.id, Event.key, Event.status).where(Event.key.in_(list(keys.values()))))}
    # A schedule can lag the match by its cache age: a game our poller saw finish is finished.
    for f in fixtures:
        if here.get(keys[f.source_id], (0, ""))[1] == "final":
            f.status = "final"
    finals = [f for f in fixtures if f.status == "final"]
    ahead = structured.upcoming(fixtures, now)
    # A game the schedule says is over that is still armed or live here, the poller didn't see finish: it gets a
    # last poll from its final board, so its result is told (structured.finish).
    unfinished = {event_id: f for f in finals if (event_id := here.get(keys[f.source_id], (0, ""))[0]) and here[keys[f.source_id]][1] in ("armed", "live")}
    boards = {}
    for event_id, f in unfinished.items():
        try:
            boards[event_id] = adapter.snapshot(f.locator)
        except AdapterError as exc:
            log.warning("team %s: no final board for %s: %s", team_id, f.source_id, exc)
            boards[event_id] = None
    if boards:
        structured.finish(boards)
    squad, squad_at = card.get("squad"), card.get("squad_at")
    if callable(getattr(adapter, "team", None)) and (not squad_at or datetime.fromisoformat(squad_at) < now - SQUAD_EVERY):
        try:
            found: SourceTeam | None = adapter.team(external)  # pyright: ignore[reportAttributeAccessIssue]: callable() said it has one
            squad = [{"athlete_id": p.athlete_id, "name": p.name, "league": p.league} for p in found.players[:SQUAD_MOST]] if found else squad
            squad_at = now.isoformat()
        except AdapterError as exc:
            log.warning("team %s keeps its squad: %s", team_id, exc)
    with session() as db:
        team = db.get(Team, team_id)
        if team is None:
            return  # removed meanwhile
        label = sports.label(team.sport)
        events = {f.source_id: structured.upsert_game(db, adapter, label, f) for f in [*finals, *ahead]}
        for event in events.values():
            db.execute(insert(EventTeam).values(event_id=event.id, team_id=team.id).on_conflict_do_nothing())
        last = _final_boards(db, [events[f.source_id].id for f in finals if not f.result])
        for f in finals:
            item = result(adapter, f, events[f.source_id], external)
            # A schedule kept a while (the source cache) can still have a game our poller saw finish as on, with no
            # result: its last board says it, from the team's side, else as the game's own. A result with nothing
            # to say isn't kept; a later read has it.
            if not item["result"] and (s := last.get(events[f.source_id].id)):
                rules_, score, clock = sports.of_state(s.state), s.score_label or "", s.clock_label or ""
                item["result"] = rules_.side_result(s.state or {}, score, clock, on_board(adapter, external)) or rules_.result_text(score, clock)
            if not item["result"]:
                continue
            db.execute(update(EventTeam).where(EventTeam.event_id == events[f.source_id].id, EventTeam.team_id == team.id).values(result=item))
        # Its last results from every one kept (EventTeam.result), not only this read's: a source that answers
        # with nothing doesn't empty the page; nor its games to come, of which those still to come stand till one
        # that lists any.
        kept = db.scalars(select(EventTeam.result).join(Event, Event.id == EventTeam.event_id).where(EventTeam.team_id == team.id, EventTeam.result.is_not(None))
                          .order_by(Event.start_utc.desc().nulls_last(), Event.id.desc()).limit(RECENT)).all()
        upcoming = [_upcoming(f, events[f.source_id], external) for f in ahead[:UPCOMING]]
        if not fixtures:
            upcoming = [g for g in card.get("upcoming") or [] if g.get("start_utc") and datetime.fromisoformat(g["start_utc"]) > now]
        team.card = {
            "upcoming": upcoming,
            "recent": list(kept),
            "squad": squad or [],
            "squad_at": squad_at,
            "source": source_name(adapter.system),
            "fetched_at": now.isoformat(),
        }
        team.fetched_at = now
        _health(team.id, ok=True, db=db)


def _versus(f: Fixture, external: str) -> str:
    """The other side of a team's game, by name ("Chelsea"); "" where the fixture doesn't say."""
    others = [n for tid, n in zip(f.team_ids, f.team_names) if tid != external and n]
    return others[0] if len(others) == 1 else ""


def _upcoming(f: Fixture, event: Event, external: str) -> dict[str, Any]:
    return {"title": f.title, "competition": f.competition, "start_utc": f.start_utc.isoformat() if f.start_utc else None, "venue": f.venue, "notes": f.detail,
            "status": event.status, "event_id": event.id, "match": structured.match_path(event.key), "versus": _versus(f, external)}


def result(adapter: Adapter, f: Fixture, event: Event, external: str) -> dict[str, Any]:
    """A finished game from the team's side, as its page lists it ("Won 2–1")."""
    return {"title": f"{f.title} · {f.detail}" if f.detail else f.title, "result": adapter.result_label(f, [external]) or "",
            "date": f.start_utc.date().isoformat() if f.start_utc else None, "start_utc": f.start_utc.isoformat() if f.start_utc else None,
            "competition": f.competition, "event_id": event.id, "match": structured.match_path(event.key), "versus": _versus(f, external)}


def on_board(adapter: Any, external: str) -> str:
    """A team's id as its source's boards have it, where the team's own says more (Adapter.board_id: "nba:13" is 13)."""
    board_id = getattr(adapter, "board_id", None)
    return str(board_id(external)) if callable(board_id) else external


def _final_boards(db: Session, event_ids: list[int]) -> dict[int, EventState]:
    """Each of these games' last board, as the poll that saw it finish had it; none for a game not polled to its end."""
    if not event_ids:
        return {}
    return {s.event_id: s for s in db.scalars(select(EventState).where(EventState.event_id.in_(event_ids), EventState.status == "final")
                                              .ext(distinct_on(EventState.event_id)).order_by(EventState.event_id, EventState.as_of.desc()))}


def _health(team_id: int, ok: bool, error: str | None = None, db: Session | None = None) -> None:
    def write(s: Session) -> None:
        team = s.get(Team, team_id)
        if team is None:
            return
        health = dict(team.health or {})
        health["checked_at"] = _now().isoformat()
        if ok:
            health.update(last_ok=health["checked_at"], failures=0, error=None)
        else:
            health.update(failures=int(health.get("failures") or 0) + 1, error=error)
        team.health = health

    if db is not None:
        write(db)
    else:
        with session() as s:
            write(s)


# ---------------------------------------------------------------- its games, as followed games (matches.py)


def watched() -> Select[int]:
    """The events of every team fans follow: polled as games followed by themselves (matches.watched)."""
    return select(EventTeam.event_id).where(EventTeam.team_id.in_(select(TeamFollow.team_id)))


def followed(db: Session, event_id: int) -> bool:
    """Whether any fan follows a team in this game."""
    return db.scalar(select(EventTeam.event_id).join(TeamFollow, TeamFollow.team_id == EventTeam.team_id).where(EventTeam.event_id == event_id).limit(1)) is not None


def rules(db: Session, event_id: int) -> list[dict[str, Any]]:
    """The alert rules ({"level"}) of everyone following a team in this game."""
    return [r or {} for r in db.scalars(select(TeamFollow.alert_rules).join(EventTeam, EventTeam.team_id == TeamFollow.team_id).where(EventTeam.event_id == event_id))]


def watches(db: Session, events: set[int]) -> dict[str, dict[int, tuple[str, set[str]]]]:
    """Of these games, the ones each fan follows a team in: their level for it (the loudest, for a fan following
    both sides), and whose side they're on (tag), of the teams they haven't set to off."""
    out: dict[str, dict[int, tuple[str, set[str]]]] = {}
    if not events:
        return out
    order = {level: i for i, level in enumerate(reversed(moments.LEVELS))}
    for fan, event_id, team_id, rules_ in db.execute(select(TeamFollow.fan_id, EventTeam.event_id, EventTeam.team_id, TeamFollow.alert_rules)
                                                     .join(EventTeam, EventTeam.team_id == TeamFollow.team_id).where(EventTeam.event_id.in_(events))):
        level = (rules_ or {}).get("level", moments.DEFAULT_LEVEL)
        had, tags = out.setdefault(fan, {}).get(event_id, (level, set()))
        out[fan][event_id] = (max(had, level, key=lambda x: order.get(x, 0)), {*tags, *([tag(team_id)] if level != "off" else [])})
    return out


def for_fan(fan: str) -> ColumnElement[bool]:
    """A game's own moment this fan may see: one told from no side, or from the side of a team they follow and
    haven't set to off (its result: side_finals); and a game's result once, from the side of their team where it's
    told so (the first team's, for a fan who follows both), in place of the game's own. The activity list's
    condition, for the games they follow by themselves as for their teams' games, as alerts.level_of and plan have
    it for a push: in the query, so a page is full and the next one starts where it ends, wherever that is."""
    def theirs(m: Any) -> Select[int]:
        mine = aliased(TeamFollow)  # apart from a query's own team follows (activity's), which it'd correlate to
        level = func.coalesce(mine.alert_rules["level"].astext, moments.DEFAULT_LEVEL)
        return select(mine.id).where(mine.fan_id == fan, level != "off", m.about.contains(func.jsonb_build_array(func.concat(TAG, mine.team_id))))

    sided_here = Moment.about.cast(Text).like(f'%"{TAG}%')
    side = aliased(Moment)  # the game's result from one of their teams' sides, told before this one or in its place
    told = select(side.id).where(side.event_id == Moment.event_id, side.player_id.is_(None), side.kind == "final", or_(~sided_here, side.id < Moment.id), theirs(side).exists())
    return and_(or_(~sided_here, theirs(Moment).exists()), or_(Moment.kind != "final", ~told.exists()))


def activity(fan: str, taken: Any, days: int) -> Select[Moment, str, bool, str]:
    """The game's own moments of the games of the teams this fan follows, since they followed each and within
    `days`, newest first, for the activity list (GET /api/me/activity): (moment, the game's title, whether they
    muted it, its sport), each once though they follow both sides. Not a team's they set to off; one from a side's,
    only from their team's. `taken` is the moment a correction takes back (an alias of Moment), as the list's other
    queries join it."""
    level = func.coalesce(TeamFollow.alert_rules["level"].astext, moments.DEFAULT_LEVEL)
    return (
        select(Moment, Event.title, NotificationMute.event_id.is_not(None), Event.sport)
        .ext(distinct_on(Moment.id))
        .join(EventTeam, EventTeam.event_id == Moment.event_id)
        .join(TeamFollow, and_(TeamFollow.team_id == EventTeam.team_id, TeamFollow.fan_id == fan))
        .join(Event, Event.id == Moment.event_id)
        .outerjoin(NotificationMute, and_(NotificationMute.fan_id == fan, NotificationMute.event_id == Moment.event_id))
        .outerjoin(taken, taken.id == Moment.corrects)
        .where(Moment.player_id.is_(None), level != "off", Moment.created_at >= TeamFollow.created_at, Moment.created_at >= func.now() - timedelta(days=days))
        .where(for_fan(fan), or_(Moment.corrects.is_(None), taken.created_at >= TeamFollow.created_at))
        .order_by(Moment.id.desc())
    )


def followed_in(db: Session, events: set[int]) -> dict[int, list[str]]:
    """The teams fans follow in each of these games, by tag: the fan stream's toasts say them, so the app finds a
    fan's level for a game's own moment by their team in it, as alerts.plan does for a push."""
    out: dict[int, list[str]] = {}
    if events:
        for event_id, team_id in db.execute(select(EventTeam.event_id, EventTeam.team_id).where(EventTeam.event_id.in_(events), EventTeam.team_id.in_(select(TeamFollow.team_id))).order_by(EventTeam.team_id)):
            out.setdefault(event_id, []).append(tag(team_id))
    return out


def side_finals(db: Session, event: Event, rules_: sports.Sport, state: dict[str, Any], score: str, clock: str) -> list[Found]:
    """A finished game's result from the side of each team fans follow in it (Sport.side_final), for those fans
    (alerts.plan: in place of the game's own): "Arsenal won 2–1 v Chelsea". About the whole game, so a fan who
    follows a player in it hears the result once, and about the team (tag)."""
    system = (event.key or "").partition(":")[0]
    found = []
    for team_id, external in db.execute(select(Team.id, Team.external_id).join(EventTeam, EventTeam.team_id == Team.id)
                                        .where(EventTeam.event_id == event.id, Team.system == system, Team.id.in_(select(TeamFollow.team_id))).order_by(Team.id)):
        said = rules_.side_final(state, score, clock, on_board(KNOWN.get(system), external))
        if said:
            found.append(Found("final", "key", said, score, f"team{team_id}", 80, ("*", tag(team_id))))
    return found


# ---------------------------------------------------------------- the app's views


def _boards(db: Session, team_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Each team's game on now (armed or live), as its board was last polled, shaped as a followed game's (matches.board)."""
    if not team_ids:
        return {}
    rows = db.execute(select(EventTeam.team_id, Event).join(Event, Event.id == EventTeam.event_id)
                      .where(EventTeam.team_id.in_(team_ids), Event.status.in_(("armed", "live"))).order_by(Event.start_utc.asc().nulls_last())).all()
    ids = [e.id for _, e in rows]
    latest = {s.event_id: s for s in db.scalars(select(EventState).where(EventState.event_id.in_(ids)).ext(distinct_on(EventState.event_id)).order_by(EventState.event_id, EventState.as_of.desc()))} if ids else {}
    out: dict[int, dict[str, Any]] = {}
    for team_id, event in rows:
        s = latest.get(event.id)
        if team_id in out or s is None:
            continue
        out[team_id] = matches.board(event, s.status, s.score_label, s.clock_label, s.state or {}, s.source_url, s.as_of)
    return out


def event_ids(db: Session, fan: str, now: datetime | None = None) -> list[int]:
    """The games of this fan's teams on now or starting within STREAM_AHEAD, by event: the fan stream's channels
    (bus.match_channel), its board as it's polled and its own moments. A stream connects again often enough (a
    phone back from the lock screen, a follow) that a game further off is on one by the time it starts."""
    now = now or _now()
    soon = or_(Event.status.in_(("armed", "live")), and_(Event.status == "scheduled", Event.start_utc.is_not(None), Event.start_utc <= now + STREAM_AHEAD, Event.start_utc >= now - timedelta(hours=6)))
    return list(db.scalars(select(EventTeam.event_id).distinct().join(Event, Event.id == EventTeam.event_id)
                           .join(TeamFollow, and_(TeamFollow.team_id == EventTeam.team_id, TeamFollow.fan_id == fan)).where(soon)))


def mine(db: Session, fan: str) -> list[dict[str, Any]]:
    """The teams this fan follows, as Following and Today list them: each with their level, its next game and its
    games to come (Today's Coming up), its last result, and its game on now as last polled."""
    rows = db.execute(select(Team, TeamFollow.alert_rules).join(TeamFollow, TeamFollow.team_id == Team.id).where(TeamFollow.fan_id == fan).order_by(TeamFollow.created_at, Team.id)).all()
    boards = _boards(db, [t.id for t, _ in rows])
    out = []
    for team, rules_ in rows:
        if not sports.on(team.sport):
            continue  # a sport turned off: its follow stays, unseen, for when it's back
        last = next(iter((team.card or {}).get("recent") or []), None)
        out.append({**summary(team, following=True), "alerts": (rules_ or {}).get("level", moments.DEFAULT_LEVEL), "last": last, "live": boards.get(team.id),
                    "upcoming": (team.card or {}).get("upcoming") or []})
    return out


def page(db: Session, team_id: int, fan: str | None) -> dict[str, Any] | None:
    """A team's page (GET /api/teams/{id}): who it is, whether this fan follows it and at what level, whether it can
    be followed now, its game on now, its games to come and last results from its side (as last read), and its
    squad as the app's result rows (followed by the source's ids, filled in for players on Sports Follow). None for
    no such team, or one of a sport not running."""
    team = db.get(Team, team_id)
    if team is None or not followable(team.sport):
        return None
    card = team.card or {}
    counted = db.execute(models.followers(TeamFollow.team_id, TeamFollow.fan_id).where(TeamFollow.team_id == team.id)).first()
    followers = counted.accounts if counted else 0  # signed-in fans, as search says (models.followers)
    level = _following(db, fan, [team.id]).get(team.id)
    can = readable(ADAPTERS.get(team.system))
    blocked = registry.blocked(db)
    label = sports.label(team.sport)
    refs = [PlayerRef(system=team.system, athlete_id=p["athlete_id"], name=p["name"], team_ids=[team.external_id], team_names=[team.name], league=p.get("league"))
            for p in card.get("squad") or [] if p.get("athlete_id") and (team.system, p["athlete_id"]) not in blocked]
    form = [o for r in card.get("recent") or [] if (o := sports.of(team.sport).outcome(r.get("result") or "", [team.name]))]  # "India won by 14 runs": a W for India
    return {
        **summary(team, followers, level is not None),
        "alerts": level or moments.DEFAULT_LEVEL,
        "can_follow": can,
        "note": None if can else NO_SOURCE,
        "live": _boards(db, [team.id]).get(team.id),
        "upcoming": card.get("upcoming") or [],
        "recent": card.get("recent") or [],
        "form": form,
        "squad": [search.public(r) for r in search.offered_refs(db, label, refs, fan)] if team.system in ADAPTERS else [],
        "source": card.get("source") or source_name(team.system),
        "fetched_at": card.get("fetched_at"),
    }


def in_game(db: Session, fan: str | None, system: str, state: dict[str, Any], event_ids: list[int]) -> list[dict[str, Any]]:
    """The teams a game's match page offers to follow (match.view): its sides as the registry has them, by their ids
    on its board (Sport's sides()), else as its events' fixtures named them (Event.participants: a game to come has
    no board yet), and the teams its events are linked to (event_team), in the game's order; each with whether this
    fan follows it, and whether it can be followed now (its sport runs, its source can say its games). A source whose
    team ids say more than its boards' (Adapter.board_id: ESPN basketball's carry the league, so "5" is the Cavaliers
    and the Fever) has a side's team told by its name, on the board or in the game's title; one it can't tell isn't
    offered. Teams of a sport whose teams can't be followed, and sides the registry doesn't have, aren't offered."""
    pairs = [(str(side.get("id")), str(side.get("name") or "")) for side in sports_sides(state) if side.get("id") not in (None, "")]
    if not pairs and event_ids:
        named = db.scalars(select(Event.participants).where(Event.id.in_(event_ids), Event.key.startswith(f"{system}:"))).all()
        pairs = [(i, "") for i in dict.fromkeys(str(x) for ids in named for x in ids or [] if x not in (None, ""))]
    titles = " | ".join(naming.fold(t) for t in db.scalars(select(Event.title).where(Event.id.in_(event_ids)))) if event_ids else ""
    board_id = getattr(KNOWN.get(system), "board_id", None)
    found: dict[int, Team] = {}
    order: dict[int, int] = {}
    for n, (side, name) in enumerate(pairs):
        exact = db.scalar(select(Team).where(Team.system == system, Team.external_id == side))
        if exact is not None:
            found[exact.id], order[exact.id] = exact, n
            continue
        if not callable(board_id):
            continue
        maybe = [t for t in db.scalars(select(Team).where(Team.system == system, Team.external_id.like(f"%:{side}"))) if str(board_id(t.external_id)) == side]
        if len(maybe) > 1:  # one number, several teams: the one this side names
            maybe = [t for t in maybe if (name and naming.team_key(t.name) == naming.team_key(name)) or (not name and naming.fold(t.name) in titles)]
        if len(maybe) == 1:
            found[maybe[0].id], order[maybe[0].id] = maybe[0], n
    if event_ids:
        for team in db.scalars(select(Team).join(EventTeam, EventTeam.team_id == Team.id).where(EventTeam.event_id.in_(event_ids))):
            found.setdefault(team.id, team)
    shown = sorted((t for t in found.values() if followable(t.sport)), key=lambda t: (order.get(t.id, len(pairs)), t.id))
    theirs = _following(db, fan, [t.id for t in shown])
    can = readable(ADAPTERS.get(system))
    return [{"team_id": t.id, "name": t.name, "sport": t.sport, "following": t.id in theirs, "can_follow": can} for t in shown]


def ended(db: Session, event_id: int) -> None:
    """A game of followed teams just ended (its last poll, structured._game_moments): their games are read again at
    the next tick, not a half hour on, so the result reaches Following and Today's Results from their side soon."""
    db.execute(update(Team).where(Team.id.in_(select(EventTeam.team_id).where(EventTeam.event_id == event_id)), Team.id.in_(select(TeamFollow.team_id))).values(fetched_at=None))


def look_again(team: Team) -> bool:
    """Whether a team's page asks its source for its games now: they're stale (nobody follows it, or the tick hasn't
    got to it), its source can say them, and no page asked within LOOK_AGAIN."""
    return stale(team) and readable(ADAPTERS.get(team.system)) and bus.cooldown(f"teamlook:{team.id}", LOOK_AGAIN)


def look_later(team_id: int) -> None:
    """A page's ask (look_again) that the minute's reads had no room for (READS): the next page may ask, not only
    one LOOK_AGAIN on, so a burst of other pages can't keep this one empty."""
    bus.release(f"teamlook:{team_id}")


def merge(db: Session, anon: str, fan: str) -> int:
    """An anonymous fan's team follows into their account's (accounts.adopt): a team both follow keeps the
    account's level and the earlier date. The account follows at most MAX: of this device's others, the earliest
    followed that fit come over. Returns how many didn't, for the fan to be told (BUG-228). One at a time with the
    account's own follows (follow's lock), so a follow on another device as this one signs in can't pass MAX."""
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"team-follow:{fan}", 0))))
    have = set(db.scalars(select(TeamFollow.team_id).where(TeamFollow.fan_id == fan)))
    room, dropped = MAX - counted(db, fan), 0
    running = set(sports.running_keys())
    rows = db.execute(select(TeamFollow, Team.sport).join(Team, Team.id == TeamFollow.team_id)
                      .where(TeamFollow.fan_id == anon).order_by(TeamFollow.created_at, TeamFollow.id)).all()
    for row, sport in rows:
        if row.team_id in have:
            db.execute(update(TeamFollow).where(TeamFollow.fan_id == fan, TeamFollow.team_id == row.team_id).values(created_at=func.least(TeamFollow.created_at, row.created_at)))
        elif sport not in running or room > 0:  # a turned-off sport's comes too, hidden, and takes no room (counted)
            db.execute(insert(TeamFollow).values(fan_id=fan, team_id=row.team_id, alert_rules=row.alert_rules, created_at=row.created_at).on_conflict_do_nothing())
            room -= sport in running
        else:
            dropped += 1
    db.execute(delete(TeamFollow).where(TeamFollow.fan_id == anon))
    return dropped


# ---------------------------------------------------------------- the command


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m sports_follow.teams", description="The team registry: import, add, remove and restore teams (team, team_override).")
    sub = parser.add_subparsers(dest="command", required=True)
    i = sub.add_parser("import", help="read the running sources' lists of teams now (the worker does it weekly)")
    i.add_argument("system", nargs="?", help="one source, by its system")
    ls = sub.add_parser("list", help="the registry's teams, and what's been decided")
    ls.add_argument("name", nargs="?")
    a = sub.add_parser("add", help="a team the lists don't have, read from its source and kept")
    a.add_argument("system")
    a.add_argument("team_id")
    a.add_argument("--note")
    r = sub.add_parser("remove", help="take a team out, with its follows, and keep it out: its id here, or system:team id")
    r.add_argument("ref")
    r.add_argument("--note")
    b = sub.add_parser("restore", help="take back what was decided about a source's team")
    b.add_argument("system")
    b.add_argument("team_id")
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            chosen = [a for a in importers() if not args.system or a.system == args.system]
            if not chosen:
                raise Refused(404, f"{args.system or 'no source'} isn't a running source with a list of teams")
            for adapter in chosen:
                print(f"{adapter.system}: {import_teams(adapter)} teams")
        elif args.command == "list":
            with session() as db:
                stmt = select(Team).order_by(Team.sport, Team.name).limit(200)
                if args.name:
                    stmt = stmt.where(*[Team.search_text.like(f"% {w}%") for w in registry.words(args.name)])
                for t in db.scalars(stmt):
                    print(f"{t.id:6}  {t.sport:10}  {t.system}:{t.external_id:12}  {t.name}{f'  ({t.short})' if t.short else ''}  {' · '.join(t.competitions or [])}")
                for o in db.scalars(select(TeamOverride).order_by(TeamOverride.created_at)):
                    print(f"{o.action:6}  {o.sport:10}  {o.system}:{o.external_id:12}  {o.name}{f'  ({o.note})' if o.note else ''}  {o.created_at:%Y-%m-%d}")
        elif args.command == "add":
            with session() as db:
                team = add(db, args.system, args.team_id, args.note)
                print(f"Kept {team.name} ({team.sport}, {team.system}:{team.external_id}, id {team.id}): no import drops it.")
        elif args.command == "remove":
            with session() as db:
                team = get(db, args.ref)
                if team is None:
                    raise Refused(404, f"no team has the id {args.ref}")
                name = team.name
                follows = remove(db, team, args.note)
                print(f"Removed {name} and its {follows} follow{'' if follows == 1 else 's'}; no import writes it again.")
        elif args.command == "restore":
            with session() as db:
                print("Taken back: the next import may write it again." if restore(db, args.system, args.team_id) else "Nothing was decided about that team.")
    except Refused as exc:
        print(exc.message, file=sys.stderr)
        return 1
    except AdapterError as exc:
        print(f"The source didn't answer: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    logs.setup()
    sys.exit(main(sys.argv[1:]))
