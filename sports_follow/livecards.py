"""A followed game on an Android phone (the app draws its own notifications: fcm.py): its live card while it's
played, kept up to date as it goes, and what every Android push about a game shows of it, a moment's scorecard
(notify.py) among them: its sport's colours, its sides and where it's at (Sport.glance).

A live card is one ongoing notification a game, which the app draws from data-only messages ("live") and takes
down once the game is over ("end"; the result itself arrives as a moment, as before). The poller hands each
poll here once it's committed (structured.apply_poll). A live game's card goes out when what it shows has
changed: at once when the score (its sides) has, else at most every THROTTLE seconds a game, so a clock that
ticks every poll doesn't send a card each time; a change that waited goes with a later poll, as polls go on
while the game does. A card that hasn't changed goes again every REFRESH seconds, as the phone takes down a card
nothing has come for in a while (a chess player's long think, a cricket innings break). When a game whose card went out is over (a final poll, or the schedule closing it without
one: structured.finish), its end goes, once. Redis keeps what each game's card last showed, and when.

Who gets a game's card: the fans who'd hear its key moments (a level of key or everything, not results only or
off) by a player in it they follow, by the game itself (matches.py, not lapsed) or by a team in it (teams.py),
and haven't muted it; on their Android devices only (a browser's push can't be updated in place), the newest
notify.MAX_DEVICES each, as a moment's push goes, less a phone with live cards off (Alerts' Live scores) or that
stopped this game's (Stop live card on it): the app would throw its cards away (android). Each card names the
fan's own players in the game, with their line so far. A game under several fixtures (a chess board is one of each
player's, each polled as its own event) is one card a fan: one who follows players on more than one has it from
their first player's fixture (by id) that's being polled, naming them all, and one that moves takes the old one down
(BHA-378). A game's end goes to every fan who follows it any
of those ways, whatever their level, on the same phones: it only takes a card down.

None of this is an alert: no notification rows, no sound, nothing in the push metrics. A device FCM says is gone
is removed, as after a moment's push (notify.settle), but another failure isn't counted against it: cards come
every few seconds, and a minute's trouble at FCM would otherwise remove a phone that's fine. A card isn't
collapsible (fcm.data_message), an end is. Nothing at all without FCM (fcm.enabled()).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from . import bus, fcm, logs, notify, sports, structured
from .db import session
from .models import Event, EventPlayer, EventState, Follow, MatchFollow, NotificationMute, Player, PlayerLine, PushSubscription
from .moments import DEFAULT_LEVEL
from .sports.base import Glance, Sport

if TYPE_CHECKING:
    from .adapters.base import Snapshot

log = logging.getLogger("sports_follow.livecards")

THROTTLE = 20  # seconds between a game's cards while only its clock moves
REFRESH = 600  # seconds after which an unchanged card goes again: the app takes one down after 20 minutes
LIVE_TTL = 60  # a card a minute old is stale: FCM may drop it, the next is on its way
END_TTL = 3600
KEEP = 6 * 3600  # seconds Redis keeps what a game's card last showed: one quiet this long has its card sent afresh
MAX_PLAYERS = 2  # lines on a card about the fan's own players in the game
QUIET = ("results", "off")  # the levels that don't hear a game's key moments, so get no card of it
GONE = (404, 410)  # what FCM says of a device that's gone (fcm.push): the only failure a card counts
OVER = ("final", "postponed")  # a poll's statuses that end a game's card (a game called off mid-way too)

# Games' cards to all their fans, a few games at once; one game's one at a time, the newest waiting one only (_queue):
# an end is never sent before the card it takes down, nor a score that's been passed. Each card's pushes go at once
# (_wire). The worker gives each a connection.
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="livecard")
_waiting: dict[int, tuple[str, dict[str, Any] | None, dict[str, str]]] = {}  # each game's newest card or end not yet sent, and its board
_sending: set[int] = set()  # the games a thread is sending for
_lock = threading.Lock()
# One card to one device: apart from a moment's pushes (notify._wire), so a goal never waits behind a card.
_wire = ThreadPoolExecutor(max_workers=8, thread_name_prefix="livecard-push")


def tag(event_id: int) -> str:
    """A game's live card's tag: its notification on the phone, and FCM's collapse key."""
    return f"live-e{event_id}"


# ---------------------------------------------------------------- a game as Android pushes show it


def sport_of(name: str | None, state: dict[str, Any] | None) -> Sport:
    """The sport a game is shown as: its event's ("CS2", in its own colour), else its board's."""
    return sports.get(name) or sports.of_state(state)


def keys(sport: Sport, competition: str | None = None, key: str | None = None, status: str | None = None, seen: Glance | None = None) -> dict[str, Any]:
    """What every Android push about a game says of it (fcm.py): its sport and its colours (the app's blue for
    one without), its league and match page, its sides and where it's at (Sport.glance), and whether it's under way.
    A push about no game (a player's news) has its sport's alone."""
    seen = seen or Glance()
    path = structured.match_path(key)
    return {
        "sport": sport.key,
        "colour": sport.colour.get("sport") or fcm.COLOUR,
        "ink": sport.colour.get("on") or fcm.INK,
        "league": competition or "",
        "match": f"/match/{path}" if path else "",
        "sides": [list(side) for side in seen.sides],
        "status": seen.status,
        "live": "1" if status == "live" else "0",
    }


@dataclass
class Board:
    """A game as Android pushes show it: its sport, what each says of it (keys), and the names its own moments are
    titled with ("Arsenal v Chelsea: …"), which a moment's scorecard leaves off (notify.py): its sides say it."""

    sport: Sport
    keys: dict[str, Any]
    names: tuple[str, ...] = ()


def boards(db: Session, event_ids: set[int] | list[int]) -> dict[int, Board]:
    """These games as Android pushes show them, each from its latest state (its last poll's board; without the key
    events, which aren't stored, so no marks)."""
    ids = sorted({e for e in event_ids if e})
    if not ids:
        return {}
    latest = {s.event_id: s for s in db.scalars(select(EventState).where(EventState.event_id.in_(ids)).ext(distinct_on(EventState.event_id)).order_by(EventState.event_id, EventState.as_of.desc()))}
    out = {}
    for event in db.scalars(select(Event).where(Event.id.in_(ids))):
        s = latest.get(event.id)
        state = (s.state or {}) if s is not None else {}
        status = s.status if s is not None else event.status
        sport = sport_of(event.sport, state)
        seen = sport.glance(state, s.score_label or "", s.clock_label or "", status, []) if s is not None else Glance()
        names = tuple(n for n in dict.fromkeys((sport.matchup(state), event.title)) if n)
        out[event.id] = Board(sport, keys(sport, event.competition, event.key, status, seen), names)
    return out


# ---------------------------------------------------------------- live cards


def polled(event: Event, snap: Snapshot) -> None:
    """A game's poll, committed (structured.apply_poll): its live card goes out if it's live and what the card shows
    has changed (at once for the score, else at most every THROTTLE seconds), or its end if it's over and a card
    went out. Decided here, against what Redis keeps of the game's last card; sent in the background, so the poll
    never waits on FCM. Never raises: a card is worth less than the poll."""
    if not fcm.enabled():
        return
    try:
        if snap.status == "live":
            _live(event, snap)
        elif snap.status in OVER:
            ended([event.id])
    except Exception:
        log.exception("live card for event %s failed", event.id)


def card(event: Event, snap: Snapshot) -> dict[str, Any]:
    """A live game's card as every fan's shows it (send adds each one's players): the keys every push about the game
    has, its score line (the fallback text) and where it's at, the progress bar ("67/90") and the marks along it."""
    sport = sport_of(event.sport, snap.state)
    seen = sport.glance(snap.state, snap.score_label, snap.clock_label, snap.status, snap.moments)
    return {
        **keys(sport, event.competition, event.key, snap.status, seen),
        "type": "live",
        "tag": tag(event.id),
        "event_id": event.id,
        "title": snap.score_label,
        "body": seen.status,
        "progress": f"{_number(seen.progress[0])}/{_number(seen.progress[1])}" if seen.progress else "",
        "marks": [[_number(at), side] for at, side in seen.marks],
    }


def _number(x: float) -> int | float:
    """A whole number as one (67, not 67.0), else to two places."""
    return int(x) if float(x).is_integer() else round(x, 2)


def _key(event_id: int) -> str:
    return f"livecard:{event_id}"


def _live(event: Event, snap: Snapshot) -> None:
    """Send a live game's card if what it shows has changed since the last one: at once if its sides (the score)
    have, else once THROTTLE seconds have passed since the last."""
    made = card(event, snap)
    shows = json.dumps([made[k] for k in ("sides", "status", "title", "progress", "marks")], ensure_ascii=False)
    redis = bus.sync_redis()
    last = json.loads(cast(str, redis.get(_key(event.id))) or "null") or {}
    now = time.time()
    if last.get("shows") == shows and now - float(last.get("at") or 0) < REFRESH:
        return  # nothing new to show, and the phone still has it
    if last.get("sides") == made["sides"] and now - float(last.get("at") or 0) < THROTTLE:
        return  # only the clock moved, and a card went out a moment ago: a later poll sends it
    redis.set(_key(event.id), json.dumps({"at": now, "sides": made["sides"], "shows": shows}), ex=KEEP)
    _queue(event.id, "live", made, on_board(event, snap))


def on_board(event: Event, snap: Snapshot) -> dict[str, str]:
    """Who's on a game's board, by their ids in its source, each with their line's headline, where the game is under
    several fixtures (a chess board is one of each player's): a fan who follows players on more than one has its card
    from one of them (audience). {} where its fixture is the game."""
    if not structured.game_key(event.key, snap.state):
        return {}
    return {athlete: line.headline for athlete, line in snap.lines.items()}


def ended(event_ids: list[int]) -> None:
    """Games that are over (a final poll, or the schedule closing them without one: structured.finish): each one
    whose live card went out has its end sent, once (Redis forgets the card as it's taken down)."""
    if not fcm.enabled() or not event_ids:
        return
    try:
        redis = bus.sync_redis()
        for event_id in event_ids:
            if redis.delete(_key(event_id)):
                _queue(event_id, "end", None)
    except Exception:
        log.exception("ending live cards for events %s failed", event_ids)


def _queue(event_id: int, kind: str, made: dict[str, Any] | None, board: dict[str, str] | None = None) -> None:
    """Send a game's card or end once its last one has gone, in place of any of its own still waiting."""
    with _lock:
        _waiting[event_id] = (kind, made, board or {})
        if event_id in _sending:
            return  # the thread sending this game's last one sends this one next
        _sending.add(event_id)
    _pool.submit(logs.carried(_drain), event_id)


def _drain(event_id: int) -> None:
    """A game's waiting card or end, and whatever replaced it meanwhile, until none waits."""
    while True:
        with _lock:
            job = _waiting.pop(event_id, None)
            if job is None:
                _sending.discard(event_id)
                return
        _send_safely(event_id, *job)


def _send_safely(event_id: int, kind: str, made: dict[str, Any] | None, board: dict[str, str] | None = None) -> None:
    try:
        send(event_id, kind, made, board)
    except Exception:
        log.exception("sending event %s's %s card failed", event_id, kind)


def send(event_id: int, kind: str, made: dict[str, Any] | None = None, board: dict[str, str] | None = None) -> int:
    """A game's live card ("live", as polled: card()) or its end ("end", its board read back) to the Android devices
    of every fan who should have it (the module docstring); each live card with the fan's own players' lines, the
    board's players (on_board) where it's under several fixtures. Each device's health is settled as after a moment's
    push. Returns how many devices took it."""
    live = kind == "live"
    with session() as db:
        if made is None:
            shown = boards(db, [event_id]).get(event_id)
            if shown is None:
                return 0
            made = {**shown.keys, "type": kind, "tag": tag(event_id), "event_id": event_id}
        if not live:
            made = {**made, "live": "0"}  # it's over, or no longer polled live, whatever its last poll said
        found = structured.boards(db, [event_id]).get(event_id) if live and board else None
        on = _players_on(event_id, board or {}, found) if found else {}
        moved: set[str] = set()
        fans = audience(db, event_id, live, on, set(found.events.values()) if found else set(), moved)
        # A fan of a game under several fixtures whose card this one sent before, and now another does: this one's
        # is taken down, so their phone shows one.
        gone = _had(event_id, set(fans)) & moved if found else set()
        theirs = lines(db, event_id, fans, on) if live else {}
        devices = android(db, [*fans, *gone], event_id)
    futures = []
    for fan, mine in devices.items():
        over = not live or fan in gone
        data = {**made, "type": "end", "live": "0"} if fan in gone else {**made, "players": theirs.get(fan, [])} if live else made
        for device_id, token in mine:
            futures.append((device_id, _wire.submit(logs.carried(fcm.send), fcm.data_message(token, data, END_TTL if over else LIVE_TTL, collapse=over))))
    results = [(device_id, future.result()) for device_id, future in futures]
    if not results:
        return 0
    notify.settle([(device_id, error) for device_id, error in results if error is None or error in GONE])
    sent = sum(1 for _, error in results if error is None)
    log.info("event %s: %s card to %d of %d device(s)", event_id, kind, sent, len(results), extra={"event_id": event_id, "devices": len(results), "delivered": sent})
    return sent


def _had(event_id: int, now: set[str]) -> set[str]:
    """The fans a game's live card went to last time (Redis keeps them as long as its card), noting these as this
    time's; none where Redis can't say."""
    key = f"livecard-fans:{event_id}"
    try:
        redis = bus.sync_redis()
        before = set(json.loads(cast(str, redis.get(key)) or "[]"))
        redis.set(key, json.dumps(sorted(now)), ex=KEEP)
    except Exception:
        log.warning("event %s: who had its live card: Redis didn't say", event_id, exc_info=True)
        return set()
    return before


def _players_on(event_id: int, board: dict[str, str], found: structured.Fixtures) -> dict[int, str]:
    """The players here on a game's board (on_board), by their ids here, each with their headline: those whose own
    fixture of it is this one, or another that's being polled (structured.told_lately), so its card goes from there."""
    lately = structured.told_lately(e for e in found.events.values() if e != event_id)
    return {player_id: board.get(external, "") for external, player_id in found.players.items() if found.events.get(player_id) == event_id or found.events.get(player_id) in lately}


def audience(db: Session, event_id: int, live: bool = True, on: dict[int, str] | None = None, fixtures: set[int] | None = None,
             moved: set[str] | None = None) -> dict[str, list[int]]:
    """The fans who should have a game's live card (live), or its end (the module docstring), each with the players
    they follow in it, those set to off left out: whom their card names. Where the game is under several fixtures,
    `on` is the players on its board (_players_on), some of them under its other `fixtures`, and muting any of them
    mutes its card; `moved` gathers the fans who have it from another of them."""
    from . import teams  # a team's games are followed games (teams reads the poller that calls here)

    levels: dict[str, set[str]] = {}
    players: dict[str, list[int]] = {}
    hearing: dict[str, list[int]] = {}  # theirs here at a level that has the card
    for fan, player_id, rules in db.execute(select(Follow.fan_id, Follow.player_id, Follow.alert_rules).join(EventPlayer, EventPlayer.player_id == Follow.player_id)
                                            .where(EventPlayer.event_id == event_id).order_by(Follow.id)):
        level = (rules or {}).get("level", DEFAULT_LEVEL)
        levels.setdefault(fan, set()).add(level)
        if level != "off":
            players.setdefault(fan, []).append(player_id)
        if level not in QUIET:
            hearing.setdefault(fan, []).append(player_id)
    games = select(MatchFollow.fan_id, MatchFollow.alert_rules).where(MatchFollow.event_id == event_id)
    for fan, rules in db.execute(games.where(MatchFollow.until.is_(None)) if live else games):
        levels.setdefault(fan, set()).add((rules or {}).get("level", DEFAULT_LEVEL))
    for fan, by_game in teams.watches(db, {event_id}).items():
        levels.setdefault(fan, set()).add(by_game[event_id][0])
    if live and levels:
        muted = set(db.scalars(select(NotificationMute.fan_id).where(NotificationMute.event_id.in_({event_id, *(fixtures or ())}), NotificationMute.fan_id.in_(list(levels)))))
        levels = {fan: theirs for fan, theirs in levels.items() if fan not in muted and theirs - set(QUIET)}
    if live and on and levels:
        # A game under several fixtures (a chess board is one of each player's, both polled): a fan who follows a
        # player on it under another has its card from their first player's (by id), whichever polls first, so their
        # phone shows one card; that card names all of theirs.
        here = set(db.scalars(select(EventPlayer.player_id).where(EventPlayer.event_id == event_id)))
        rows = select(Follow.fan_id, Follow.player_id, Follow.alert_rules).where(Follow.player_id.in_(set(on) - here), Follow.fan_id.in_(list(levels))).order_by(Follow.id)
        for fan, player_id, rules in db.execute(rows):
            level = (rules or {}).get("level", DEFAULT_LEVEL)
            first = min(hearing.get(fan, []), default=None)
            if level not in QUIET and (first is None or player_id < first):
                levels.pop(fan, None)  # their card comes from that player's fixture
                if moved is not None:
                    moved.add(fan)
            elif level != "off" and fan in levels:
                players.setdefault(fan, []).append(player_id)
    return {fan: players.get(fan, []) for fan in levels}


def lines(db: Session, event_id: int, fans: dict[str, list[int]], on: dict[int, str] | None = None) -> dict[str, list[str]]:
    """Each fan's own players in a game as their card lists them, at most MAX_PLAYERS, those with a line first: their
    name and their line's headline so far ("Bukayo Saka · 1 goal, 1 assist"), or their name alone before they have one.
    A player on its board under another of its fixtures (`on`) has their line from this poll."""
    ids = sorted({p for mine in fans.values() for p in mine})
    if not ids:
        return {}
    names = {player_id: name for player_id, name in db.execute(select(Player.id, Player.name).where(Player.id.in_(ids)))}
    latest = select(PlayerLine.player_id, PlayerLine.stats).where(PlayerLine.event_id == event_id, PlayerLine.player_id.in_(ids))
    heads = {player_id: str((stats or {}).get("headline") or "") for player_id, stats in db.execute(latest.ext(distinct_on(PlayerLine.player_id)).order_by(PlayerLine.player_id, PlayerLine.as_of.desc()))}
    heads = {**{p: head for p, head in (on or {}).items() if p in names}, **heads}
    out = {}
    for fan, mine in fans.items():
        ranked = sorted((p for p in dict.fromkeys(mine) if names.get(p)), key=lambda p: (not heads.get(p), names[p]))
        out[fan] = [" · ".join(x for x in (names[p], heads.get(p)) if x) for p in ranked[:MAX_PLAYERS]]
    return out


def android(db: Session, fans: list[str], event_id: int) -> dict[str, list[tuple[int, str]]]:
    """These fans' Android devices (id, FCM token) that show a game's live card: of the newest notify.MAX_DEVICES
    each, as a moment's push goes, those with live cards on that haven't stopped this game's (the app's Live scores
    and Stop live card: server.register_fcm, stop_live_card). A card sent to one of the others would wake it to
    be thrown away."""
    out: dict[str, list[tuple[int, str]]] = {}
    if not fans:
        return out
    newest: dict[str, int] = {}
    for device_id, fan, token, on, stopped in db.execute(select(PushSubscription.id, PushSubscription.fan_id, PushSubscription.endpoint, PushSubscription.live_cards, PushSubscription.live_stopped)
                                                         .where(PushSubscription.fan_id.in_(fans), PushSubscription.transport == "fcm").order_by(PushSubscription.id.desc())):
        newest[fan] = newest.get(fan, 0) + 1
        if newest[fan] <= notify.MAX_DEVICES and on and event_id not in (stopped or []):
            out.setdefault(fan, []).append((device_id, token))
    return out
