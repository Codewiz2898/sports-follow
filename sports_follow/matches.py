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

from sqlalchemy import Select, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.orm import Session

from . import sports, structured
from .adapters import ADAPTERS, KNOWN, source_name
from .models import Event, EventState, MatchFollow
from .moments import DEFAULT_LEVEL

if TYPE_CHECKING:
    from .match import Said

MAX = 20  # games a fan follows at once
LAPSE_AFTER = timedelta(days=1)  # a game to come that never started lapses this long past its start
UNDATED = timedelta(days=14)  # and one with no date, this long after it was followed
NO_ALERTS = "Alerts aren't available for this game: its source can't be followed live here."
OVER = "This game is over."


class Refused(Exception):
    """A follow that can't be made: its HTTP status, and what the fan is told."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def watched() -> Select[int]:
    """The events fans follow by themselves now: the poller arms and polls them as a followed player's."""
    return select(MatchFollow.event_id).where(MatchFollow.until.is_(None))


def followed(db: Session, event_id: int) -> bool:
    """Whether any fan follows this game by itself now."""
    return db.scalar(select(MatchFollow.id).where(MatchFollow.event_id == event_id, MatchFollow.until.is_(None)).limit(1)) is not None


def rules(db: Session, event_id: int) -> list[dict[str, Any]]:
    """The alert rules ({"level"}) of everyone following this game by itself now."""
    return [r or {} for r in db.scalars(select(MatchFollow.alert_rules).where(MatchFollow.event_id == event_id, MatchFollow.until.is_(None)))]


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


def offer(db: Session, fan: str | None, system: str, fixture: str, page: dict[str, Any]) -> dict[str, Any]:
    """What a match page's Follow button says (match.view's page): whether this fan follows the game, whether
    its moments can reach them at all, and if not, why."""
    ids = page.get("event_ids") or []
    following = bool(fan and ids and db.scalar(select(MatchFollow.id).where(MatchFollow.fan_id == fan, MatchFollow.event_id.in_(ids), MatchFollow.until.is_(None)).limit(1)))
    if page.get("status") == "final":
        return {"following": following, "alerts": False, "note": OVER}
    events = list(db.scalars(select(Event).where(Event.id.in_(ids)).order_by(Event.id))) if ids else []
    can = poller(system, fixture, events) is not None
    return {"following": following, "alerts": can, "note": None if can else NO_ALERTS}


def _over(known: Said) -> bool:
    """Whether the game's over: polled to its end here, or its own read found it over and settled (match.read: a
    chess mini-match can go on after a read called it over), or, with neither, the league's card says so."""
    read_over = (known.game or {}).get("status") == "final" and bool((known.read or {}).get("settled"))
    return any(e.status == "final" for e in known.events) or read_over or (not known.events and not known.game and (known.big or {}).get("status") == "final")


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
    if _over(known):
        raise Refused(409, OVER)
    binding = poller(system, fixture, known.events)
    if binding is None:
        raise Refused(409, NO_ALERTS)
    # One follow at a time per fan, so two at once can't both pass the count.
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"match-follow:{fan}", 0))))
    ids = [e.id for e in known.events]
    mine = db.scalar(select(MatchFollow).where(MatchFollow.fan_id == fan, MatchFollow.event_id.in_(ids)).limit(1)) if ids else None
    if mine is None or mine.until is not None:
        count = db.scalar(select(func.count()).select_from(MatchFollow).where(MatchFollow.fan_id == fan, MatchFollow.until.is_(None))) or 0
        if count >= MAX:
            raise Refused(429, f"You follow {MAX} games already: unfollow one to follow this.")
    if mine is not None:
        mine.until = None  # a lapsed follow of a game that's on again (postponed, then played)
        return mine.event_id
    event = next((e for e in known.events if (e.live_binding or {}).get("adapter") == binding["adapter"]), None) or _event(db, system, fixture, known, sport, binding)
    # From the database's clock, as its moments' times are: the activity list shows those since.
    db.execute(insert(MatchFollow).values(fan_id=fan, event_id=event.id, alert_rules={}).on_conflict_do_update(index_elements=["fan_id", "event_id"], set_={"until": None}))
    return event.id


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
    """Stop following the game at an address: nothing more of it is sent to this fan."""
    from . import match

    keys = [f"{system}:{f}" for f in match.find(db, system, fixture).fixtures] if match.ID.fullmatch(fixture) else []
    if keys:
        db.execute(delete(MatchFollow).where(MatchFollow.fan_id == fan, MatchFollow.event_id.in_(select(Event.id).where(Event.key.in_(keys)))))


def lapse(db: Session, now: datetime | None = None) -> None:
    """Follows of games that are over lapse now (the tick, pipeline.due_work): a game that ended, a game to come
    a day past its start that never started, one with no date a fortnight after it was followed."""
    now = now or datetime.now(timezone.utc)
    over = select(Event.id).where(or_(Event.status == "final", and_(Event.status.in_(("scheduled", "postponed")), Event.start_utc < now - LAPSE_AFTER)))
    undated = select(Event.id).where(Event.start_utc.is_(None), Event.status.in_(("scheduled", "postponed")))
    done = update(MatchFollow).where(MatchFollow.until.is_(None), or_(MatchFollow.event_id.in_(over), and_(MatchFollow.event_id.in_(undated), MatchFollow.created_at < now - UNDATED)))
    # Stamped by the database's clock as the statement runs, as a moment's time is by its poll's (its
    # transaction's start): the result found by the poll that saw the game end is always before it.
    db.execute(done.values(until=func.clock_timestamp()))


def board(event: Event, status: str, score: str | None, clock: str | None, state: dict[str, Any], source_url: str | None, as_of: datetime) -> dict[str, Any]:
    """A followed game's board, shaped like a card's live block (structured._apply_snapshot), so Today draws it the same way."""
    system = (event.key or "").partition(":")[0]
    return {"is_live": status == "live", "event_id": event.id, "match": structured.match_path(event.key), "event": event.title, "competition": event.competition, "score": score,
            "clock": clock, "state": state, "source": source_name(system), "source_url": source_url, "as_of": as_of.isoformat(), "player_stats": []}


def item(event: Event, live: dict[str, Any] | None, level: str = DEFAULT_LEVEL) -> dict[str, Any]:
    """One game a fan follows, as the app lists it: its address, sport, when, where it's at, its board as last
    polled (None before its first poll), and their level."""
    return {
        "event_id": event.id,
        "key": event.key,
        "match": structured.match_path(event.key),
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
    rows = db.execute(select(MatchFollow.alert_rules, Event).join(Event, Event.id == MatchFollow.event_id)
                      .where(MatchFollow.fan_id == fan, MatchFollow.until.is_(None)).order_by(Event.start_utc.asc().nulls_last(), Event.id)).all()
    ids = [event.id for _, event in rows]
    latest = {s.event_id: s for s in db.scalars(select(EventState).where(EventState.event_id.in_(ids)).ext(distinct_on(EventState.event_id)).order_by(EventState.event_id, EventState.as_of.desc()))} if ids else {}
    out = []
    for rules_, event in rows:
        s = latest.get(event.id)
        live = board(event, s.status, s.score_label, s.clock_label, s.state or {}, s.source_url, s.as_of) if s is not None else None
        out.append(item(event, live, (rules_ or {}).get("level", DEFAULT_LEVEL)))
    return out


def event_ids(db: Session, fan: str) -> list[int]:
    """The games this fan follows by themselves now, by event: the stream's channels (bus.match_channel)."""
    return list(db.scalars(select(MatchFollow.event_id).where(MatchFollow.fan_id == fan, MatchFollow.until.is_(None))))
