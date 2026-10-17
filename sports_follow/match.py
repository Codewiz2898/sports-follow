"""A game's match page (GET /api/match/<source>/<game>, web/src/pages/MatchPage.tsx): one game, whoever follows
anyone in it, live, to come or over: its board, its key moments, the fan's players in it with their lines, and
both sides' players to follow. Every game on Today opens it, live, over or to come, and so does a game on a
player's Matches tab.

A game's address is its source and the id a fixture of it has there (Fixture.source_id), as an event's key
holds them: "sportmonks_football:19134567" is /match/sportmonks_football/19134567. So it's the same at every
read, before the game and after it, whether or not a fan follows a player in it. A chess board is a fixture of
each of its two players, so it has two addresses, and both show it. The server gives each game it lists its
address (`match`), where it has a page: the app never makes one up.

Where a fan follows someone in it, the game is polled already (structured.py): its board and the source's key
events are their live card's, as fresh as the polls, and its moments are those found for every player in it.
Otherwise its board is the newer of its own last read and Live now's (live.py); a finished game's is its final
one, its own read's once that's settled. Its own read, from its source (an adapter's optional match()), brings
both sides' line-ups and every player's line as well (a cricket scorecard). The worker reads it
(worker.read_match) at most once every FRESH seconds a game and only while someone has the page open: the API
serves the last read from Redis and asks for the next, so a page never waits on a source, and more fans
looking cost a source nothing more. A read is asked only for a game a source has named (on Live now, among
Today's big results and big games coming up (league.py), read before, or a followed player's, their results
included), so a made-up address spends nothing; at most READS a minute of one source's allowance, whichever
games; for a game still hours off, or called off, once every AHEAD seconds. A read that finds the game over
is read once more CONFIRM later, when the source has filled in what it hadn't at the final whistle, and that
one, over and whole, is the last for FINAL_KEEP, a week: so a crowd opening yesterday's final costs its source
two reads. One that finds it not there is the last for hours.

What the page shows under the board is its sport's (Sport.match_page): a football timeline, a cricket
scorecard, an esports series by map or a lobby's standings, a finished chess game's moves. Over a finished
game's board its sport says who won (Sport.verdict).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import bus, league, live, matches, registry, search, sports
from .adapters import ADAPTERS, KNOWN, Adapter, LiveGame, PlayerRef, source_name
from .db import session
from .models import Event, EventPlayer, EventState, Follow, Moment, Player, PlayerCard, PlayerIdentity, PlayerLine

log = logging.getLogger(__name__)

FRESH = 60  # seconds a game's read is served before a look at its page asks for another
AHEAD = 30 * 60  # or, for a game to come more than SOON from its start, this long: little changes but its line-ups,
SOON = 2 * 3600  # out an hour or so before
KEEP = 6 * 3600  # and Redis keeps it this long: a page open at full time, or opened again later, still has it
# A read that finds the game over is the first word, not the last: the source fills in what's missing in the
# minutes after (a scorecard's lines, a result's note, a goal's correction), and a chess round's next game can
# follow a decided one. So it's read once more CONFIRM after the game was first found over (and every CONFIRM
# while the source says its read is partial, for PARTIAL at most), and that read, still over and whole, is
# settled: kept FINAL_KEEP and not asked for again till then. A crowd opening yesterday's final costs its
# source two reads. Till it's settled, a live board newer than it by LAG shows instead (a chess mini-match's
# next game), and the game is read every FRESH again.
CONFIRM = 30 * 60
PARTIAL = 6 * 3600  # what a source's read of a finished game still lacks this long after it was first found over isn't coming
FINAL_KEEP = 7 * 86400  # a week
LAG = 2 * 60  # how much newer than a final board a live one must be to show over it: Live now's list can lag a game's end
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
    whether the source has it: one it hasn't is kept as such, so the page says so. A read that finds the game
    over says since when it's been found so (final_since, carried from the reads before it that did), and
    whether it's settled: whole, and CONFIRM or more after that."""
    adapter = reader(system)
    if adapter is None or not adapter.fixture_pattern.fullmatch(fixture):
        return False
    game: LiveGame | None = cast(Any, adapter).match(fixture)
    sport = (game.sport if game else "") or (adapter.sports[0] if len(adapter.sports) == 1 else "")  # a source of several says the game's
    with session() as db:
        players = _roster(db, sport, system, game) if game else []
        blocked = registry.blocked(db) if game and game.bench else set()
        if game:
            matches.learn(db, system, [fixture, *game.fixtures])  # a board's other address, for its follows
    bench = [{"athlete_id": r.athlete_id, "name": r.name, "team": (r.team_names or [""])[0]} for r in (game.bench if game else []) if (system, r.athlete_id) not in blocked]
    now = now or datetime.now(timezone.utc)
    over = game is not None and game.snapshot.status == "final"
    before = last_read(system, fixture) or {}
    since = (before.get("final_since") if ((before.get("game") or {}).get("status") == "final") else None) or now.isoformat()
    waited = now - datetime.fromisoformat(since)
    partial = over and cast(LiveGame, game).partial and waited < timedelta(seconds=PARTIAL)
    settled = over and not partial and waited >= timedelta(seconds=CONFIRM)
    payload = {"as_of": now.isoformat(), "game": _dump(adapter, game, sport, players, bench) if game else None,
               "final_since": since if over else None, "partial": partial, "settled": settled}
    bus.sync_redis().set(_key(system, fixture), json.dumps(payload, default=str), ex=FINAL_KEEP if settled else KEEP)
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


def _big(system: str, fixture: str) -> dict[str, Any] | None:
    """The game as the league's last read lists it, if it's one of Today's big results or big games coming up
    (league.py): its title, when, and whether it's over."""
    key = f"{system}:{fixture}"
    return next((g for g in (league.last_read(system) or {}).get("games") or [] if g.get("key") == key), None)


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


Board = tuple[str, str, dict[str, Any], list[dict[str, Any]], bool]


def _boards(db: Session, system: str, events: list[Event], read_: dict[str, Any] | None, listed: dict[str, Any] | None) -> list[Board]:
    """Every board of the game there is, as (as_of, status, live block, the source's key events, whether it's
    the game's own read, settled): a followed player's live card (as fresh as its polls), the last state stored,
    its own read, Live now's."""
    ids = [e.id for e in events]
    boards = []
    if ids:
        followed = db.scalars(select(PlayerCard.card["live"]).join(EventPlayer, EventPlayer.player_id == PlayerCard.player_id).where(EventPlayer.event_id.in_(ids), PlayerCard.is_live)).all()
        for block in followed:
            if isinstance(block, dict) and block.get("is_live") and block.get("event_id") in ids and block.get("as_of"):
                shown = {k: block.get(k) for k in ("event", "competition", "score", "clock", "state", "source", "live_note", "lag_note", "source_url", "as_of")}
                boards.append((block["as_of"], "live", {**shown, "is_live": True, "player_stats": []}, block.get("moments") or [], False))
        stored = db.scalar(select(EventState).where(EventState.event_id.in_(ids)).order_by(EventState.as_of.desc()).limit(1))
        if stored is not None:
            boards.append((stored.as_of.isoformat(), stored.status, {"is_live": stored.status == "live", "score": stored.score_label, "clock": stored.clock_label, "state": stored.state,
                                                                      "source": source_name(system), "source_url": stored.source_url, "as_of": stored.as_of.isoformat(), "player_stats": []}, [], False))
    game, as_of = (read_ or {}).get("game"), (read_ or {}).get("as_of")
    if game and as_of:
        boards.append((as_of, game["status"], {**game["live"], "as_of": as_of}, game.get("events") or [], bool((read_ or {}).get("settled"))))
    if listed:
        boards.append((listed["as_of"], "live", {**listed["live"], "source": source_name(system), "as_of": listed["as_of"]}, [], False))
    return boards


def _shown(boards: list[Board]) -> Board:
    """The board the page shows: the game's own read once it's settled (over, confirmed, and with all the page
    shows: a chess game's moves); else the newest, a final board standing over a live one less than LAG newer
    (Live now's list can lag a game's end), so a game that goes on after its source called it over (a chess
    mini-match's next game) shows live."""
    def rank(b: Board) -> tuple[bool, datetime]:
        at = datetime.fromisoformat(b[0])
        return b[1] == "final" and b[4], at + timedelta(seconds=LAG) if b[1] == "final" else at

    return max(boards, key=rank)


def _fresh(status: str, start: str | None, read_: dict[str, Any] | None, now: datetime) -> float:
    """How long a read of the game is served before a look asks for another: FRESH while the board shown is live
    (a newer one than a read that found it over, too: a mini-match's next game); CONFIRM after a read that found
    it over but isn't settled; AHEAD for a game called off (cancelled, abandoned, suspended: a new date comes
    slowly, if at all), or to come more than SOON from its start, or with its day still to be fixed."""
    if status == "live":
        return FRESH
    if ((read_ or {}).get("game") or {}).get("status") == "final":
        return CONFIRM
    if status == "postponed" or (status == "scheduled" and (not start or datetime.fromisoformat(start) - now > timedelta(seconds=SOON))):
        return AHEAD
    return FRESH


@dataclass
class Said:
    """What's known of a game by its address: its source's last read of it, Live now's copy, the league's, the
    fixtures it's under (a chess board is one of each player's) and its events here, one per fixture."""

    read: dict[str, Any] | None
    listed: dict[str, Any] | None
    big: dict[str, Any] | None
    fixtures: list[str]
    events: list[Event]

    @property
    def game(self) -> dict[str, Any] | None:
        return (self.read or {}).get("game")

    def named(self, source: Adapter | None) -> bool:
        """Whether a source has named the game here (on Live now, among the league's big games, in a read, or a
        followed player's), and its source runs a sport that's on: a sport turned off has no pages, as it isn't
        searched or offered."""
        return bool(self.events or self.game or self.listed or self.big) and not (source and not any(sports.on(k) for k in source.sports))

    def sport(self, source: Adapter | None) -> str:
        """The game's sport, which a read or Live now says (a source of several names each game's: "cs2"), else
        its event's, else its source's one; "" when none says."""
        events = self.events
        return ((self.game or {}).get("sport") or (self.listed or {}).get("sport") or (self.big or {}).get("sport") or (events[0].sport if events else "")
                or (source.sports[0] if source and len(source.sports) == 1 else ""))


def find(db: Session, system: str, fixture: str) -> Said:
    """What's known of the game at an address (Said)."""
    read_ = last_read(system, fixture)
    game = (read_ or {}).get("game")
    listed = _listed(system, fixture)
    fixtures: list[str] = list(dict.fromkeys([fixture, *((game or {}).get("fixtures") or []), *((listed or {}).get("fixtures") or [])]))
    events = list(db.scalars(select(Event).where(Event.key.in_([f"{system}:{f}" for f in fixtures])).order_by(Event.id)))
    return Said(read_, listed, _big(system, fixture), fixtures, events)


def view(db: Session, fan: str | None, system: str, fixture: str, now: datetime | None = None) -> tuple[dict[str, Any] | None, bool]:
    """A game's match page, and whether to ask its source for a new read; None when no source has named the
    game here (on Live now, among the league's big games, in a read, or a followed player's)."""
    now = now or datetime.now(timezone.utc)
    known = find(db, system, fixture)
    read_, game, listed, big, fixtures, events = known.read, known.game, known.listed, known.big, known.fixtures, known.events
    source = KNOWN.get(system)
    if not known.named(source):
        return None, False
    # Else, by its board's kind, the rules it's read by (esports' for any game).
    named = known.sport(source)

    boards = _boards(db, system, events, read_, listed)
    unread = events[0].status if events else (big or {}).get("status") or "scheduled"
    as_of, status, board, game_events, _ = _shown(boards) if boards else (None, unread, None, [], False)
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
    said = game or listed or big or {}  # what a source last said of the game, where nothing's stored
    start = event.start_utc.isoformat() if event and event.start_utc else said.get("start_utc")

    roster = (game or {}).get("players") or (listed or {}).get("players") or []
    names = {p["athlete_id"]: (p["name"], p.get("team") or "") for p in [*roster, *((game or {}).get("bench") or [])]}
    state = (board or {}).get("state") or {}
    page = rules.match_page(state, game_events, (game or {}).get("lines") or {}, names)
    # Who won, over a finished game's board; before its first read, what the league's card says ("Arsenal beat
    # Chelsea", or the score).
    verdict = ""
    if status == "final":
        verdict = (rules.verdict(state, board.get("score") or "", board.get("clock") or "") if board else "") or (big or {}).get("line") or (big or {}).get("word") or ""
    adapter = reader(system)
    # A read that found the game over and settled (read), or found nothing (a source that hasn't got it), is the
    # last: the next comes when Redis has dropped it.
    settled = read_ is not None and (game is None or bool(read_.get("settled")))
    ask = bool(adapter and adapter.fixture_pattern.fullmatch(fixture) and not settled and _age((read_ or {}).get("as_of"), now) > _fresh(status, start, read_, now))
    return {
        "key": f"{system}:{fixture}",
        "match": path(f"{system}:{fixture}"),
        "sport": rules.key or "other",
        "title": event.title if event else said.get("title"),
        "competition": event.competition if event else said.get("competition"),
        "detail": event.notes if event else (big or {}).get("detail"),
        "start_utc": start,
        "status": status,
        "live": board,
        "verdict": verdict,
        "page": page,
        "moments": _moments(db, [e.id for e in events]),
        "yours": _yours(db, fan, system, [e.id for e in events], (game or {}).get("lines") or {}),
        "sides": _sides(db, fan, rules, system, roster, (game or {}).get("teams") or [], [e.id for e in events]),
        "event_ids": [e.id for e in events],
        "fixtures": fixtures,
    }, ask


def _moments(db: Session, event_ids: list[int]) -> list[dict[str, Any]]:
    """What was found for the players followed in the game, newest first (moments.py), but what each of them
    gets alike. Not the game's own moments, for fans following the game (matches.py): its board and timeline
    say those."""
    if not event_ids:
        return []
    rows = db.scalars(select(Moment).where(Moment.event_id.in_(event_ids), Moment.player_id.is_not(None), Moment.kind.not_in(ALIKE)).order_by(Moment.created_at.desc(), Moment.id.desc()).limit(MOMENTS))
    return [{"id": m.id, "at": m.created_at.isoformat(), "kind": m.kind, "level": m.level, "title": m.title, "body": m.body, "player_id": m.player_id} for m in rows]


def _yours(db: Session, fan: str | None, system: str, event_ids: list[int], lines: dict[str, Any]) -> list[dict[str, Any]]:
    """The players this fan follows in the game, by name, each with their line in it (the newest a poll stored,
    else the game's read's) and, once it's over, their result from their side ("Won 2–1")."""
    if not fan or not event_ids:
        return []
    rows = db.execute(select(Player.id, Player.name, Player.sport, EventPlayer.result).join(EventPlayer, EventPlayer.player_id == Player.id)
                      .join(Follow, (Follow.player_id == Player.id) & (Follow.fan_id == fan)).where(EventPlayer.event_id.in_(event_ids)).order_by(Player.name, Player.id)).all()
    if not rows:
        return []
    ids = list(dict.fromkeys(r.id for r in rows))
    stored: dict[int, dict[str, Any]] = {}
    for player_id, stats in db.execute(select(PlayerLine.player_id, PlayerLine.stats).where(PlayerLine.event_id.in_(event_ids), PlayerLine.player_id.in_(ids)).order_by(PlayerLine.as_of.desc())):
        stored.setdefault(player_id, stats)
    known = dict(db.execute(select(PlayerIdentity.player_id, PlayerIdentity.external_id).where(PlayerIdentity.player_id.in_(ids), PlayerIdentity.system == system)).all())
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        line = stored.get(r.id) or lines.get(known.get(r.id) or "") or {}
        result = r.result.get("result") if isinstance(r.result, dict) else None
        out.setdefault(r.id, {"player_id": r.id, "name": r.name, "sport": r.sport, "result": result or None, "headline": line.get("headline"), "stats": line.get("stats") or []})
    return list(out.values())


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
