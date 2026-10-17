"""A game's match page (GET /api/match/<source>/<game>, web/src/pages/MatchPage.tsx): one game, whoever follows
anyone in it, live, to come or over: its board, its key moments, and both sides' players to follow. Every live
game on Today opens it.

A game's address is its source and the id a fixture of it has there (Fixture.source_id), as an event's key
holds them: "sportmonks_football:19134567" is /match/sportmonks_football/19134567. So it's the same at every
read, before the game and after it, whether or not a fan follows a player in it. A chess board is a fixture of
each of its two players, so it has two addresses, and both show it.

Where a fan follows someone in it, the game is polled already (structured.py): its board and the source's key
events are their live card's, as fresh as the polls, and its moments are those found for every player in it.
Otherwise its board is the newer of its own last read and Live now's (live.py). Its own read, from its source
(an adapter's optional match()), brings both sides' line-ups and every player's line as well (a cricket
scorecard). The worker reads it (worker.read_match) at most once every FRESH seconds a game and only while
someone has the page open: the API serves the last read from Redis and asks for the next, so a page never
waits on a source, and more fans looking cost a source nothing more. A read is asked only for a game a source
has named (on Live now, read before, or a followed player's), so a made-up address spends nothing; at most
READS a minute of one source's allowance, whichever games; and a read that finds the game over, or not
there, is its last for hours.

What the page shows under the board is its sport's (Sport.match_page): a football timeline, a cricket
scorecard, an esports series by map or a lobby's standings. A chess board says it all.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import bus, live, registry, search, sports
from .adapters import ADAPTERS, KNOWN, Adapter, LiveGame, PlayerRef, source_name
from .db import session
from .models import Event, EventPlayer, EventState, Moment, Player, PlayerCard, PlayerIdentity

log = logging.getLogger(__name__)

FRESH = 60  # seconds a game's read is served before a look at its page asks for another
KEEP = 6 * 3600  # and Redis keeps it this long: a page open at full time, or opened again later, still has it
READS = 6  # reads a minute of one source for match pages, whichever games: what fans' looks may spend
MOMENTS = 40  # the most of a game's moments a page lists
# Moments every followed player in a game gets alike (their game started, they're in the XI): the board and
# the line-ups say those.
ALIKE = ("start", "lineup")
# A game's id in its source, as an address may carry it: an event's key holds the source and it in 64.
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,62}")


def path(key: str) -> str:
    """A game's address under /match/, from its event's key: "sportmonks_football:1" is "sportmonks_football/1"."""
    return key.replace(":", "/", 1)


def _key(system: str, fixture: str) -> str:
    return f"match:{system}:{fixture}"


def reader(system: str) -> Adapter | None:
    """The running source that can read one game (match()), or None."""
    adapter = ADAPTERS.get(system)
    return adapter if adapter is not None and callable(getattr(adapter, "match", None)) else None


def read(system: str, fixture: str, now: datetime | None = None) -> bool:
    """Read one game from its source, with its players, and keep it for the API (worker.read_match). Returns
    whether the source has it: one it hasn't is kept as such, so the page says so."""
    adapter = reader(system)
    if adapter is None or not adapter.fixture_pattern.fullmatch(fixture):
        return False
    game: LiveGame | None = cast(Any, adapter).match(fixture)
    sport = (game.sport if game else "") or (adapter.sports[0] if len(adapter.sports) == 1 else "")  # a source of several says the game's
    with session() as db:
        players = _roster(db, sport, system, game) if game else []
        blocked = registry.blocked(db) if game and game.bench else set()
    bench = [{"athlete_id": r.athlete_id, "name": r.name, "team": (r.team_names or [""])[0]} for r in (game.bench if game else []) if (system, r.athlete_id) not in blocked]
    payload = {"as_of": (now or datetime.now(timezone.utc)).isoformat(), "game": _dump(adapter, game, sport, players, bench) if game else None}
    bus.sync_redis().set(_key(system, fixture), json.dumps(payload, default=str), ex=KEEP)
    return game is not None


def _roster(db: Session, sport: str, system: str, game: LiveGame) -> list[dict[str, Any]]:
    """A game's players as its source lists them (its line-ups), without anyone an operator removed; for a
    game that names only its teams (an esports series), their pros the registry knows in its sport (its game's:
    the registry keeps each pro under theirs), as Live now offers them."""
    blocked = registry.blocked(db)
    if not game.players:
        return [{k: p[k] for k in ("athlete_id", "name", "team", "league")} for p in live._ranked(db, sport, system, game, blocked)[0]]
    return [{"athlete_id": r.athlete_id, "name": r.name, "team": (r.team_names or [""])[0], "league": r.league or game.league}
            for r in game.players if (system, r.athlete_id) not in blocked]


def _dump(adapter: Adapter, game: LiveGame, sport: str, players: list[dict[str, Any]], bench: list[dict[str, Any]]) -> dict[str, Any]:
    snap = game.snapshot
    return {
        "source_id": game.source_id,
        "sport": sport,  # the game's (an esports game's: "cs2"), as Live now's games say theirs
        "fixtures": game.fixtures or [game.source_id],
        "title": game.title,
        "competition": game.competition,
        "start_utc": game.start_utc.isoformat() if game.start_utc else None,
        "teams": game.teams,
        "status": snap.status,
        # Shaped like a card's live block (structured._apply_snapshot), so the app draws it the same way.
        "live": {"is_live": snap.status == "live", "event": game.title, "competition": game.competition, "score": snap.score_label, "clock": snap.clock_label,
                 "state": snap.state, "source": adapter.credit(snap), "live_note": adapter.live_note, "lag_note": adapter.lag_note,
                 "source_url": snap.source_url, "player_stats": []},
        "events": snap.moments,
        "lines": {athlete_id: {"headline": line.headline, "stats": line.stats} for athlete_id, line in snap.lines.items()},
        "players": players,
        "bench": bench,  # named for the lines they have, not offered to follow
    }


def allowance(system: str) -> str:
    """The allowance a source's reads spend (adapters/budget.py: its first budget's name; Sportmonks' football
    and cricket share one), which match pages' reads are counted against together."""
    adapter = KNOWN.get(system)
    budgets = adapter.budgets() if adapter else ()
    return budgets[0].name if budgets else system


def last_read(system: str, fixture: str) -> dict[str, Any] | None:
    """A game's last read ({"as_of", "game"}: None for a game its source hasn't got), or None: never read,
    dropped, or Redis can't say."""
    try:
        raw = cast(str | None, bus.sync_redis().get(_key(system, fixture)))
    except Exception:
        log.warning("match: %s:%s's read couldn't be fetched", system, fixture, exc_info=True)
        return None
    return json.loads(raw) if raw else None


def _listed(system: str, fixture: str) -> dict[str, Any] | None:
    """The game as Live now last read it, with when, if it's one of the games there."""
    read_ = live.last_read(system)
    for game in (read_ or {}).get("games") or []:
        if fixture in (game.get("fixtures") or [game["source_id"]]):
            return {**game, "as_of": read_["as_of"]} if read_ else None
    return None


def _age(as_of: str | None, now: datetime) -> float:
    return (now - datetime.fromisoformat(as_of)).total_seconds() if as_of else float("inf")


def _merged(newer: list[dict[str, Any]], older: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A game's key events, newest first, from the newest board's and an older read's: a followed player's
    card keeps only the newest few, and a read has those before them. The read's go after the newest board's
    oldest, where the read has that one too (else none: where they'd go can't be told), so one the newer
    board took back among its own (a card VAR rescinded) isn't brought back."""
    if not newer:
        return older

    def same(e: dict[str, Any]) -> tuple[Any, Any]:
        return e.get("clock"), e.get("text")

    # Two alike (a player scoring twice in a minute): the newest board's oldest is the read's as many-th one
    # alike as the newest board has, so neither goal shows twice or goes missing.
    oldest = same(newer[-1])
    nth = sum(same(e) == oldest for e in newer)
    alike = [i for i, e in enumerate(older) if same(e) == oldest]
    return newer + older[alike[nth - 1] + 1:] if len(alike) >= nth else newer


def _boards(db: Session, system: str, events: list[Event], game: dict[str, Any] | None, listed: dict[str, Any] | None, as_of: str | None) -> list[tuple[str, str, dict[str, Any], list[dict[str, Any]]]]:
    """Every board of the game there is, as (as_of, status, live block, the source's key events): a followed
    player's live card (as fresh as its polls), the last state stored, its own read, Live now's."""
    ids = [e.id for e in events]
    boards = []
    if ids:
        followed = db.scalars(select(PlayerCard.card["live"]).join(EventPlayer, EventPlayer.player_id == PlayerCard.player_id).where(EventPlayer.event_id.in_(ids), PlayerCard.is_live)).all()
        for block in followed:
            if isinstance(block, dict) and block.get("is_live") and block.get("event_id") in ids and block.get("as_of"):
                shown = {k: block.get(k) for k in ("event", "competition", "score", "clock", "state", "source", "live_note", "lag_note", "source_url", "as_of")}
                boards.append((block["as_of"], "live", {**shown, "is_live": True, "player_stats": []}, block.get("moments") or []))
        stored = db.scalar(select(EventState).where(EventState.event_id.in_(ids)).order_by(EventState.as_of.desc()).limit(1))
        if stored is not None:
            boards.append((stored.as_of.isoformat(), stored.status, {"is_live": stored.status == "live", "score": stored.score_label, "clock": stored.clock_label, "state": stored.state,
                                                                      "source": source_name(system), "source_url": stored.source_url, "as_of": stored.as_of.isoformat(), "player_stats": []}, []))
    if game and as_of:
        boards.append((as_of, game["status"], {**game["live"], "as_of": as_of}, game.get("events") or []))
    if listed:
        boards.append((listed["as_of"], "live", {**listed["live"], "source": source_name(system), "as_of": listed["as_of"]}, []))
    return boards


def view(db: Session, fan: str | None, system: str, fixture: str, now: datetime | None = None) -> tuple[dict[str, Any] | None, bool]:
    """A game's match page, and whether to ask its source for a new read; None when no source has named the
    game here (on Live now, in a read, or a followed player's)."""
    now = now or datetime.now(timezone.utc)
    read_ = last_read(system, fixture)
    game = (read_ or {}).get("game")
    listed = _listed(system, fixture)
    fixtures = list(dict.fromkeys([fixture, *((game or {}).get("fixtures") or []), *((listed or {}).get("fixtures") or [])]))
    events = list(db.scalars(select(Event).where(Event.key.in_([f"{system}:{f}" for f in fixtures])).order_by(Event.id)))
    source = KNOWN.get(system)
    if not (events or game or listed) or (source and not any(sports.on(k) for k in source.sports)):
        return None, False  # a sport turned off has no pages, as it isn't searched or offered
    # The game's sport, which a read or Live now says (a source of several names each game's: "cs2"), else its
    # event's, else its source's one; else, by its board's kind, the rules it's read by (esports' for any game).
    named = (game or {}).get("sport") or (listed or {}).get("sport") or (events[0].sport if events else "") or (source.sports[0] if source and len(source.sports) == 1 else "")

    boards = _boards(db, system, events, game, listed, (read_ or {}).get("as_of"))
    as_of, status, board, game_events = max(boards, key=lambda b: datetime.fromisoformat(b[0])) if boards else (None, events[0].status, None, [])
    game_events = _merged(game_events, (game or {}).get("events") or [])
    # Read by its board's kind ("cricket"), as the source drew it; for an esports game, the kind is the group's,
    # and the game's own sport ("cs2") is its, with the same rules.
    by_kind = sports.get(((board or {}).get("state") or {}).get("kind"))
    rules = sports.get(named) or by_kind or sports.OTHER
    if by_kind is not None and rules is not by_kind and rules.group != by_kind.key:
        rules = by_kind
    if not sports.on(rules.key):
        return None, False  # a game turned off, though its source runs for another
    event = events[0] if events else None

    roster = (game or {}).get("players") or (listed or {}).get("players") or []
    names = {p["athlete_id"]: (p["name"], p.get("team") or "") for p in [*roster, *((game or {}).get("bench") or [])]}
    page = rules.match_page((board or {}).get("state") or {}, game_events, (game or {}).get("lines") or {}, names)
    adapter = reader(system)
    # A read that found the game over, or found nothing (a source that hasn't got it), is settled: the next
    # comes when Redis has dropped it.
    settled = read_ is not None and (game is None or game.get("status") == "final")
    ask = bool(adapter and adapter.fixture_pattern.fullmatch(fixture) and not settled and _age((read_ or {}).get("as_of"), now) > FRESH)
    return {
        "key": f"{system}:{fixture}",
        "match": path(f"{system}:{fixture}"),
        "sport": rules.key or "other",
        "title": event.title if event else (game or listed or {}).get("title"),
        "competition": event.competition if event else (game or listed or {}).get("competition"),
        "detail": event.notes if event else None,
        "start_utc": event.start_utc.isoformat() if event and event.start_utc else (game or listed or {}).get("start_utc"),
        "status": status,
        "live": board,
        "page": page,
        "moments": _moments(db, [e.id for e in events]),
        "sides": _sides(db, fan, rules, system, roster, (game or {}).get("teams") or [], [e.id for e in events]),
        "event_ids": [e.id for e in events],
        "fixtures": fixtures,
    }, ask


def _moments(db: Session, event_ids: list[int]) -> list[dict[str, Any]]:
    """What was found for the players followed in the game, newest first (moments.py), but what each of them
    gets alike."""
    if not event_ids:
        return []
    rows = db.scalars(select(Moment).where(Moment.event_id.in_(event_ids), Moment.kind.not_in(ALIKE)).order_by(Moment.created_at.desc(), Moment.id.desc()).limit(MOMENTS))
    return [{"id": m.id, "at": m.created_at.isoformat(), "kind": m.kind, "level": m.level, "title": m.title, "body": m.body, "player_id": m.player_id} for m in rows]


def _sides(db: Session, fan: str | None, rules: sports.Sport, system: str, roster: list[dict[str, Any]], teams: list[str], event_ids: list[int]) -> list[dict[str, Any]]:
    """Each side's players to follow ({"name", "players"}: search results, in the line-up's order), the sides in
    the game's order. A game whose line-ups no read has offers the players on Sports Follow in it, as one side
    with no name."""
    blocked = registry.blocked(db)
    refs = [PlayerRef(system=system, athlete_id=p["athlete_id"], name=p["name"], team_ids=[], team_names=[p.get("team") or ""], league=p.get("league"))
            for p in roster if (system, p["athlete_id"]) not in blocked]
    if not refs and event_ids:
        on = db.execute(select(PlayerIdentity.external_id, Player.name).join(Player, Player.id == PlayerIdentity.player_id).join(EventPlayer, EventPlayer.player_id == Player.id)
                        .where(EventPlayer.event_id.in_(event_ids), PlayerIdentity.system == system).order_by(Player.name)).all()
        refs = [PlayerRef(system=system, athlete_id=aid, name=name, team_ids=[], team_names=[""]) for aid, name in dict(on).items()]
    rows = search.offered_refs(db, rules.label, refs, fan)
    sides: dict[str, list[dict[str, Any]]] = {team: [] for team in teams}
    for ref, row in zip(refs, rows):
        sides.setdefault(ref.team_names[0], []).append(search.public(row))
    return [{"name": name, "players": players} for name, players in sides.items() if players]
