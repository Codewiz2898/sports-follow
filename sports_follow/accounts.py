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
from typing import Any

from sqlalchemy import ColumnElement, SQLColumnExpression, case, delete, func, null, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import clips, fansports, follows, matches, notify, predictions, sports, teams
from .db import session
from .models import Account, AccountSession, ClipReport, Event, FanSource, FanSport, FeedFeedback, Follow, LeagueMember, MatchFollow, Notification, NotificationMute, Pick, Player, PushSubscription, TeamFollow

FAN_PREFIX = "acct-"  # an account's fan id: never taken from a fan cookie (server._unsigned_fan)
# The rows kept under a fan id here; predictions.py keeps its own (picks, places in leagues: predictions.forget).
FAN_ROWS = (Follow, MatchFollow, TeamFollow, FanSport, NotificationMute, Notification, PushSubscription, FanSource, FeedFeedback, ClipReport)
SESSION_COOKIE = "sf_session"
SESSION_LIFE = timedelta(days=365)  # since the device last opened the app
COOKIE_MAX_AGE = 400 * 24 * 3600  # browsers keep a cookie at most 400 days; the app renews it daily
BUMP_EVERY = timedelta(days=1)
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")
GMAIL = ("gmail.com", "googlemail.com")  # one mailbox, where dots in an address make no difference (inbox)
STANDING_AFTER = timedelta(days=1)  # an account this old has standing: the sign-in codes kept for accounts are for it


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
    an invisible character in it isn't (BUG-096). Its +tag stays: me+1@ is an account of its own (inbox counts it)."""
    email = str(raw or "").strip().lower()
    return email if len(email) <= 254 and email.isprintable() and EMAIL.fullmatch(email) else None


def inbox(email: str) -> str:
    """The mailbox an address (normal_email's) reaches, for counting accounts, never for who one is: its +tag dropped
    (me+1@ and me+2@ reach me@), and on Gmail its dots too, googlemail.com being gmail.com. One inbox can make any
    number of accounts so, so whatever counts accounts counts inboxes (BUG-296). inbox_of is its SQL twin."""
    local, _, domain = email.partition("@")
    local = local.split("+", 1)[0] or local  # nothing before the tag: the address as it is
    if domain in GMAIL:
        return f"{local.replace('.', '')}@gmail.com"
    return f"{local}@{domain}"


def inbox_of(email: SQLColumnExpression[str | None]) -> ColumnElement[str | None]:
    """inbox, in SQL: the inbox of the address in a column, for a query that counts fans (models.followers,
    starters._most_followed); NULL for none, as an outer join's row with no account has."""
    local, domain = func.split_part(email, "@", 1), func.split_part(email, "@", 2)
    untagged = func.coalesce(func.nullif(func.split_part(local, "+", 1), ""), local)
    return case((email.is_(None), null()), (domain.in_(GMAIL), func.concat(func.replace(untagged, ".", ""), "@gmail.com")),
                else_=func.concat(untagged, "@", domain))


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


def standing(db: Session, email: str) -> bool:
    """Whether this address has an account that has stood a day (STANDING_AFTER): part of the day's sign-in codes is
    kept for those (server.email_start). Not any account: one made minutes before, for it, could spend them (BUG-093).
    Every account signs in as it's made, so one that has stood a day is one that signed in before today's."""
    made = db.scalar(select(Account.created_at).where(Account.email == email))
    return made is not None and made <= datetime.now(timezone.utc) - STANDING_AFTER


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
    players_dropped: int = 0  # and of its players (follows.MAX)
    games_dropped: int = 0  # and of the games it followed (matches.MAX)


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
    return SignedIn(token=token, session_id=session_id, fan_id=fan, players=moved.players, teams_dropped=moved.teams_dropped,
                    players_dropped=moved.players_dropped, games_dropped=moved.games_dropped)


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
    players_dropped: int = 0  # and of its players (follows.MAX)
    games_dropped: int = 0  # and of its games (matches.MAX)


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
    # Each kind of row is taken off `anon` in one statement (_take) and then put under the account: a row a request
    # under way writes to `anon` as this runs is left there for the next move (adopt_later), never deleted unmoved.
    # A player both follow keeps the account's alert level, and the earlier follow date: the activity
    # list shows moments since then. The account follows at most follows.MAX players: of this device's others, the
    # earliest followed that fit come over, and a turned-off sport's, which take no room (follows.counted). One at a
    # time with the account's own follows (server._room_to_follow's lock), so one on another device can't pass it.
    _lock_follows(db, "follow", fan)
    have = set(db.scalars(select(Follow.player_id).where(Follow.fan_id == fan)))
    room, players_dropped = follows.MAX - follows.counted(db, fan), 0
    followed = _take(db, Follow, anon, fan, Follow.id, Follow.player_id, Follow.alert_rules, Follow.created_at)
    sport_of = dict(db.execute(select(Player.id, Player.sport).where(Player.id.in_([f["player_id"] for f in followed]))).all()) if followed else {}
    moving = []
    for row in sorted(followed, key=lambda f: (f["created_at"], f["id"])):
        del row["id"]
        if row["player_id"] not in sport_of:
            continue  # the player was removed as this ran
        counts = row["player_id"] not in have and sports.on(sport_of[row["player_id"]])  # one it doesn't follow yet, in sight
        if not counts or room > 0:
            moving.append(row)
            room -= 1 if counts else 0
        else:
            players_dropped += 1
    players = len(moving)
    if moving:
        moved = insert(Follow).values(moving)
        db.execute(moved.on_conflict_do_update(index_elements=["fan_id", "player_id"], set_={"created_at": func.least(Follow.created_at, moved.excluded.created_at)}))
    # The games followed by themselves (matches.py), as players are: a game both follow keeps the account's
    # level and the earlier date, and stays followed if either still did. The account follows at most
    # matches.MAX games at once: of this device's others, the soonest that fit come over, and every lapsed one. A
    # game the account let lapse but the device still follows is on again, so it needs room too (BUG-099). Those that
    # don't fit are counted for the fan, and the account's own follows wait (matches.follow's lock), as with players.
    _lock_follows(db, "match-follow", fan)
    have = set(db.scalars(select(MatchFollow.event_id).where(MatchFollow.fan_id == fan, MatchFollow.until.is_(None))))
    room, games_dropped = matches.MAX - len(have), 0
    games = _take(db, MatchFollow, anon, fan, MatchFollow.id, MatchFollow.event_id, MatchFollow.alert_rules, MatchFollow.keys, MatchFollow.created_at, MatchFollow.until)
    starts = dict(db.execute(select(Event.id, Event.start_utc).where(Event.id.in_([g["event_id"] for g in games]))).all()) if games else {}
    unknown = datetime.max.replace(tzinfo=timezone.utc)  # a game with no start time yet: after every other
    moving = []
    for game in sorted(games, key=lambda g: (starts.get(g["event_id"]) or unknown, g["id"])):
        del game["id"]
        counts = game["until"] is None and game["event_id"] not in have  # a game it doesn't follow yet, still on
        if not counts or room > 0:
            moving.append(game)
            room -= 1 if counts else 0
        else:
            games_dropped += 1
    if moving:
        moved = insert(MatchFollow).values(moving)
        db.execute(moved.on_conflict_do_update(index_elements=["fan_id", "event_id"], set_={
            "created_at": func.least(MatchFollow.created_at, moved.excluded.created_at),
            "until": case((or_(MatchFollow.until.is_(None), moved.excluded.until.is_(None)), None), else_=func.greatest(MatchFollow.until, moved.excluded.until)),
        }))
    teams_dropped = teams.merge(db, anon, fan)  # the teams followed (teams.py), as players are, at most teams.MAX
    fansports.merge(db, anon, fan)  # the sports they watch (fansports.py): the account's first, then the device's others
    predictions.merge(db, anon, fan)  # the games called (predictions.py): a question both picked keeps the account's pick
    clips.merge(db, anon, fan)  # clips reported (clips.py): one each
    muted = _take(db, NotificationMute, anon, fan, NotificationMute.event_id, NotificationMute.created_at)
    if muted:
        db.execute(insert(NotificationMute).values(muted).on_conflict_do_nothing())
    # What the fan hid from players' feeds (feed.py): an item both hid keeps the account's word on it.
    hid = _take(db, FeedFeedback, anon, fan, FeedFeedback.player_id, FeedFeedback.item_id, FeedFeedback.verdict, FeedFeedback.created_at)
    if hid:
        db.execute(insert(FeedFeedback).values(hid).on_conflict_do_nothing())
    # Where the fan first came from, unless the account already says (an earlier device's first link).
    came = _take(db, FanSource, anon, fan, FanSource.tag, FanSource.utm_source, FanSource.utm_medium, FanSource.utm_campaign, FanSource.landing, FanSource.created_at)
    if came:
        db.execute(insert(FanSource).values(came).on_conflict_do_nothing(index_elements=["fan_id"]))
    # The push log the noise rules read (how often this phone buzzed, the game's story so far).
    db.execute(update(Notification).where(Notification.fan_id == anon).values(fan_id=fan))
    # This device's alerts: now the account's, and this session's. The account's oldest devices make room.
    if session_id is None:
        db.execute(delete(PushSubscription).where(PushSubscription.fan_id == anon))
        return Moved(players, teams_dropped, players_dropped, games_dropped)
    devices = list(db.scalars(update(PushSubscription).where(PushSubscription.fan_id == anon).values(fan_id=fan, session_id=session_id).returning(PushSubscription.id)))
    if devices:
        notify.trim_devices(db, fan, PushSubscription.id.in_(devices), notify.MAX_DEVICES - len(devices))
    return Moved(players, teams_dropped, players_dropped, games_dropped)


def _lock_follows(db: Session, kind: str, fan: str) -> None:
    """The lock the fan's own follows of this kind take ("follow": server._room_to_follow, "match-follow":
    matches.follow), held till this commits: their count and the move's can't both pass the cap."""
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"{kind}:{fan}", 0))))


def _take(db: Session, model: Any, anon: str, fan: str, *columns: Any) -> list[dict[str, Any]]:
    """The rows kept under `anon` in this table, taken off it in one statement (DELETE … RETURNING these columns), each
    ready to go under the account's fan id. A row written to `anon` as the move runs isn't among them, so it isn't
    deleted either: copying then deleting in two statements deleted it unmoved (BUG-327)."""
    return [{**row._mapping, "fan_id": fan} for row in db.execute(delete(model).where(model.fan_id == anon).returning(*columns))]


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
