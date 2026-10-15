"""Telling fans about moments (moments.py): store each once, then push them to every follower who
wants them, on every device they turned notifications on for, by the noise rules in alerts.py.

A poll's (or a rebuild's) new moments go out together as one batch: claimed in the database so they
are sent once, planned per fan (one push per game, sound rationed), logged as notification rows, then
delivered to each device at once. A batch no sender claimed (the worker restarted between finding a
moment and sending it) is swept up by the tick; a live moment that has gone stale by then is dropped.

Delivery is Web Push today (browsers, the installed app, the Android app, iPhone home-screen apps);
it is one function, push(), so native push (FCM, APNs) can sit beside it later. A fan's quiet hours
don't drop a moment: it arrives silently. Devices the push service says are gone are removed.

    .venv/bin/python -m sports_follow.notify keys        # make this server's VAPID keys, once
    .venv/bin/python -m sports_follow.notify stats [7]   # what was sent in the last days, and opened
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as clock, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import alerts, bus, logs, metrics
from .config import SITE_URL
from .db import session
from .moments import DEFAULT_LEVEL, Found
from .models import Follow, Moment, Notification, NotificationMute, PushSubscription

log = logging.getLogger("sports_follow.notify")

VAPID_KEY = Path(os.environ.get("SPORTS_FOLLOW_VAPID_KEY", Path.home() / ".config" / "sports-follow" / "vapid-private.pem"))


def vapid_subject(site: str) -> str:
    """Who is sending, for push services (RFC 8292): a URL for this project, never a person's address.
    The site once it's hosted; in development, where no push service checks it, localhost."""
    return site if site.startswith("https://") else "https://localhost"


VAPID_SUBJECT = os.environ.get("SPORTS_FOLLOW_VAPID_SUBJECT") or vapid_subject(SITE_URL)
MAX_FAILURES = 5
MAX_DEVICES = 10  # per fan: subscribing another drops their oldest, and a push goes to the newest this many
SWEEP_AFTER = 60  # seconds a batch has to be claimed by the sender it was handed to before the tick takes it
# A push's JSON before encryption, in bytes. Push services promise only 4,096 bytes encrypted (RFC 8030)
# and refuse more with a 413; encryption adds about 100, so this keeps a push under 3.5 KB. A longer
# body (then title) is cut short.
PAYLOAD_BYTES = 3400

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="notify")  # batches
_wire = ThreadPoolExecutor(max_workers=16, thread_name_prefix="push")  # one push to one device each


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
    was. Returns the new ids."""
    ids = []
    for f in found:
        corrects = None
        if f.kind == "correction":
            corrects = db.scalar(select(Moment.id).where(Moment.key == _key(event_id, player_id, f.detail)))
            if corrects is None:
                continue  # what it takes back was never found, so nobody was told it
        key = _key(event_id, player_id, f"{f.kind}:{f.detail}")
        new_id = db.scalar(
            insert(Moment)
            .values(key=key, player_id=player_id, event_id=event_id, kind=f.kind, level=f.level, priority=f.priority or 50, title=f.title[:200], body=f.body, url=url or f"/player/{player_id}", corrects=corrects)
            .on_conflict_do_nothing()
            .returning(Moment.id)
        )
        if new_id:
            ids.append(new_id)
            metrics.moment(f.kind)
    return ids


def told(db: Session, player_id: int, event_id: int) -> list[tuple[str, str]]:
    """The moments stored for a player in a game, oldest first, as each one's kind and detail (read back
    from its key): what the sport may take back (moments.detect)."""
    prefix = _key(event_id, player_id, "")
    keys = db.scalars(select(Moment.key).where(Moment.player_id == player_id, Moment.event_id == event_id).order_by(Moment.id))
    return [(kind, detail) for kind, _, detail in (key.removeprefix(prefix).partition(":") for key in keys)]


def _key(event_id: int | None, player_id: int, kind_detail: str) -> str:
    """A moment's key: its game, its player, its kind and detail. The same moment found again has the same key."""
    return f"{event_id or '-'}:{player_id}:{kind_detail}"[:200]


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
    now = now or datetime.now(timezone.utc)
    with session() as db:
        # Claim first: whichever sender gets here first sends; the other finds nothing to do.
        claimed = db.scalars(update(Moment).where(Moment.id.in_(moment_ids), Moment.sent_at.is_(None)).values(sent_at=now).returning(Moment.id)).all()
        if not claimed:
            return 0
        rows = db.scalars(select(Moment).where(Moment.id.in_(claimed)).order_by(Moment.id)).all()
        items = [alerts.Item(m.id, m.player_id, m.event_id, m.kind, m.level, m.priority, m.title, m.body, m.url, m.created_at, m.corrects) for m in rows]
        taken_back = _taken_back(db, items)
        jobs = _plan(db, items, now, taken_back)
        # A correction's toast follows its moment's: one sent in an earlier batch (not taken back before it went).
        corrects = {i.corrects for i in items if i.corrects is not None}
        toasted = set(db.scalars(select(Moment.id).where(Moment.id.in_(corrects), Moment.id.not_in(claimed), Moment.sent_at.is_not(None))).all()) if corrects else set()
    # The app shows them too, as toasts, to anyone with it open; it filters by the fan's level itself.
    for item in items:
        if item.id not in taken_back and (item.corrects is None or item.corrects in toasted) and (now - item.created_at).total_seconds() <= alerts.ttl(item.kind):
            bus.publish(item.player_id, {"type": "moment", "player_id": item.player_id, "moment": {"id": item.id, "title": item.title, "body": item.body, "url": item.url, "player_id": item.player_id, "event_id": item.event_id, "kind": item.kind, "level": item.level}})
    if not jobs:
        return 0
    futures = [(notification_id, device, _wire.submit(logs.carried(push), device[1], device[2], device[3], payload, ttl, topic)) for notification_id, device, payload, ttl, topic in jobs]
    results = [(notification_id, device[0], future.result()) for notification_id, device, future in futures]
    _settle(results)
    sent = sum(1 for _, _, error in results if error is None)
    pushes = len({n for n, _, _ in results})
    # The numbers as fields too, for the Notifications dashboard (deploy/grafana/): among them how long
    # the oldest moment waited, from its poll or build finding it to its push.
    waited = max((now - item.created_at).total_seconds() for item in items)
    metrics.pushes([(urlparse(device[1]).hostname, error) for (_, device, _), (_, _, error) in zip(futures, results)], waited)
    log.info("moments %s: %d push(es) to %d of %d device(s)", claimed, pushes, sent, len(results),
             extra={"moments": len(claimed), "pushes": pushes, "devices": len(results), "delivered": sent, "waited_ms": round(waited * 1000)})
    return sent


def _taken_back(db: Session, items: list[alerts.Item]) -> set[int]:
    """The moments of a batch's games a correction has taken back (a goal VAR ruled out), the batch's
    own corrections among them: not sent if they haven't been, and out of the story underneath."""
    events = {i.event_id for i in items if i.event_id}
    return {m for m in db.scalars(select(Moment.corrects).where(Moment.event_id.in_(events), Moment.corrects.is_not(None))).all() if m} if events else set()


def _plan(db: Session, items: list[alerts.Item], now: datetime, taken_back: set[int] | None = None) -> list[tuple[int, tuple, dict[str, Any], int, str]]:
    """Plan every follower's pushes for a batch and log them as notification rows. Returns one job per
    device: (notification id, (device id, endpoint, p256dh, auth), payload, ttl, topic)."""
    players = {i.player_id for i in items}
    events = {i.event_id for i in items if i.event_id}
    levels: dict[str, dict[int, str]] = {}
    for fan, player_id, rules in db.execute(select(Follow.fan_id, Follow.player_id, Follow.alert_rules).where(Follow.player_id.in_(players))):
        levels.setdefault(fan, {})[player_id] = (rules or {}).get("level", DEFAULT_LEVEL)
    devices: dict[str, list[PushSubscription]] = {}
    for d in db.scalars(select(PushSubscription).where(PushSubscription.fan_id.in_(levels)).order_by(PushSubscription.id.desc())) if levels else []:
        mine = devices.setdefault(d.fan_id, [])
        # However many are stored, one fan's fan-out can't fill the push threads every other fan waits on.
        if len(mine) < MAX_DEVICES:
            mine.append(d)
    if not devices:
        return []
    muted: dict[str, set[int]] = {}
    for fan, event_id in db.execute(select(NotificationMute.fan_id, NotificationMute.event_id).where(NotificationMute.fan_id.in_(devices), NotificationMute.event_id.in_(events))) if events else []:
        muted.setdefault(fan, set()).add(event_id)
    sent: dict[str, list[alerts.Sent]] = {}
    earlier = select(Notification).where(Notification.fan_id.in_(devices), or_(Notification.created_at >= now - timedelta(hours=1), Notification.event_id.in_(events), and_(Notification.event_id.is_(None), Notification.player_id.in_(players))))
    for n in db.scalars(earlier):
        sent.setdefault(n.fan_id, []).append(alerts.Sent(n.event_id, n.player_id, n.loud, n.lines or [], n.created_at, n.moment_ids or []))

    jobs = []
    for fan, fan_devices in devices.items():
        for p in alerts.plan(items, levels[fan], muted.get(fan, set()), sent.get(fan, []), now, taken_back):
            row = Notification(fan_id=fan, event_id=p.event_id, player_id=p.player_id, moment_ids=p.moment_ids, kind=p.kind, lines=p.lines, loud=p.loud, devices=len(fan_devices), delivered=0, created_at=now)
            db.add(row)
            db.flush()
            payload = {"id": row.id, "title": p.title, "body": p.body, "url": p.url, "tag": p.tag, "event_id": p.event_id, "player_id": p.player_id, "kind": p.kind, "loud": p.loud}
            for d in fan_devices:
                jobs.append((row.id, (d.id, d.endpoint, d.p256dh, d.auth), {**payload, "quiet": quiet(d.timezone, d.quiet_start, d.quiet_end, now)}, p.ttl, p.tag))
    return jobs


def _settle(results: list[tuple[int, int, int | str | None]]) -> None:
    """Write back what the push services said: deliveries per notification, and each device's health."""
    with session() as db:
        delivered: dict[int, int] = {}
        for notification_id, device_id, error in results:
            delivered[notification_id] = delivered.get(notification_id, 0) + (error is None)
            _health(db, device_id, error)
        for notification_id, n in delivered.items():
            db.execute(update(Notification).where(Notification.id == notification_id).values(delivered=n))


def _health(db: Session, device_id: int, error: int | str | None) -> None:
    """A device the push service says is gone (404, 410) is removed; one that keeps failing is removed after MAX_FAILURES.
    A message too big for the push service (413) isn't counted against the device."""
    if error is None:
        db.execute(update(PushSubscription).where(PushSubscription.id == device_id).values(last_ok_at=datetime.now(timezone.utc), failures=0))
    elif error in (404, 410):
        db.execute(delete(PushSubscription).where(PushSubscription.id == device_id))
    elif error == 413:
        return  # the message was too big for the push service: not the device's fault
    else:
        row = db.get(PushSubscription, device_id)
        if row is not None:
            row.failures += 1
            if row.failures >= MAX_FAILURES:
                db.delete(row)


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
            timeout=10,
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


def send_test(fan_id: str) -> int:
    """A test notification to each of this fan's devices. Returns how many took it."""
    with session() as db:
        newest = select(PushSubscription).where(PushSubscription.fan_id == fan_id).order_by(PushSubscription.id.desc()).limit(MAX_DEVICES)
        devices = [(d.id, d.endpoint, d.p256dh, d.auth) for d in db.scalars(newest)]
    payload = {"id": 0, "title": "Notifications are on", "body": "You'll hear about goals, fifties, big games and results for the players you follow.", "url": "/", "tag": "test", "kind": "test", "loud": True, "quiet": False}
    results = [(i, push(e, p, a, payload, 600)) for i, e, p, a in devices]
    with session() as db:
        for device_id, error in results:
            _health(db, device_id, error)
    return sum(1 for _, error in results if error is None)


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
