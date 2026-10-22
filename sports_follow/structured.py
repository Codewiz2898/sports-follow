"""Structured sources in the pipeline (design sections 4 and 5).

Once the research agent has said who a player is and what they play, bind() looks the player up
in that sport's adapter and records the identity and source bindings. From then on the player's
fixtures, results and stats come from the source (refresh), and every event with a live binding
is polled by one job shared by every player in it (poll_event), or, where the source can fetch many
events in one request, together with all of that source's other events (poll_lane). A game a fan follows
by itself (matches.py) is polled the same way, whether or not anyone follows a player in it, and each of
its polls finds the game's own moments too (Sport.game_moments). The agent keeps the profile and the news,
and stays the only source for sports without an adapter.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy import and_, delete, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.orm import Session

from . import alerts, bus, engine, livecards, matches, metrics, moments, naming, notify, predictions, registry, shutdown, sports, tracing
from .adapters import ADAPTERS, KNOWN, NOT_FOUND, Adapter, AdapterError, Fixture, Line, NotFound, PlayerRef, Snapshot, budget, for_sport, providers, source_name
from .config import ARM_BEFORE_START, POLL_SESSION
from .db import session
from .models import Event, EventPlayer, EventState, EventTeam, Follow, Player, PlayerCard, PlayerIdentity, PlayerLine, SourceBinding, Team

log = logging.getLogger("sports_follow.structured")

# Card sections a bound player's source owns; an agent rebuild keeps them as they are.
OWNED = ("live", "upcoming", "recent_results", "season_stats", "season_stats_note", "provenance")
BIND_RETRY = 6 * 3600
# A bound player is read again from their source this often, in a refresh (reread): one request, which the sources
# keep in their caches as long. A transfer then moves their fixtures to the new team's within hours.
REREAD = 6 * 3600
# A refresh's lock outlasts the worker's job_timeout (900 s): arq's timeout doesn't stop the refresh's
# thread (a Lichess refresh reads for minutes), so the lock, not the job, says one is still running.
REFRESH_LOCK = 20 * 60
# A failed refresh is tried again after REFRESH_RETRY, twice as long after each failure in a row, at most
# REFRESH_RETRY_MAX: an outage, or a team the plan doesn't cover, mustn't cost a request every minute.
REFRESH_RETRY = 2 * 60
REFRESH_RETRY_MAX = 2 * 3600
# Polls of a live event further apart than this (or three of its source's rounds) left a gap: a restart,
# or the source failing for a while. What changed meanwhile is old news, not a moment.
POLL_GAP = 5 * 60
# A fixture whose poll committed this lately tells its side of a game the fan hears from the other side too
# (told_lately): well inside POLL_GAP, so its next poll still comes in time to find the moment rather than start a
# new baseline.
TELLS_WITHIN = 2 * 60
# A game's fixtures (a chess board is one of each player's) start within this of each other: a delayed round's new
# start read by one player's refresh and not yet by the other's (boards).
SAME_GAME = timedelta(hours=6)
PRE_MATCH = ("scheduled", "armed", "postponed")
UPCOMING_LIMIT = 8
RECENT_LIMIT = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def match_path(key: str | None) -> str | None:
    """A game's match page address (match.py: after /match/), from its event's key: a source's game
    ("sportmonks_football:19134567") is "sportmonks_football/19134567"; one the research agent wrote up, which
    no source has, has none."""
    system, sep, game = (key or "").partition(":")
    return f"{system}/{game}" if sep and game and system in KNOWN else None


def game_key(key: str | None, state: dict[str, Any] | None) -> str | None:
    """The game an event's board is of, where one game is under several fixtures (a chess board is one of each
    player's): its key, the same under each and its questions' (predictions.game_key), so Today shows the game once
    whichever of the fan's players it's under. None where the fixture is the game: its event says it."""
    state = state or {}
    return board_key(key, sports.of_state(state).board(state))


def board_key(key: str | None, board: str) -> str | None:
    """game_key by the board's id itself (Sport.board's), as a fixture says it before its game has a board to read
    (Fixture.board: a chess round once it's paired), so a game to come is the same game it is once it's on."""
    if not board:
        return None
    system, _, fixture = (key or "").partition(":")
    return predictions.game_key(system, fixture, board)


@dataclass
class Fixtures:
    """A game under several fixtures (a chess board is one of each player's, each polled as an event of its own), as
    one of them knows it: the players on it here by their ids in its source, and each one's fixture of it, the same
    all through a mini-match's games."""

    players: dict[str, int]
    events: dict[int, int]  # player id: their fixture's event


def _boards_shown(db: Session, ids: Iterable[int]) -> dict[int, tuple[str, datetime | None, str, tuple[str, ...], str]]:
    """These events' latest boards where a game is under several fixtures: each one's source, start, board (Sport.board,
    the game on it now), the players on it (Sport.board_players), sorted, and which meeting of theirs it is
    (Sport.pairing)."""
    latest = (select(EventState.event_id, Event.key, Event.start_utc, EventState.state).join(Event, Event.id == EventState.event_id).where(EventState.event_id.in_(sorted(set(ids))))
              .ext(distinct_on(EventState.event_id)).order_by(EventState.event_id, EventState.as_of.desc()))
    out = {}
    for event_id, key, start, state in db.execute(latest):
        rules = sports.of_state(state or {})
        board = rules.board(state or {})
        players = tuple(sorted(rules.board_players(state or {}))) if board else ()
        pairing = rules.pairing(state or {}) if players else ""
        if pairing:  # one whose board can't say which meeting it is isn't taken for another's fixture: each tells its own
            out[event_id] = ((key or "").partition(":")[0], start, board, players, pairing)
    return out


def boards(db: Session, event_ids: Iterable[int | None]) -> dict[int, Fixtures]:
    """Each of these events that's one fixture of a game under several (Sport.board, by its latest state), with the
    game's fixtures here: of the players on it (Sport.board_players), each one's event whose latest board is the same
    meeting of theirs (Sport.pairing: not the same players in another round), from the same source, starting within
    SAME_GAME of it (or with no start, touched in the last day); of two, the one on the same board now, else the
    nearer start, else the newer. A fixture whose board isn't out yet isn't one of them till it is. Alerts, live cards
    and mutes take them for one game (BHA-378). An event whose fixture is the game, or with no board yet, isn't in it."""
    on = _boards_shown(db, {e for e in event_ids if e})
    if not on:
        return {}
    pairs = {(system, p) for system, _, _, players, _ in on.values() for p in players}
    here = {(system, external): player_id for player_id, system, external in db.execute(
        select(PlayerIdentity.player_id, PlayerIdentity.system, PlayerIdentity.external_id).where(tuple_(PlayerIdentity.system, PlayerIdentity.external_id).in_(pairs)))}
    rows: list[tuple[int, int]] = []
    if here:
        starts = [start for _, start, _, _, _ in on.values() if start is not None]
        lately = Event.updated_at >= func.now() - timedelta(days=1)
        when = [and_(Event.start_utc.is_(None), lately)]
        if starts:
            when.append(Event.start_utc.between(min(starts) - SAME_GAME, max(starts) + SAME_GAME))
        if len(starts) < len(on):
            when.append(lately)  # one with no start of its own: its others may have one
        found = select(EventPlayer.player_id, Event.id).join(Event, Event.id == EventPlayer.event_id).where(EventPlayer.player_id.in_(set(here.values())), or_(*when)).order_by(Event.id)
        rows = [(player_id, e) for player_id, e in db.execute(found)]
    shown = _boards_shown(db, {e for _, e in rows}) if rows else {}
    out = {}
    for event_id, (system, start, board, players, pairing) in on.items():
        mine = {p: here[(system, p)] for p in players if (system, p) in here}
        best: dict[int, tuple[tuple[bool, float, int], int]] = {}
        for player_id, e in rows:
            other = shown.get(e)
            if player_id not in mine.values() or other is None or other[0] != system or other[3] != players or other[4] != pairing:
                continue
            apart = abs((other[1] - start).total_seconds()) if other[1] is not None and start is not None else 0.0
            if apart > SAME_GAME.total_seconds():
                continue
            rank = (other[2] != board, apart, -e)
            if player_id not in best or rank < best[player_id][0]:
                best[player_id] = (rank, e)
        out[event_id] = Fixtures(mine, {player_id: e for player_id, (_, e) in best.items()})
    return out


def told_lately(event_ids: Iterable[int]) -> set[int]:
    """Which of these events told their side within TELLS_WITHIN (apply_poll notes each poll once it's committed), so
    their players' moments are being found and will be: a fixture that stalls past it, or whose polls keep failing,
    lets the other side say them; none where Redis can't say."""
    ids = sorted(set(event_ids))
    if not ids:
        return set()
    try:
        told = cast(list, bus.sync_redis().mget([f"told_at:{e}" for e in ids]))
    except Exception:
        log.warning("when events %s last told their side: Redis didn't say", ids, exc_info=True)
        return set()
    now = time.time()
    return {e for e, at in zip(ids, told) if at and now - float(at) <= TELLS_WITHIN}


def game_events(db: Session, event_id: int) -> list[int]:
    """Every event of the game an event is of, itself first: a game under several fixtures (boards) is muted, and its
    live card stopped, under each of them."""
    found = boards(db, [event_id]).get(event_id)
    return [event_id, *sorted(set(found.events.values()) - {event_id})] if found else [event_id]


def binding(db: Session, player_id: int) -> SourceBinding | None:
    """The player's fixtures binding, if a running adapter knows them (a licensed-only build ignores
    bindings to sources it has turned off, so those players fall back to the agent)."""
    return db.scalar(
        select(SourceBinding)
        .where(SourceBinding.player_id == player_id, SourceBinding.purpose == "fixtures", SourceBinding.adapter.in_(list(ADAPTERS)))
        .order_by(SourceBinding.priority.desc(), SourceBinding.id.desc())
        .limit(1)
    )


def _teams_overlap(found: list[str], known: list[str]) -> bool:
    a = [naming.fold(t) for t in found if t]
    b = [naming.fold(t) for t in known if t]
    return any(x in y or y in x for x in a for y in b)


# ---------------------------------------------------------------- recognizing a name without the agent


def _name_key(name: str) -> list[str]:
    """A name's words in any order, folded as the registry folds them ("N’Golo Kanté" == "ngolo kante")."""
    return sorted(registry.words(name))


def identify(query: str) -> tuple[Adapter, PlayerRef] | NotFound | None:
    """The one athlete a typed name means, found in the structured sources alone, or None.

    Only a full name that exactly one source claims counts: "Caitlin Clark" yes; "Clark", a typo,
    or a name two sports share go to the research agent, which decides as before (None). A name only
    athletes nobody here follows have is NOT_FOUND, unless a later provider has someone by it: nobody to
    research either.
    """
    if len(_name_key(query)) < 2:
        return None
    # Each provider that can recognise a name is asked in turn (adapters.base.Provider.recognise): ESPN
    # first, as it knows namesakes in every sport, then Lichess's FIDE list.
    not_found = False
    for provider, adapters in providers():
        if provider.recognise is None:
            continue
        found = provider.recognise(query, adapters)
        if found is False:
            return None
        if isinstance(found, tuple):
            return found
        not_found = not_found or found == NOT_FOUND
    return NOT_FOUND if not_found else None


# ---------------------------------------------------------------- binding


def bind(player_id: int, ref: PlayerRef | None = None) -> bool:
    """Record the player's identity and source bindings. False if no adapter knows them.

    With a ref (a source already said exactly who this is: a search result the fan picked, or a name
    only one athlete has) it is recorded as given. Without one, a player already bound or holding an
    identity is re-read by that id, and only a player with neither is looked up by name.
    """
    given = ref is not None
    with session() as db:
        player = db.get(Player, player_id)
        if player is None:
            return False
        name, sport, teams = player.name, player.sport, list(player.teams or [])
        adapter = ADAPTERS.get(ref.system) if ref else for_sport(sport)
        if adapter is None:
            return False
        pinned = None
        person = None
        if ref is None:
            row = db.scalar(select(SourceBinding).where(SourceBinding.player_id == player_id, SourceBinding.purpose == "fixtures", SourceBinding.adapter == adapter.system))
            ident = db.scalar(select(PlayerIdentity).where(PlayerIdentity.player_id == player_id, PlayerIdentity.system == adapter.system))
            qid = db.scalar(select(PlayerIdentity.external_id).where(PlayerIdentity.player_id == player_id, PlayerIdentity.system == "wikidata"))
            if row and (row.locator or {}).get("athlete_id"):
                pinned = (str(row.locator["athlete_id"]), row.locator.get("league"))
            elif ident:
                pinned = (ident.external_id, None)
            elif qid:
                athlete = registry.get(db, qid, sport.lower())
                person = registry.Person.of(athlete) if athlete else None
    if ref is None:
        try:
            # Re-read every build, as a refresh re-reads a bound player every few hours (reread): a transfer shows
            # up in the team ids within hours.
            if pinned:
                ref = adapter.player(*pinned)
            elif person is not None:
                # In the registry: found by the id Wikidata has, or by name and birth date, never name alone.
                found = registry.resolve(person)
                ref = found[1] if found else None
                pinned = (ref.athlete_id, ref.league) if ref else None
            else:
                ref = adapter.find_player(name)
        except AdapterError as exc:
            log.warning("bind %s: %s lookup failed: %s", player_id, adapter.system, exc)
            return False
    if ref is None:
        log.info("bind %s: %s has no athlete %s", player_id, adapter.system, f"with id {pinned[0]}" if pinned else f"named {name!r}")
        return False
    # Picked by id, or the name matched and a team the agent also named makes it certain; so does the same
    # full name when the agent named no team, or in individual sports, where the source has none. A name
    # found by search and nothing else is a namesake as often as not (the agent's "Mohamed Salah" of Al
    # Ahly isn't ESPN's of Liverpool), so it isn't bound. Esports handles repeat across games ("Jonathan"
    # plays BGMI, another Jonathan LoL): there the team must match.
    same_name = _name_key(ref.name) == _name_key(name)
    overlap = _teams_overlap(ref.team_names, teams)
    individual = ref.team_ids == [ref.athlete_id]
    certain = bool(pinned) or overlap or (same_name and not getattr(adapter, "games", None) and (not teams or individual))
    if not (certain or given):
        log.info("bind %s: %s's %r (%s) may not be the %r of %s; not binding", player_id, adapter.system, ref.name, ", ".join(ref.team_names) or "no team", name, ", ".join(teams) or "no team")
        return False
    confidence = 1.0 if certain else 0.8
    known = {naming.team_key(t) for t in ref.team_names}
    ref.other_teams = [t for t in teams if t and naming.team_key(t) not in known]
    return record(player_id, ref, confidence)


def record(player_id: int, ref: PlayerRef, confidence: float = 1.0) -> bool:
    """Write a player's identity in one source and their fixtures and stats bindings to it."""
    with session() as db:
        holder = db.scalar(select(PlayerIdentity).where(PlayerIdentity.system == ref.system, PlayerIdentity.external_id == ref.athlete_id))
        if holder and holder.player_id != player_id:
            log.warning("bind %s: %s athlete %s already belongs to player %s", player_id, ref.system, ref.athlete_id, holder.player_id)
            return False
        db.execute(delete(PlayerIdentity).where(PlayerIdentity.player_id == player_id, PlayerIdentity.system == ref.system, PlayerIdentity.external_id != ref.athlete_id))
        if holder is None:
            db.add(PlayerIdentity(player_id=player_id, system=ref.system, external_id=ref.athlete_id, confidence=confidence, evidence_url=ref.profile_url))
        else:
            holder.confidence, holder.evidence_url = confidence, ref.profile_url
        for purpose in ("fixtures", "stats"):
            row = db.scalar(select(SourceBinding).where(SourceBinding.player_id == player_id, SourceBinding.purpose == purpose, SourceBinding.adapter == ref.system))
            if row is None:
                row = SourceBinding(player_id=player_id, purpose=purpose, adapter=ref.system, priority=10, health={})
                db.add(row)
            row.locator = asdict(ref)
            row.confidence = confidence
        # The agent's guesses at upcoming games give way to the source's fixtures.
        guessed = select(Event.id).where(Event.live_binding.is_(None), Event.status.in_(("scheduled", "armed", "live")))
        db.execute(delete(EventPlayer).where(EventPlayer.player_id == player_id, EventPlayer.event_id.in_(guessed)))
        # A player moving to another source for their sport leaves the old one behind: its bindings, and
        # its games, played or not, so one match isn't polled, alerted or listed twice. The new source's
        # refresh and history lane read the results back.
        old = db.scalars(select(SourceBinding.adapter).where(SourceBinding.player_id == player_id, SourceBinding.adapter != ref.system).distinct()).all()
        for system in old:
            theirs = select(Event.id).where(Event.key.like(f"{system}:%"))
            db.execute(delete(EventPlayer).where(EventPlayer.player_id == player_id, EventPlayer.event_id.in_(theirs)))
        if old:
            db.execute(delete(SourceBinding).where(SourceBinding.player_id == player_id, SourceBinding.adapter.in_(old)))
            log.info("bind %s: moved from %s to %s", player_id, ", ".join(old), ref.system)
    log.info("bind %s: %s athlete %s (%s), confidence %.1f", player_id, ref.system, ref.athlete_id, ", ".join(ref.team_names), confidence)
    return True


def rekey_bound(adapter: Adapter) -> int:
    """Move every player bound to a source that has changed how it names its athletes (its rekey) to the
    id it gives them now, followed or not: their live lines are keyed by it, and search lists a player by
    it, beside the registry's row. A player whose new id another already holds is left as they are and
    named once a day, for an operator to merge the two. Returns how many moved."""
    rekey = getattr(adapter, "rekey", None)
    if rekey is None:
        return 0
    with session() as db:
        rows = db.scalars(select(SourceBinding).where(SourceBinding.adapter == adapter.system, SourceBinding.purpose == "fixtures"))
        bound = [(row.player_id, PlayerRef(**row.locator), row.confidence) for row in rows if row.player_id is not None]
    moved = 0
    for player_id, ref, confidence in bound:
        new = rekey(ref)
        if new is None:
            continue
        with session() as db:
            holder = db.scalar(select(PlayerIdentity.player_id).where(PlayerIdentity.system == new.system, PlayerIdentity.external_id == new.athlete_id))
        if holder is not None and holder != player_id:
            if bus.cooldown(f"rekey:{player_id}", 24 * 3600):
                log.warning("rekey %s: player %s (%s) is player %s (%s); merge them", adapter.system, player_id, ref.athlete_id, holder, new.athlete_id)
            continue
        moved += record(player_id, new, confidence)
    return moved


def reread(player_id: int, adapter: Adapter, ref: PlayerRef) -> PlayerRef:
    """A bound player as their source has them now, read again in a refresh every REREAD seconds (one request, in
    the refresh's lane, so counted against the source's budget there), their bindings moved to the teams it names
    if those changed (record_move); otherwise the ref as bound. An athlete of an individual sport has no team to
    change and isn't read again. A source with nothing left for the refresh lane is asked at the next refresh, and
    that's no failure; one that fails is asked after REREAD. Either way the refresh goes on as bound."""
    mark = f"reread:{player_id}"
    if ref.team_ids == [ref.athlete_id] or not bus.cooldown(mark, REREAD):
        return ref
    try:
        fresh = adapter.player(ref.athlete_id, ref.league)
    except budget.RateLimited:
        bus.release(mark)
        return ref
    except AdapterError as exc:
        log.warning("refresh %s: %s couldn't say again who athlete %s is: %s", player_id, adapter.system, ref.athlete_id, exc)
        return ref
    return (record_move(player_id, adapter, fresh) if fresh else None) or ref


def record_move(player_id: int, adapter: Adapter, fresh: PlayerRef) -> PlayerRef | None:
    """Move a bound player's bindings to a fresh read of them from their source (a refresh's reread, or a fan's
    pick from search) where it names other teams than the bound ones: a transfer, or a pro leaving their team.
    Their fixtures are then the new team's, their old team's games still to come are no longer theirs (one against
    the new team stays), and their card's fixtures are due at the next tick. What the read leaves out (a league, a
    birth date) stays as bound, and so do the other sides the agent named, less any the source names now. A read
    naming no team moves them off their team only where the source says that's real (Adapter.no_team_is_real).
    Returns the ref now bound, or None where nothing moved: not bound to that source, another athlete, the same
    teams, or no team from a source that may leave it out."""
    with session() as db:
        row = db.scalar(select(SourceBinding).where(SourceBinding.player_id == player_id, SourceBinding.purpose == "fixtures", SourceBinding.adapter == fresh.system))
        if row is None or not row.locator:
            return None
        old, confidence = PlayerRef(**row.locator), row.confidence
    if fresh.athlete_id != old.athlete_id or set(fresh.team_ids) == set(old.team_ids):
        return None
    if not fresh.team_ids and not getattr(adapter, "no_team_is_real", False):
        log.info("bind %s: %s's read of athlete %s names no team; kept on %s", player_id, fresh.system, fresh.athlete_id, ", ".join(old.team_names) or "no team")
        return None
    known = {naming.team_key(t) for t in fresh.team_names if t}
    ref = replace(fresh, profile_url=fresh.profile_url or old.profile_url, league=fresh.league or old.league, born=fresh.born or old.born,
                  game=fresh.game or old.game, other_teams=[t for t in old.other_teams if naming.team_key(t) not in known])
    if not record(player_id, ref, confidence):
        return None
    left, teams = set(old.team_ids) - set(ref.team_ids), set(ref.team_ids)
    with session() as db:
        ahead = db.scalars(select(Event).join(EventPlayer, EventPlayer.event_id == Event.id).where(
            EventPlayer.player_id == player_id, Event.key.like(f"{ref.system}:%"), Event.status.in_(PRE_MATCH)))
        sides = {e.id: {str(t) for t in e.participants or []} for e in ahead}
        gone = [event_id for event_id, ids in sides.items() if ids & left and not ids & teams]
        if gone:
            db.execute(delete(EventPlayer).where(EventPlayer.player_id == player_id, EventPlayer.event_id.in_(gone)))
        card = db.get(PlayerCard, player_id)
        if card is not None:
            card.freshness = {k: v for k, v in (card.freshness or {}).items() if k != "fixtures"}
    log.info("bind %s: %s has athlete %s on %s now, not %s", player_id, ref.system, ref.athlete_id, ", ".join(ref.team_names) or "no team", ", ".join(old.team_names) or "no team")
    return ref


def describe(player_id: int) -> str | None:
    """Who a bound player is, for the research agent: the exact athlete, so it can't research a namesake."""
    with session() as db:
        player = db.get(Player, player_id)
        row = binding(db, player_id)
        if player is None:
            return None
        if row is None:
            return registry.describe(player_id)
        loc = row.locator or {}
        teams = [t for t in loc.get("team_names") or [] if t and t != loc.get("name")]
        where = f", {', '.join(teams)}" if teams else ""
        adapter = ADAPTERS.get(row.adapter)
        kind = sports.BY_KEY.get(adapter.sport_of(PlayerRef(**loc))) if adapter else None
        league = f" ({loc['league'].upper()})" if kind and loc.get("league") in kind.leagues else ""
        if kind and kind.group:
            league = f" ({kind.label} pro, known by this handle)"  # one game of a group: an esports pro's
        page = f" Their page on {source_name(row.adapter)}: {loc['profile_url']}." if loc.get("profile_url") else ""
        return f"{loc.get('name') or player.name}, {player.sport if league else player.sport.lower()}{where}{league}.{page} Other athletes may share the name; report only on this one."


# ---------------------------------------------------------------- fixtures, results, stats


def refresh(player_id: int) -> None:
    """Re-read a bound player's fixtures, recent results and stats from the source and publish the card.

    A player followed before their sport had an adapter is bound here first, and one bound to a source
    their sport has since moved off (ESPN football once Sportmonks runs) is bound again; a lookup that
    finds nobody isn't retried for BIND_RETRY seconds, and a moving player keeps the old source till then.
    A source down to what its live games need isn't read (spare), nor one whose share for this lane is spent
    for now (budget.RateLimited): both are tried at the next tick, and neither is a failure. A failure is noted
    in the binding's health, which the scheduler waits on before the next try (retry_at). A player of a sport
    turned off isn't read at all, though their source runs for another sport (an esports game).
    """
    lock = f"structured:{player_id}"
    token = bus.try_lock(lock, REFRESH_LOCK)
    if not token:
        return
    try:
        with session() as db:
            row = binding(db, player_id)
            sport = db.scalar(select(Player.sport).where(Player.id == player_id))
            preferred = for_sport(sport)
            current = row.adapter if row else None
        if not sports.on(sport):
            log.info("refresh %s: %s is turned off; not read", player_id, sport)
            return
        reading = ADAPTERS.get(current) if current else preferred
        if reading is not None and not spare(reading):
            log.info("refresh %s: %s is down to what live games need; next tick", player_id, reading.system)
            return
        if rebind(current, preferred):
            rebound = bus.cooldown(f"bindtry:{player_id}", BIND_RETRY) and bind(player_id)
            if not rebound and current is None:
                return
        _refresh(player_id)
    except budget.RateLimited as exc:
        log.info("refresh %s: %s; next tick", player_id, exc)
    except AdapterError as exc:
        log.warning("refresh %s failed: %s", player_id, exc)
        _record_health(player_id, ok=False, error=str(exc))
    except Exception as exc:
        log.exception("refresh %s crashed", player_id)
        _record_health(player_id, ok=False, error=f"crashed: {exc}"[:300])
    finally:
        bus.release(lock, token)


def spare(adapter: Adapter) -> bool:
    """Whether a background read (a refresh, older results) leaves what a source with an hourly allowance
    keeps for live games (Sportmonks, PandaScore, Liquipedia). A source without one always has room."""
    check = getattr(adapter, "spare", None)
    return check() if check else True


def retry_at(health: dict[str, Any] | None) -> datetime | None:
    """When a player whose refreshes are failing is tried again (from their binding's health), or None
    if the last one worked: REFRESH_RETRY after the first failure, twice as long after each one in a row."""
    failures = int((health or {}).get("failures") or 0)
    checked = (health or {}).get("checked_at")
    if not failures or not checked:
        return None
    wait = min(REFRESH_RETRY * 2 ** min(failures - 1, 16), REFRESH_RETRY_MAX)
    return datetime.fromisoformat(checked) + timedelta(seconds=wait)


def sport_name(adapter: Adapter, ref: PlayerRef) -> str:
    """A player's sport as Sports Follow names it: the sport their source has them in ("Football"; an esports
    pro's game, "CS2", "BGMI")."""
    return sports.label(adapter.sport_of(ref))


def rebind(current: str | None, preferred: Adapter | None) -> bool:
    """Whether a player is looked up again: they have no source yet, or their sport's preferred source is
    another one (ESPN football once Sportmonks runs). A source covering only some games of a sport
    (esports: PandaScore's team titles, Liquipedia's battle royale) sits beside the others, so its
    players stay."""
    if current is None:
        return True
    return bool(preferred and preferred.system != current and not getattr(ADAPTERS.get(current), "games", None))


def _refresh(player_id: int) -> None:
    with session() as db:
        row = binding(db, player_id)
        if row is None:
            return
        adapter = ADAPTERS.get(row.adapter)
        ref = PlayerRef(**row.locator)
    if adapter is None:
        return
    ref = reread(player_id, adapter, ref)  # every few hours: a player who has changed teams moves to the new team's games

    now = _now()
    fixtures = adapter.fixtures(ref)
    # A schedule can lag the match by its cache age; a game our poller saw finish is finished.
    with session() as db:
        seen_final = set(db.scalars(select(Event.key).where(Event.key.in_([f"{adapter.system}:{f.source_id}" for f in fixtures]), Event.status == "final")))
    for f in fixtures:
        if f"{adapter.system}:{f.source_id}" in seen_final and not (f.status == "live" and getattr(adapter, "final_may_reopen", False)):
            f.status = "final"
    finals_all = [f for f in fixtures if f.status == "final"]
    finals = finals_all[-RECENT_LIMIT:]  # scorecards read on every refresh; older ones by history.py
    ahead = upcoming(fixtures, now)
    # A game the schedule says is over but that is still armed or live here, the poller didn't see finish
    # (it was between sessions, or the source's live feed was failing): finish gives it a last poll.
    with session() as db:
        unfinished = {key: event_id for event_id, key in db.execute(select(Event.id, Event.key).where(Event.key.in_([f"{adapter.system}:{f.source_id}" for f in finals_all]), Event.status.in_(("armed", "live")))).all()}

    def scorecard(f: Fixture) -> tuple[Fixture, Snapshot | None]:
        try:
            # A final read may come from the cache, which for an unfinished game holds the last live poll.
            return f, adapter.snapshot(f.locator, final=f"{adapter.system}:{f.source_id}" not in unfinished)
        except AdapterError as exc:
            log.warning("refresh %s: no scorecard for %s: %s", player_id, f.source_id, exc)
            return f, None

    # The scorecards are read side by side, in this refresh's lane: each is about a second from the server,
    # and a new follower's first card waits on them all.
    with ThreadPoolExecutor(max_workers=RECENT_LIMIT, thread_name_prefix="scorecard") as pool:
        recent = list(pool.map(budget.carry(scorecard), finals))
    stats, note = adapter.stats(ref, [(f, s) for f, s in recent if s])
    snaps = {f.source_id: snap for f, snap in recent if snap}
    finish({event_id: snaps.get(key.split(":", 1)[1]) for key, event_id in unfinished.items()})

    with session() as db:
        player = db.get(Player, player_id)
        card_row = db.get(PlayerCard, player_id)
        if player is None or card_row is None:
            return
        kind = sports.BY_KEY.get(adapter.sport_of(ref))
        if kind and kind.group and sports.get(player.sport) is not kind:
            player.sport = kind.label  # an esports pro's sport is their game (bound as "Esports", or changed games)
        events = {f.source_id: upsert_event(db, adapter, player, f) for f in [*finals_all, *ahead]}
        dropped = unpicked(db, adapter, player_id, fixtures)
        for f, snap in recent:
            if snap:
                _record_final(db, events[f.source_id], player_id, snap, snap.lines.get(ref.athlete_id))
        over = found_over(db, [events[f.source_id].id for f in finals_all])
        # Every finished game the source returns joins the player's results history, kept for good.
        for f in finals_all:
            snap = snaps.get(f.source_id)
            keep_result(db, events[f.source_id].id, player_id, result_item(adapter, f, snap, events[f.source_id], ref, over.get(events[f.source_id].id)), scorecard=snap is not None)
        _record_health(player_id, ok=True, db=db)
        if card_row.status != "ready":
            return  # nothing to show yet; the build that follows reads the same events
        source = source_name(adapter.system)
        stamp = now.isoformat()
        card = copy.deepcopy(card_row.card)
        card["player"] = {**card.get("player", {}), "sport": player.sport}
        card["upcoming"] = [_upcoming_item(f, events[f.source_id], ref) for f in ahead]
        # A game whose scorecard couldn't be read this time is still the game an earlier read said it was.
        games = {r.get("event_id"): r.get("game_key") for r in card.get("recent_results") or []}
        card["recent_results"] = []
        for f, snap in reversed(recent):
            item = result_item(adapter, f, snap, events[f.source_id], ref, over.get(events[f.source_id].id))
            if snap is None:
                item["game_key"] = games.get(item["event_id"])
            card["recent_results"].append(item)
        if stats:
            card["season_stats"] = stats
            card["season_stats_note"] = note
        card["provenance"] = {
            section: {"source": source, "url": ref.profile_url, "at": stamp}
            for section in ("upcoming", "recent_results", *(("season_stats",) if stats else ()))
        }
        if ref.profile_url and ref.profile_url not in card.get("sources", []):
            card["sources"] = [ref.profile_url, *card.get("sources", [])]
        live = card.get("live") or {}
        if (live.get("is_live") and not live.get("event_id")) or live.get("event_id") in dropped:
            # The agent's reading of a live game gives way to the poller's; a game they were left out of isn't theirs.
            card["live"] = {"is_live": False, "player_stats": []}
            card_row.is_live = False
        card["freshness"] = {**card.get("freshness", {}), "fixtures": stamp, "stats": stamp}
        card["version"] = (card_row.version or 0) + 1
        card_row.card = card
        card_row.version = card["version"]
        card_row.freshness = card["freshness"]
    bus.store_card(player_id, card)


def unpicked(db: Session, adapter: Adapter, player_id: int, fixtures: list[Fixture]) -> set[int]:
    """Take a player out of the games still to come or on that their source no longer lists for them, where it lists
    only the games a player is picked for and reads them whole (its picked_only: a match's XI once it's out, else
    the squad): one they've been left out of isn't polled, shown live or told for them, and their fans don't get its
    live card. A read that lists nothing at all says nothing about any one game. Returns the games they're out of."""
    if not getattr(adapter, "picked_only", False) or not fixtures:
        return set()
    listed = [f"{adapter.system}:{f.source_id}" for f in fixtures]
    gone = set(db.scalars(select(Event.id).join(EventPlayer, EventPlayer.event_id == Event.id).where(
        EventPlayer.player_id == player_id, Event.key.like(f"{adapter.system}:%"), Event.key.not_in(listed), Event.status.in_(("scheduled", "armed", "live")))))
    if gone:
        db.execute(delete(EventPlayer).where(EventPlayer.player_id == player_id, EventPlayer.event_id.in_(gone)))
        log.info("refresh %s: %s no longer lists event(s) %s for them; taken out", player_id, adapter.system, ", ".join(map(str, sorted(gone))))
    return gone


def upcoming(fixtures: list[Fixture], now: datetime) -> list[Fixture]:
    """The games still to come or under way, at most UPCOMING_LIMIT. A "scheduled" game long past its
    start is one the source never updated, and a postponed one long past is off (or waits for a new
    date): both are left out. Not one its source says is next (Fixture.next_up), whose start is only its day."""
    stale = now - timedelta(hours=12)
    return [f for f in fixtures if f.status != "final" and not (f.status in ("scheduled", "postponed") and f.start_utc and f.start_utc < stale and not f.next_up)][:UPCOMING_LIMIT]


def finish(unfinished: dict[int, Snapshot | None]) -> None:
    """Games the schedule says are over that the poller never saw finish, with each one's scorecard read
    afresh (None if it couldn't be). A final one goes the poller's way (apply_poll), so its cards leave
    LIVE and the result is told; the rest are closed, and their cards stop showing them live."""
    unseen = []
    for event_id, snap in unfinished.items():
        watching = _watching(event_id) if snap is not None and snap.status == "final" else None
        if watching is None:
            unseen.append(event_id)
            continue
        live_binding, source, watchers = watching
        log.info("refresh: event %s ended between polls; applying its final scorecard", event_id)
        apply_poll(event_id, live_binding, source, watchers, snap)
    if not unseen:
        return
    with session() as db:
        for event in db.scalars(select(Event).where(Event.id.in_(unseen))):
            event.status = "final"
        cleared = close_live(db, unseen)
    for player_id, card in cleared:
        bus.store_card(player_id, card)
    livecards.ended(unseen)  # and their live cards on Android phones come down


def put_off(db: Session) -> list[int]:
    """Armed and live games of a sport turned off in the admin panel, which isn't polled (_watching): put off, as a
    game called off is ("postponed"), so they aren't polled or armed again, and once committed nothing of them
    stays LIVE on a card (close_live) or a phone (livecards.ended), and a follow of one lapses as a postponed
    game's does (matches.lapse). Not final, which no read undoes (_refresh's seen_final): their players' and teams'
    games are read again at the first tick their sport is on, which brings each back as its source has it then
    (next_status): under way, polled again from a fresh look; over, final with nothing told, its result old news.
    Returns the games put off."""
    playing = db.scalars(select(Event.sport).where(Event.status.in_(("armed", "live"))).distinct()).all()
    off = [sport for sport in playing if not sports.on(sport)]
    if not off:
        return []
    ids = list(db.scalars(update(Event).where(Event.status.in_(("armed", "live")), Event.sport.in_(off)).values(status="postponed").returning(Event.id)))
    for card_row in db.scalars(select(PlayerCard).where(PlayerCard.player_id.in_(select(EventPlayer.player_id).where(EventPlayer.event_id.in_(ids))))):
        card_row.freshness = {**(card_row.freshness or {}), "fixtures": None}  # the tick skips them while their sport is off
    db.execute(update(Team).where(Team.id.in_(select(EventTeam.team_id).where(EventTeam.event_id.in_(ids)))).values(fetched_at=None))
    log.info("put off %d game(s) of %s, turned off: %s", len(ids), ", ".join(sorted(off)), ids)
    return ids


def close_live(db: Session, event_ids: list[int]) -> list[tuple[int, dict[str, Any]]]:
    """Cards still showing one of these events live, now it's over without a final poll: their live section
    is cleared, the game leaves their upcoming list, and their fixtures are due for a re-read. Returns the
    cards to publish once committed."""
    if not event_ids:
        return []
    ids = set(event_ids)
    players = select(EventPlayer.player_id).where(EventPlayer.event_id.in_(sorted(ids)))
    cleared = []
    for card_row in db.scalars(select(PlayerCard).where(PlayerCard.player_id.in_(players))):
        if ((card_row.card or {}).get("live") or {}).get("event_id") not in ids:
            continue
        card = copy.deepcopy(card_row.card)
        card["live"] = {"is_live": False, "player_stats": []}
        card["upcoming"] = [u for u in card.get("upcoming", []) if u.get("event_id") not in ids]
        card["freshness"] = {**card.get("freshness", {}), "fixtures": None}
        card["version"] = (card_row.version or 0) + 1
        card_row.card, card_row.version, card_row.freshness, card_row.is_live = card, card["version"], card["freshness"], False
        cleared.append((card_row.player_id, card))
    return cleared


_ORDER = {"scheduled": 0, "postponed": 0, "armed": 1, "live": 2, "final": 3}


def arm_before(sport: str | None) -> timedelta:
    """How long before its start a game is armed, and polled: ARM_BEFORE_START, or earlier for a sport
    whose team sheets come out sooner (Sport.team_sheet_before), so its line-up is told when it's out."""
    return timedelta(seconds=max(ARM_BEFORE_START, sports.of(sport).team_sheet_before))


def arm_before_any() -> timedelta:
    """The longest arm_before of any sport: every game that may be due for arming starts within it."""
    return timedelta(seconds=max(ARM_BEFORE_START, *(s.team_sheet_before for s in sports.ALL)))


def next_status(current: str | None, from_schedule: str, moved_later: bool = False) -> str:
    """An event's status after a schedule read. Schedules can be minutes old, so a read never moves a
    game backwards: it doesn't un-arm one or reopen one the poller saw finish. A postponed game can
    come back as scheduled, and an armed one the schedule has moved hours later (an esports series
    after an overrunning one) is disarmed, so it isn't polled all that time, or closed unplayed."""
    if current == "armed" and from_schedule == "scheduled" and moved_later:
        return "scheduled"
    if current is None or current == "postponed" or _ORDER.get(from_schedule, 0) >= _ORDER.get(current, 0):
        return from_schedule
    return current


def upsert_event(db: Session, adapter: Adapter, player: Player, f: Fixture) -> Event:
    event = upsert_game(db, adapter, player.sport, f)
    db.execute(insert(EventPlayer).values(event_id=event.id, player_id=player.id).on_conflict_do_nothing())
    return event


def upsert_game(db: Session, adapter: Adapter, sport: str, f: Fixture) -> Event:
    """A fixture's event, made or brought up to date from it, by its source's key for it: a followed player's
    game (upsert_event), or a followed team's (teams.refresh). `sport` is the sport as an event names it ("Football")."""
    key = f"{adapter.system}:{f.source_id}"
    event = db.scalar(select(Event).where(Event.key == key))
    if event is None:
        # A teammate's refresh may be adding the same game at this moment: the first insert makes the
        # row, the other waits for it to commit and reads it back instead of failing on the key.
        db.execute(insert(Event).values(key=key, sport=sport, title=f.title, status=f.status).on_conflict_do_nothing(index_elements=[Event.key]))
        event = db.scalars(select(Event).where(Event.key == key)).one()
    event.title = f.title
    event.competition = f.competition
    event.venue = f.venue
    event.notes = f.detail
    event.participants = f.team_ids
    event.live_binding = {"adapter": adapter.system, "locator": f.locator, "cadence": adapter.live_cadence}
    reschedule(db, event, f.start_utc, f.status, sport, f.next_up)
    if event.status == "final" and f.status == "live" and getattr(adapter, "final_may_reopen", False):
        event.status = "live"  # on again after it was over (final_may_reopen): polled from here, what's new told
    db.flush()
    return event


def reschedule(db: Session, event: Event, start: datetime | None, status: str, sport: str | None = None, next_up: bool = False) -> None:
    """An event's start and status as its source last said: a schedule read (upsert_game), or a read of the game
    itself for one no followed player's or team's refresh brings up to date (predictions.made). Put back (or brought
    forward), its questions start then (predictions.moved); an armed one put back hours is disarmed (next_status).
    One with no start time of its own is armed while its source says it's next (Fixture.next_up: a chess round set
    to start once the one before is over, a lobby with only its day), so its start is seen when it comes, and one with
    no start at all is disarmed once it isn't."""
    if event.start_utc is not None and start is not None and start != event.start_utc:
        predictions.moved(db, event, start)
    event.start_utc = start
    later = start is not None and start > _now() + arm_before(event.sport or sport) + timedelta(hours=1)
    if status == "scheduled" and (next_up or start is None):
        status, later = ("armed", False) if next_up else (status, True)
    event.status = next_status(event.status, status, moved_later=later)


def _record_final(db: Session, event: Event, player_id: int, snap: Snapshot, line: Line | None) -> None:
    """Keep one final state per finished event and the player's line in it."""
    if not db.scalar(select(EventState.id).where(EventState.event_id == event.id, EventState.status == "final").limit(1)):
        db.add(EventState(event_id=event.id, as_of=_now(), status="final", score_label=snap.score_label, clock_label=snap.clock_label, state=snap.state, source_url=snap.source_url))
    if line and not db.scalar(select(PlayerLine.id).where(PlayerLine.event_id == event.id, PlayerLine.player_id == player_id).limit(1)):
        db.add(PlayerLine(event_id=event.id, player_id=player_id, as_of=_now(), stats={"headline": line.headline, "stats": line.stats}))


def found_over(db: Session, event_ids: Iterable[int | None]) -> dict[int, datetime]:
    """When each of these games was found over, where that says when it ended: its first final state since it was last
    seen on (or to come, or called off) here, so a game on again after it seemed over (a super over) or replayed under
    its fixture after it was called off (notify.editions) is dated by the end of the game it is now; and no later than
    its sport says a game that started when it did can end (Sport.over_by). A game first seen here over (a new
    follower's first read, a history read) or seen over long after it was played (a worker down for days) says nothing
    of when it ended. A result is dated by it (result_item), so a game of several days (a Test) is one of the day it
    ended on Today, and in its Big results (league.py)."""
    ids = sorted({e for e in event_ids if e})
    if not ids:
        return {}
    last_on = (select(EventState.event_id, func.max(EventState.as_of).label("at")).where(EventState.event_id.in_(ids), EventState.status != "final")
               .group_by(EventState.event_id).subquery())
    rows = db.execute(select(EventState.event_id, EventState.as_of, EventState.state, Event.start_utc).join(Event, Event.id == EventState.event_id)
                      .join(last_on, last_on.c.event_id == EventState.event_id)
                      .where(EventState.event_id.in_(ids), EventState.status == "final", EventState.as_of > last_on.c.at)
                      .order_by(EventState.event_id, EventState.as_of)).all()
    out: dict[int, datetime] = {}
    first: set[int] = set()
    for event_id, as_of, state, start in rows:
        if event_id in first:
            continue
        first.add(event_id)
        by = sports.of_state(state).over_by(start, state or {})
        if by is not None and as_of <= by:
            out[event_id] = as_of
    return out


def _record_health(player_id: int, ok: bool, error: str | None = None, db: Session | None = None) -> None:
    metrics.card("refresh", "ok" if ok else "failed")
    def write(s: Session) -> None:
        for row in s.scalars(select(SourceBinding).where(SourceBinding.player_id == player_id)):
            health = dict(row.health or {})
            health["checked_at"] = _now().isoformat()
            if ok:
                health.update(last_ok=health["checked_at"], failures=0, error=None)
            else:
                health.update(failures=int(health.get("failures") or 0) + 1, error=error)
            row.health = health

    if db is not None:
        write(db)
    else:
        with session() as s:
            write(s)


def _team_of(f: Fixture, ref: PlayerRef) -> str | None:
    by_id = next((name for tid, name in zip(ref.team_ids, ref.team_names) if tid in f.team_ids), None)
    if by_id:
        return by_id
    wanted = {naming.team_key(t) for t in ref.other_teams}
    return next((n for n in f.team_names if naming.team_key(n) in wanted), None)


def _upcoming_item(f: Fixture, event: Event, ref: PlayerRef) -> dict[str, Any]:
    return {
        "title": f.title,
        "competition": f.competition,
        "team": _team_of(f, ref),
        "start_utc": _iso(f.start_utc),
        "venue": f.venue,
        "notes": f.detail,
        "status": event.status,
        "event_id": event.id,
        "match": match_path(event.key),  # its match page's address
        "game_key": board_key(event.key, f.board),  # the game it's one fixture of, as its live block and result say it
    }


def _side(f: Fixture, snap: Snapshot | None, ref: PlayerRef) -> str | None:
    """The id of the side the player played for in a game: the one the scorecard has them in (a traded player's old
    side), else the one of their teams in it."""
    if snap is not None and (side := snap.sides.get(ref.athlete_id)):
        return side
    ours = [t for t in f.team_ids if t in ref.team_ids]
    return ours[0] if len(ours) == 1 else None


def result_item(adapter: Adapter, f: Fixture, snap: Snapshot | None, event: Event | None, ref: PlayerRef, ended: datetime | None = None) -> dict[str, Any]:
    """A finished game as the player's result; `ended`, when it was found over (found_over), where that's known."""
    # A lagging schedule has no result yet for a game the poller saw finish; the scorecard does.
    said = adapter.result_label(f, ref.team_ids) or snap_result(snap)
    # The game's sides as the source marks them: its final board, else the schedule's result.
    board = snap.state if snap is not None and snap.status == "final" else f.result or (snap.state if snap else None)
    rules = sports.of(f.sport or adapter.sport_of(ref))
    return {
        "title": f"{f.title} · {f.detail}" if f.detail else f.title,
        "result": said,
        # Won, drew or lost, told now from the side the player played for, so the app needn't guess it from names.
        "outcome": rules.outcome(said, [*ref.team_names, *ref.other_teams], board, _side(f, snap, ref)),
        "date": f.start_utc.date().isoformat() if f.start_utc else None,
        "start_utc": _iso(f.start_utc),  # the app dates a result by the fan's local day of the game
        "ended_utc": _iso(ended),  # and Today's since yesterday by the day it ended, a Test's its last
        "player_contribution": contribution(snap, ref.athlete_id),
        "competition": f.competition,
        "event_id": event.id if event else None,
        "match": match_path(event.key) if event else None,  # its match page's address
        "game_key": game_key(f"{adapter.system}:{f.source_id}", snap.state) if snap else None,  # its board says it
        "source_url": snap.source_url if snap else None,
    }


def contribution(snap: Snapshot | None, athlete_id: str) -> str | None:
    """The player's line in a game, in a few words ("2 goals", "Not in the matchday squad")."""
    line = snap.lines.get(athlete_id) if snap else None
    return line.headline if line else (sports.of_state(snap.state).no_line if snap and snap.lines else None)


def snap_result(snap: Snapshot | None) -> str:
    return (snap and sports.of_state(snap.state).result_text(snap.score_label, snap.clock_label)) or ""


def merged_result(old: dict[str, Any] | None, item: dict[str, Any], scorecard: bool) -> dict[str, Any]:
    """A result read again: a read without the scorecard keeps what an earlier scorecard added, and the outcome it
    told from the side it had the player in (the schedule alone knows only their teams now); and when it was found
    over, once known."""
    old = old or {}
    new = {**item, "scorecard": True if scorecard else old.get("scorecard", False)}
    if not scorecard:
        for key in ("player_contribution", "source_url", "result", "game_key", "ended_utc"):
            if old.get(key) and not new.get(key):
                new[key] = old[key]
        if old.get("outcome") and (old.get("scorecard") is True or not new.get("outcome")):
            new["outcome"] = old["outcome"]
    return new


def keep_result(db: Session, event_id: int, player_id: int, item: dict[str, Any], scorecard: bool) -> None:
    """Store a finished event's result in the player's history (event_player.result)."""
    row = db.get(EventPlayer, (event_id, player_id))
    if row is None:
        return
    new = merged_result(row.result, item, scorecard)
    if new != row.result:
        row.result = new


def add_scorecard(db: Session, event: Event, player_id: int, athlete_id: str, snap: Snapshot) -> None:
    """A finished event's scorecard, read later (history.py): the final state, the player's line, and
    their contribution in the stored result."""
    _record_final(db, event, player_id, snap, snap.lines.get(athlete_id))
    row = db.get(EventPlayer, (event.id, player_id))
    if row is None or row.result is None:
        return
    said = row.result.get("result") or snap_result(snap)
    # The outcome again, from the side the scorecard has the player in, where it says.
    side = snap.sides.get(athlete_id)
    told = sports.of_state(snap.state).outcome(said, [], snap.state, side) if side else None
    row.result = {**row.result, "player_contribution": contribution(snap, athlete_id), "source_url": snap.source_url, "result": said, "scorecard": True,
                  **({"outcome": told} if told else {})}


# ---------------------------------------------------------------- live polling


def poll_event(event_id: int) -> None:
    """Poll one event while it is armed or live: every few seconds in play, every minute before.

    One job per event, however many players and fans are watching it. The job hands over after
    POLL_SESSION seconds; the next scheduler tick starts a fresh one if the game is still on.
    A game that stops changing for a minute (stumps, rain, half-time) is polled once a minute
    until it moves again.
    """
    lock = f"poll:{event_id}"
    token = bus.try_lock(lock, 120)
    if not token:
        return
    try:
        deadline = time.monotonic() + POLL_SESSION
        quiet_since = time.monotonic()
        while True:
            with tracing.span("poll round", {"event_id": event_id}) as round_span:
                status, wait, changed = poll_once(event_id)
                round_span.set_attribute("event.status", status)
            if status in ("final", "postponed", "idle") or time.monotonic() + wait > deadline:
                return
            if changed:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since > 60:
                wait = max(wait, 60)
            bus.extend(lock, wait + 120, token)
            if not shutdown.sleep(wait):
                return  # the worker is stopping: the next one polls on
    except Exception:
        log.exception("poll %s crashed", event_id)
    finally:
        bus.release(lock, token)


def holds_back(adapter: Adapter) -> bool:
    """Whether a game nobody follows a player in, followed by itself (matches.py), waits to be polled: its source
    is polled a request a game (not a lane, where it costs nothing more) and is down to what its live games need
    (spare), which followed players' games keep."""
    return not batched(adapter.system) and not spare(adapter)


def batched(system: str | None) -> bool:
    """Whether a source's events are polled together by one lane: its adapter fetches many events in
    one request (snapshots). The rest get one poll_event job per event."""
    return hasattr(ADAPTERS.get(system or ""), "snapshots")


def watched() -> Any:
    """The events the poller looks after: a followed player is in it, a fan follows the game by itself
    (matches.py), or a fan's pick waits on a question it settles (predictions.py). A select of event ids."""
    return select(EventPlayer.event_id).where(EventPlayer.player_id.in_(select(Follow.player_id))).union(matches.watched(), predictions.watched())


def lane_events(system: str) -> list[int]:
    """The armed and live events this source polls with a followed player in them, followed by themselves, or with
    a pick waiting on them."""
    with session() as db:
        return list(db.scalars(
            select(Event.id)
            .where(Event.status.in_(("armed", "live")), Event.live_binding["adapter"].astext == system, Event.id.in_(watched()))
            .order_by(Event.id)
        ))


def poll_lane(system: str) -> None:
    """Poll every armed and live event of one source together, for a source that fetches many events
    in one request: one request a round however many games are on, and one job and one thread for all
    of them instead of one each per game. A round comes every live_cadence seconds while a game is in
    play and changing, otherwise once a minute. Hands over after POLL_SESSION, as poll_event does.
    """
    adapter = ADAPTERS.get(system)
    if adapter is None:
        return
    lock = f"lane:{system}"
    token = bus.try_lock(lock, 120)
    if not token:
        return
    try:
        deadline = time.monotonic() + POLL_SESSION
        moved: dict[int, float] = {}
        missing: set[int] = set()
        while True:
            with tracing.span("lane round", {"system": system}):
                wait = lane_round(adapter, moved, missing)
            if wait is None or time.monotonic() + wait > deadline:
                return
            bus.extend(lock, wait + 120, token)
            if not shutdown.sleep(wait):
                return  # the worker is stopping: the next one polls on
    except Exception:
        log.exception("poll lane %s crashed", system)
    finally:
        bus.release(lock, token)


def lane_round(adapter: Adapter, moved: dict[int, float], missing: set[int]) -> int | None:
    """One round of a lane: fetch all its events at once, then apply each. Returns how long to wait
    before the next round, or None when none of them is followed any more (a player in it, or itself).

    moved holds when each event last changed (a game quiet for a minute stops setting the pace);
    missing, the events the source didn't return, so each is logged once a session.
    """
    targets = {event_id: w for event_id in lane_events(adapter.system) if (w := _watching(event_id))}
    if not targets:
        return None
    try:
        snaps = adapter.snapshots({event_id: live_binding["locator"] for event_id, (live_binding, _, _) in targets.items()})
    except AdapterError as exc:
        log.warning("poll lane %s: %s", adapter.system, exc)
        return 30
    now = time.monotonic()
    busy = False
    for event_id, (live_binding, _, watchers) in targets.items():
        snap = snaps.get(event_id)
        if snap is None:
            if event_id not in missing:
                missing.add(event_id)
                log.warning("poll lane %s: no data for event %s (%s)", adapter.system, event_id, live_binding["locator"])
            continue
        try:
            status, _, changed = apply_poll(event_id, live_binding, adapter, watchers, snap)
        except Exception:
            log.exception("poll %s crashed", event_id)
            continue
        if changed or event_id not in moved:
            moved[event_id] = now
        busy = busy or (status == "live" and now - moved[event_id] <= 60)
    # A source read every few minutes (Liquipedia) isn't read more often while nothing moves.
    return adapter.live_cadence if busy else max(60, adapter.live_cadence)


def _evaluate(rules: sports.Sport, db: Session, event_id: int, snap: Snapshot, latest: EventState | None, player_ids: list[int]) -> None:
    """A sport our engine evaluates (chess): ask the engine for the game's position while it's on and a
    follower wants to hear about swings, and put the game's latest evaluation (engine.py) into the state,
    settled by the sport once for the board (each followed player's event of it takes the same). A game
    with its result is evaluated no more: the engine lets go of it, and its last evaluation stays as it
    was, so nothing turns after the result (a mini-match stays live after its game is decided)."""
    state = snap.state
    game, fen = state.get("game_id"), state.get("fen")
    if not (game and fen and engine.enabled()):
        return
    prev = latest.state if latest is not None and latest.state else {}
    before = prev.get("eval") if prev.get("game_id") == game else None
    if snap.status != "live" or state.get("result"):
        engine.drop(game)
        if before:
            state["eval"] = before
        return
    # Its followed players' fans, and the game's own (a game followed by itself).
    wanted = [*db.scalars(select(Follow.alert_rules).where(Follow.player_id.in_(player_ids))).all(), *matches.rules(db, event_id)]
    if any(moments.wanted((r or {}).get("level", moments.DEFAULT_LEVEL), "key", "swing") for r in wanted):
        engine.request(game, fen, by=event_id, deepen=engine.deepens(event_id))  # past its first look while someone has it open
    else:
        engine.drop(game, by=event_id)  # nobody here wants its swings; the other player's event may
    ev = engine.latest(game)
    if ev and (settled := engine.settled(game, ev, rules.settle, before)):
        state["eval"] = settled


def baseline(latest: EventState | None, last: PlayerLine | None, snap: Snapshot, recent: bool = True, line: dict[str, Any] | None = None) -> moments.Seen | None:
    """What a player's moments are found against: the event's last state, with their last line. A first
    look at a game already under way is the baseline (None), which tells only a result or a line-up: no
    earlier state; a gap in polling (recent is False: a restart, an hour of a source's errors), so what
    happened meanwhile is old news; a game last seen before the start and now over, or (football) well
    past kick-off; or, from a source with player lines, no earlier line for them while it was live, unless
    the line they have now (`line`) is news since (first_line_news: a batter's first ball, a first-ball duck).
    Esports has no player lines, so its earlier state alone is enough."""
    if latest is None or not recent or (last is None and latest.status == "live" and snap.lines and not first_line_news(latest, snap, line)):
        return None
    if latest.status in PRE_MATCH and (snap.status == "final" or (snap.status == "live" and _under_way(snap))):
        return None
    return moments.Seen(latest.status, latest.state or {}, last.stats if last else None, latest.score_label or "", latest.clock_label or "")


def long_over(rules: sports.Sport, event: Event, snap: Snapshot, now: datetime) -> bool:
    """Whether a poll finds a game over that can't have ended lately: past the latest its sport's games end after its
    start (Sport.over_by), and a result's time to be news (alerts.LATE_TTL) more. A first look at it (baseline: polling
    came back to it after its sport was off, or a worker down for days) tells nothing: its result is old news (BUG-259)."""
    by = rules.over_by(event.start_utc, snap.state) if snap.status == "final" else None
    return by is not None and now - by > timedelta(seconds=alerts.LATE_TTL)


def first_line_news(latest: EventState, snap: Snapshot, line: dict[str, Any] | None) -> bool:
    """Whether a player's first line in a game polled all along, with none at its last poll while it was live, is
    news since that poll, as their sport says (Sport.first_line_is_news): a cricketer has none till their first
    ball, so it may be a first-ball duck. Not where every player has one from the start: a follow, or the source's
    lines, came mid-game."""
    if line is None:
        return False
    was = moments.Seen(latest.status, latest.state or {}, None, latest.score_label or "", latest.clock_label or "")
    return sports.of_state(snap.state).first_line_is_news(was, moments.Seen(snap.status, snap.state, line, snap.score_label, snap.clock_label))


def _under_way(snap: Snapshot) -> bool:
    """A game well past its start by its clock (football's "61'"). Polled every minute before the start, a
    game is first seen live a minute or two in, so one this far along was missed (the source lagged)."""
    return sports.of_state(snap.state).missed_start(snap)


def opened_live(adapter: Adapter, event: Event, latest: EventState | None, now: datetime) -> bool:
    """Whether a first look at a game comes right after an empty board before its start: its first poll ever, from a
    source that lists a game only once it's under way, within the while after its start the source says
    (listed_after_start: PUBG's lobbies appear once their first match is over). Its start and what happened since are
    then told as they'd have been had it been listed ahead. Any other first look is the baseline (baseline)."""
    within = getattr(adapter, "listed_after_start", None)
    return latest is None and within is not None and event.start_utc is not None and now - event.start_utc <= within


def reopened(adapter: Adapter, latest: EventState | None, snap: Snapshot) -> bool:
    """Whether a poll finds a game on again after it was over, from a source whose games may reopen (final_may_reopen:
    a later match joins a lobby after a long break). It wasn't polled meanwhile, so the gap is no reason to take this
    look as a first one: what happened since it was over is told against its last state."""
    return latest is not None and latest.status == "final" and snap.status == "live" and bool(getattr(adapter, "final_may_reopen", False))


def _polled(event_id: int, cadence: int) -> bool:
    """Note a poll of an event. Returns whether the one before it was recent: within POLL_GAP, or three of
    a slow source's rounds (Liquipedia reads every five minutes)."""
    seconds = max(POLL_GAP, 3 * cadence)
    redis = bus.sync_redis()
    redis.set(f"polled_at:{event_id}", int(time.time()), ex=6 * 3600)  # how long since, for metrics.live
    return redis.set(f"polled:{event_id}", "1", ex=seconds, get=True) is not None


def _team_ids(db: Session, system: str, player_ids: list[int]) -> dict[int, list[str]]:
    """Each player's team ids in a source, as their fixtures binding holds them."""
    rows = db.execute(select(SourceBinding.player_id, SourceBinding.locator).where(SourceBinding.player_id.in_(player_ids), SourceBinding.purpose == "fixtures", SourceBinding.adapter == system))
    return {pid: [str(t) for t in (loc or {}).get("team_ids") or []] for pid, loc in rows}


def _watching(event_id: int) -> tuple[dict[str, Any], Adapter, list[tuple[int, str]]] | None:
    """An event's live binding, its adapter and the followed players in it (player id, their id in the
    source) whose sport runs; None when there are none, nor a fan following the game by itself, or its source
    doesn't run. A source of several sports (esports games) runs while one does: a game turned off isn't
    polled."""
    with session() as db:
        event = db.get(Event, event_id)
        if event is None or not event.live_binding:
            return None
        live_binding = dict(event.live_binding)
        adapter = ADAPTERS.get(live_binding.get("adapter", ""))
        if adapter is None:
            return None
        followed = select(Follow.player_id)
        watchers = db.execute(
            select(PlayerIdentity.player_id, PlayerIdentity.external_id, Player.sport)
            .join(EventPlayer, EventPlayer.player_id == PlayerIdentity.player_id)
            .join(Player, Player.id == PlayerIdentity.player_id)
            .where(EventPlayer.event_id == event_id, PlayerIdentity.system == adapter.system, PlayerIdentity.player_id.in_(followed))
        ).all()
        # Followed by itself, or a pick waits on a question it settles (predictions.py).
        itself = sports.on(event.sport) and (matches.followed(db, event_id) or predictions.waiting(db, event_id))
    on = [(pid, xid) for pid, xid, sport in watchers if sports.on(sport)]
    return (live_binding, adapter, on) if on or itself else None


def poll_once(event_id: int) -> tuple[str, int, bool]:
    """One poll: fetch the snapshot, append what changed, update every followed player's card in the event.

    Returns the event's status, how long to wait before the next poll, and whether anything changed.
    """
    watching = _watching(event_id)
    if watching is None:
        return "idle", 0, False
    live_binding, adapter, watchers = watching
    if not watchers and holds_back(adapter):
        return "idle", 0, False  # nobody's player in it, and its source is down to its live reserve: it waits, every round
    try:
        snap = adapter.snapshot(live_binding["locator"])
    except AdapterError as exc:
        log.warning("poll %s: %s", event_id, exc)
        return "error", 30, False
    return apply_poll(event_id, live_binding, adapter, watchers, snap)


def apply_poll(event_id: int, live_binding: dict[str, Any], adapter: Adapter, watchers: list[tuple[int, str]], snap: Snapshot) -> tuple[str, int, bool]:
    """Append what changed in a fetched snapshot and update every followed player's card in the event
    (poll_once's second half, shared with the lanes); once that's committed, send its moments, and its live card
    on Android phones (livecards.py). Returns as poll_once does."""
    now = _now()
    published: list[tuple[int, dict[str, Any]]] = []
    published_game: dict[str, Any] | None = None
    game_kept: tuple[str, str, int] | None = None  # the game's own poll, for Redis once this one is committed
    several = False  # a fixture of a game under several (boards), which notes it's told its side once committed
    new_moments: list[int] = []
    with session() as db:
        event = db.get(Event, event_id)
        if event is None:
            return "idle", 0, False
        people = {pid: (name, sport) for pid, name, sport in db.execute(select(Player.id, Player.name, Player.sport).where(Player.id.in_([w[0] for w in watchers])))}
        # Esports moments are told from the player's team's side ("Sentinels win map 2"): no line says it.
        rules = sports.of_state(snap.state)
        several = bool(rules.board(snap.state))
        sides = _team_ids(db, adapter.system, [w[0] for w in watchers]) if rules.team_moments else {}
        latest = db.scalar(select(EventState).where(EventState.event_id == event_id).order_by(EventState.as_of.desc()).limit(1))
        # Only changes are stored, so the latest state can be old in a game polled all along (the
        # minutes before kick-off); whether polling had a gap comes from the polls themselves.
        recent = _polled(event_id, int(live_binding.get("cadence") or 10))
        opened = opened_live(adapter, event, latest, now)
        snap = hold_result(rules, snap, latest)
        reopens = reopened(adapter, latest, snap)
        old_news = long_over(rules, event, snap, now)  # a first look at it tells nothing
        if rules.engine:
            _evaluate(rules, db, event_id, snap, latest, [w[0] for w in watchers])
        if snap.squad:
            snap.state["team_sheet"] = True  # so the poll that first sees line-ups counts as a change
        changed = latest is None or (latest.status, latest.score_label, latest.clock_label, latest.state) != (snap.status, snap.score_label, snap.clock_label, snap.state)
        if changed:
            db.add(EventState(event_id=event_id, as_of=now, status=snap.status, score_label=snap.score_label, clock_label=snap.clock_label, state=snap.state, source_url=snap.source_url))
        ended = found_over(db, [event_id]).get(event_id) if snap.status == "final" else None  # its result is dated by it
        if not (snap.status == "scheduled" and event.status == "armed"):
            event.status = snap.status
        # Its questions (predictions.py): made while it's to come, closed as it starts, settled from its board. A
        # fault there costs this poll nothing else.
        try:
            with db.begin_nested():
                predictions.polled(db, event, snap.status, snap.state, now)
        except Exception:
            log.exception("poll %s: predictions", event_id)

        for player_id, athlete_id in watchers:
            line = snap.lines.get(athlete_id)
            line_json = {"headline": line.headline, "stats": line.stats} if line else None
            line_changed = False
            last = db.scalar(select(PlayerLine).where(PlayerLine.event_id == event_id, PlayerLine.player_id == player_id).order_by(PlayerLine.as_of.desc()).limit(1))
            if line_json is not None:
                line_changed = last is None or last.stats != line_json
                if line_changed:
                    db.add(PlayerLine(event_id=event_id, player_id=player_id, as_of=now, stats=line_json))
            # What happened to this player since the last poll.
            name, sport = people.get(player_id, ("", ""))
            seen = moments.Seen(snap.status, snap.state, line_json, snap.score_label, snap.clock_label, lineups=bool(snap.lines), role=snap.squad.get(athlete_id), events=snap.moments,
                                teams=sides.get(player_id, []), squad_known=bool(snap.squad))
            if changed or line_changed:
                before = baseline(latest, last, snap, recent, line_json)
                if before is None and opened:
                    before = moments.Seen("scheduled")  # a game listed only once under way, first seen just after its start
                elif before is None and reopens:
                    before = baseline(latest, last, snap)  # on again after it was over: against how it was then
                told = notify.told(db, player_id, event_id) if before else []  # what a source may take back (a goal VAR rules out)
                if before is not None or not old_news:
                    new_moments += notify.record(db, player_id, event_id, moments.detect(sport, name, athlete_id, before, seen, told))
            card_row = db.get(PlayerCard, player_id)
            if card_row is None or card_row.status != "ready":
                continue
            card_row.live_checked_at = now
            # A player the team sheet leaves out (Sport.left_out) isn't in the game: their page doesn't show it live, nor
            # its result as theirs; it's taken down if it went up before the sheet was out.
            out = snap.status in ("live", "final") and sports.of(sport).left_out(seen)
            if out:
                if ((card_row.card or {}).get("live") or {}).get("event_id") != event.id:
                    continue
            elif not (changed or line_changed) and card_row.is_live == (snap.status == "live"):
                continue
            card = copy.deepcopy(card_row.card)
            if out:
                card["live"] = {"is_live": False, "player_stats": []}
                card["freshness"] = {**card.get("freshness", {}), "live": now.isoformat()}
            else:
                _apply_snapshot(card, event, snap, line, adapter, now, ended)
            card["version"] = (card_row.version or 0) + 1
            card_row.card = card
            card_row.version = card["version"]
            card_row.freshness = card["freshness"]
            card_row.is_live = bool(card["live"].get("is_live"))
            published.append((player_id, card))
        if matches.followed(db, event_id):
            found, game_kept = _game_moments(db, event, adapter, snap, recent, people, watchers, old_news)
            new_moments += found
            if changed:
                live = matches.board(event, snap.status, snap.score_label, snap.clock_label, snap.state, snap.source_url, now)
                live["source"] = adapter.credit(snap)
                published_game = matches.item(event, live)
    if several:
        # A fixture of a game under several has told its side (told_lately): only now, as a poll that fails before
        # its commit has found nothing, and the other side must say it.
        try:
            bus.sync_redis().set(f"told_at:{event_id}", int(time.time()), ex=6 * 3600)
        except Exception:
            log.warning("poll %s: couldn't note it was told", event_id, exc_info=True)
    if game_kept is not None:
        # Only now: a poll whose moments weren't committed must find them again, against the poll before it.
        bus.sync_redis().set(game_kept[0], game_kept[1], ex=game_kept[2])
    for player_id, card in published:
        bus.store_card(player_id, card)
    if published_game is not None:
        bus.publish_match(event_id, {"type": "match", "event_id": event_id, "match": published_game})
    notify.dispatch(new_moments)
    livecards.polled(event, snap)  # its live card on Android phones, or its end
    if published:
        log.info("poll %s: %s | %s -> %d card(s)", event_id, snap.score_label, snap.clock_label, len(published))
    wait = int(live_binding.get("cadence") or 10) if snap.status == "live" else 60
    return snap.status, wait, changed


def _game_moments(db: Session, event: Event, adapter: Adapter, snap: Snapshot, recent: bool,
                  people: dict[int, tuple[str, str]], watchers: list[tuple[int, str]], old_news: bool = False) -> tuple[list[int], tuple[str, str, int]]:
    """The game's own moments since its last poll, for the fans following it by itself (matches.py), as its
    sport finds them (Sport.game_moments), against that poll as Redis kept it, for as long as a poll after it
    is recent (_polled's gap): a copy from before a gap (nobody followed the game a while, though its players'
    polls went on) would tell what happened meanwhile as new. A first look (no recent copy, or polling had a
    gap) is the baseline, as a player's is: only a result is told from it, and not that of a game long over
    (`old_news`: long_over). Returns the new moments' ids, and this
    poll as Redis is to keep it (its key, value and seconds), which apply_poll writes once they're committed. A
    game a fan follows a team in (teams.py) tells its result from that team's side too, for them."""
    from . import teams  # a team's games are followed games (teams reads cards and boards from here)

    names = {**_roster_names(event), **snap.names, **{xid: people[pid][0] for pid, xid in watchers if pid in people}}
    now = sports.Played(snap.status, snap.state, snap.score_label, snap.clock_label, snap.moments,
                        {a: {"headline": ln.headline, "stats": ln.stats} for a, ln in snap.lines.items()}, names)
    key = f"game:{event.id}"
    try:
        kept = json.loads(cast(str, bus.sync_redis().get(key)) or "null")
    except Exception:
        kept = None
    before = sports.Played(**{**kept, "names": names}) if kept and recent else None
    if before is not None and before.status in PRE_MATCH and (snap.status == "final" or (snap.status == "live" and _under_way(snap))):
        before = None  # last seen before the start and now over, or well under way: what happened meanwhile is old news
    rules = sports.of_state(snap.state)
    name = rules.matchup(snap.state) or event.title
    found = rules.game_moments(name, before, now, notify.told_game(db, event.id) if before else [])
    if any(f.kind == "final" for f in found):
        # And from the side of each team fans follow in it, for them (teams.py): "Arsenal won 2–1 v Chelsea"; and
        # their games read again soon, so the result reaches their Following and Today.
        found += teams.side_finals(db, event, rules, snap.state, snap.score_label, snap.clock_label)
        teams.ended(db, event.id)
    if before is None and old_news:
        found = []  # a first look at a game long over: its result is old news
    for f in found:
        f.priority = f.priority or moments.PRIORITY.get(f.kind, 50)
    gap = max(POLL_GAP, 3 * int((event.live_binding or {}).get("cadence") or 10))
    value = json.dumps({k: getattr(now, k) for k in ("status", "state", "score", "clock", "events", "lines")}, default=str)
    return notify.record_game(db, event.id, found, f"/match/{match_path(event.key)}"), (key, value, gap)


HOLD_RESULT = 2  # polls in a row a finished game's result waits for its source to say who won (hold_result)


def hold_result(rules: sports.Sport, snap: Snapshot, latest: EventState | None) -> Snapshot:
    """A poll of a finished game whose result is still to come from its source (Sport.result_pending: cricket's
    "Result" before "India won by 57 runs"), as it's applied: still on, for up to HOLD_RESULT polls in a row, so it's
    polled again and its fans hear who won; then over, however it reads. Its state counts the polls held ("held").
    One already over here isn't held."""
    if snap.status != "final" or (latest is not None and latest.status == "final") or not rules.result_pending(snap.state, snap.score_label, snap.clock_label):
        return snap
    polls = int(((latest.state if latest is not None else None) or {}).get("held") or 0)
    return snap if polls >= HOLD_RESULT else replace(snap, status="live", state={**snap.state, "held": polls + 1})


def _roster_names(event: Event) -> dict[str, str]:
    """Who plays in a game, by their ids in its source, as its match page's last read named them (match.read):
    a source whose polls don't name its lines' players has them there."""
    from . import match  # the match page's reads (match reads Live now, which reads search, which reads this)

    system, _, fixture = (event.key or "").partition(":")
    game = (match.last_read(system, fixture) or {}).get("game") or {}
    return {p["athlete_id"]: p["name"] for p in [*(game.get("players") or []), *(game.get("bench") or [])] if p.get("athlete_id") and p.get("name")}


def _apply_snapshot(card: dict[str, Any], event: Event, snap: Snapshot, line: Line | None, adapter: Adapter, now: datetime, ended: datetime | None = None) -> None:
    """A poll's snapshot on a player's card: their live game, or once it's over (`ended`: when it was found over,
    found_over) their result."""
    stamp = now.isoformat()
    title = f"{event.title} · {event.notes}" if event.notes else event.title
    if snap.status == "live":
        card["live"] = {
            "is_live": True,
            "event_id": event.id,
            "match": match_path(event.key),  # its match page's address
            "game_key": game_key(event.key, snap.state),  # the game it's one fixture of, where it has several
            "event": title,
            "competition": event.competition,
            "score": snap.score_label,
            "clock": snap.clock_label,
            "headline": line.headline if line else None,
            "player_stats": line.stats if line else [],
            "state": snap.state,
            "moments": snap.moments[:10],
            "source": adapter.credit(snap),
            "live_note": adapter.live_note,
            "lag_note": adapter.lag_note,
            "source_url": snap.source_url,
            "as_of": stamp,
        }
    elif (card.get("live") or {}).get("event_id") == event.id:
        card["live"] = {"is_live": False, "player_stats": []}

    if snap.status == "final":
        card["upcoming"] = [u for u in card.get("upcoming", []) if u.get("event_id") != event.id]
        result = {
            "title": title,
            "result": snap_result(snap),
            "date": event.start_utc.date().isoformat() if event.start_utc else None,
            "start_utc": _iso(event.start_utc),
            "ended_utc": _iso(ended),
            "player_contribution": line.headline if line else None,
            "competition": event.competition,
            "event_id": event.id,
            "match": match_path(event.key),
            "game_key": game_key(event.key, snap.state),
            "source_url": snap.source_url,
        }
        card["recent_results"] = [result, *[r for r in card.get("recent_results", []) if r.get("event_id") != event.id]][:RECENT_LIMIT]
        # Stats and the result label are recomputed by the next scheduled refresh; make it due now.
        card["freshness"] = {**card.get("freshness", {}), "live": stamp, "fixtures": None}
        return
    for u in card.get("upcoming", []):
        if u.get("event_id") == event.id:
            u["status"] = event.status
    card["freshness"] = {**card.get("freshness", {}), "live": stamp}
