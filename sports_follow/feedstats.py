"""What the feed's metrics and the admin panel's channel yield need that the pipeline doesn't keep (BHA-326):
whether we get enough candidates, and where they're lost.

- The funnel, counted once for each player and video (keep_counted): a trusted channel's latest uploads are read
  again every poll, so only what's new to a player counts as matched, and is then kept or refused for a reason
  (REFUSALS). A moment's search is a fresh read each time: what it finds that's stored already, or was refused
  lately, counts too, as already_stored or refused_before, which is what the search spent for nothing.
- Each trusted channel's yield (read_channel, channel_yield): its uploads of the last YIELD_DAYS, and those naming
  a followed player, as sets in Redis by the day each was published, kept a day longer than that; the latest
  upload it was seen with. Kept and served are counted from the candidates themselves. For the admin's Feed tab:
  a channel that posts nothing, or nothing about anyone followed, is one to drop; a sport without one is a gap.
- Coverage (coverage, report_coverage): for each running sport, its followed players and those with a video the
  Feed would serve, published in the last COVERED_DAYS (or pinned), worked out once a poll by the curator.

Labels are sport, stream and reason: never a channel or a player (metrics.py).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import distinct, exists, func, select
from sqlalchemy.orm import Session

from . import bus, metrics, sports
from .adapters import NEWS, Upload, VideoFacts
from .models import FeedCandidate, Follow, Player

if TYPE_CHECKING:
    from .candidates import Found

log = logging.getLogger("sports_follow.feedstats")

YIELD_DAYS = 30  # a channel's yield covers its uploads published this many days back
COVERED_DAYS = 7  # a player is covered while a servable video about them was published this recently
_KEY = "feed:yield"


# ---------------------------------------------------------------- the funnel


def reason(why: str, facts: VideoFacts | None) -> str:
    """A hard filter's refusal (candidates.refused) as the funnel labels it (metrics.REFUSALS)."""
    from .candidates import MAX_SECONDS

    if why == "length":
        return "too_long" if facts is not None and (facts.seconds or 0) > MAX_SECONDS else "too_short"
    if why.startswith("blocked"):
        return "blocked"
    return why.replace(" ", "_")


def keep_counted(stream: str, found: list[Found], have: set[tuple[int, str]], skip: Iterable[str] = (), why: dict[str, str] | None = None,
                 facts: dict[str, VideoFacts] | None = None, kept: Iterable[str] = ()) -> None:
    """Count what candidates.keep did with what was found: each player and video matched, why the ones not kept
    weren't, and the sports of those kept. have: the (player, video) pairs stored already; skip: the videos
    refused lately (remembered in Redis); why: the hard filters' refusals of the rest; kept: a sport for each row
    stored."""
    skip, why, facts = set(skip), why or {}, facts or {}
    first = [f for f in found if (f.player_id, f.upload.video_id) not in have and f.upload.video_id not in skip]
    lost = [(f.sport, reason(why[f.upload.video_id], facts.get(f.upload.video_id))) for f in first if f.upload.video_id in why]
    if stream == "search":
        for f in found:
            if (f.player_id, f.upload.video_id) in have:
                first.append(f)
                lost.append((f.sport, "already_stored"))
            elif f.upload.video_id in skip:
                first.append(f)
                lost.append((f.sport, "refused_before"))
    metrics.feed_step("matched", stream, (f.sport for f in first))
    metrics.feed_refused(stream, lost)
    metrics.feed_step("kept", stream, kept)


# ---------------------------------------------------------------- each trusted channel's yield


def _day(at: datetime | None, now: datetime) -> str:
    return (at or now).astimezone(timezone.utc).strftime("%Y-%m-%d")


def _set(channel: str, day: str, what: str) -> str:
    return f"{_KEY}:{channel}:{day}:{what}"


def read_channel(channel: str, listed_by: Iterable[str], uploads: list[Upload], found: list[Found], now: datetime | None = None) -> int:
    """Note a channel's latest uploads and those naming a followed player (found), and count the uploads new to us
    for each sport that lists the channel (listed_by: candidates.channels' keys, a group's left out where one of
    its sports is there) as metrics.feed_step "uploads". Uploads published over YIELD_DAYS ago are old news, and
    left out. Returns how many were new; 0 without Redis."""
    now = now or datetime.now(timezone.utc)
    recent = [u for u in uploads if u.published is None or u.published >= now - timedelta(days=YIELD_DAYS)]
    named = {f.upload.video_id for f in found}
    try:
        pipe = bus.sync_redis().pipeline()
        adds = 0
        for u in recent:
            day = _day(u.published, now)
            ends = int((datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=YIELD_DAYS + 1)).timestamp())
            for what in ("read", "matched") if u.video_id in named else ("read",):
                pipe.sadd(_set(channel, day, what), u.video_id)
                pipe.expireat(_set(channel, day, what), ends)
                adds += what == "read"
        latest = max((u.published for u in uploads if u.published), default=None)
        if latest is not None:
            pipe.hset(f"{_KEY}:last", channel, latest.isoformat())
        answers = cast(list[Any], pipe.execute())
    except Exception as exc:  # noqa: BLE001 — without Redis the yield waits, and the poll goes on
        log.debug("feed: %s's yield not noted: %s", channel, exc)
        return 0
    # The answers come in the order asked: each upload's read SADD (1: new), its EXPIREAT, then its matched pair.
    new, i = 0, 0
    for u in recent:
        new += int(answers[i] or 0)
        i += 4 if u.video_id in named else 2
    keys = set(listed_by)
    running = {s.key for s in sports.ENABLED}
    for sport in sorted(keys & running) or sorted(keys)[:1] or ["other"]:
        metrics.feed_step("uploads", "channels", [sport] * new)
    return new


def channel_yield(session: Session, channels: Iterable[str], now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Each channel's last YIELD_DAYS: uploads read, those naming a followed player, kept (videos stored for
    anyone) and served (of those, any the Feed shows), and when its latest upload was published. A channel
    nothing is known of has zeros and no latest upload."""
    now = now or datetime.now(timezone.utc)
    wanted = sorted({c for c in channels if c})
    out: dict[str, dict[str, Any]] = {c: {"read": 0, "matched": 0, "kept": 0, "served": 0, "last_upload": None} for c in wanted}
    if not wanted:
        return out
    since = now - timedelta(days=YIELD_DAYS)
    cid = FeedCandidate.candidate["channel_id"].astext
    servable = FeedCandidate.score.is_not(None) & FeedCandidate.review.is_distinct_from("hidden")
    for channel, kept, served in session.execute(
        select(cid, func.count(distinct(FeedCandidate.external_id)), func.count(distinct(FeedCandidate.external_id)).filter(servable))
        .where(cid.in_(wanted), FeedCandidate.fetched_at >= since).group_by(cid)
    ):
        out[channel].update(kept=kept, served=served)
    days = [_day(now - timedelta(days=n), now) for n in range(YIELD_DAYS + 1)]
    try:
        pipe = bus.sync_redis().pipeline()
        for channel in wanted:
            for day in days:
                pipe.scard(_set(channel, day, "read"))
                pipe.scard(_set(channel, day, "matched"))
        pipe.hmget(f"{_KEY}:last", wanted)
        answers = cast(list[Any], pipe.execute())
    except Exception as exc:  # noqa: BLE001 — the panel shows what the database knows
        log.info("feed: channel yield not read from Redis: %s", exc)
        return out
    step = 2 * len(days)
    for n, channel in enumerate(wanted):
        theirs = answers[n * step:(n + 1) * step]
        out[channel].update(read=sum(int(x or 0) for x in theirs[0::2]), matched=sum(int(x or 0) for x in theirs[1::2]))
    for channel, last in zip(wanted, answers[-1] or []):
        out[channel]["last_upload"] = last or None
    return out


# ---------------------------------------------------------------- coverage


def coverage(session: Session, now: datetime | None = None) -> dict[str, tuple[int, int]]:
    """For each running sport (metrics.sport_label), how many players fans follow, and how many of them have a
    video the Feed would serve (scored, not hidden) published in the last COVERED_DAYS, or pinned."""
    from .rank import when

    now = now or datetime.now(timezone.utc)
    sport_of = {pid: metrics.sport_label(sport) for pid, sport in session.execute(
        select(Player.id, Player.sport).where(exists().where(Follow.player_id == Player.id))) if sports.on(sport)}
    covered: set[int] = set()
    if sport_of:
        rows = session.execute(
            select(FeedCandidate.player_id, FeedCandidate.candidate["published"].astext, FeedCandidate.review)
            .where(FeedCandidate.player_id.in_(sorted(sport_of)), FeedCandidate.source.not_in(list(NEWS)), FeedCandidate.score.is_not(None),
                   FeedCandidate.review.is_distinct_from("hidden")))
        for pid, published, review in rows:
            at = when(published)
            if review == "pinned" or (at is not None and at >= now - timedelta(days=COVERED_DAYS)):
                covered.add(pid)
    out: dict[str, tuple[int, int]] = {}
    for pid, sport in sport_of.items():
        followed, have = out.get(sport, (0, 0))
        out[sport] = (followed + 1, have + (pid in covered))
    return out


def report_coverage(now: datetime | None = None) -> dict[str, int]:
    """Work coverage out (once a poll, curator.rank_new) for the metrics to report until the next. Returns the
    totals, for the poll's log line."""
    from . import db

    with db.session() as session:
        found = coverage(session, now)
    metrics.feed_coverage(found)
    return {"followed": sum(f for f, _ in found.values()), "covered": sum(c for _, c in found.values())}
