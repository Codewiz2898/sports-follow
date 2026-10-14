"""Accounts, which are optional: sign in with a code sent by email (or with Google) and a fan's players,
alert levels, muted games and devices are the same on every device they sign in on.

An account has a fan id of its own ("acct-…"). Every per-fan row is kept under it, as an anonymous fan's
rows are kept under their cookie's id, so the routes work the same for both. That id is never in a
cookie: a signed-in device holds a random session token, and only its hash is stored here.

Signing in moves what this device's anonymous fan had into the account (adopt). Signing out leaves the
device a new, empty anonymous fan, and its alerts stop: a device turned on while signed in belongs to
that session. Deleting the account deletes every row kept under its fan id.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import notify
from .db import session
from .models import Account, AccountSession, Follow, Notification, NotificationMute, PushSubscription

FAN_PREFIX = "acct-"  # an account's fan id: never taken from a fan cookie (server._unsigned_fan)
SESSION_COOKIE = "sf_session"
SESSION_LIFE = timedelta(days=365)  # since the device last opened the app
COOKIE_MAX_AGE = 400 * 24 * 3600  # browsers keep a cookie at most 400 days; the app renews it daily
BUMP_EVERY = timedelta(days=1)
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")


@dataclass(frozen=True)
class Signed:
    """Who a request is signed in as."""

    account_id: int
    fan_id: str
    session_id: int


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def email_key(email: str) -> str:
    """An email address as Redis keys name it, so they don't hold the address itself."""
    return hashlib.sha256(email.encode()).hexdigest()


def normal_email(raw: object) -> str | None:
    """The address as accounts are kept by (trimmed, lower case), or None if it isn't one."""
    email = str(raw or "").strip().lower()
    return email if len(email) <= 254 and EMAIL.fullmatch(email) else None


def find_or_create(db: Session, email: str) -> int:
    """The account for this email address, made the first time it signs in. Returns its id."""
    db.execute(insert(Account).values(email=email, fan_id=f"{FAN_PREFIX}{secrets.token_urlsafe(18)}").on_conflict_do_nothing(index_elements=["email"]))
    return db.scalar(select(Account.id).where(Account.email == email))


def describe(db: Session, account_id: int) -> dict | None:
    account = db.get(Account, account_id)
    if account is None:
        return None
    return {"signed_in": True, "email": account.email, "google": account.google_sub is not None, "created_at": account.created_at.isoformat()}


# ---------------------------------------------------------------- sessions


def session_for(db: Session, token_hash: str) -> Signed | None:
    row = db.execute(
        select(AccountSession.id, Account.id, Account.fan_id)
        .join(Account, Account.id == AccountSession.account_id)
        .where(AccountSession.token_hash == token_hash, AccountSession.expires_at > func.now())
    ).first()
    return Signed(account_id=row[1], fan_id=row[2], session_id=row[0]) if row else None


def bump(db: Session, session_id: int) -> bool:
    """The device opened the app: its session lasts SESSION_LIFE from now (written once a day). True
    when it was renewed, so the cookie is sent again with the same life."""
    now = datetime.now(timezone.utc)
    renewed = db.execute(
        update(AccountSession)
        .where(AccountSession.id == session_id, AccountSession.last_seen_at < now - BUMP_EVERY)
        .values(last_seen_at=now, expires_at=now + SESSION_LIFE)
    ).rowcount
    return bool(renewed)


@dataclass(frozen=True)
class SignedIn:
    token: str  # for the cookie; only its hash is stored
    session_id: int
    fan_id: str
    players: int  # how many players this device's anonymous fan brought


def sign_in(db: Session, account_id: int, anon: str | None, ending: int | None = None) -> SignedIn:
    """A new session for this device, and what its anonymous fan had moved into the account. `ending` is
    the session this device was signed in with until now: it ends."""
    if anon:
        _lock(db, anon, account_id)  # before anything here touches the account's row, as adopt_later does
    if ending is not None:
        db.execute(delete(AccountSession).where(AccountSession.id == ending))
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    session_id = db.scalar(insert(AccountSession).values(account_id=account_id, token_hash=token_hash(token), expires_at=now + SESSION_LIFE).returning(AccountSession.id))
    fan = db.scalar(update(Account).where(Account.id == account_id).values(last_sign_in_at=now).returning(Account.fan_id))
    players = adopt(db, anon, account_id, session_id) if anon else 0
    return SignedIn(token=token, session_id=session_id, fan_id=fan, players=players)


def end_sessions(db: Session, account_id: int, session_id: int | None = None) -> list[str]:
    """Sign out one session, or every one of the account's (session_id None). Their devices go with
    them. Returns the ended sessions' token hashes, for their cached copies."""
    ended = delete(AccountSession).where(AccountSession.account_id == account_id)
    if session_id is not None:
        ended = ended.where(AccountSession.id == session_id)
    hashes = list(db.scalars(ended.returning(AccountSession.token_hash)))
    if session_id is None:
        # A device left under the account without a session (none should be) goes too: no device is signed in now.
        fan = db.scalar(select(Account.fan_id).where(Account.id == account_id))
        db.execute(delete(PushSubscription).where(PushSubscription.fan_id == fan))
    return hashes


def purge_expired() -> int:
    """Sessions not used for SESSION_LIFE: their devices go too. Returns how many."""
    with session() as db:
        return len(list(db.scalars(delete(AccountSession).where(AccountSession.expires_at <= func.now()).returning(AccountSession.id))))


# ---------------------------------------------------------------- moving a device's fan into an account


def _lock(db: Session, anon: str, account_id: int) -> str | None:
    """One move at a time per anonymous fan and per account (a sign-in and its straggler job, two devices
    signing in at once, a delete). Returns the account's fan id, or None if it's gone."""
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(anon, 0))))
    return db.scalar(select(Account.fan_id).where(Account.id == account_id).with_for_update())


def adopt(db: Session, anon: str, account_id: int, session_id: int) -> int:
    """Move everything kept under the anonymous fan id `anon` into the account, in this transaction.
    Doing it again moves only what has appeared since. Returns how many players came over."""
    fan = _lock(db, anon, account_id)
    if fan is None:
        return 0
    players = db.scalar(select(func.count()).select_from(Follow).where(Follow.fan_id == anon)) or 0
    # A player both follow keeps the account's alert level, and the earlier follow date: the activity
    # list shows moments since then.
    followed = insert(Follow).from_select(
        ["fan_id", "player_id", "alert_rules", "created_at"],
        select(literal(fan), Follow.player_id, Follow.alert_rules, Follow.created_at).where(Follow.fan_id == anon),
    )
    db.execute(followed.on_conflict_do_update(index_elements=["fan_id", "player_id"], set_={"created_at": func.least(Follow.created_at, followed.excluded.created_at)}))
    db.execute(delete(Follow).where(Follow.fan_id == anon))
    muted = insert(NotificationMute).from_select(
        ["fan_id", "event_id", "created_at"],
        select(literal(fan), NotificationMute.event_id, NotificationMute.created_at).where(NotificationMute.fan_id == anon),
    )
    db.execute(muted.on_conflict_do_nothing())
    db.execute(delete(NotificationMute).where(NotificationMute.fan_id == anon))
    # The push log the noise rules read (how often this phone buzzed, the game's story so far).
    db.execute(update(Notification).where(Notification.fan_id == anon).values(fan_id=fan))
    # This device's alerts: now the account's, and this session's. The account's oldest devices make room.
    devices = list(db.scalars(update(PushSubscription).where(PushSubscription.fan_id == anon).values(fan_id=fan, session_id=session_id).returning(PushSubscription.id)))
    if devices:
        notify.trim_devices(db, fan, PushSubscription.id.in_(devices), notify.MAX_DEVICES - len(devices))
    return players


def adopt_later(anon: str, account_id: int, session_id: int) -> int:
    """A minute after a sign-in: what a request already under way then wrote to the anonymous fan."""
    with session() as db:
        if db.get(AccountSession, session_id) is None:
            return 0  # signed out since
        return adopt(db, anon, account_id, session_id)


# ---------------------------------------------------------------- deleting an account


@dataclass(frozen=True)
class Deleted:
    fan_id: str
    email: str
    token_hashes: list[str]


def delete_account(db: Session, account_id: int) -> Deleted | None:
    """Every row kept under the account's fan id, its sessions and the account. Players and their
    pages are everyone's, so they stay."""
    account = db.execute(select(Account).where(Account.id == account_id).with_for_update()).scalar_one_or_none()
    if account is None:
        return None
    hashes = list(db.scalars(select(AccountSession.token_hash).where(AccountSession.account_id == account_id)))
    for model in (Follow, NotificationMute, Notification, PushSubscription):
        db.execute(delete(model).where(model.fan_id == account.fan_id))
    db.execute(delete(Account).where(Account.id == account_id))  # its sessions go with it
    return Deleted(fan_id=account.fan_id, email=account.email, token_hashes=hashes)
