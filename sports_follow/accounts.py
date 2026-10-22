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

from sqlalchemy import case, delete, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import clips, fansports, matches, notify, predictions, teams
from .db import session
from .models import Account, AccountSession, ClipReport, Event, FanSource, FanSport, FeedFeedback, Follow, LeagueMember, MatchFollow, Notification, NotificationMute, Pick, PushSubscription, TeamFollow

FAN_PREFIX = "acct-"  # an account's fan id: never taken from a fan cookie (server._unsigned_fan)
# The rows kept under a fan id here; predictions.py keeps its own (picks, places in leagues: predictions.forget).
FAN_ROWS = (Follow, MatchFollow, TeamFollow, FanSport, NotificationMute, Notification, PushSubscription, FanSource, FeedFeedback, ClipReport)
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
    """The address as accounts are kept by (trimmed, lower case), or None if it isn't one: one with a control or
    an invisible character in it isn't (BUG-096)."""
    email = str(raw or "").strip().lower()
    return email if len(email) <= 254 and email.isprintable() and EMAIL.fullmatch(email) else None


def find_or_create(db: Session, email: str) -> int:
    """The account for this email address, made the first time it signs in. Returns its id."""
    db.execute(insert(Account).values(email=email, fan_id=f"{FAN_PREFIX}{secrets.token_urlsafe(18)}").on_conflict_do_nothing(index_elements=["email"]))
    return db.scalar(select(Account.id).where(Account.email == email))


def find_or_create_google(db: Session, sub: str, email: str) -> int | None:
    """The account for this Google account: by its Google id, else the account with the address Google
    verified (it gets the Google id), else a new one. None when that address's account is already
    linked to a different Google account: the emailed code still signs in to it."""
    for _ in range(2):  # a second pass if another request made the address's account in between
        found = db.scalar(select(Account.id).where(Account.google_sub == sub))
        if found is not None:
            return found
        account = db.execute(select(Account).where(Account.email == email).with_for_update()).scalar_one_or_none()
        if account is not None:
            if account.google_sub is not None:
                return None
            account.google_sub = sub
            return account.id
        made = db.scalar(insert(Account).values(email=email, google_sub=sub, fan_id=f"{FAN_PREFIX}{secrets.token_urlsafe(18)}").on_conflict_do_nothing().returning(Account.id))
        if made is not None:
            return made
    return None


def known(db: Session, email: str) -> bool:
    """Whether this address has an account: part of the day's sign-in codes is kept for those (server.email_start)."""
    return db.scalar(select(Account.id).where(Account.email == email)) is not None


def blocked(db: Session, email: str | None = None, account_id: int | None = None) -> bool:
    """Whether an admin blocked this account (admin.py): it can't sign in."""
    where = Account.email == email if account_id is None else Account.id == account_id
    return db.scalar(select(Account.blocked_at).where(where)) is not None


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
        .where(AccountSession.token_hash == token_hash, AccountSession.expires_at > func.now(), Account.blocked_at.is_(None))
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
    teams_dropped: int = 0  # how many of its teams didn't fit in the account (teams.MAX), for the fan to be told


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
    moved = adopt(db, anon, account_id, session_id) if anon else Moved(0, 0)
    return SignedIn(token=token, session_id=session_id, fan_id=fan, players=moved.players, teams_dropped=moved.teams_dropped)


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


def fan_of(db: Session, account_id: int) -> str | None:
    """The account's fan id: its own channel's (bus.follows_channel), where its devices' open streams hear that its
    sessions ended. None if there's no such account."""
    return db.scalar(select(Account.fan_id).where(Account.id == account_id))


def purge_expired() -> int:
    """Sessions not used for SESSION_LIFE: their devices go too. Returns how many. And what's left under the fan ids
    of accounts deleted since (forget_orphans)."""
    with session() as db:
        forget_orphans(db)
        return len(list(db.scalars(delete(AccountSession).where(AccountSession.expires_at <= func.now()).returning(AccountSession.id))))


# ---------------------------------------------------------------- moving a device's fan into an account


def _lock(db: Session, anon: str, account_id: int) -> str | None:
    """One move at a time per anonymous fan and per account (a sign-in and its straggler job, two devices
    signing in at once, a delete). Returns the account's fan id, or None if it's gone."""
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(anon, 0))))
    return db.scalar(select(Account.fan_id).where(Account.id == account_id).with_for_update())


@dataclass(frozen=True)
class Moved:
    players: int  # how many players came over
    teams_dropped: int  # how many of the device's teams didn't fit (teams.merge)


def adopt(db: Session, anon: str, account_id: int, session_id: int | None) -> Moved:
    """Move everything kept under the anonymous fan id `anon` into the account, in this transaction.
    Doing it again moves only what has appeared since. Its devices become the session's; with that session
    ended (or none), they go instead: their alerts stopped with it. With the account gone, everything under
    `anon` goes, as the account's did: no device holds that fan id now (BUG-099)."""
    fan = _lock(db, anon, account_id)
    if fan is None:
        forget(db, anon)
        return Moved(0, 0)
    if session_id is not None and db.scalar(select(AccountSession.id).where(AccountSession.id == session_id).with_for_update(read=True)) is None:
        session_id = None  # signed out since: held until this commits, so a sign-out under way takes what moved with it
    players = db.scalar(select(func.count()).select_from(Follow).where(Follow.fan_id == anon)) or 0
    # A player both follow keeps the account's alert level, and the earlier follow date: the activity
    # list shows moments since then.
    followed = insert(Follow).from_select(
        ["fan_id", "player_id", "alert_rules", "created_at"],
        select(literal(fan), Follow.player_id, Follow.alert_rules, Follow.created_at).where(Follow.fan_id == anon),
    )
    db.execute(followed.on_conflict_do_update(index_elements=["fan_id", "player_id"], set_={"created_at": func.least(Follow.created_at, followed.excluded.created_at)}))
    db.execute(delete(Follow).where(Follow.fan_id == anon))
    # The games followed by themselves (matches.py), as players are: a game both follow keeps the account's
    # level and the earlier date, and stays followed if either still did. The account follows at most
    # matches.MAX games at once: of this device's others, the soonest that fit come over, and every lapsed one. A
    # game the account let lapse but the device still follows is on again, so it needs room too (BUG-099).
    have = set(db.scalars(select(MatchFollow.event_id).where(MatchFollow.fan_id == fan, MatchFollow.until.is_(None))))
    room = matches.MAX - len(have)
    moving = []
    for follow_id, event_id, until in db.execute(select(MatchFollow.id, MatchFollow.event_id, MatchFollow.until).join(Event, Event.id == MatchFollow.event_id)
                                                 .where(MatchFollow.fan_id == anon).order_by(Event.start_utc.asc().nulls_last(), MatchFollow.id)):
        counts = until is None and event_id not in have  # a game it doesn't follow yet, still on
        if not counts or room > 0:
            moving.append(follow_id)
            room -= 1 if counts else 0
    games = insert(MatchFollow).from_select(
        ["fan_id", "event_id", "alert_rules", "keys", "created_at", "until"],
        select(literal(fan), MatchFollow.event_id, MatchFollow.alert_rules, MatchFollow.keys, MatchFollow.created_at, MatchFollow.until).where(MatchFollow.id.in_(moving)),
    )
    db.execute(games.on_conflict_do_update(index_elements=["fan_id", "event_id"], set_={
        "created_at": func.least(MatchFollow.created_at, games.excluded.created_at),
        "until": case((or_(MatchFollow.until.is_(None), games.excluded.until.is_(None)), None), else_=func.greatest(MatchFollow.until, games.excluded.until)),
    }))
    db.execute(delete(MatchFollow).where(MatchFollow.fan_id == anon))
    teams_dropped = teams.merge(db, anon, fan)  # the teams followed (teams.py), as players are, at most teams.MAX
    fansports.merge(db, anon, fan)  # the sports they watch (fansports.py): the account's first, then the device's others
    predictions.merge(db, anon, fan)  # the games called (predictions.py): a question both picked keeps the account's pick
    clips.merge(db, anon, fan)  # clips reported (clips.py): one each
    muted = insert(NotificationMute).from_select(
        ["fan_id", "event_id", "created_at"],
        select(literal(fan), NotificationMute.event_id, NotificationMute.created_at).where(NotificationMute.fan_id == anon),
    )
    db.execute(muted.on_conflict_do_nothing())
    db.execute(delete(NotificationMute).where(NotificationMute.fan_id == anon))
    # What the fan hid from players' feeds (feed.py): an item both hid keeps the account's word on it.
    hid = insert(FeedFeedback).from_select(
        ["fan_id", "player_id", "item_id", "verdict", "created_at"],
        select(literal(fan), FeedFeedback.player_id, FeedFeedback.item_id, FeedFeedback.verdict, FeedFeedback.created_at).where(FeedFeedback.fan_id == anon),
    )
    db.execute(hid.on_conflict_do_nothing())
    db.execute(delete(FeedFeedback).where(FeedFeedback.fan_id == anon))
    # Where the fan first came from, unless the account already says (an earlier device's first link).
    came = insert(FanSource).from_select(
        ["fan_id", "tag", "utm_source", "utm_medium", "utm_campaign", "landing", "created_at"],
        select(literal(fan), FanSource.tag, FanSource.utm_source, FanSource.utm_medium, FanSource.utm_campaign, FanSource.landing, FanSource.created_at).where(FanSource.fan_id == anon),
    )
    db.execute(came.on_conflict_do_nothing(index_elements=["fan_id"]))
    db.execute(delete(FanSource).where(FanSource.fan_id == anon))
    # The push log the noise rules read (how often this phone buzzed, the game's story so far).
    db.execute(update(Notification).where(Notification.fan_id == anon).values(fan_id=fan))
    # This device's alerts: now the account's, and this session's. The account's oldest devices make room.
    if session_id is None:
        db.execute(delete(PushSubscription).where(PushSubscription.fan_id == anon))
        return Moved(players, teams_dropped)
    devices = list(db.scalars(update(PushSubscription).where(PushSubscription.fan_id == anon).values(fan_id=fan, session_id=session_id).returning(PushSubscription.id)))
    if devices:
        notify.trim_devices(db, fan, PushSubscription.id.in_(devices), notify.MAX_DEVICES - len(devices))
    return Moved(players, teams_dropped)


def adopt_later(anon: str, account_id: int, session_id: int) -> int:
    """A minute after a sign-in: what a request already under way then wrote to the anonymous fan, which no device
    holds now. It comes into the account even if the device signed out since (BUG-099). Returns how many players."""
    with session() as db:
        return adopt(db, anon, account_id, session_id).players


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
    forget(db, account.fan_id)
    db.execute(delete(Account).where(Account.id == account_id))  # its sessions go with it
    return Deleted(fan_id=account.fan_id, email=account.email, token_hashes=hashes)


def forget(db: Session, fan: str) -> None:
    """Every row kept under a fan id."""
    for model in FAN_ROWS:
        db.execute(delete(model).where(model.fan_id == fan))
    predictions.forget(db, fan)  # picks and places in leagues; a league they made stays its other members'


def forget_orphans(db: Session) -> int:
    """What's kept under the fan id of an account that's gone: a write another of its devices had under way as it was
    deleted, with the fan id it had read just before, lands after the deletion (BUG-223). Returns how many fan ids."""
    gone = set()
    for model in (*FAN_ROWS, Pick, LeagueMember):
        gone.update(db.scalars(select(model.fan_id).distinct().where(model.fan_id.startswith(FAN_PREFIX, autoescape=True),
                                                                     ~select(Account.id).where(Account.fan_id == model.fan_id).exists())))
    for fan in gone:
        forget(db, fan)
    return len(gone)
