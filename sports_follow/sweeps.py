"""The feed's sweeps (BHA-336): searches for videos about a followed player that no moment asked for. A trusted
channel's feed holds only its latest fifteen uploads (candidates.py), and a moment's search needs a big moment our
sources fire (searches.py), so a player who's had neither this week has nothing to watch. Two searches find them
some, from any channel, on the video sources that can search (searches.searching), once a day (curator.sweep):

- The gap search: each followed player with nothing the Feed would serve published in the last week
  (feedstats.covered), searched for by their name and their sport's words (Sport.video_words), for videos of the
  last GAP_DAYS, most watched first. The most followed go first, up to FEED_GAP_SEARCHES in any 24 hours; one
  whose last gap search kept nothing waits GAP_BACKOFF before the next, so a cap that binds moves on to others.
- The famous sweep: each followed player's most watched videos ever, with no date limit, for "The ones everyone's
  seen" (rank.evergreen). Each player at most once in FAMOUS_EVERY, and FEED_FAMOUS_SEARCHES in any 7 days, no
  more than a seventh of them a day: the players due are spread over the week, the most followed first.

What either finds goes the way a moment search's does: kept where it names the player and passes the hard filters
(candidates.keep), unless it's stored for them already (counted as already_stored: units spent for nothing), then
judged and ranked with the rest (curator.rank_new). Each search a source answered is a row (FeedSweep) with what it
found and kept, and each candidate it kept names it, which says what it served (report). In the feed's funnel each
is a stream of its own, "gap" and "famous".

Moment searches come first: sweeps stop while less than KEEP_SHARE of a source's tightest window is left, twice what
moment searches leave. With no source that can search running (no key), nothing runs.
"""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import candidates, db, feedstats, metrics, searches, shutdown, sports
from .adapters import AdapterError, VideoSource, budget
from .config import FEED_COUNTRY, FEED_FAMOUS_SEARCHES, FEED_GAP_SEARCHES
from .models import FeedCandidate, FeedSweep, Follow, Player
from .sports.base import words

log = logging.getLogger("sports_follow.sweeps")

GAP, FAMOUS = "gap", "famous"  # their streams (metrics.STREAMS)
GAP_DAYS = timedelta(days=14)  # a gap search looks for videos published this recently
GAP_EVERY = timedelta(hours=20)  # a player's gap search runs once a day: the sweep is daily, and may run a little early
GAP_BACKOFF = timedelta(days=3)  # after a gap search that kept nothing, the player's next waits this long
FAMOUS_EVERY = timedelta(days=30)  # a player's most watched videos are searched for again no sooner than this
DAY, WEEK = timedelta(hours=24), timedelta(days=7)  # the caps' windows, rolling
KEEP_FOR = timedelta(days=60)  # a sweep's row goes after this: the famous sweep looks back FAMOUS_EVERY
KEEP_SHARE = 0.5  # sweeps stop while less than this share of a source's tightest window is left
_EVER = datetime.min.replace(tzinfo=timezone.utc)  # never swept: first among the equally followed


def run(now: datetime | None = None) -> dict[str, int]:
    """The day's gap searches, then the famous sweep's, within their caps, keeping what they find. Returns what it did."""
    now = now or datetime.now(timezone.utc)
    counts = {"gap searched": 0, "famous searched": 0, "found": 0, "kept": 0, "refused": 0}
    sources = searches.searching()
    if not sources or FEED_GAP_SEARCHES + FEED_FAMOUS_SEARCHES <= 0:
        return counts
    with db.session() as session:
        session.execute(delete(FeedSweep).where(FeedSweep.ran_at < now - KEEP_FOR))
        players = [p for p in candidates.followed(session) if sports.get(p.sport) and sports.on(p.sport)]
        if not players:
            return counts
        followers: dict[int, int] = {pid: n for pid, n in session.execute(select(Follow.player_id, func.count()).group_by(Follow.player_id))}
        gaps, famous = gap_due(session, players, followers, now), famous_due(session, players, followers, now)
    names: dict[str, dict[int, set[tuple[str, ...]]]] = {}  # each sport's players' names, told apart among them
    for p in players:
        if p.sport not in names:
            names[p.sport] = candidates.phrases([q for q in players if q.sport == p.sport])
    if _sweep(GAP, gaps, now - GAP_DAYS, now, sources, names, counts):
        _sweep(FAMOUS, famous, None, now, sources, names, counts)
    metrics.feed("kept", counts["kept"])
    metrics.feed("refused", counts["refused"])
    return counts


def _ranked(players: list[candidates.Followed], followers: dict[int, int], last: dict[int, datetime]) -> list[candidates.Followed]:
    """The most followed first; of those followed alike, the longest since their last sweep (never, first)."""
    return sorted(players, key=lambda p: (-followers.get(p.player_id, 0), last.get(p.player_id, _EVER), p.player_id))


def _since(session: Session, stream: str, since: datetime) -> int:
    return session.scalar(select(func.count()).where(FeedSweep.stream == stream, FeedSweep.ran_at > since)) or 0


def gap_due(session: Session, players: list[candidates.Followed], followers: dict[int, int], now: datetime) -> list[candidates.Followed]:
    """The players a gap search is for now, in order, as many as FEED_GAP_SEARCHES leaves in the last DAY: those with
    nothing the Feed would serve this week, but one searched for in the last GAP_EVERY, or in the last GAP_BACKOFF by
    a search that kept nothing."""
    left = FEED_GAP_SEARCHES - _since(session, GAP, now - DAY)
    if left <= 0:
        return []
    covered = feedstats.covered(session, [p.player_id for p in players], now)
    last: dict[int, tuple[datetime, int]] = {}
    for pid, at, kept in session.execute(select(FeedSweep.player_id, FeedSweep.ran_at, FeedSweep.kept).where(FeedSweep.stream == GAP).order_by(FeedSweep.ran_at)):
        last[pid] = (at, kept)
    waiting = {pid for pid, (at, kept) in last.items() if at > now - GAP_EVERY or (kept == 0 and at > now - GAP_BACKOFF)}
    due = [p for p in players if p.player_id not in covered and p.player_id not in waiting]
    return _ranked(due, followers, {pid: at for pid, (at, _) in last.items()})[:left]


def famous_due(session: Session, players: list[candidates.Followed], followers: dict[int, int], now: datetime) -> list[candidates.Followed]:
    """The players the famous sweep is for now, in order: those not swept in the last FAMOUS_EVERY, as many as
    FEED_FAMOUS_SEARCHES leaves in the last WEEK, and no more than its seventh does in the last DAY."""
    per_day = math.ceil(FEED_FAMOUS_SEARCHES / 7)
    left = min(FEED_FAMOUS_SEARCHES - _since(session, FAMOUS, now - WEEK), per_day - _since(session, FAMOUS, now - DAY))
    if left <= 0:
        return []
    last: dict[int, datetime] = {pid: at for pid, at in session.execute(select(FeedSweep.player_id, func.max(FeedSweep.ran_at))
                                                                       .where(FeedSweep.stream == FAMOUS).group_by(FeedSweep.player_id))}  # fmt: skip
    due = [p for p in players if p.player_id not in last or last[p.player_id] <= now - FAMOUS_EVERY]
    return _ranked(due, followers, last)[:left]


def query(player: candidates.Followed) -> str:
    """What a sweep searches for: the player's name and their sport's words for its videos."""
    return words(player.name, sports.of(player.sport).video_words())[:300]


def _sweep(stream: str, due: list[candidates.Followed], after: datetime | None, now: datetime, sources: list[VideoSource],
           names: dict[str, dict[int, set[tuple[str, ...]]]], counts: dict[str, int]) -> bool:
    """Search for each of these players, on each source, for videos published after then (or ever), and keep what
    names them. Returns False when it stopped for want of room in a source's allowance, or for a shutdown."""
    for p in due:
        said, sweep, found, kept = query(p), None, 0, 0
        for source in sources:
            if shutdown.stopping.is_set() or not searches.room(source, KEEP_SHARE):
                return False
            try:
                uploads = source.search(said, after, FEED_COUNTRY)
            except budget.RateLimited as exc:
                log.warning("feed: %s: %s; sweeps wait", source.system, exc)
                return False
            except AdapterError as exc:
                log.info("feed: %s %s search %r failed: %s", source.system, stream, said, exc)
                continue
            metrics.feed_search(p.sport, source.search_cost or 0, stream)
            metrics.feed_step("uploads", stream, [p.sport] * len(uploads))
            if sweep is None:
                sweep = _record(p.player_id, stream, said, now)
                if sweep is None:
                    break  # the player was removed meanwhile
            hits = [replace(f, sweep=sweep) for f in candidates.match(uploads, [p], names[p.sport])]
            found += len(hits)
            try:
                got = candidates.keep(source, hits, now, stream=stream)
            except (AdapterError, budget.RateLimited) as exc:
                log.warning("feed: %s: what %s search %r found not kept: %s", source.system, stream, said, exc)
                got = {}
            kept += got.get("kept", 0)
            counts["refused"] += got.get("refused", 0)
        if sweep is None:
            continue  # no source answered: due again at the next sweep
        counts[f"{stream} searched"] += 1
        counts["found"] += found
        counts["kept"] += kept
        with db.session() as session:
            row = session.get(FeedSweep, sweep)
            if row is not None:
                row.found, row.kept = found, kept
    return True


def _record(player_id: int, stream: str, said: str, now: datetime) -> int | None:
    """A sweep's row, once a source answered it: its id, or None for a player no longer there."""
    try:
        with db.session() as session:
            return session.scalar(insert(FeedSweep).values(player_id=player_id, stream=stream, query=said, ran_at=now).returning(FeedSweep.id))
    except Exception as exc:  # noqa: BLE001 — a player removed meanwhile: the foreign key refuses the row
        log.info("feed: %s search for player %s not recorded: %s", stream, player_id, type(exc).__name__)
        return None


def report(session: Session, since: datetime) -> list[dict[str, Any]]:
    """Each sweep since then, newest first: whose, which stream, what it searched for, when, what it found and kept,
    and how many of those kept the Feed serves now."""
    rows = session.execute(select(FeedSweep, Player.name).join(Player, Player.id == FeedSweep.player_id)
                           .where(FeedSweep.ran_at >= since).order_by(FeedSweep.ran_at.desc(), FeedSweep.id.desc())).all()  # fmt: skip
    swept = FeedCandidate.candidate["swept"].astext
    served: dict[str, int] = {sid: n for sid, n in session.execute(select(swept, func.count()).where(swept.in_([str(r.id) for r, _ in rows]),
                                                                                                    FeedCandidate.score.is_not(None)).group_by(swept))} if rows else {}  # fmt: skip
    return [{"player": name, "stream": r.stream, "query": r.query, "ran_at": r.ran_at, "found": r.found, "kept": r.kept, "served": served.get(str(r.id), 0)}
            for r, name in rows]
