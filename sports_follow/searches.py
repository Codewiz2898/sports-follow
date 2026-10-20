"""The feed's moment searches (L0, BHA-303): fans' videos of a followed player's big moment, from any channel. Fans
post edits of a hundred or a brace within hours of it, mostly on channels no sport lists (candidates.py polls only
those), so each big moment is searched for on the video sources that can search (VideoSource.search_cost).

Which moments, and in which words, is the sport's to say (Sport.search_words): "hundred Lord's", "brace Liverpool vs
Everton", "wins game 4", after the player's name. A followed player's key moment the sport has words for is queued
(queue), one search per player and game: a later moment of theirs in the game takes it over (the brace over the goal)
until its last run, and it looks for videos published since the first.

Each search runs twice (run): FIRST_AFTER the moment, and again SECOND_AFTER it (and at least RUNS_APART after its
first), as edits keep coming; one not run by then runs once, as its last. A run no source answered is tried again at
the next poll. A search whose moment is taken back (a goal ruled out) goes, and the game's moments that stand queue
it again. What it finds goes the way a channel's uploads go: kept where it names the player and
passes the hard filters (candidates.keep), unless it's stored for them already, with the moment it was searched for;
then judged and ranked with the rest (curator.rank_new).

A search costs a hundred reads of a source's allowance, so they're capped: FEED_MOMENT_SEARCHES runs in any 24 hours
(so a source's own quota day, wherever it starts, holds no more), the biggest moments first when there are more, and none while less than KEEP_SHARE of the source's tightest window is
left, which is the channel poll's and the weekly check's. They run in the feed's lane, a background one
(adapters/budget.py). With no source that can search running (no key), nothing is queued or run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from . import candidates, db, metrics, notify, shutdown, sports
from .adapters import AdapterError, VideoSource, budget, videos
from .config import FEED_COUNTRY, FEED_MOMENT_SEARCHES
from .models import Event, FeedSearch, Follow, Moment, Player
from .sports.base import words

log = logging.getLogger("sports_follow.searches")

FIRST_AFTER = timedelta(hours=3)  # a moment's first search: its first edits are up
SECOND_AFTER = timedelta(hours=24)  # and its last, a day on
RUNS_APART = timedelta(hours=8)  # a search the cap held back runs its last no sooner than this after its first
DAY = timedelta(hours=24)  # the cap's window, rolling
GIVE_UP = timedelta(hours=48)  # a search not run by then (the cap bound for two days) isn't: its moment is old news
KEEP_SHARE = 0.25  # searches stop while less than this share of a source's tightest window is left


def searching() -> list[VideoSource]:
    """The running video sources that can search."""
    return [s for s in videos() if s.search_cost]


def queue(now: datetime | None = None) -> dict[str, int]:
    """Queue a search for each followed player's key moment of the last SECOND_AFTER that its sport has words for
    (Sport.search_words), not taken back, one per player and game: a later moment in a game takes over the game's
    search until its last run. Returns how many were queued and taken over."""
    now = now or datetime.now(timezone.utc)
    counts = {"queued": 0, "taken over": 0, "taken back": 0}
    if not searching() or FEED_MOMENT_SEARCHES <= 0:
        return counts
    with db.session() as session:
        taken_back = select(Moment.corrects).where(Moment.corrects.is_not(None))
        # A search whose moment was taken back since: gone, before its last run, and queued again below from what stands.
        counts["taken back"] = len(session.execute(delete(FeedSearch).where(FeedSearch.second_at.is_(None), FeedSearch.moment_id.in_(taken_back))
                                                   .returning(FeedSearch.id)).all())
        rows = session.execute(
            select(Moment.id, Moment.player_id, Moment.event_id, Moment.kind, Moment.key, Moment.title, Moment.body, Moment.priority, Moment.created_at,
                   Player.name, Player.sport, Event.title.label("game"), Event.competition, Event.venue)
            .join(Player, Player.id == Moment.player_id).join(Event, Event.id == Moment.event_id)
            .where(Moment.level == "key", Moment.corrects.is_(None), Moment.id.not_in(taken_back), Moment.created_at > now - SECOND_AFTER,
                   Moment.created_at <= now, exists().where(Follow.player_id == Moment.player_id))
            .order_by(Moment.id)).all()  # fmt: skip
        if not rows:
            return counts
        pairs = {(r.player_id, r.event_id) for r in rows}
        have = {(s.player_id, s.event_id): s for s in session.scalars(select(FeedSearch).where(FeedSearch.player_id.in_({p for p, _ in pairs})))
                if (s.player_id, s.event_id) in pairs}
        seen: dict[tuple[int, int, str], int] = {}  # each kind's moments so far, by player and game: a second goal is 2
        for r in rows:
            kind, detail = notify.kind_detail(r.key, r.event_id, r.player_id)
            n = seen[(r.player_id, r.event_id, kind)] = seen.get((r.player_id, r.event_id, kind), 0) + 1
            if not sports.on(r.sport):
                continue
            fired = sports.Fired(kind, detail, r.title, r.body or "", r.priority, n, r.game or "", r.competition or "", r.venue or "")
            said = sports.of(r.sport).search_words(fired)
            if not said:
                continue
            query = words(r.name, said)[:300]
            row = have.get((r.player_id, r.event_id))
            if row is None:
                new = session.scalar(insert(FeedSearch).values(player_id=r.player_id, event_id=r.event_id, moment_id=r.id, query=query, priority=r.priority,
                                                               fired_at=r.created_at).on_conflict_do_nothing().returning(FeedSearch.id))
                if new is not None:
                    have[(r.player_id, r.event_id)] = session.get_one(FeedSearch, new)
                    counts["queued"] += 1
            elif row.second_at is None and r.id > row.moment_id:
                row.moment_id, row.query, row.priority = r.id, query, max(row.priority, r.priority)
                counts["taken over"] += 1
    return counts


def room(source: VideoSource, share: float = KEEP_SHARE) -> bool:
    """Whether a search leaves the source more than this share of each budget's tightest window (a sweep's is more:
    sweeps.KEEP_SHARE)."""
    for b in source.budgets():
        left = budget.room(b)
        if left is not None and left[0] - (source.search_cost or 0) < share * left[1]:
            return False
    return True


def spent(since: datetime) -> int:
    """Searches run since then: each run counts, first and last."""
    with db.session() as session:
        firsts = session.scalar(select(func.count()).where(FeedSearch.first_at >= since)) or 0
        lasts = session.scalar(select(func.count()).where(FeedSearch.second_at >= since)) or 0
    return firsts + lasts


def run(now: datetime | None = None) -> dict[str, int]:
    """Run the searches that are due, the biggest moments first, up to what's left of FEED_MOMENT_SEARCHES in the last
    DAY, and keep what they find. Drops searches GIVE_UP old (once none of their runs counts against the cap). Returns
    what it did."""
    now = now or datetime.now(timezone.utc)
    counts = {"searched": 0, "found": 0, "kept": 0, "refused": 0, "dropped": 0}
    sources = searching()
    if not sources or FEED_MOMENT_SEARCHES <= 0:
        return counts
    since = now - DAY
    with db.session() as session:
        old = delete(FeedSearch).where(FeedSearch.fired_at < now - GIVE_UP, or_(FeedSearch.first_at.is_(None), FeedSearch.first_at < since),
                                       or_(FeedSearch.second_at.is_(None), FeedSearch.second_at < since))
        counts["dropped"] = len(session.execute(old.returning(FeedSearch.id)).all())
    left = FEED_MOMENT_SEARCHES - spent(since)
    if left <= 0:
        return counts
    with db.session() as session:
        due = session.execute(
            select(FeedSearch.id, FeedSearch.player_id, FeedSearch.moment_id, FeedSearch.query, FeedSearch.fired_at, Player.sport)
            .join(Player, Player.id == FeedSearch.player_id)
            .where(FeedSearch.second_at.is_(None), FeedSearch.fired_at <= now - FIRST_AFTER, FeedSearch.fired_at >= now - GIVE_UP,
                   or_(FeedSearch.first_at.is_(None), (FeedSearch.fired_at <= now - SECOND_AFTER) & (FeedSearch.first_at <= now - RUNS_APART)),
                   exists().where(Follow.player_id == FeedSearch.player_id))
            .order_by(FeedSearch.priority.desc(), FeedSearch.fired_at, FeedSearch.id).limit(left)).all()  # fmt: skip
        if not due:
            return counts
        players = {p.player_id: p for p in candidates.followed(session)}
    names: dict[str, dict[int, set[tuple[str, ...]]]] = {}  # each sport's players' names, told apart among them
    for s in due:
        target = players.get(s.player_id)
        if target is None or not sports.on(s.sport):
            continue  # its sport turned off: it waits, and goes at GIVE_UP
        if target.sport not in names:
            names[target.sport] = candidates.phrases([p for p in players.values() if p.sport == target.sport])
        kept, answered = 0, False
        for source in sources:
            if shutdown.stopping.is_set() or not room(source):
                return _done(counts)
            try:
                uploads = source.search(s.query, s.fired_at, FEED_COUNTRY)
            except budget.RateLimited as exc:
                log.warning("feed: %s: %s; moment searches wait", source.system, exc)
                return _done(counts)
            except AdapterError as exc:
                log.info("feed: %s search %r failed: %s", source.system, s.query, exc)
                continue
            answered = True
            metrics.feed_search(target.sport, source.search_cost or 0)
            metrics.feed_step("uploads", "search", [target.sport] * len(uploads))
            found = [candidates.Found(f.player_id, f.sport, f.upload, f.name, f.where, s.moment_id) for f in candidates.match(uploads, [target], names[target.sport])]
            counts["found"] += len(found)
            try:
                got = candidates.keep(source, found, now, stream="search")
            except (AdapterError, budget.RateLimited) as exc:
                log.warning("feed: %s: what search %r found not kept: %s", source.system, s.query, exc)
                got = {}
            kept += got.get("kept", 0)
            counts["refused"] += got.get("refused", 0)
        if not answered:
            continue  # tried again at the next poll
        counts["searched"] += 1
        counts["kept"] += kept
        with db.session() as session:
            row = session.get(FeedSearch, s.id)
            if row is not None:
                if s.fired_at <= now - SECOND_AFTER:
                    row.second_at = now
                else:
                    row.first_at = now
                row.kept += kept
    return _done(counts)


def _done(counts: dict[str, int]) -> dict[str, int]:
    metrics.feed("kept", counts["kept"])
    metrics.feed("refused", counts["refused"])
    return counts
