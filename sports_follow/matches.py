"""Following a game by itself (the match page's Follow, BHA-270): a fan hears a game's key moments from both
sides, whoever plays in it and whether or not they follow anyone in it, until it ends.

A follow is a row (MatchFollow) of a fan and the game's event, the unit of polling a followed player's games
share. Following a game nobody follows a player in makes its event, from what its source last said of it
(its match page's read, Live now, the league's big games) and the locator its source polls it by (an
adapter's optional locator(): a game by its id alone). From then on the poller treats it as it does a
followed player's game (structured.py, pipeline.due_work): armed before its start by its sport's lead,
polled at its source's cadence in the live lane, through the same lanes and the same allowances
(adapters/budget.py keeps a reserve for live games), and left alone once it ends. A game whose source can't
poll it by itself (one only a followed player's fixtures bring) can be followed while such an event is
here; otherwise its button says alerts aren't available, so nothing is promised that can't be sent.

Each poll of a followed game finds its own moments (Sport.game_moments: football's goals and cards, a
cricketer's wickets and fifties, a chess board turning, an esports series' maps, every result), titled with
the game's name ("India v Australia: Kohli out for 82") and saying whom they're about, so a fan who follows
a player in it as well hears each once, as their player's (alerts.plan). They reach the fan by the level
they chose (key moments, the default), on the game's one notification, muted with it, as their players'
moments do; the activity list has them, and the app's toasts (the game's channel, bus.match_channel).

When the game ends the follow lapses (until): the tick marks it within the minute, and what the poll that
saw it end found (the result) is the last thing sent. A game to come that never started lapses a day past
its start, and one with no date a fortnight after it was followed. A fan follows at most MAX games at
once; an address no source has named makes no follow, no event and no poll.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import CompoundSelect, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import array, distinct_on, insert
from sqlalchemy.orm import Session

from . import sports, structured
from .adapters import ADAPTERS, KNOWN, source_name
from .models import Event, EventPlayer, EventState, Follow, MatchFollow, NotificationMute
from .moments import DEFAULT_LEVEL

if TYPE_CHECKING:
    from .match import Said

MAX = 20  # games a fan follows at once
LAPSE_AFTER = timedelta(days=1)  # a game to come that never started lapses this long past its start
UNDATED = timedelta(days=14)  # and one with no date, this long after it was followed
NO_ALERTS = "Alerts aren't available for this game: its source can't be followed live here."
OVER = "This game is over."
HELD = "Live alerts for this game are paused: its source is busy with games fans follow players in. They resume when it has room."
NOT_STARTED = ("scheduled", "armed", "postponed")  # a game that hasn't started: armed, it's polled, and its source still says it's to come


class Refused(Exception):
    """A follow that can't be made: its HTTP status, and what the fan is told."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def watched() -> CompoundSelect[int]:
    """The events fans follow by themselves now, or a team in (teams.py): the poller arms and polls them as a
    followed player's."""
    from . import teams  # a team's games are followed games (teams reads the boards of them here)

    return select(MatchFollow.event_id).where(MatchFollow.until.is_(None)).union(teams.watched())


def followed(db: Session, event_id: int) -> bool:
    """Whether any fan follows this game by itself now, or a team in it."""
    from . import teams

    return db.scalar(select(MatchFollow.id).where(MatchFollow.event_id == event_id, MatchFollow.until.is_(None)).limit(1)) is not None or teams.followed(db, event_id)


def rules(db: Session, event_id: int) -> list[dict[str, Any]]:
    """The alert rules ({"level"}) of everyone following this game by itself now, or a team in it."""
    from . import teams

    return [*(r or {} for r in db.scalars(select(MatchFollow.alert_rules).where(MatchFollow.event_id == event_id, MatchFollow.until.is_(None)))), *teams.rules(db, event_id)]


def poller(system: str, fixture: str, events: list[Event]) -> dict[str, Any] | None:
    """How a game would be polled: the live binding of its event here (a followed player's fixture of it, or an
    earlier follow's) with a running source, else its source's locator for it; None when no running source
    can poll it by itself."""
    for event in events:
        if (event.live_binding or {}).get("adapter") in ADAPTERS:
            return dict(event.live_binding or {})
    adapter = ADAPTERS.get(system)
    make = getattr(adapter, "locator", None)
    locator = make(fixture) if adapter is not None and callable(make) else None
    return {"adapter": system, "locator": locator, "cadence": adapter.live_cadence} if adapter is not None and locator else None


def _of_game(fan: str, event_ids: list[int], key: str) -> Any:
    """A fan's follows of a game: of one of its events, or made at any of its addresses (a chess board's other one)."""
    return and_(MatchFollow.fan_id == fan, or_(MatchFollow.event_id.in_(event_ids), MatchFollow.keys.contains([key])))


def held(db: Session, event: Event) -> bool:
    """Whether a followed game under way isn't being polled for now: nobody follows a player in it, and its source,
    polled a request a game, is down to what its live games need (structured.holds_back)."""
    adapter = ADAPTERS.get((event.live_binding or {}).get("adapter", ""))
    if event.status not in ("armed", "live") or adapter is None or not structured.holds_back(adapter):
        return False
    return db.scalar(select(EventPlayer.event_id).join(Follow, Follow.player_id == EventPlayer.player_id).where(EventPlayer.event_id == event.id).limit(1)) is None


def offer(db: Session, fan: str | None, system: str, fixture: str, page: dict[str, Any]) -> dict[str, Any]:
    """What a match page's Follow button says (match.view's page): whether this fan follows the game (at this
    address or another of the game's), which event that follow is on (to stop it by), whether its moments can
    reach them at all and if not why, and whether its alerts are paused for now (held)."""
    ids = page.get("event_ids") or []
    mine = db.scalar(select(MatchFollow).where(_of_game(fan, ids, f"{system}:{fixture}"), MatchFollow.until.is_(None)).limit(1)) if fan else None
    state = {"following": mine is not None, "event_id": mine.event_id if mine else None, "held": False}
    if page.get("status") == "final":
        return {**state, "alerts": False, "note": OVER}
    events = list(db.scalars(select(Event).where(Event.id.in_(ids)).order_by(Event.id))) if ids else []
    can = poller(system, fixture, events) is not None
    followed = db.get(Event, mine.event_id) if mine else None
    paused = followed is not None and held(db, followed)
    return {**state, "alerts": can, "held": paused, "note": HELD if paused else None if can else NO_ALERTS}


def _over(known: Said, now: datetime) -> bool:
    """Whether the game's over: polled to its end here, or never started a day past its start (as lapse() has
    it), or its own read found it over and settled (match.read: a chess mini-match can go on after a read called
    it over), or, with neither, the league's card says so."""
    read_over = (known.game or {}).get("status") == "final" and bool((known.read or {}).get("settled"))
    gone = any(e.status in NOT_STARTED and e.start_utc is not None and e.start_utc < now - LAPSE_AFTER for e in known.events)
    return any(e.status == "final" for e in known.events) or gone or read_over or (not known.events and not known.game and (known.big or {}).get("status") == "final")


def follow(db: Session, fan: str, system: str, fixture: str) -> int:
    """Follow the game at an address (its source and a fixture's id, as its match page has it), making its event
    if none is here yet. Returns the event's id; raises Refused for a game no source has named here, one over,
    one no running source can poll, or a fan already following MAX games."""
    from . import match  # the match page's: what's known of a game (it reads Live now, which reads search)

    source = KNOWN.get(system)
    if source is None or not match.ID.fullmatch(fixture):
        raise Refused(404, "No such game.")
    known = match.find(db, system, fixture)
    sport = known.sport(source)
    if not known.named(source) or not sports.on(sport):
        raise Refused(404, "No such game.")
    if _over(known, datetime.now(timezone.utc)):
        raise Refused(409, OVER)
    binding = poller(system, fixture, known.events)
    if binding is None:
        raise Refused(409, NO_ALERTS)
    # One follow at a time per fan, so two at once can't both pass the count.
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"match-follow:{fan}", 0))))
    ids = [e.id for e in known.events]
    keys = list(dict.fromkeys(f"{system}:{f}" for f in known.fixtures))
    mine = db.scalar(select(MatchFollow).where(_of_game(fan, ids, f"{system}:{fixture}")).limit(1))
    if mine is None or mine.until is not None:
        count = db.scalar(select(func.count()).select_from(MatchFollow).where(MatchFollow.fan_id == fan, MatchFollow.until.is_(None))) or 0
        if count >= MAX:
            raise Refused(429, f"You follow {MAX} games already: unfollow one to follow this.")
    if mine is not None:
        mine.until = None  # a lapsed follow of a game that's on again (postponed, then played)
        mine.keys = list(dict.fromkeys([*(mine.keys or []), *keys]))
        return mine.event_id
    # A game here as several events (a chess board is a fixture of each of its players) is followed as the one
    # of a player the fan follows in it: one notification for the game, its moments heard once, one board on Today.
    polled = [e for e in known.events if (e.live_binding or {}).get("adapter") == binding["adapter"]]
    theirs = set(db.scalars(select(EventPlayer.event_id).join(Follow, and_(Follow.player_id == EventPlayer.player_id, Follow.fan_id == fan))
                            .where(EventPlayer.event_id.in_([e.id for e in polled])))) if len(polled) > 1 else set()
    event = next((e for e in polled if e.id in theirs), None) or (polled[0] if polled else None) or _event(db, system, fixture, known, sport, binding)
    # From the database's clock, as its moments' times are: the activity list shows those since.
    keys = list(dict.fromkeys([event.key, *keys]))
    db.execute(insert(MatchFollow).values(fan_id=fan, event_id=event.id, alert_rules={}, keys=keys).on_conflict_do_update(index_elements=["fan_id", "event_id"], set_={"until": None, "keys": keys}))
    return event.id


def event_for(db: Session, system: str, fixture: str) -> Event | None:
    """The event a game at an address is polled by (a pick waiting on it, predictions.pick): one of its events here
    a running source polls, else one made from what its source last said of it, as a follow makes it; None for a
    game no source has named here, or that no running source can poll."""
    from . import match

    source = KNOWN.get(system)
    if source is None or not match.ID.fullmatch(fixture):
        return None
    known = match.find(db, system, fixture)
    sport = known.sport(source)
    binding = poller(system, fixture, known.events)
    if binding is None or not known.named(source) or not sports.on(sport):
        return None
    polled = [e for e in known.events if (e.live_binding or {}).get("adapter") == binding["adapter"]]
    return polled[0] if polled else _event(db, system, fixture, known, sport, binding)


def _event(db: Session, system: str, fixture: str, known: Said, sport: str, binding: dict[str, Any]) -> Event:
    """The game's event, from what its source last said of it, polled by its source's locator: as a followed
    player's fixture would make it (structured.upsert_event), whose refresh may later fill it in."""
    said = known.game or known.listed or known.big or {}
    status = (known.game or {}).get("status") or ("live" if known.listed else None) or (known.big or {}).get("status") or "scheduled"
    start = said.get("start_utc")
    key = f"{system}:{fixture}"
    db.execute(insert(Event).values(
        key=key, sport=sports.label(sport) if sport else "", title=(said.get("title") or key)[:300], competition=(said.get("competition") or "")[:200],
        start_utc=datetime.fromisoformat(start) if start else None, notes=(known.big or {}).get("detail"), status=status if status in ("scheduled", "live", "postponed") else "scheduled",
        participants=[], live_binding=binding,
    ).on_conflict_do_nothing(index_elements=[Event.key]))
    return db.scalars(select(Event).where(Event.key == key)).one()


def unfollow(db: Session, fan: str, system: str, fixture: str) -> None:
    """Stop following the game at an address: nothing more of it is sent to this fan. Its follow is found by any
    of the game's addresses, whichever of its events it's on."""
    from . import match

    if not match.ID.fullmatch(fixture):
        return
    keys = [f"{system}:{f}" for f in match.find(db, system, fixture).fixtures]
    ids = select(Event.id).where(Event.key.in_(keys))
    db.execute(delete(MatchFollow).where(MatchFollow.fan_id == fan, or_(MatchFollow.event_id.in_(ids), MatchFollow.keys.contains([f"{system}:{fixture}"]))))


def unfollow_event(db: Session, fan: str, event_id: int) -> None:
    """Stop following a game by the event its follow is on (GET /api/me/matches, or the page's follow state)."""
    db.execute(delete(MatchFollow).where(MatchFollow.fan_id == fan, MatchFollow.event_id == event_id))


def learn(db: Session, system: str, games: list[list[str]]) -> None:
    """A read that lists games' every fixture (a chess board's two: Live now's, a match page's) adds them to the
    follows of any of them, so a board followed before anything listed both still settles (settle). One query a
    read, however many games it lists."""
    boards = [keys for keys in (list(dict.fromkeys(f"{system}:{f}" for f in fixtures)) for fixtures in games) if len(keys) > 1]
    every = [k for keys in boards for k in keys]
    if not every:
        return
    for row, key in db.execute(select(MatchFollow, Event.key).join(Event, Event.id == MatchFollow.event_id)
                               .where(MatchFollow.until.is_(None), or_(Event.key.in_(every), MatchFollow.keys.has_any(array(every))))).all():
        had = list(row.keys or [])
        mine = {key, *had}
        known = list(dict.fromkeys([*had, *(k for keys in boards if mine & set(keys) for k in keys)]))
        if known != had:
            row.keys = known


def settle(db: Session) -> None:
    """A follow of a game here as several events (a chess board: one per player's fixture) moves to the one a
    player the fan follows is in, once there is one (the tick, pipeline.due_work): one notification for the game,
    each moment heard once (alerts.same), one board on Today. Following the board, then tapping a player on it, is
    the natural order."""
    for follow_id, fan, event_id, keys in db.execute(select(MatchFollow.id, MatchFollow.fan_id, MatchFollow.event_id, MatchFollow.keys)
                                                     .where(MatchFollow.until.is_(None), func.jsonb_array_length(MatchFollow.keys) > 1)).all():
        theirs = select(EventPlayer.event_id).join(Follow, and_(Follow.player_id == EventPlayer.player_id, Follow.fan_id == fan))
        if db.scalar(select(EventPlayer.event_id).where(EventPlayer.event_id == event_id, EventPlayer.event_id.in_(theirs)).limit(1)) is not None:
            continue  # on their player's already
        to = db.scalar(select(Event.id).where(Event.key.in_(keys), Event.id != event_id, Event.live_binding.is_not(None), Event.id.in_(theirs)).order_by(Event.id).limit(1))
        if to is None:
            continue
        if db.scalar(select(MatchFollow.id).where(MatchFollow.fan_id == fan, MatchFollow.event_id == to)) is not None:
            db.execute(delete(MatchFollow).where(MatchFollow.id == follow_id))
        else:
            db.execute(update(MatchFollow).where(MatchFollow.id == follow_id).values(event_id=to))
        # A game the fan muted stays muted on the event it's followed on now.
        if db.scalar(select(NotificationMute.event_id).where(NotificationMute.fan_id == fan, NotificationMute.event_id == event_id)) is not None:
            db.execute(insert(NotificationMute).values(fan_id=fan, event_id=to).on_conflict_do_nothing())


def lapse(db: Session, now: datetime | None = None) -> None:
    """Follows of games that are over lapse now (the tick, pipeline.due_work): a game that ended, a game a day past
    its start that never started (armed and polled, its source still saying it's to come), one with no date a
    fortnight after it was followed."""
    now = now or datetime.now(timezone.utc)
    over = select(Event.id).where(or_(Event.status == "final", and_(Event.status.in_(NOT_STARTED), Event.start_utc < now - LAPSE_AFTER)))
    undated = select(Event.id).where(Event.start_utc.is_(None), Event.status.in_(NOT_STARTED))
    done = update(MatchFollow).where(MatchFollow.until.is_(None), or_(MatchFollow.event_id.in_(over), and_(MatchFollow.event_id.in_(undated), MatchFollow.created_at < now - UNDATED)))
    # Stamped by the database's clock as the statement runs, as a moment's time is by its poll's (its
    # transaction's start): the result found by the poll that saw the game end is always before it.
    db.execute(done.values(until=func.clock_timestamp()))


def board(event: Event, status: str, score: str | None, clock: str | None, state: dict[str, Any], source_url: str | None, as_of: datetime) -> dict[str, Any]:
    """A followed game's board, shaped like a card's live block (structured._apply_snapshot), so Today draws it the same way."""
    system = (event.key or "").partition(":")[0]
    return {"is_live": status == "live", "event_id": event.id, "match": structured.match_path(event.key), "event": event.title, "competition": event.competition, "score": score,
            "clock": clock, "state": state, "source": source_name(system), "source_url": source_url, "as_of": as_of.isoformat(), "player_stats": []}


def item(event: Event, live: dict[str, Any] | None, level: str = DEFAULT_LEVEL, keys: list[str] | None = None, paused: bool = False) -> dict[str, Any]:
    """One game a fan follows, as the app lists it: its address and every other it has (a chess board's), sport,
    when, where it's at, its board as last polled (None before its first poll), their level, and whether its
    alerts are paused for now (held)."""
    return {
        "event_id": event.id,
        "key": event.key,
        "match": structured.match_path(event.key),
        "addresses": [a for a in (structured.match_path(str(k)) for k in dict.fromkeys([event.key, *(keys or [])])) if a],
        "held": paused,
        "sport": sports.of(event.sport).key or "other",
        "title": event.title,
        "competition": event.competition,
        "detail": event.notes,
        "start_utc": event.start_utc.isoformat() if event.start_utc else None,
        "status": event.status,
        "live": live,
        "alerts": level,
    }


def mine(db: Session, fan: str) -> list[dict[str, Any]]:
    """The games this fan follows by themselves, none lapsed, soonest first, each with its board as last polled."""
    rows = db.execute(select(MatchFollow.alert_rules, MatchFollow.keys, Event).join(Event, Event.id == MatchFollow.event_id)
                      .where(MatchFollow.fan_id == fan, MatchFollow.until.is_(None)).order_by(Event.start_utc.asc().nulls_last(), Event.id)).all()
    ids = [event.id for _, _, event in rows]
    latest = {s.event_id: s for s in db.scalars(select(EventState).where(EventState.event_id.in_(ids)).ext(distinct_on(EventState.event_id)).order_by(EventState.event_id, EventState.as_of.desc()))} if ids else {}
    out = []
    for rules_, keys, event in rows:
        s = latest.get(event.id)
        live = board(event, s.status, s.score_label, s.clock_label, s.state or {}, s.source_url, s.as_of) if s is not None else None
        out.append(item(event, live, (rules_ or {}).get("level", DEFAULT_LEVEL), keys, held(db, event)))
    return out


def event_ids(db: Session, fan: str) -> list[int]:
    """The games this fan follows by themselves now, by event: the stream's channels (bus.match_channel)."""
    return list(db.scalars(select(MatchFollow.event_id).where(MatchFollow.fan_id == fan, MatchFollow.until.is_(None))))
