"""Telling fans about their calls (predictions.py, BHA-321): "Make your pick" before a campaign's game, and how
each pick went once its question settles, with the fan's place in their league.

The reminder goes once a game, LEAD before its first question closes (and not once there's under LAST left), to the
fans who follow a player in it, a team in it or the game itself and haven't picked; only for a campaign's games
(campaigns.player_ids: a game of a campaign's players near one on its schedule): the big events fans came for, not
every board a followed player sits at. How a pick went
goes once a question settles, to every fan who picked it: right or not, the points, and their best place among their
leagues. A voided question tells no one.

Each is a push as a moment's is (notify.deliver: Web Push, and FCM for the Android app), logged as a notification row
(kind "pick" or "called") that the open rate is measured from; and as each fan set it: nothing from a game they muted,
nothing about a game whose players they all set to "off", no reminder where they hear only results; a device's quiet
hours make it silent, not dropped. A game's pushes share one tag, so a series' maps replace each other on the phone,
the first of them in LOUD_GAP loud and the rest silent. The worker runs it every minute (worker.prediction_pushes);
each question is marked as it's sent (reminded_at, told_at), so a second worker, or a run after a restart, sends
nothing twice.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from urllib.parse import urlparse

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import campaigns, logs, metrics, notify, predictions, sports
from .db import session
from .models import Event, EventPlayer, EventTeam, Follow, League, LeagueMember, MatchFollow, Notification, NotificationMute, Pick, PushSubscription, Question, TeamFollow
from .moments import DEFAULT_LEVEL
from .sports.base import ordinal

log = logging.getLogger("sports_follow.predictions_push")

LEAD = timedelta(hours=2)  # the reminder goes this long before a game's first question closes
LAST = timedelta(minutes=15)  # and not once there's less than this left
FRESH = timedelta(hours=6)  # how a pick went isn't told of a question settled longer ago (a worker down that long)
LOUD_GAP = timedelta(minutes=20)  # a call push this soon after the last one about the same game is silent
RESULT_TTL = 6 * 3600  # seconds a push service may hold how a pick went for a phone that's off
HEARS = ("everything", "key")  # the levels that hear a reminder: not "results", not "off"
_wire = ThreadPoolExecutor(max_workers=8, thread_name_prefix="call-push")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run(now: datetime | None = None) -> int:
    """One run (worker.prediction_pushes): the reminders due and the results settled since the last. Returns how many
    devices took a push."""
    now = now or _now()
    return _send(remind(now)) + _send(tell(now))


# ---------------------------------------------------------------- who hears


def _events(db: Session, q: Question) -> list[int]:
    """Every event the question's game is here as (a chess board is one per player)."""
    ids = set(db.scalars(select(Event.id).where(Event.key.in_(q.keys or [])))) if q.keys else set()
    return sorted(ids | ({q.event_id} if q.event_id is not None else set()))


def _levels(db: Session, event_ids: list[int]) -> dict[str, set[str]]:
    """Each fan who follows a player, a team or the game itself in these events, with the levels they chose for them."""
    out: dict[str, set[str]] = {}
    rows = [
        *db.execute(select(Follow.fan_id, Follow.alert_rules).join(EventPlayer, EventPlayer.player_id == Follow.player_id).where(EventPlayer.event_id.in_(event_ids))),
        *db.execute(select(MatchFollow.fan_id, MatchFollow.alert_rules).where(MatchFollow.event_id.in_(event_ids), MatchFollow.until.is_(None))),
        *db.execute(select(TeamFollow.fan_id, TeamFollow.alert_rules).join(EventTeam, EventTeam.team_id == TeamFollow.team_id).where(EventTeam.event_id.in_(event_ids))),
    ]
    for fan, rules in rows:
        out.setdefault(fan, set()).add((rules or {}).get("level", DEFAULT_LEVEL))
    return out


def _muted(db: Session, fans: set[str], event_ids: list[int]) -> set[str]:
    if not fans or not event_ids:
        return set()
    return set(db.scalars(select(NotificationMute.fan_id).where(NotificationMute.fan_id.in_(fans), NotificationMute.event_id.in_(event_ids))))


def _places(db: Session, fans: set[str]) -> dict[str, tuple[int, str]]:
    """Each fan's best place among their leagues, and that league's name; each league's table read once."""
    out: dict[str, tuple[int, str]] = {}
    member_of: dict[int, set[str]] = {}
    for league_id, fan in db.execute(select(LeagueMember.league_id, LeagueMember.fan_id).where(LeagueMember.fan_id.in_(fans))) if fans else []:
        member_of.setdefault(league_id, set()).add(fan)
    for league in db.scalars(select(League).where(League.id.in_(list(member_of)))) if member_of else []:
        for row in predictions.table(db, league):
            if row["fan"] in member_of[league.id] and (row["fan"] not in out or row["place"] < out[row["fan"]][0]):
                out[row["fan"]] = (row["place"], league.name)
    return out


# ---------------------------------------------------------------- "Make your pick"


def _until(locks_at: datetime, now: datetime) -> str:
    """How long till picks close: "80 minutes", "1 hour 40 minutes", "2 hours"."""
    minutes = max(1, round((locks_at - now).total_seconds() / 60))
    if minutes < 90:
        return f"{minutes} minutes"
    hours, rest = divmod(minutes, 60)
    whole = f"{hours} hour{'s' if hours > 1 else ''}"
    return f"{whole} {rest} minute{'s' if rest > 1 else ''}" if rest else whole


def remind(now: datetime) -> list[tuple]:
    """The reminders due now, logged: one job per device (see _jobs). Each question due is marked as reminded,
    whoever heard; but not one whose game is postponed (predictions.held), which waits: back on at its time, it's
    reminded then, and its new date reminds again anyway (predictions.moved)."""
    jobs: list[tuple] = []
    with session() as db:
        due = list(db.scalars(select(Question).where(Question.status == predictions.OPEN, Question.reminded_at.is_(None), Question.locks_at > now + LAST,
                                                     Question.locks_at <= now + LEAD).with_for_update(skip_locked=True)))
        on_hold = predictions.held(db, due)
        for q in due:
            if q.id in on_hold:
                continue
            q.reminded_at = now
            ids = _events(db, q)
            theirs = campaigns.player_ids(db, q.locks_at or now)
            if not sports.on(q.sport) or not ids or not theirs:
                continue
            if db.scalar(select(EventPlayer.event_id).where(EventPlayer.event_id.in_(ids), EventPlayer.player_id.in_(theirs)).limit(1)) is None:
                continue  # not a campaign's game
            levels = _levels(db, ids)
            picked = set(db.scalars(select(Pick.fan_id).where(Pick.question_id == q.id)))
            muted = _muted(db, set(levels), ids)
            fans = [f for f, said in levels.items() if f not in picked and f not in muted and said & set(HEARS)]
            locks_at = q.locks_at or now
            jobs += _jobs(db, fans, q, "pick", f"Make your pick: {q.game_title}", f"{q.title} Picks close when it starts, in {_until(locks_at, now)}.",
                          loud={f: True for f in fans}, ttl=max(60, int((locks_at - now).total_seconds())), now=now)
    return jobs


# ---------------------------------------------------------------- how a pick went


def _label(q: Question, key: str | None) -> str:
    return next((o["label"] for o in q.options or [] if o["key"] == key), "")


def tell(now: datetime) -> list[tuple]:
    """How each pick went, for the questions settled since the last run, oldest first (a game's pushes replace each
    other: the latest stays), logged: one job per device. A voided question is marked and tells no one; settled
    again (predictions.score clears its mark), it's told then."""
    jobs: list[tuple] = []
    with session() as db:
        due = list(db.scalars(select(Question).where(Question.status.in_((predictions.SETTLED, predictions.VOIDED)), Question.told_at.is_(None),
                                                     Question.settled_at >= now - FRESH).order_by(Question.settled_at, Question.position).with_for_update(skip_locked=True)))
        for q in due:
            q.told_at = now
            if q.status != predictions.SETTLED or not sports.on(q.sport):
                continue
            picks = {fan: (option, points) for fan, option, points in db.execute(select(Pick.fan_id, Pick.option, Pick.points).where(Pick.question_id == q.id))}
            if not picks:
                continue
            ids = _events(db, q)
            levels = _levels(db, ids)
            muted = _muted(db, set(picks), ids)
            # Hears it unless they muted the game, or set every follow of theirs in it to "off".
            fans = [f for f in picks if f not in muted and not (f in levels and levels[f] <= {"off"})]
            places = _places(db, set(fans))
            recent = set(db.scalars(select(Notification.fan_id).where(Notification.fan_id.in_(fans), Notification.kind.in_(notify.CALLS),
                                                                      Notification.event_id.in_(ids), Notification.created_at >= now - LOUD_GAP))) if fans and ids else set()
            answer = _label(q, q.answer)
            for fan in fans:
                option, points = picks[fan]
                place = places.get(fan)
                where = f" · {ordinal(place[0])} in {place[1]}" if place else ""
                if option == q.answer:
                    title, body = f"You got it right: {answer}", f"{q.title} {q.game_title} · +{points or 0} points{where}"
                else:
                    title, body = f"{answer}: not this time", f"You said {_label(q, option)}. {q.title} {q.game_title}{where}"
                jobs += _jobs(db, [fan], q, "called", title, body, loud={fan: fan not in recent}, ttl=RESULT_TTL, now=now)
    return jobs


# ---------------------------------------------------------------- sending


def _jobs(db: Session, fans: list[str], q: Question, kind: str, title: str, body: str, loud: dict[str, bool], ttl: int, now: datetime) -> list[tuple]:
    """A notification row for each fan with a device, and a job for each of their newest devices: (notification id,
    device, payload, ttl, topic). The tag a phone replaces the game's last call with is the game's; the Web Push topic
    (RFC 8030: at most 32 characters, URL-safe base64) is a digest of it."""
    if not fans:
        return []
    devices: dict[str, list[PushSubscription]] = {}
    for d in db.scalars(select(PushSubscription).where(PushSubscription.fan_id.in_(fans), PushSubscription.transport.in_(notify.transports())).order_by(PushSubscription.id.desc())):
        mine = devices.setdefault(d.fan_id, [])
        if len(mine) < notify.MAX_DEVICES:
            mine.append(d)
    tag = f"call-{q.game}"
    topic = "c" + base64.urlsafe_b64encode(hashlib.blake2b(q.game.encode(), digest_size=15).digest()).decode()  # 1 + 20 characters
    jobs = []
    for fan, theirs in devices.items():
        row = Notification(fan_id=fan, event_id=q.event_id, player_id=None, moment_ids=[], kind=kind, lines=[title], loud=loud.get(fan, True), devices=len(theirs), delivered=0, created_at=now)
        db.add(row)
        db.flush()
        payload = {"id": row.id, "title": title, "body": body, "url": f"/match/{q.match}", "tag": tag, "event_id": q.event_id, "kind": kind, "loud": row.loud}
        for d in theirs:
            jobs.append((row.id, (d.id, d.transport, d.endpoint, d.p256dh, d.auth), {**payload, "quiet": notify.quiet(d.timezone, d.quiet_start, d.quiet_end, now)}, ttl, topic))
    return jobs


def _send(jobs: list[tuple]) -> int:
    """Deliver logged pushes (after their rows are committed), then write back what the push services said: each
    notification's deliveries, each device's health. Returns how many devices took one."""
    if not jobs:
        return 0
    futures = [(notification_id, device, _wire.submit(logs.carried(notify.deliver), device, payload, ttl, topic)) for notification_id, device, payload, ttl, topic in jobs]
    results = [(notification_id, device[0], future.result()) for notification_id, device, future in futures]
    # On the Notifications dashboard with moments' pushes (deploy/grafana/): each device's push service and its answer.
    metrics.pushes([("fcm" if device[1] == "fcm" else urlparse(device[2]).hostname, error) for (_, device, _), (_, _, error) in zip(futures, results)])
    notify.settle([(device_id, error) for _, device_id, error in results])
    delivered: dict[int, int] = {}
    for notification_id, _, error in results:
        delivered[notification_id] = delivered.get(notification_id, 0) + (error is None)
    with session() as db:
        for notification_id, n in delivered.items():
            db.execute(update(Notification).where(Notification.id == notification_id).values(delivered=n))
    sent = sum(1 for _, _, error in results if error is None)
    log.info("calls: %d push(es) to %d of %d device(s)", len(delivered), sent, len(results), extra={"pushes": len(delivered), "devices": len(results), "delivered": sent})
    return sent

