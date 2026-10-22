"""Telling fans about moments (moments.py): store each once, then push them to every follower who
wants them, on every device they turned notifications on for, by the noise rules in alerts.py. A game's
own moments (Sport.game_moments) go the same way to the fans following the game itself (matches.py), or a
team in it (teams.py).

A poll's (or a rebuild's) new moments go out together as one batch: claimed in the database so they
are sent once, planned per fan (one push per game, sound rationed), logged as notification rows, then
delivered to each device at once. A batch no sender claimed (the worker restarted between finding a
moment and sending it) is swept up by the tick; a live moment that has gone stale by then is dropped.

Each device gets its push by its own transport (deliver()): Web Push for browsers, the installed web app
and iPhone home-screen apps (push(), below), and FCM for the Android app (fcm.py). A fan's quiet hours
don't drop a moment: it arrives silently. Devices the push service says are gone are removed.

The Android app draws a push itself, as a scorecard (scorecard()): its game as every push about it shows it
(livecards.py: the sport's colours, the sides and where it's at), a big word over the moment's line, the story
under it and the player's photo. Those are looked up once a batch, and only Android devices are sent them: a
browser's push stays as it was.

    .venv/bin/python -m sports_follow.notify keys        # make this server's VAPID keys, once
    .venv/bin/python -m sports_follow.notify stats [7]   # what was sent in the last days, and opened
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import datetime, time as clock, timedelta, timezone
from functools import lru_cache, partial
from itertools import zip_longest
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import alerts, bus, fcm, logs, metrics, sports
from .config import SITE_URL
from .db import session
from .moments import DEFAULT_LEVEL, Found
from .models import Event, EventState, Follow, MatchFollow, Moment, Notification, NotificationMute, Player, PlayerCard, PlayerIdentity, PushSubscription

if TYPE_CHECKING:
    from . import livecards

log = logging.getLogger("sports_follow.notify")

VAPID_KEY = Path(os.environ.get("SPORTS_FOLLOW_VAPID_KEY", Path.home() / ".config" / "sports-follow" / "vapid-private.pem"))


def vapid_subject(site: str) -> str:
    """Who is sending, for push services (RFC 8292): a URL for this project, never a person's address.
    The site once it's hosted; in development, where no push service checks it, localhost."""
    return site if site.startswith("https://") else "https://localhost"


VAPID_SUBJECT = os.environ.get("SPORTS_FOLLOW_VAPID_SUBJECT") or vapid_subject(SITE_URL)
MAX_FAILURES = 5
# The kinds of a fan's notification rows that aren't moments' (predictions_push.py: "Make your pick", how a pick went):
# the noise rules for moments (alerts.plan) don't count them as the game's pushes, nor their lines as its story.
CALLS = ("pick", "called")
MAX_DEVICES = 10  # per fan: subscribing another drops their oldest, and a push goes to the newest this many
SWEEP_AFTER = 60  # seconds a batch has to be claimed by the sender it was handed to before the tick takes it
# Redis: each moment left to its board's other side to tell (alerts.voiced), by id: the fans it was left for, when it
# was found and its kind, till that side says it or it's sent them late (send_held).
HELD = "held-moments"
# A push's JSON before encryption, in bytes. Push services promise only 4,096 bytes encrypted (RFC 8030)
# and refuse more with a 413; encryption adds about 100, so this keeps a push under 3.5 KB. A longer
# body (then title) is cut short.
PAYLOAD_BYTES = 3400
# An Android scorecard's big word by its moment's kind, for the kinds every sport has, where neither its sport
# (Sport.high: "Hundred"; Sport.kind_words for its own kinds: "Goal") nor its title ("Red card: Saka") says one.
# A title's own word is at most WORD_CHARS long: past that it's a sentence.
WORDS = {"final": "Result", "start": "Live", "lineup": "Line-up", "news": "News"}
WORD_CHARS = 20
STORY_LINES, STORY_CHARS = 3, 80  # the earlier lines under a scorecard's, each cut to this

# Seconds a push service has to take a Web Push's connection (on each of its host's addresses), then to answer:
# a device that never answers costs its push thread this long, not minutes (BUG-181). FCM's are fcm.TIMEOUT.
CONNECT_TIMEOUT, READ_TIMEOUT = 2, 5
DELIVER_WAIT = 15  # seconds a batch waits for its pushes: one still going then is written back when it ends
TEST_WAIT = 10  # seconds a test push is waited for (send_test)

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="notify")  # batches
# One push to one device each. A device that took its last push goes by _wire; a new one, or one failing lately, by
# _doubt. So devices that never answer (a push service that's down, or subscriptions made to hang) hold up only
# others like them, never the pushes to devices that work, in their batch or any other (BUG-181).
_wire = ThreadPoolExecutor(max_workers=16, thread_name_prefix="push")
_doubt = ThreadPoolExecutor(max_workers=8, thread_name_prefix="push-doubt")
_tests = ThreadPoolExecutor(max_workers=4, thread_name_prefix="push-test")  # test pushes (send_test), apart from the API's threads


# ---------------------------------------------------------------- keys


@lru_cache(maxsize=1)
def public_key() -> str | None:
    """The VAPID public key browsers subscribe with, or None if this server has no keys yet."""
    if not VAPID_KEY.exists():
        return None
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(VAPID_KEY.read_bytes(), password=None)
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@lru_cache(maxsize=1)
def signer():
    """The VAPID signer. py_vapid only takes a mailto: or a bare https:// host as the sender; RFC 8292
    allows any https URL, and a project URL is the right contact, so its strict check is off."""
    from py_vapid import Vapid

    vapid = Vapid.from_file(private_key_file=str(VAPID_KEY))
    vapid.conf = {**(getattr(vapid, "conf", None) or {}), "no-strict": True}
    return vapid


def make_keys() -> str:
    from py_vapid import Vapid

    if VAPID_KEY.exists():
        raise SystemExit(f"{VAPID_KEY} exists; delete it first (every device would have to subscribe again)")
    VAPID_KEY.parent.mkdir(parents=True, exist_ok=True)
    vapid = Vapid()
    vapid.generate_keys()
    vapid.save_key(str(VAPID_KEY))
    VAPID_KEY.chmod(0o600)
    public_key.cache_clear()
    signer.cache_clear()
    return public_key() or ""


# ---------------------------------------------------------------- moments


def record(db: Session, player_id: int, event_id: int | None, found: list[Found], url: str | None = None) -> list[int]:
    """Store new moments; ones already stored (the same key) are skipped. A correction is stored with the
    moment it takes back (its detail is that moment's kind and detail), and not at all if that one never
    was. A game played again after it was called off has moments of its own (edition_of). Returns the new ids."""
    ids = []
    edition = edition_of(db, event_id) if event_id and found else ""
    for f in found:
        corrects = None
        if f.kind == "correction":
            corrects = db.scalar(select(Moment.id).where(Moment.key == _key(event_id, player_id, f.detail, edition)))
            if corrects is None:
                continue  # what it takes back was never found, so nobody was told it
        key = _key(event_id, player_id, f"{f.kind}:{f.detail}", edition)
        new_id = db.scalar(
            insert(Moment)
            .values(key=key, player_id=player_id, event_id=event_id, kind=f.kind, level=f.level, priority=f.priority or 50, title=f.title[:200], body=f.body, url=url or f"/player/{player_id}", corrects=corrects,
                    about=[f.voice] if f.voice else None)
            .on_conflict_do_nothing()
            .returning(Moment.id)
        )
        if new_id:
            ids.append(new_id)
            metrics.moment(f.kind)
    return ids


def record_game(db: Session, event_id: int, found: list[Found], url: str) -> list[int]:
    """Store a game's own moments (Sport.game_moments), for the fans following the game itself; ones already
    stored are skipped, and a correction is stored with the moment it takes back, or not at all. Of its edition
    (edition_of). Returns the new ids."""
    ids = []
    edition = edition_of(db, event_id) if found else ""
    for f in found:
        corrects = None
        if f.kind == "correction":
            corrects = db.scalar(select(Moment.id).where(Moment.key == _game_key(event_id, f.detail, edition)))
            if corrects is None:
                continue
        new_id = db.scalar(
            insert(Moment)
            .values(key=_game_key(event_id, f"{f.kind}:{f.detail}", edition), player_id=None, event_id=event_id, kind=f.kind, level=f.level, priority=f.priority or 50,
                    title=f.title[:200], body=f.body, url=url, corrects=corrects, about=list(f.about or ("*",)))
            .on_conflict_do_nothing()
            .returning(Moment.id)
        )
        if new_id:
            ids.append(new_id)
            metrics.moment(f.kind)
    return ids


def told_game(db: Session, event_id: int) -> list[tuple[str, str]]:
    """A game's own moments so far, of its edition (edition_of), oldest first, as each one's kind and detail: what it
    may take back."""
    prefix = _game_key(event_id, "", edition_of(db, event_id))
    keys = db.scalars(select(Moment.key).where(Moment.event_id == event_id, Moment.player_id.is_(None), Moment.key.startswith(prefix, autoescape=True)).order_by(Moment.id))
    return [(kind, detail) for kind, _, detail in (key.removeprefix(prefix).partition(":") for key in keys)]


def _game_key(event_id: int, kind_detail: str, edition: str = "") -> str:
    """A game's own moment's key: its game and edition, "game" where a player's has their id, its kind and detail."""
    return f"{event_id}{edition}:game:{kind_detail}"[:200]


def told(db: Session, player_id: int, event_id: int) -> list[tuple[str, str]]:
    """The moments stored for a player in a game, of its edition (edition_of), oldest first, as each one's kind and
    detail (read back from its key): what the sport may take back (moments.detect)."""
    prefix = _key(event_id, player_id, "", edition_of(db, event_id))
    keys = db.scalars(select(Moment.key).where(Moment.player_id == player_id, Moment.event_id == event_id, Moment.key.startswith(prefix, autoescape=True)).order_by(Moment.id))
    return [kind_detail(key, event_id, player_id) for key in keys]


def kind_detail(key: str, event_id: int | None, player_id: int) -> tuple[str, str]:
    """A player's moment's kind and detail, read back from its key, whichever edition of its game it's of."""
    _, _, kind, detail = (key.split(":", 3) + ["", ""])[:4]
    return kind, detail


def _key(event_id: int | None, player_id: int, kind_detail: str, edition: str = "") -> str:
    """A moment's key: its game and edition (edition_of), its player, its kind and detail. The same moment found again
    has the same key."""
    return f"{event_id or '-'}{edition}:{player_id}:{kind_detail}"[:200]


def editions(db: Session, event_ids: Iterable[int | None]) -> dict[int, datetime]:
    """When each of these games began as the game it is now, where that isn't its first time: one called off
    (postponed) and set again for later under the same fixture (a match abandoned and replayed), from the last time
    it was called off before its start now. Its moments and pushes from before are the game called off's: the replay's
    line-up, start and cards are told afresh (each moment's key has its edition: edition_of), its notification's story
    is its own, and nothing of the game called off is taken back in it (BUG-185). A game stopped and on again under the
    start it had (suspended a while) is the same game."""
    ids = sorted({e for e in event_ids if e})
    if not ids:
        return {}
    rows = db.execute(select(EventState.event_id, func.max(EventState.as_of)).join(Event, Event.id == EventState.event_id)
                      .where(EventState.event_id.in_(ids), EventState.status == "postponed", EventState.as_of < Event.start_utc)
                      .group_by(EventState.event_id))
    return {event_id: at for event_id, at in rows if at is not None}


def edition_of(db: Session, event_id: int) -> str:
    """The mark of a game's edition (editions) its moments' keys carry: none for its first, else when it began."""
    since = editions(db, [event_id]).get(event_id)
    return f"~{int(since.timestamp())}" if since is not None else ""


def dispatch(moment_ids: list[int]) -> None:
    """Send a batch in the background: a poll mustn't wait on push services."""
    if moment_ids:
        _pool.submit(logs.carried(_send_safely), moment_ids)  # logged under the poll's or build's id


def _send_safely(moment_ids: list[int]) -> None:
    try:
        send(moment_ids)
    except Exception:
        log.exception("sending moments %s failed", moment_ids)


def pending(now: datetime | None = None) -> list[int]:
    """Moments no sender claimed, for the tick to send: older than SWEEP_AFTER, young enough to matter."""
    now = now or datetime.now(timezone.utc)
    with session() as db:
        return list(db.scalars(
            select(Moment.id)
            .where(Moment.sent_at.is_(None), Moment.created_at <= now - timedelta(seconds=SWEEP_AFTER), Moment.created_at >= now - timedelta(seconds=alerts.LATE_TTL))
            .order_by(Moment.id)
            .limit(500)
        ))


def send(moment_ids: list[int], now: datetime | None = None) -> int:
    """Push a batch of moments to everyone who should hear about them. Returns how many devices took a push."""
    from . import teams  # whose teams are in a game (teams reads the pipeline that sends through here)

    now = now or datetime.now(timezone.utc)
    with session() as db:
        # Claim first: whichever sender gets here first sends; the other finds nothing to do.
        claimed = db.scalars(update(Moment).where(Moment.id.in_(moment_ids), Moment.sent_at.is_(None)).values(sent_at=now).returning(Moment.id)).all()
        if not claimed:
            return 0
        rows = db.scalars(select(Moment).where(Moment.id.in_(claimed)).order_by(Moment.id)).all()
        items = on_boards(db, rows, polled=True)
        taken_back = _taken_back(db, items)
        jobs = _plan(db, items, now, taken_back)
        # The followed teams in each of the batch's games, for the app's toasts (below).
        followed = teams.followed_in(db, {i.event_id for i in items if i.player_id is None and i.event_id is not None})
        # A correction's toast follows its moment's: one sent in an earlier batch (not taken back before it went).
        corrects = {i.corrects for i in items if i.corrects is not None}
        toasted = set(db.scalars(select(Moment.id).where(Moment.id.in_(corrects), Moment.id.not_in(claimed), Moment.sent_at.is_not(None))).all()) if corrects else set()
    # The app shows them too, as toasts, to anyone with it open; it filters by the fan's level itself. A game's
    # own go to the fans following the game, or a team in it: each says which of its teams fans follow (teams), so
    # the app finds the fan's level by their team, as a push does; one from a team's side says whose (sides), and
    # the game's own result whose sides tell it too (told_by), so the app shows each fan their team's.
    shown = [i for i in items if i.id not in taken_back and (i.corrects is None or i.corrects in toasted) and (now - i.created_at).total_seconds() <= alerts.ttl(i.kind)]
    told_by: dict[int | None, set[str]] = {}
    for item in shown:
        if item.player_id is None and item.kind == "final":
            told_by.setdefault(item.event_id, set()).update(alerts.sided(item))
    for item in shown:
        moment: dict[str, Any] = {"id": item.id, "title": item.title, "body": item.body, "url": item.url, "player_id": item.player_id, "event_id": item.event_id, "kind": item.kind, "level": item.level}
        if item.voice is not None and item.voice != item.player_id:
            moment["voice"] = item.voice  # a board's other side tells it: a fan who follows them too sees theirs (alerts.voiced)
        if item.player_id is None and item.event_id is not None:
            sides = alerts.sided(item)
            moment["teams"] = followed.get(item.event_id, [])
            if sides:
                moment["sides"] = sorted(sides)
            elif item.kind == "final" and told_by.get(item.event_id):
                moment["told_by"] = sorted(told_by[item.event_id])
        told_ = {"type": "moment", "player_id": item.player_id, "moment": moment}
        if item.player_id is not None:
            bus.publish(item.player_id, told_)
        elif item.event_id is not None:
            bus.publish_match(item.event_id, told_)
    return _deliver(jobs, items, now)


def _deliver(jobs: list[tuple[int, tuple, dict[str, Any], int, str]], items: list[alerts.Item], now: datetime) -> int:
    """Push planned notifications to each of their devices at once, and write back what the push services said.
    Devices that took their last push go by _wire, the rest by _doubt, each in turns (_fair). The batch waits at
    most DELIVER_WAIT: a push still going then is written back when it ends (_settle_late), so a device that never
    answers keeps neither the batch nor its sender waiting. Returns how many devices took one in that time."""
    if not jobs:
        return 0
    try:
        proven = _proven({device[0] for _, device, *_ in jobs})
    except Exception:
        log.exception("moments %s: devices' health unread, all sent as ones that work", [i.id for i in items])
        proven = {device[0] for _, device, *_ in jobs}
    futures = [(notification_id, device, (_wire if device[0] in proven else _doubt).submit(logs.carried(deliver), device, payload, ttl, topic))
               for notification_id, device, payload, ttl, topic in _fair(jobs)]
    done, going = wait([future for *_, future in futures], timeout=DELIVER_WAIT)
    results = [(notification_id, device, future.result()) for notification_id, device, future in futures if future in done]
    for notification_id, device, future in futures:
        if future in going:
            future.add_done_callback(logs.carried(partial(_settle_late, notification_id, device)))
    _settle([(notification_id, device[0], error) for notification_id, device, error in results])
    sent = sum(1 for _, _, error in results if error is None)
    pushes = len({n for n, *_ in jobs})
    # The numbers as fields too, for the Notifications dashboard (deploy/grafana/): among them how long
    # the oldest moment waited, from its poll or build finding it to its push.
    waited = max((now - item.created_at).total_seconds() for item in items)
    metrics.pushes([(_service(device), error) for _, device, error in results], waited)
    log.info("moments %s: %d push(es) to %d of %d device(s)%s", [i.id for i in items], pushes, sent, len(jobs), f", {len(going)} still going" if going else "",
             extra={"moments": len(items), "pushes": pushes, "devices": len(jobs), "delivered": sent, "going": len(going), "waited_ms": round(waited * 1000)})
    return sent


def _proven(device_ids: set[int]) -> set[int]:
    """Those of these devices whose last push went through (and so go by _wire)."""
    with session() as db:
        return set(db.scalars(select(PushSubscription.id).where(PushSubscription.id.in_(device_ids), PushSubscription.failures == 0, PushSubscription.last_ok_at.is_not(None))))


def _fair(jobs: list[tuple[int, tuple, dict[str, Any], int, str]]) -> list[tuple[int, tuple, dict[str, Any], int, str]]:
    """A batch's pushes in turns: each notification's first device, then each one's second, and so on, so one fan's
    many devices never go ahead of another fan's first."""
    turns: dict[int, list[tuple[int, tuple, dict[str, Any], int, str]]] = {}
    for job in jobs:
        turns.setdefault(job[0], []).append(job)
    return [job for turn in zip_longest(*turns.values()) for job in turn if job is not None]


def _settle_late(notification_id: int, device: tuple, future: Future) -> None:
    """Write back a push its batch stopped waiting for (_deliver), once it has ended."""
    try:
        error = future.result()
    except Exception as exc:
        error = str(exc)[:100]
    try:
        _settle([(notification_id, device[0], error)])
        metrics.pushes([(_service(device), error)])
    except Exception:
        log.exception("a late push to device %s: what its push service said wasn't written back", device[0])


def held_due(now: datetime | None = None) -> list[int]:
    """The moments left to their board's other side to tell (alerts.voiced) for longer than structured.TELLS_WITHIN,
    for the tick to send late where that side never said them (send_held); those past their TTL are forgotten.
    Nothing where Redis can't say."""
    from . import structured  # how long a side has to tell its moments (structured sends through here)

    at = (now or datetime.now(timezone.utc)).timestamp()
    try:
        redis = bus.sync_redis()
        held = cast(dict, redis.hgetall(HELD))
        due, stale = [], []
        for key, raw in held.items():
            entry = json.loads(raw)
            if at - entry["at"] > alerts.ttl(entry["kind"]):
                stale.append(key)
            elif at - entry["at"] >= structured.TELLS_WITHIN:
                due.append(int(key))
        if stale:
            redis.hdel(HELD, *stale)
    except Exception:
        log.warning("moments held for a board's other side: Redis didn't say", exc_info=True)
        return []
    return sorted(due)


def send_held(moment_ids: list[int], now: datetime | None = None) -> int:
    """Send late, to the fans it was left for alone, a moment its board's other side was to tell them (held_due) where
    that side never said it: no moment of that player's of the same play is stored on the board's fixtures (the side
    crashed, stalled past a poll gap, or settled the game another way). In its own side's words, on the game's
    notification, as a push of it is. Each is forgotten once sent, or once its other side's is found; a fan it reached
    already isn't sent it again (this ran before, and went down before forgetting it). Returns how many devices took
    one."""
    now = now or datetime.now(timezone.utc)
    try:
        redis = bus.sync_redis()
        entries = {m: json.loads(raw) for m, raw in zip(moment_ids, cast(list, redis.hmget(HELD, [str(m) for m in moment_ids]))) if raw}
    except Exception:
        log.warning("moments %s held for a board's other side: Redis didn't say", moment_ids, exc_info=True)
        return 0
    jobs, late = [], []
    with session() as db:
        for item in on_boards(db, db.scalars(select(Moment).where(Moment.id.in_(list(entries))).order_by(Moment.id)).all()):
            twins = [_key(e, item.voice, f"{item.kind}:{item.detail}", edition_of(db, e)) for e in item.fixtures] if item.voice is not None else []
            if twins and db.scalar(select(Moment.id).where(Moment.key.in_(twins)).limit(1)) is not None:
                continue  # its other side said it
            told = replace(item, voice=None)  # told by its own side now
            fans = set(entries[item.id]["fans"])
            fans -= set(db.scalars(select(Notification.fan_id).where(Notification.fan_id.in_(fans), Notification.moment_ids.contains([item.id]), Notification.delivered > 0)))
            jobs += _plan(db, [told], now, _taken_back(db, [told]), fans=fans) if fans else []
            late.append(told)
    sent = _deliver(jobs, late, now)
    if entries:  # only now: one that went down before this is sent again, never not at all
        try:
            redis.hdel(HELD, *[str(m) for m in entries])
        except Exception:
            log.warning("moments %s held for a board's other side: couldn't forget them", list(entries), exc_info=True)
    return sent


def _hold(held: dict[int, tuple[alerts.Item, set[str]]]) -> bool:
    """Keep the moments a batch left to their board's other side to tell (alerts.plan's held), with the fans each was
    left for, so they're sent late should that side never say them (held_due). Whether they were kept."""
    if not held:
        return True
    try:
        redis = bus.sync_redis()
        for item, fans in held.values():
            had = json.loads(cast(str, redis.hget(HELD, str(item.id))) or "{}")
            redis.hset(HELD, str(item.id), json.dumps({"fans": sorted({*had.get("fans", []), *fans}), "at": item.created_at.timestamp(), "kind": item.kind}))
        redis.expire(HELD, alerts.LATE_TTL)
    except Exception:
        log.warning("moments %s left to their board's other side: couldn't keep them, so they're sent now", list(held), exc_info=True)
        return False
    return True


def as_items(rows: Sequence[Moment]) -> list[alerts.Item]:
    """Moments as the planner sees them: a game's own with whom it's about, each with its detail, read back from
    its key. A player's says whom it's about, and each its place among its kind, once for_games() has looked them up."""
    return [alerts.Item(m.id, m.player_id, m.event_id, m.kind, m.level, m.priority, m.title, m.body, m.url, m.created_at, m.corrects,
                        frozenset(m.about or ()) if m.player_id is None else frozenset(), (m.key.split(":", 3)[3:] or [""])[0]) for m in rows]


def on_boards(db: Session, rows: Sequence[Moment], polled: bool = False) -> list[alerts.Item]:
    """Moments as the planner sees them (as_items), each with its game's fixtures where a game is under several
    (structured.boards: a chess board is one of each player's), and a player's there with the player here whose side
    tells it (Item.voice), from its stored id in the source. With `polled`, a voice is kept only where that player's
    own fixture of the game is being polled (structured.told_lately), so their side will say it: else both sides
    do, twice beating never. Looked up only for such games."""
    from . import structured  # an event's board (structured sends through here)

    items = as_items(rows)
    found = structured.boards(db, {i.event_id for i in items})
    if not found:
        return items
    lately = structured.told_lately(e for f in found.values() for e in f.events.values()) if polled else set()
    out = []
    for m, i in zip(rows, items):
        board = found.get(i.event_id or 0)
        if board is None:
            out.append(i)
            continue
        voice = board.players.get(str(m.about[0])) if m.player_id is not None and m.about else None
        if polled and voice is not None and voice != i.player_id and board.events.get(voice) not in lately:
            voice = None  # their fixture isn't here, or isn't being polled: this side says it too
        out.append(replace(i, fixtures=frozenset({i.event_id or 0, *board.events.values()}), voice=voice))
    return out


def for_games(db: Session, items: list[alerts.Item]) -> list[alerts.Item]:
    """What tells a game's own moment to be one of a player's (alerts.same), looked up only where it's needed (a
    game someone follows by itself): each player moment's player by their id in its game's source, and each
    moment's place among its game's of its kind about the same player (Item.nth: their first goal, the game's
    first goal by them), for a play one side names by the source's id and the other only by its count."""
    pairs = {(i.player_id, i.event_id) for i in items if i.player_id is not None and i.event_id is not None}
    events = {i.event_id for i in items if i.event_id is not None}
    if not events:
        return items
    systems = {e.id: (e.key or "").partition(":")[0] for e in db.scalars(select(Event).where(Event.id.in_(events)))}
    ids: dict[tuple[int, str], set[str]] = {}
    for player_id, system, external in db.execute(select(PlayerIdentity.player_id, PlayerIdentity.system, PlayerIdentity.external_id).where(PlayerIdentity.player_id.in_({p for p, _ in pairs}))) if pairs else []:
        ids.setdefault((player_id, system), set()).add(external)
    counted: Counter[tuple[int | None, str, Any]] = Counter()
    nth: dict[int, int] = {}
    began = editions(db, events)  # a game played again after it was called off counts its own
    for moment_id, event_id, kind, player_id, about, at in db.execute(select(Moment.id, Moment.event_id, Moment.kind, Moment.player_id, Moment.about, Moment.created_at)
                                                                      .where(Moment.event_id.in_(events), Moment.kind.in_({i.kind for i in items})).order_by(Moment.id)):
        if event_id in began and at < began[event_id]:
            continue
        whose = player_id if player_id is not None else ("game", (about or ["*"])[0])
        counted[(event_id, kind, whose)] += 1
        nth[moment_id] = counted[(event_id, kind, whose)]
    return [replace(i, about=frozenset(ids.get((i.player_id, systems.get(i.event_id, "")), ())) if i.player_id is not None and i.event_id is not None else i.about,
                    nth=nth.get(i.id, 0)) for i in items]


def _taken_back(db: Session, items: list[alerts.Item]) -> set[int]:
    """The moments of a batch's games a correction has taken back (a goal VAR ruled out), the batch's
    own corrections among them: not sent if they haven't been, and out of the story underneath."""
    events = {i.event_id for i in items if i.event_id}
    return {m for m in db.scalars(select(Moment.corrects).where(Moment.event_id.in_(events), Moment.corrects.is_not(None))).all() if m} if events else set()


def _plan(db: Session, items: list[alerts.Item], now: datetime, taken_back: set[int] | None = None, fans: set[str] | None = None) -> list[tuple[int, tuple, dict[str, Any], int, str]]:
    """Plan every follower's pushes for a batch (of `fans` alone, where given) and log them as notification rows, and
    keep what was left to a board's other side to tell (_hold). Returns one job per device: (notification id,
    (device id, transport, endpoint, p256dh, auth), payload, ttl, topic)."""
    from . import teams  # whose team is in a game (teams reads the pipeline that sends through here)

    players = {i.player_id for i in items if i.player_id is not None}
    events = {i.event_id for i in items if i.event_id}
    # Each player as their moments' titles name them: one play two of a fan's players tell alike is one line (alerts.fold).
    names = {pid: name for pid, name in db.execute(select(Player.id, Player.name).where(Player.id.in_(players)))} if players else {}
    items = [replace(i, who=names.get(i.player_id, "")) if i.player_id is not None else i for i in items]
    levels: dict[str, dict[int, str]] = {}
    for fan, player_id, rules in db.execute(select(Follow.fan_id, Follow.player_id, Follow.alert_rules).where(Follow.player_id.in_(players))) if players else []:
        levels.setdefault(fan, {})[player_id] = (rules or {}).get("level", DEFAULT_LEVEL)
    # And these fans' levels for the players whose side tells a moment of theirs (a chess board's other side, whose own
    # fixture finds it too): one who follows both hears it from there (alerts.voiced).
    voices = {i.voice for i in items if i.voice is not None} - players
    theirs = select(Follow.fan_id).where(Follow.player_id.in_(players))
    for fan, player_id, rules in db.execute(select(Follow.fan_id, Follow.player_id, Follow.alert_rules).where(Follow.player_id.in_(voices), Follow.fan_id.in_(theirs))) if voices and levels else []:
        levels[fan][player_id] = (rules or {}).get("level", DEFAULT_LEVEL)
    # The batch's games that fans follow by themselves, lapsed or not: plan() keeps a lapsed follow's moments
    # found before it lapsed (the result), and a player's moment one of them already said isn't said again.
    games: dict[str, dict[int, alerts.Watch]] = {}
    for fan, event_id, rules, until in db.execute(select(MatchFollow.fan_id, MatchFollow.event_id, MatchFollow.alert_rules, MatchFollow.until).where(MatchFollow.event_id.in_(events))) if events else []:
        games.setdefault(fan, {})[event_id] = alerts.Watch((rules or {}).get("level", DEFAULT_LEVEL), until)
    # And the games of the teams fans follow (teams.py), followed as long as the team is: a game a fan follows by
    # itself as well keeps its level there while that follow's on, and hears its result from their team's side too.
    for fan, by_game in teams.watches(db, events).items():
        for event_id, (level, sides) in by_game.items():
            had = games.setdefault(fan, {}).get(event_id)
            games[fan][event_id] = alerts.Watch(had.level if had is not None and had.until is None else level, None, frozenset(sides))
    fans = (set(levels) | set(games)) & fans if fans is not None else set(levels) | set(games)
    if games:
        items = for_games(db, items)  # whom each player moment is about, and where, to tell a game's own of the same
    devices: dict[str, list[PushSubscription]] = {}
    for d in db.scalars(select(PushSubscription).where(PushSubscription.fan_id.in_(fans), PushSubscription.transport.in_(transports())).order_by(PushSubscription.id.desc())) if fans else []:
        mine = devices.setdefault(d.fan_id, [])
        # However many are stored, one fan's fan-out can't fill the push threads every other fan waits on.
        if len(mine) < MAX_DEVICES:
            mine.append(d)
    if not devices:
        return []
    # A game under several fixtures is muted, and its story told, under any of them (alerts.plan).
    fixtures = events | {e for i in items for e in i.fixtures}
    muted: dict[str, set[int]] = {}
    for fan, event_id in db.execute(select(NotificationMute.fan_id, NotificationMute.event_id).where(NotificationMute.fan_id.in_(devices), NotificationMute.event_id.in_(fixtures))) if events else []:
        muted.setdefault(fan, set()).add(event_id)
    sent: dict[str, list[alerts.Sent]] = {}
    earlier = select(Notification).where(Notification.fan_id.in_(devices), Notification.kind.not_in(CALLS), or_(Notification.created_at >= now - timedelta(hours=1), Notification.event_id.in_(fixtures), and_(Notification.event_id.is_(None), Notification.player_id.in_(players))))
    began = editions(db, fixtures)
    for n in db.scalars(earlier):
        if n.event_id in began and n.created_at < began[n.event_id]:
            continue  # the game called off before this one, played again under its fixture (editions): not this one's
        sent.setdefault(n.fan_id, []).append(alerts.Sent(n.event_id, n.player_id, n.loud, n.lines or [], n.created_at, n.moment_ids or []))

    # What earlier pushes of these games sent the fans who follow one by itself, or a player on a board under several
    # fixtures: a moment one side said there isn't said again by the other (alerts.plan).
    followed = {e for watches in games.values() for e in watches}
    on_board = {e for i in items for e in i.fixtures}
    earlier_ids = {m for fan, theirs in sent.items() for s in theirs if (fan in games and s.event_id in followed) or s.event_id in on_board for m in s.moment_ids}
    told = {i.id: i for i in for_games(db, on_boards(db, db.scalars(select(Moment).where(Moment.id.in_(earlier_ids))).all()))} if earlier_ids else {}
    # What the Android app draws each push with, looked up once for the batch, should any of these fans have it.
    # It reads every source's stored boards: one it can't read sends the batch plain, never not at all.
    drawn = None
    if any(d.transport == "fcm" for mine in devices.values() for d in mine):
        try:
            drawn = _drawn(db, items)
        except Exception:
            log.exception("moments %s: no scorecards, sent plain", [i.id for i in items])
    jobs = []
    held: dict[int, tuple[alerts.Item, set[str]]] = {}
    for fan, fan_devices in devices.items():
        heard = [told[m] for s in sent.get(fan, []) if s.event_id in games.get(fan, {}) or s.event_id in on_board for m in s.moment_ids if m in told]
        left: list[alerts.Item] = []
        hushed = [quiet(d.timezone, d.quiet_start, d.quiet_end, now) for d in fan_devices]  # each device's quiet hours
        for p in alerts.plan(items, levels.get(fan, {}), muted.get(fan, set()), sent.get(fan, []), now, taken_back, games.get(fan, {}), heard, left):
            # Logged as loud only where it makes a sound on a device: one that arrives silent in quiet hours on all of
            # them leaves the hour's sound (alerts.LOUD_PER_HOUR) and the game's to the pushes after it (BUG-242).
            row = Notification(fan_id=fan, event_id=p.event_id, player_id=p.player_id, moment_ids=p.moment_ids, kind=p.kind, lines=p.lines, loud=p.loud and not all(hushed),
                               devices=len(fan_devices), delivered=0, created_at=now)
            db.add(row)
            db.flush()
            payload = {"id": row.id, "title": p.title, "body": p.body, "url": p.url, "tag": p.tag, "event_id": p.event_id, "player_id": p.player_id, "kind": p.kind, "loud": p.loud}
            android = {**payload, **_scorecard_or_none(p, drawn)}
            for d, hush in zip(fan_devices, hushed):
                jobs.append((row.id, (d.id, d.transport, d.endpoint, d.p256dh, d.auth), {**(android if d.transport == "fcm" else payload), "quiet": hush}, p.ttl, p.tag))
        for item in left:
            held.setdefault(item.id, (item, set()))[1].add(fan)
    if not _hold(held):
        # Not kept to send late: sent now in this side's words, as if the other side's weren't coming (twice beats never).
        for item, whom in held.values():
            jobs += _plan(db, [replace(item, voice=None)], now, taken_back, fans=whom)
    return jobs


def _scorecard_or_none(p: alerts.Push, drawn: Drawn | None) -> dict[str, Any]:
    """A push's scorecard keys (scorecard), or none, and so a plain push, where its board can't be read."""
    if drawn is None:
        return {}
    try:
        return scorecard(p, *drawn)
    except Exception:
        log.exception("no scorecard for a push of event %s", p.event_id)
        return {}


# What a batch's Android scorecards are drawn from (_drawn).
Drawn = tuple[dict[int, alerts.Item], dict[int, "livecards.Board"], dict[int, tuple[str, str, str]]]


def _drawn(db: Session, items: list[alerts.Item]) -> Drawn:
    """What a batch's Android scorecards are drawn from (scorecard): its moments by id; its games as Android pushes
    show them (livecards.boards); and its players' names, sports and photos (the one their card shows: photos.py)."""
    from . import livecards  # a game as Android pushes show it (livecards sends through here)

    players = {i.player_id for i in items if i.player_id is not None}
    photo = PlayerCard.card["player"]["photo"]["url"].astext
    who = {pid: (name, sport or "", url or "") for pid, name, sport, url in db.execute(
        select(Player.id, Player.name, Player.sport, photo).outerjoin(PlayerCard, PlayerCard.player_id == Player.id).where(Player.id.in_(players)))} if players else {}
    return {i.id: i for i in items}, livecards.boards(db, {i.event_id for i in items if i.event_id}), who


def scorecard(p: alerts.Push, items: dict[int, alerts.Item], boards: dict[int, livecards.Board], players: dict[int, tuple[str, str, str]]) -> dict[str, Any]:
    """What the Android app draws a push with beside its title and body (fcm.py): its game as every push about it
    shows it (livecards.Board: the sport's colours, its sides and where it's at; a player's news, their sport's
    alone); the big word over the lead moment's line (word_and_line); the story under it, the push's other lines and then
    the game's earlier ones, newest first, at most STORY_LINES of STORY_CHARS; and the player's photo, by its full
    address (none without SITE_URL, as in development). A game's own moments are read without the game's name
    their titles start with ("Arsenal v Chelsea: …"): the sides say it."""
    from . import livecards  # a game as Android pushes show it (livecards sends through here)

    lead = items.get(p.moment_ids[0]) if p.moment_ids else None
    who, sport_name, photo = players.get(p.player_id, ("", "", "")) if p.player_id is not None else ("", "", "")
    who = p.who or who  # several of the fan's players, where its line is theirs at once (alerts.fold)
    board = boards.get(p.event_id) if p.event_id is not None else None
    sport = board.sport if board is not None else sports.of(sport_name)

    def bare(line: str) -> str:
        name = next((n for n in board.names if line.startswith(f"{n}: ")), None) if board is not None else None
        return line[len(name) + 2:] if name else line

    word, line = word_and_line(sport, lead, bare(p.title), who)
    story = [s if len(s) <= STORY_CHARS else s[:STORY_CHARS - 1].rstrip() + "…" for s in map(bare, [*p.lines[1:], *p.story])][:STORY_LINES]
    return {**(board.keys if board is not None else livecards.keys(sport)), "word": word, "line": line, "story": story,
            "photo": f"{SITE_URL}{photo}" if SITE_URL and photo else ""}


def word_and_line(sport: sports.Sport, lead: alerts.Item | None, title: str, who: str) -> tuple[str, str]:
    """A push's big word on an Android scorecard, and the line under it: the word its sport has for its lead moment
    (Sport.high: "Hundred", "Late equaliser"), else its title's own before ": " ("Goal", "Red card"; not the player's
    name, which "Jokić: 20 points" starts with), else its kind's (its sport's kind_words, then WORDS), else none. A word is at most WORD_CHARS
    and never the whole title. The line is the title without the word's lead ("Goal: Saka" is "Saka"), else the
    title."""
    high = sport.high(Found(lead.kind, lead.level, title, lead.body, lead.detail, lead.priority), "") if lead is not None else None
    head, said, _ = title.partition(": ")
    kind = lead.kind if lead is not None else ""
    words = [high.word if high is not None else "", head if said and head.casefold() != who.casefold() else "", sport.kind_words.get(kind, ""), WORDS.get(kind, "")]
    chosen = next((w for w in words if w and len(w) <= WORD_CHARS and w != title), "")
    lead_in = f"{chosen}: "
    return chosen, title[len(lead_in):] if chosen and title.casefold().startswith(lead_in.casefold()) else title


def _settle(results: list[tuple[int, int, int | str | None]]) -> None:
    """Write back what the push services said: deliveries per notification, and each device's health."""
    with session() as db:
        delivered: dict[int, int] = {}
        for notification_id, device_id, error in results:
            delivered[notification_id] = delivered.get(notification_id, 0) + (error is None)
            _health(db, device_id, error)
        for notification_id, n in delivered.items():
            # Added to: a push its batch stopped waiting for is written back on its own, later (_settle_late).
            db.execute(update(Notification).where(Notification.id == notification_id).values(delivered=Notification.delivered + n))


def _health(db: Session, device_id: int, error: int | str | None) -> None:
    """A device the push service says is gone (404, 410) is removed; one that keeps failing is removed after MAX_FAILURES.
    A message too big for the push service (413) isn't counted against the device, nor a refusal of this
    server's own key (401: an FCM key that's wrong or revoked would otherwise remove every Android device), nor the
    push service holding back a device sent more than its quota (429: FCM's for a busy fan's phone). A failure is
    added to the count in the database, so batches writing back at once each count theirs (BUG-187)."""
    if error is None:
        db.execute(update(PushSubscription).where(PushSubscription.id == device_id).values(last_ok_at=datetime.now(timezone.utc), failures=0))
    elif error in (404, 410):
        db.execute(delete(PushSubscription).where(PushSubscription.id == device_id))
    elif error in (401, 413, 429):
        return  # this server's key, a message too big for the push service, or one holding pushes back: not the device's fault
    else:
        failures = db.scalar(update(PushSubscription).where(PushSubscription.id == device_id).values(failures=PushSubscription.failures + 1).returning(PushSubscription.failures))
        if failures is not None and failures >= MAX_FAILURES:
            db.execute(delete(PushSubscription).where(PushSubscription.id == device_id))


def transports() -> tuple[str, ...]:
    """The transports this server can send by: Web Push always (a browser can't subscribe without its
    keys), FCM with its key (fcm.py). A device of a transport that's off is neither sent to nor counted."""
    return ("webpush", "fcm") if fcm.enabled() else ("webpush",)


def deliver(device: tuple, payload: dict[str, Any], ttl: int, topic: str | None = None) -> int | str | None:
    """One push to one device, (id, transport, endpoint, p256dh, auth), by its transport. Returns None
    when the push service took it, else what _health reads."""
    _, transport, endpoint, p256dh, auth = device
    if transport == "fcm":
        return fcm.push(endpoint, json.loads(_fit(payload)), ttl)  # FCM's tag and collapse key are the payload's tag
    return push(endpoint, p256dh, auth, payload, ttl, topic)


def _service(device: tuple) -> str | None:
    """Which push service a device's pushes go to, for the metrics: FCM for the Android app, else its endpoint's host."""
    return "fcm" if device[1] == "fcm" else urlparse(device[2]).hostname


def push(endpoint: str, p256dh: str, auth: str, payload: dict[str, Any], ttl: int, topic: str | None = None) -> int | str | None:
    """One Web Push message. Returns None when the push service took it, else its status code (or the
    error, if it never answered).

    The topic replaces a message still waiting for an offline phone with the newer one for the same
    game (RFC 8030), so a phone back online gets the latest, not a burst. Urgency lets a phone in
    battery saver wake for a moment with sound and batch the silent ones."""
    from pywebpush import WebPushException, webpush

    loud = payload.get("loud", True) and not payload.get("quiet")
    headers = {"Urgency": "high" if loud else "normal"}
    if topic:
        headers["Topic"] = topic
    try:
        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=_fit(payload),
            vapid_private_key=signer(),
            vapid_claims={"sub": VAPID_SUBJECT},
            ttl=ttl,
            headers=headers,
            timeout=cast(float, (CONNECT_TIMEOUT, READ_TIMEOUT)),  # requests takes the two apart, pywebpush passes them on
        )
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        log.warning("push to %s… failed (%s): %s", endpoint[:40], status, str(exc)[:200], extra={"push_service": urlparse(endpoint).hostname, "status": status})
        return status or str(exc)[:100]
    except Exception as exc:  # a timeout or a refused connection is that device's failure, not the batch's
        log.warning("push to %s… failed: %s", endpoint[:40], exc, extra={"push_service": urlparse(endpoint).hostname})
        return str(exc)[:100]
    return None


def _fit(payload: dict[str, Any]) -> str:
    """A push's JSON, at most PAYLOAD_BYTES: the body, then the title, cut short on a whole character.
    Written as UTF-8, not escaped: escaped, a Hindi headline takes 6 bytes a letter instead of 3."""
    data = json.dumps(payload, ensure_ascii=False)
    for key in ("body", "title"):
        over = len(data.encode()) - PAYLOAD_BYTES
        if over <= 0:
            break
        # A character cut saves at least its UTF-8 bytes in the JSON (escaping only adds), so this is enough.
        raw = str(payload.get(key) or "").encode()
        keep = len(raw) - over - len("…".encode())
        payload = {**payload, key: raw[:keep].decode(errors="ignore").rstrip() + "…" if keep > 0 else ""}
        data = json.dumps(payload, ensure_ascii=False)
    return data


def newest_devices(db: Session, fan_id: str) -> list[tuple]:
    """A fan's devices a push goes to, (id, transport, endpoint, p256dh, auth): their newest, of the transports this
    server sends by, as a moment's push (_plan). A test push goes to these (send_test)."""
    newest = select(PushSubscription).where(PushSubscription.fan_id == fan_id, PushSubscription.transport.in_(transports())).order_by(PushSubscription.id.desc()).limit(MAX_DEVICES)
    return [(d.id, d.transport, d.endpoint, d.p256dh, d.auth) for d in db.scalars(newest)]


# On Android, a moment of no sport, in the app's blue (fcm.message).
TEST = {"id": 0, "title": "Notifications are on", "body": "You'll hear about goals, fifties, big games and results for the players you follow.", "url": "/", "tag": "test", "kind": "test", "loud": True, "quiet": False}


async def send_test(devices: list[tuple]) -> int:
    """A test notification to each of these devices (newest_devices) at once, on the test threads (_tests), which write
    back each device's health as its push ends. Waited for without holding a thread, for at most TEST_WAIT: one not
    taken by then counts as not taken (and one not yet started isn't sent). Returns how many took it."""
    if not devices:
        return 0
    pushes = [asyncio.wrap_future(_tests.submit(logs.carried(_test_one), device)) for device in devices]
    done, going = await asyncio.wait(pushes, timeout=TEST_WAIT)
    for push in going:
        push.cancel()
    return sum(1 for push in done if push.exception() is None and push.result() is None)


def _test_one(device: tuple) -> int | str | None:
    """One test push, then its device's health written back."""
    error = deliver(device, TEST, 600)
    try:
        settle([(device[0], error)])
    except Exception:
        log.exception("a test push to device %s: what its push service said wasn't written back", device[0])
    return error


def settle(results: list[tuple[int, int | str | None]]) -> None:
    """Write back each device's health after pushes that log no notification row (a test push, a game's live card:
    livecards.py), as _settle does after a batch's: (device id, what its push service said)."""
    with session() as db:
        for device_id, error in results:
            _health(db, device_id, error)


def trim_devices(db: Session, fan_id: str, keep: Any, room: int) -> None:
    """Delete this fan's devices beyond the newest `room` that aren't `keep` (a condition on
    PushSubscription), so with the kept ones a push goes to at most MAX_DEVICES (BUG-055)."""
    others = PushSubscription.fan_id == fan_id, ~keep
    newest = select(PushSubscription.id).where(*others).order_by(PushSubscription.id.desc()).limit(max(room, 0))
    db.execute(delete(PushSubscription).where(*others, PushSubscription.id.not_in(newest)))


def opened(db: Session, fan_id: str, notification_id: int) -> bool:
    """The fan tapped a notification: the first tap is its open time."""
    return bool(db.execute(
        update(Notification).where(Notification.id == notification_id, Notification.fan_id == fan_id, Notification.opened_at.is_(None)).values(opened_at=func.now())
    ).rowcount)


# ---------------------------------------------------------------- quiet hours


def quiet(tz: str | None, start: str | None, end: str | None, now: datetime | None = None) -> bool:
    """Whether it's within a device's quiet hours ("22:00" to "07:00" in its own timezone)."""
    if not start or not end or start == end:
        return False
    try:
        local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz or "UTC")).time()
        a, b = clock.fromisoformat(start), clock.fromisoformat(end)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return a <= local < b if a < b else (local >= a or local < b)


# ---------------------------------------------------------------- stats


def stats(days: int = 7) -> list[dict[str, Any]]:
    """Per lead moment kind over the last days: pushes, how many made a sound, how many were opened.
    A kind fans rarely open is a kind to make quieter."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with session() as db:
        rows = db.execute(
            select(Notification.kind, func.count(), func.count().filter(Notification.loud), func.count(Notification.opened_at), func.count(func.distinct(Notification.fan_id)))
            .where(Notification.created_at >= since, Notification.delivered > 0)
            .group_by(Notification.kind)
            .order_by(func.count().desc())
        ).all()
    return [{"kind": k, "pushes": n, "loud": loud, "opened": o, "fans": fans} for k, n, loud, o, fans in rows]


if __name__ == "__main__":
    if sys.argv[1:] == ["keys"]:
        print("VAPID public key:", make_keys())
        print("Private key saved to", VAPID_KEY)
    elif sys.argv[1:2] == ["stats"]:
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        print(f"Pushes delivered in the last {days} days, by the moment that led them:")
        print(f"{'kind':<10} {'pushes':>7} {'loud':>6} {'opened':>7} {'rate':>5} {'fans':>6}")
        for r in stats(days):
            rate = r["opened"] / r["pushes"] if r["pushes"] else 0
            print(f"{r['kind']:<10} {r['pushes']:>7} {r['loud']:>6} {r['opened']:>7} {rate:>5.0%} {r['fans']:>6}")
    else:
        sys.exit("usage: python -m sports_follow.notify keys | stats [days]")
