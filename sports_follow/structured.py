"""Structured sources in the pipeline (design sections 4 and 5).

Once the research agent has said who a player is and what they play, bind() looks the player up
in that sport's adapter and records the identity and source bindings. From then on the player's
fixtures, results and stats come from the source (refresh), and every event with a live binding
is polled by one job shared by every player in it (poll_event). The agent keeps the profile and
the news, and stays the only source for sports without an adapter.
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import bus, engine, moments, notify, registry
from .adapters import ADAPTERS, Adapter, AdapterError, Fixture, Line, PlayerRef, Snapshot, espn, for_sport
from .config import POLL_SESSION
from .db import session
from .models import Event, EventPlayer, EventState, Follow, Player, PlayerCard, PlayerIdentity, PlayerLine, SourceBinding

log = logging.getLogger("sports_follow.structured")

# Card sections a bound player's source owns; an agent rebuild keeps them as they are.
OWNED = ("live", "upcoming", "recent_results", "season_stats", "season_stats_note", "provenance")
BIND_RETRY = 6 * 3600
UPCOMING_LIMIT = 8
RECENT_LIMIT = 5
SOURCE_NAMES = {"espn_soccer": "ESPN", "espn_cricket": "ESPNcricinfo", "espn_basketball": "ESPN", "espn_tennis": "ESPN", "lichess_chess": "Lichess"}
NO_LINE = {"cricket": "Did not bat or bowl", "football": "Not in the matchday squad", "basketball": "Not on the game's roster"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def binding(db: Session, player_id: int) -> SourceBinding | None:
    """The player's fixtures binding, if an adapter knows them."""
    return db.scalar(
        select(SourceBinding)
        .where(SourceBinding.player_id == player_id, SourceBinding.purpose == "fixtures")
        .order_by(SourceBinding.priority.desc(), SourceBinding.id.desc())
        .limit(1)
    )


def _teams_overlap(found: list[str], known: list[str]) -> bool:
    a = [espn.fold(t) for t in found if t]
    b = [espn.fold(t) for t in known if t]
    return any(x in y or y in x for x in a for y in b)


# ---------------------------------------------------------------- recognizing a name without the agent

# FIDE's list holds about a million players, most of them amateurs; a typed name is only taken to
# mean a chess player straight away if they're titled or rated 2200+.
CHESS_TITLES = {"GM", "IM", "WGM", "WIM", "FM", "WFM"}


def _name_key(name: str) -> list[str]:
    return sorted(espn.fold(name).replace("-", " ").replace(".", " ").split())


def identify(query: str) -> tuple[Adapter, PlayerRef] | None:
    """The one athlete a typed name means, found in the structured sources alone, or None.

    Only a full name that exactly one source claims counts: "Caitlin Clark" yes; "Clark", a typo,
    or a name two sports share go to the research agent, which decides as before.
    """
    wanted = _name_key(query)
    if len(wanted) < 2:
        return None
    # Two athletes anywhere on ESPN with this exact name (any sport, supported or not) is a question
    # for the agent: "John Smith" is dozens of people, and the first one found is rarely the one meant.
    try:
        search = espn.get_json(f"{espn.WEB}/search/v2?query={espn.quote(query)}&limit=10", ttl=3600)
    except AdapterError:
        return None
    namesakes = [item for group in search.get("results", []) if group.get("type") == "player" for item in group.get("contents", []) if _name_key(item.get("displayName", "")) == wanted]
    if len(namesakes) > 1:
        # ESPN keeps a separate college record ("A'Ja Wilson, South Carolina, NCAAW" beside the Aces
        # star). One professional among college namesakes is the one a fan means.
        pros = [n for n in namesakes if not (n.get("description") or "").upper().startswith("NCAA")]
        if len(pros) != 1:
            log.info("identify %r: %d athletes share the name; leaving it to the agent", query, len(namesakes))
            return None
        namesakes = pros
    found = []
    for adapter in ADAPTERS.values():
        if adapter.system == "lichess_chess":
            continue
        try:
            ref = adapter.find_player(query)
        except AdapterError:
            continue
        # It must be the one ESPN athlete with the typed name (their record may carry a longer legal
        # name: "Jasprit Bumrah" is "Jasprit Jasbirsingh Bumrah" on ESPNcricinfo), not another person
        # the adapter's own lookup happened to reach.
        if ref and len(namesakes) == 1 and (namesakes[0].get("uid") or "").endswith(f"~a:{ref.athlete_id}"):
            found.append((adapter, ref))
    if len(found) == 1:
        return found[0]
    if found or namesakes:
        # Claimed by two of our sources, or an ESPN athlete in a sport we don't cover (Max Verstappen).
        log.info("identify %r: not a single covered athlete; leaving it to the agent", query)
        return None
    chess = ADAPTERS.get("lichess_chess")
    try:
        ref = chess.find_player(query) if chess else None
        if ref:
            from .adapters import lichess_chess

            record = lichess_chess.get_json(f"{lichess_chess.API}/fide/player/{ref.athlete_id}", ttl=12 * 3600)
            if not record.get("inactive") and (record.get("title") in CHESS_TITLES or (record.get("standard") or 0) >= 2200):
                return chess, ref
    except AdapterError:
        pass
    return None


# ---------------------------------------------------------------- binding


def bind(player_id: int, ref: PlayerRef | None = None) -> bool:
    """Record the player's identity and source bindings. False if no adapter knows them.

    With a ref (a source already said exactly who this is: a search result the fan picked, or a name
    only one athlete has) it is recorded as given. Without one, a player already bound or holding an
    identity is re-read by that id, and only a player with neither is looked up by name.
    """
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
            # Re-read every build, so a transfer shows up in the team ids within hours.
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
    # Picked by id, or the name matched and a team the agent also named (or, in individual sports, the
    # same full name) makes it certain.
    same_name = sorted(espn.fold(ref.name).split()) == sorted(espn.fold(name).split())
    confidence = 1.0 if pinned or _teams_overlap(ref.team_names, teams) or (not teams and same_name) else 0.8
    known = {espn.team_key(t) for t in ref.team_names}
    ref.other_teams = [t for t in teams if t and espn.team_key(t) not in known]
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
    log.info("bind %s: %s athlete %s (%s), confidence %.1f", player_id, ref.system, ref.athlete_id, ", ".join(ref.team_names), confidence)
    return True


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
        league = f" ({loc['league'].upper()})" if loc.get("league") in ("nba", "wnba", "atp", "wta") else ""
        page = f" Their page on {SOURCE_NAMES.get(row.adapter, row.adapter)}: {loc['profile_url']}." if loc.get("profile_url") else ""
        return f"{loc.get('name') or player.name}, {player.sport.lower()}{where}{league}.{page} Other athletes may share the name; report only on this one."


# ---------------------------------------------------------------- fixtures, results, stats


def refresh(player_id: int) -> None:
    """Re-read a bound player's fixtures, recent results and stats from the source and publish the card.

    A player followed before their sport had an adapter is bound here first; a lookup that finds
    nobody isn't retried for BIND_RETRY seconds.
    """
    lock = f"structured:{player_id}"
    if not bus.try_lock(lock, 300):
        return
    try:
        with session() as db:
            bound = binding(db, player_id) is not None
        if not bound:
            if not bus.try_lock(f"bindtry:{player_id}", BIND_RETRY) or not bind(player_id):
                return
        _refresh(player_id)
    except AdapterError as exc:
        log.warning("refresh %s failed: %s", player_id, exc)
        _record_health(player_id, ok=False, error=str(exc))
    except Exception:
        log.exception("refresh %s crashed", player_id)
    finally:
        bus.release(lock)


def _refresh(player_id: int) -> None:
    with session() as db:
        row = binding(db, player_id)
        if row is None:
            return
        adapter = ADAPTERS.get(row.adapter)
        ref = PlayerRef(**row.locator)
    if adapter is None:
        return

    now = _now()
    fixtures = adapter.fixtures(ref)
    # A schedule can lag the match by its cache age; a game our poller saw finish is finished.
    with session() as db:
        seen_final = set(db.scalars(select(Event.key).where(Event.key.in_([f"{adapter.system}:{f.source_id}" for f in fixtures]), Event.status == "final")))
    for f in fixtures:
        if f"{adapter.system}:{f.source_id}" in seen_final:
            f.status = "final"
    finals_all = [f for f in fixtures if f.status == "final"]
    finals = finals_all[-RECENT_LIMIT:]  # scorecards read on every refresh; older ones by history.py
    # A "scheduled" game long past its start is one the source never updated: leave it out.
    ahead = [f for f in fixtures if f.status != "final" and not (f.status == "scheduled" and f.start_utc and f.start_utc < now - timedelta(hours=12))][:UPCOMING_LIMIT]
    recent: list[tuple[Fixture, Snapshot | None]] = []
    for f in finals:
        try:
            recent.append((f, adapter.snapshot(f.locator, final=True)))
        except AdapterError as exc:
            log.warning("refresh %s: no scorecard for %s: %s", player_id, f.source_id, exc)
            recent.append((f, None))
    stats, note = adapter.stats(ref, [(f, s) for f, s in recent if s])

    with session() as db:
        player = db.get(Player, player_id)
        card_row = db.get(PlayerCard, player_id)
        if player is None or card_row is None:
            return
        events = {f.source_id: upsert_event(db, adapter, player, f) for f in [*finals_all, *ahead]}
        for f, snap in recent:
            if snap:
                _record_final(db, events[f.source_id], player_id, snap, snap.lines.get(ref.athlete_id))
        # Every finished game the source returns joins the player's results history, kept for good.
        snaps = {f.source_id: snap for f, snap in recent if snap}
        for f in finals_all:
            snap = snaps.get(f.source_id)
            keep_result(db, events[f.source_id].id, player_id, result_item(adapter, f, snap, events[f.source_id], ref), scorecard=snap is not None)
        _record_health(player_id, ok=True, db=db)
        if card_row.status != "ready":
            return  # nothing to show yet; the build that follows reads the same events
        source = SOURCE_NAMES.get(adapter.system, adapter.system)
        stamp = now.isoformat()
        card = copy.deepcopy(card_row.card)
        card["upcoming"] = [_upcoming_item(f, events[f.source_id], ref) for f in ahead]
        card["recent_results"] = [result_item(adapter, f, snap, events[f.source_id], ref) for f, snap in reversed(recent)]
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
        if live.get("is_live") and not live.get("event_id"):
            # The agent's reading of a live game gives way to the poller's.
            card["live"] = {"is_live": False, "player_stats": []}
            card_row.is_live = False
        card["freshness"] = {**card.get("freshness", {}), "fixtures": stamp, "stats": stamp}
        card["version"] = (card_row.version or 0) + 1
        card_row.card = card
        card_row.version = card["version"]
        card_row.freshness = card["freshness"]
    bus.store_card(player_id, card)


_ORDER = {"scheduled": 0, "postponed": 0, "armed": 1, "live": 2, "final": 3}


def next_status(current: str | None, from_schedule: str) -> str:
    """An event's status after a schedule read. Schedules can be minutes old, so a read never moves a
    game backwards: it doesn't un-arm one or reopen one the poller saw finish. A postponed game can
    come back as scheduled."""
    if current is None or current == "postponed" or _ORDER.get(from_schedule, 0) >= _ORDER.get(current, 0):
        return from_schedule
    return current


def upsert_event(db: Session, adapter: Adapter, player: Player, f: Fixture) -> Event:
    key = f"{adapter.system}:{f.source_id}"
    event = db.scalar(select(Event).where(Event.key == key))
    if event is None:
        event = Event(key=key, sport=player.sport, title=f.title, status=f.status)
        db.add(event)
    event.title = f.title
    event.competition = f.competition
    event.start_utc = f.start_utc
    event.venue = f.venue
    event.notes = f.detail
    event.participants = f.team_ids
    event.live_binding = {"adapter": adapter.system, "locator": f.locator, "cadence": adapter.live_cadence}
    event.status = next_status(event.status, f.status)
    db.flush()
    db.execute(insert(EventPlayer).values(event_id=event.id, player_id=player.id).on_conflict_do_nothing())
    return event


def _record_final(db: Session, event: Event, player_id: int, snap: Snapshot, line: Line | None) -> None:
    """Keep one final state per finished event and the player's line in it."""
    if not db.scalar(select(EventState.id).where(EventState.event_id == event.id, EventState.status == "final").limit(1)):
        db.add(EventState(event_id=event.id, as_of=_now(), status="final", score_label=snap.score_label, clock_label=snap.clock_label, state=snap.state, source_url=snap.source_url))
    if line and not db.scalar(select(PlayerLine.id).where(PlayerLine.event_id == event.id, PlayerLine.player_id == player_id).limit(1)):
        db.add(PlayerLine(event_id=event.id, player_id=player_id, as_of=_now(), stats={"headline": line.headline, "stats": line.stats}))


def _record_health(player_id: int, ok: bool, error: str | None = None, db: Session | None = None) -> None:
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
    wanted = {espn.team_key(t) for t in ref.other_teams}
    return next((n for n in f.team_names if espn.team_key(n) in wanted), None)


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
    }


def result_item(adapter: Adapter, f: Fixture, snap: Snapshot | None, event: Event | None, ref: PlayerRef) -> dict[str, Any]:
    return {
        "title": f"{f.title} · {f.detail}" if f.detail else f.title,
        # A lagging schedule has no result yet for a game the poller saw finish; the scorecard does.
        "result": adapter.result_label(f, ref.team_ids) or snap_result(snap),
        "date": f.start_utc.date().isoformat() if f.start_utc else None,
        "player_contribution": contribution(snap, ref.athlete_id),
        "competition": f.competition,
        "event_id": event.id if event else None,
        "source_url": snap.source_url if snap else None,
    }


def contribution(snap: Snapshot | None, athlete_id: str) -> str | None:
    """The player's line in a game, in a few words ("2 goals", "Not in the matchday squad")."""
    line = snap.lines.get(athlete_id) if snap else None
    return line.headline if line else (NO_LINE.get(snap.state.get("kind"), "No stats") if snap and snap.lines else None)


def snap_result(snap: Snapshot | None) -> str:
    return (snap and (snap.clock_label if snap.state.get("kind") == "cricket" else snap.score_label)) or ""


def merged_result(old: dict[str, Any] | None, item: dict[str, Any], scorecard: bool) -> dict[str, Any]:
    """A result read again: a read without the scorecard keeps what an earlier scorecard added."""
    old = old or {}
    new = {**item, "scorecard": True if scorecard else old.get("scorecard", False)}
    if not scorecard:
        for key in ("player_contribution", "source_url", "result"):
            if old.get(key) and not new.get(key):
                new[key] = old[key]
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
    row.result = {**row.result, "player_contribution": contribution(snap, athlete_id), "source_url": snap.source_url, "result": row.result.get("result") or snap_result(snap), "scorecard": True}


# ---------------------------------------------------------------- live polling


def poll_event(event_id: int) -> None:
    """Poll one event while it is armed or live: every few seconds in play, every minute before.

    One job per event, however many players and fans are watching it. The job hands over after
    POLL_SESSION seconds; the next scheduler tick starts a fresh one if the game is still on.
    A game that stops changing for a minute (stumps, rain, half-time) is polled once a minute
    until it moves again.
    """
    lock = f"poll:{event_id}"
    if not bus.try_lock(lock, 120):
        return
    try:
        deadline = time.monotonic() + POLL_SESSION
        quiet_since = time.monotonic()
        while True:
            status, wait, changed = poll_once(event_id)
            if status in ("final", "postponed", "idle") or time.monotonic() + wait > deadline:
                return
            if changed:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since > 60:
                wait = max(wait, 60)
            bus.extend(lock, wait + 120)
            time.sleep(wait)
    except Exception:
        log.exception("poll %s crashed", event_id)
    finally:
        bus.release(lock)


def _evaluate(db: Session, snap: Snapshot, latest: EventState | None, player_ids: list[int]) -> None:
    """Chess: ask the engine for the game's position while it's on and a follower wants to hear
    about swings, and put the game's latest evaluation (engine.py) into the state."""
    state = snap.state
    game, fen = state.get("game_id"), state.get("fen")
    if not (game and fen and engine.enabled()):
        return
    if snap.status == "live" and not state.get("result"):
        rules = db.scalars(select(Follow.alert_rules).where(Follow.player_id.in_(player_ids))).all()
        if any(moments.wanted((r or {}).get("level", moments.DEFAULT_LEVEL), "key", "swing") for r in rules):
            engine.request(game, fen)
    ev = engine.latest(game)
    if ev:
        prev = latest.state if latest is not None and latest.state else {}
        state["eval"] = moments.settle(prev.get("eval") if prev.get("game_id") == game else None, ev)


def poll_once(event_id: int) -> tuple[str, int, bool]:
    """One poll: fetch the snapshot, append what changed, update every followed player's card in the event.

    Returns the event's status, how long to wait before the next poll, and whether anything changed.
    """
    with session() as db:
        event = db.get(Event, event_id)
        if event is None or not event.live_binding:
            return "idle", 0, False
        live_binding = dict(event.live_binding)
        adapter = ADAPTERS.get(live_binding.get("adapter", ""))
        if adapter is None:
            return "idle", 0, False
        followed = select(Follow.player_id)
        watchers = db.execute(
            select(PlayerIdentity.player_id, PlayerIdentity.external_id)
            .join(EventPlayer, EventPlayer.player_id == PlayerIdentity.player_id)
            .where(EventPlayer.event_id == event_id, PlayerIdentity.system == adapter.system, PlayerIdentity.player_id.in_(followed))
        ).all()
    if not watchers:
        return "idle", 0, False
    try:
        snap = adapter.snapshot(live_binding["locator"])
    except AdapterError as exc:
        log.warning("poll %s: %s", event_id, exc)
        return "error", 30, False

    now = _now()
    published: list[tuple[int, dict[str, Any]]] = []
    new_moments: list[int] = []
    with session() as db:
        event = db.get(Event, event_id)
        if event is None:
            return "idle", 0, False
        people = {pid: (name, sport) for pid, name, sport in db.execute(select(Player.id, Player.name, Player.sport).where(Player.id.in_([w[0] for w in watchers])))}
        latest = db.scalar(select(EventState).where(EventState.event_id == event_id).order_by(EventState.as_of.desc()).limit(1))
        if snap.state.get("kind") == "chess":
            _evaluate(db, snap, latest, [w[0] for w in watchers])
        changed = latest is None or (latest.status, latest.score_label, latest.clock_label, latest.state) != (snap.status, snap.score_label, snap.clock_label, snap.state)
        if changed:
            db.add(EventState(event_id=event_id, as_of=now, status=snap.status, score_label=snap.score_label, clock_label=snap.clock_label, state=snap.state, source_url=snap.source_url))
        if not (snap.status == "scheduled" and event.status == "armed"):
            event.status = snap.status

        for player_id, athlete_id in watchers:
            line = snap.lines.get(athlete_id)
            line_json = {"headline": line.headline, "stats": line.stats} if line else None
            line_changed = False
            last = db.scalar(select(PlayerLine).where(PlayerLine.event_id == event_id, PlayerLine.player_id == player_id).order_by(PlayerLine.as_of.desc()).limit(1))
            if line_json is not None:
                line_changed = last is None or last.stats != line_json
                if line_changed:
                    db.add(PlayerLine(event_id=event_id, player_id=player_id, as_of=now, stats=line_json))
            # What happened to this player since the last poll. A first look at a game already under
            # way (no earlier state, or no earlier line for them while it was live) is the baseline.
            if changed or line_changed:
                before = None
                if latest is not None and not (last is None and latest.status == "live"):
                    before = moments.Seen(latest.status, latest.state or {}, last.stats if last else None, latest.score_label or "", latest.clock_label or "")
                name, sport = people.get(player_id, ("", ""))
                seen = moments.Seen(snap.status, snap.state, line_json, snap.score_label, snap.clock_label, lineups=bool(snap.lines))
                new_moments += notify.record(db, player_id, event_id, moments.detect(sport, name, athlete_id, before, seen))
            card_row = db.get(PlayerCard, player_id)
            if card_row is None or card_row.status != "ready":
                continue
            card_row.live_checked_at = now
            if not (changed or line_changed) and card_row.is_live == (snap.status == "live"):
                continue
            card = copy.deepcopy(card_row.card)
            _apply_snapshot(card, event, snap, line, adapter, now)
            card["version"] = (card_row.version or 0) + 1
            card_row.card = card
            card_row.version = card["version"]
            card_row.freshness = card["freshness"]
            card_row.is_live = bool(card["live"].get("is_live"))
            published.append((player_id, card))
    for player_id, card in published:
        bus.store_card(player_id, card)
    notify.dispatch(new_moments)
    if published:
        log.info("poll %s: %s | %s -> %d card(s)", event_id, snap.score_label, snap.clock_label, len(published))
    wait = int(live_binding.get("cadence") or 10) if snap.status == "live" else 60
    return snap.status, wait, changed


def _apply_snapshot(card: dict[str, Any], event: Event, snap: Snapshot, line: Line | None, adapter: Adapter, now: datetime) -> None:
    stamp = now.isoformat()
    title = f"{event.title} · {event.notes}" if event.notes else event.title
    if snap.status == "live":
        card["live"] = {
            "is_live": True,
            "event_id": event.id,
            "event": title,
            "competition": event.competition,
            "score": snap.score_label,
            "clock": snap.clock_label,
            "headline": line.headline if line else None,
            "player_stats": line.stats if line else [],
            "state": snap.state,
            "moments": snap.moments[:10],
            "source": SOURCE_NAMES.get(adapter.system, adapter.system),
            "source_url": snap.source_url,
            "as_of": stamp,
        }
    elif (card.get("live") or {}).get("event_id") == event.id:
        card["live"] = {"is_live": False, "player_stats": []}

    if snap.status == "final":
        card["upcoming"] = [u for u in card.get("upcoming", []) if u.get("event_id") != event.id]
        result = {
            "title": title,
            "result": snap.clock_label if snap.state.get("kind") == "cricket" else snap.score_label,
            "date": event.start_utc.date().isoformat() if event.start_utc else None,
            "player_contribution": line.headline if line else None,
            "competition": event.competition,
            "event_id": event.id,
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
