"""Telling fans about moments (moments.py): store each once, then send it to every follower who
wants it, on every device they turned notifications on for.

Delivery is Web Push today (browsers, the installed app, the Android app, iPhone home-screen apps);
it is one function, deliver(), so native push (FCM, APNs) can sit beside it later. A fan's quiet
hours don't drop a moment: it arrives silently. Devices the push service says are gone are removed.

    .venv/bin/python -m sports_follow.notify keys     # make this server's VAPID keys, once
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as clock, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import bus
from .db import session
from .moments import DEFAULT_LEVEL, Found, wanted
from .models import Follow, Moment, PushSubscription

log = logging.getLogger("sports_follow.notify")

VAPID_KEY = Path(os.environ.get("SPORTS_FOLLOW_VAPID_KEY", Path.home() / ".config" / "sports-follow" / "vapid-private.pem"))
# Push services ask who is sending (RFC 8292): a URL for this project, never a person's address.
VAPID_SUBJECT = os.environ.get("SPORTS_FOLLOW_VAPID_SUBJECT", "https://github.com/Codewiz2898/sports-follow")
LIVE_TTL = 15 * 60  # a goal an hour late isn't news; results and injuries keep for half a day
LATE_TTL = 12 * 3600
MAX_FAILURES = 5

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="notify")


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
    """Store new moments; ones already stored (the same key) are skipped. Returns the new ids."""
    ids = []
    for f in found:
        key = f"{event_id or '-'}:{player_id}:{f.kind}:{f.detail}"[:200]
        new_id = db.scalar(
            insert(Moment)
            .values(key=key, player_id=player_id, event_id=event_id, kind=f.kind, level=f.level, title=f.title[:200], body=f.body, url=url or f"/player/{player_id}")
            .on_conflict_do_nothing()
            .returning(Moment.id)
        )
        if new_id:
            ids.append(new_id)
    return ids


def dispatch(moment_ids: list[int]) -> None:
    """Send moments in the background: a poll mustn't wait on push services."""
    for moment_id in moment_ids:
        _pool.submit(_send_safely, moment_id)


def _send_safely(moment_id: int) -> None:
    try:
        send(moment_id)
    except Exception:
        log.exception("sending moment %s failed", moment_id)


def send(moment_id: int) -> int:
    """Deliver one moment to every device of every follower whose level wants it. Returns how many."""
    with session() as db:
        moment = db.get(Moment, moment_id)
        if moment is None or moment.sent_at is not None:
            return 0
        payload = {"id": moment.id, "title": moment.title, "body": moment.body, "url": moment.url, "player_id": moment.player_id, "kind": moment.kind, "level": moment.level}
        fans = [
            fan for fan, rules in db.execute(select(Follow.fan_id, Follow.alert_rules).where(Follow.player_id == moment.player_id))
            if wanted((rules or {}).get("level", DEFAULT_LEVEL), moment.level, moment.kind)
        ]
        devices = db.scalars(select(PushSubscription).where(PushSubscription.fan_id.in_(fans))).all() if fans else []
        db.execute(update(Moment).where(Moment.id == moment_id).values(sent_at=datetime.now(timezone.utc)))
        ttl = LATE_TTL if moment.kind in ("final", "news") else LIVE_TTL
        targets = [(d.id, d.endpoint, d.p256dh, d.auth, quiet(d.timezone, d.quiet_start, d.quiet_end)) for d in devices]
    # The app shows it too, as a toast, to anyone with the player's page or the Following list open.
    bus.publish(payload["player_id"], {"type": "moment", "player_id": payload["player_id"], "moment": payload})
    sent = sum(deliver(device_id, endpoint, p256dh, auth, {**payload, "quiet": is_quiet}, ttl) for device_id, endpoint, p256dh, auth, is_quiet in targets)
    if targets:
        log.info("moment %s %r: %d of %d device(s)", moment_id, payload["title"], sent, len(targets))
    return sent


def deliver(device_id: int, endpoint: str, p256dh: str, auth: str, payload: dict[str, Any], ttl: int) -> bool:
    """One Web Push message. A device the push service says is gone is removed; others that keep
    failing are removed after MAX_FAILURES."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=json.dumps(payload),
            vapid_private_key=signer(),
            vapid_claims={"sub": VAPID_SUBJECT},
            ttl=ttl,
            headers={"Urgency": "normal" if payload.get("quiet") else "high"},
            timeout=10,
        )
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        with session() as db:
            if status in (404, 410):
                db.execute(delete(PushSubscription).where(PushSubscription.id == device_id))
            else:
                row = db.get(PushSubscription, device_id)
                if row is not None:
                    row.failures += 1
                    if row.failures >= MAX_FAILURES:
                        db.delete(row)
        log.warning("push to device %s failed (%s): %s", device_id, status, str(exc)[:200])
        return False
    with session() as db:
        db.execute(update(PushSubscription).where(PushSubscription.id == device_id).values(last_ok_at=datetime.now(timezone.utc), failures=0))
    return True


def send_test(fan_id: str) -> int:
    """A test notification to each of this fan's devices. Returns how many took it."""
    with session() as db:
        devices = [(d.id, d.endpoint, d.p256dh, d.auth) for d in db.scalars(select(PushSubscription).where(PushSubscription.fan_id == fan_id))]
    payload = {"id": 0, "title": "Notifications are on", "body": "You'll hear about goals, fifties, big games and results for the players you follow.", "url": "/", "kind": "test", "level": "key", "quiet": False}
    return sum(deliver(i, e, p, a, payload, 600) for i, e, p, a in devices)


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


if __name__ == "__main__":
    if sys.argv[1:] == ["keys"]:
        print("VAPID public key:", make_keys())
        print("Private key saved to", VAPID_KEY)
    else:
        sys.exit("usage: python -m sports_follow.notify keys")
