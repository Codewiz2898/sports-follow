"""The feed's ranking at ingest (L1, BHA-301): the judge's verdict on each candidate the fetch kept, then a number
for the ones it lets through, and the moment each one is about. Run by the curator after each channel poll
(curator.py), never by a request: the Feed (feed.py, L2) only reads what's stored here.

A candidate row keeps three things apart, so any of them can be worked out again without the others:

    features   what the source said (views, likes, length, two reads with their times): candidates.py's
    verdict    what the judge said (curate.py): who it's about, what it is, how interesting, what's wrong
    score      this module's number, from the two and the channel's trust; NULL for a row never served

Only a share about the player is served. A verdict that says it only mentions them, that it isn't about them,
that it's paid for, or that names any risk is kept (for the admin panel, for channel trust and for the judge's
tuning) but never served: its score stays NULL. So is a video from a channel its player's sport doesn't list
(candidates.on_list: official channels and a few top creators, BHA-349), a row stored before the list was cut. An
admin's word on a row (review) stands over the judge's and the list: approved or pinned serves it, hidden never does.

The score, 0 to 1 (a pinned row's goes past 1, ahead of everything):

    trust x (INTEREST x the judge's interest + POPULAR x how widely it's watched for its age + LIKES x its like ratio)

It's age-fair and stays put as days pass: nothing in it is "now". How widely it's watched is its views a day since
it was published, as its latest read counted them, on a log scale (popular_part); a read changes it, a day going
by doesn't. Recency is the Feed's to weigh, as it serves (feed.py's fade), so last season's best and today's are
scored alike and the Feed decides how much newer counts.

A story from a publisher's feed (news.py) has no views or likes: how many outlets ran it stands in for both,

    trust x (INTEREST x the judge's interest + (POPULAR + LIKES) x how many outlets ran it, up to OUTLETS_TOP)

and of a player's stories about the same thing within a day (news.clusters) only the best is served, counting
the others' outlets (features["outlets"]); the rest keep a NULL score. A feed's trust is a channel's: high while a
running sport lists it.

Its reads are the first when it was found, then a re-read every few hours while it's under two days old, then the
weekly check: how fast it's watched lately (between the last two) says whether an older one is evergreen. A channel's trust is high when a running sport lists it, lower for one no sport lists any more,
or what the admin panel sets for it (controls.channel_trust: 0 hides it). Signed-in fans reporting its
videos, or saying they aren't about the player, lower a default trust, down to half of it at most; an
admin's setting stands as it is.

`python -m sports_follow.curator rescore [--rejudge]` works every score out again from what's stored, and
with --rejudge asks the judge again first: tune the weights or the prompt without fetching anything.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast, get_args

from pydantic import ValidationError
from sqlalchemy import not_, select, update
from sqlalchemy.orm import Session

from . import candidates, controls, curate, db, metrics, naming, news, shutdown, sports
from .accounts import FAN_PREFIX
from .adapters import NEWS, feed_source
from .models import FeedCandidate, FeedFeedback, Moment, Player, PlayerAlias, PlayerCard

log = logging.getLogger("sports_follow.rank")

JUDGE_PER_RUN = 300  # candidates sent to the judge a run: what a poll finds is far fewer; a backlog clears in a few
JUDGE_FOR = 12 * 60  # seconds a run of the judge may take: well inside the poll's lock (curator.POLL_LOCK)
JUDGE_CHUNK = curate.WORKERS * 2  # judged side by side and stored together, so a run cut short keeps what it did
INTEREST, POPULAR, LIKES = 0.45, 0.35, 0.20  # the score's weights; trust multiplies the sum
# Views a day since it was published that count as the most a video can be watched (popular_part), on a log scale:
# 1,000 a day is half of it, 30 a day a quarter. A read in its first hours counts that long as MIN_AGE: an hour's
# burst isn't a day's pace.
PER_DAY_TOP = 1_000_000
MIN_AGE = timedelta(hours=6)
LIKES_TOP = 0.06  # a like ratio that counts as the most liked; 4-6% is a lot on YouTube
LIKES_UNKNOWN = 0.5  # its part while there are too few views to tell (or likes are hidden)
LIKES_FROM = 100  # views before the like ratio means anything
OUTLETS_TOP = 5  # outlets running one story that count as the most a story can be covered
MENTION = 0.6  # a share that only mentions the player, served only when an admin approves it, is worth this much of one about them
LISTED, UNLISTED = 1.0, 0.5  # a channel's trust: a running sport lists it, or none does now
PIN = 10.0  # added to a pinned row's score: ahead of everything the numbers rank
# Signed-in fans' feedback on a channel's items lowers its default trust: each report counts this much, each "not
# about the player" half that ("not interested" is taste, not the channel's fault). The trust is divided by 1 + their
# sum / FEEDBACK_SCALE, and never below FEEDBACK_FLOOR of it: four reports halve it, and more can't sink a channel
# by themselves (the admin panel can). Anonymous fans' words hide items from them alone: a new cookie is free.
FEEDBACK = {"report": 1.0, "not_about_player": 0.5}
FEEDBACK_SCALE = 4.0
FEEDBACK_FLOOR = 0.5
MOMENT_WINDOW = timedelta(hours=48)  # a share published this long after a moment of the player's is about it
# Moments a share isn't grouped under: before the game (a line-up), its start, and taking one back.
NOT_A_HEAD = ("lineup", "start", "correction")
# Older than this, a share is evergreen while it's still watched this fast: a month, so younger ones stay in their
# dated group (feed.py's This week and This month).
EVERGREEN_AFTER = timedelta(days=30)
EVERGREEN_PACE = 25.0  # views an hour between its two reads
EVERGREEN_VIEWS = 20_000
REVIEWS = ("approved", "hidden", "pinned")  # what an admin may say of a row (admin.py)
TAGS = {  # the verdict's kind, as the Feed's pill says it; by who made it where that tells it apart
    "match_moment": "Highlights", "milestone": "Milestone", "personal_or_fun": "Fun", "interview_or_analysis": "Analysis",
    "news": "News", "rumour": "Rumour", "promo": "Promo", "other": "Video",
}  # fmt: skip
STORY_TAGS = {**TAGS, "match_moment": "Match report", "personal_or_fun": "Feature", "other": "News"}  # a story's (an article's)


# ---------------------------------------------------------------- the numbers, from what's stored


def when(value: Any) -> datetime | None:
    """An ISO time as a source or a read wrote it, in UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def whole(value: Any) -> int | None:
    """A count as stored, or None for anything else."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def growth(features: dict[str, Any]) -> float | None:
    """Views an hour between a row's last two reads (candidates.read_again keeps the first and the latest two):
    how fast it's watched lately. None with one read, or reads an hour apart or less."""
    reads = [r for r in features.get("reads") or [] if isinstance(r, dict)]
    if len(reads) < 2:
        return None
    first, last = reads[-2], reads[-1]
    a, b, va, vb = when(first.get("at")), when(last.get("at")), whole(first.get("views")), whole(last.get("views"))
    if a is None or b is None or va is None or vb is None or (b - a) <= timedelta(hours=1):
        return None
    return max(0, vb - va) / ((b - a).total_seconds() / 3600)


def per_day(features: dict[str, Any], published: datetime | None) -> float | None:
    """Views a day since it was published, as its latest read counted them (at least MIN_AGE of its life): None
    without a publication time or a read's."""
    reads = [r for r in features.get("reads") or [] if isinstance(r, dict) and when(r.get("at")) is not None]
    if published is None or not reads:
        return None
    last = reads[-1]
    views = whole(last.get("views"))
    views = whole(features.get("views")) if views is None else views
    read_at = when(last.get("at"))
    if views is None or read_at is None:
        return None
    return views / (max(read_at - published, MIN_AGE) / timedelta(days=1))


def like_part(features: dict[str, Any]) -> float:
    views, likes = whole(features.get("views")), whole(features.get("likes"))
    if views is None or likes is None or views < LIKES_FROM:
        return LIKES_UNKNOWN
    return min(1.0, likes / views / LIKES_TOP)


def popular_part(features: dict[str, Any], published: datetime | None) -> float:
    """How widely it's watched for its age, 0 to 1: log views a day over PER_DAY_TOP's; 0 when that can't be told."""
    found = per_day(features, published)
    return 0.0 if found is None else min(1.0, math.log10(1 + max(0.0, found)) / math.log10(1 + PER_DAY_TOP))


def stream(source: str, kept: dict[str, Any] | None) -> str:
    """Where a stored row was found, as the feed's metrics count it: a publisher's feed (news), a moment's search, a
    sweep's (gap, wide, famous: sweeps.py), or a channel."""
    if is_story(source):
        return "news"
    kept = kept or {}
    if kept.get("searched_for") is not None:
        return "search"
    return kept["stream"] if kept.get("stream") in metrics.STREAMS else "channels"


def passes(verdict: dict[str, Any] | None) -> bool:
    """Whether the judge lets a share be served: about the player (a passing mention never is), not paid for, no risk."""
    return bool(verdict) and verdict.get("about_player") == "subject" and not verdict.get("sponsored") and not verdict.get("risks")


def score(verdict: dict[str, Any], features: dict[str, Any], trust: float, published: datetime | None) -> float:
    """The row's number, 0 to 1, before an admin's pin (see the module's docstring): the same whatever day it's
    worked out on, for the same reads."""
    try:
        interest = min(1.0, max(0.0, float(verdict.get("interest") or 0)))
    except (TypeError, ValueError):
        interest = 0.0
    raw = INTEREST * interest + POPULAR * popular_part(features, published) + LIKES * like_part(features)
    if verdict.get("about_player") == "mention":
        raw *= MENTION
    return round(max(0.0, trust) * raw, 4)


def outlets_part(outlets: int) -> float:
    """How widely a story ran, 0 to 1: one outlet is a little under half, OUTLETS_TOP or more all of it."""
    return min(1.0, math.log10(1 + max(0, outlets)) / math.log10(1 + OUTLETS_TOP))


def story_score(verdict: dict[str, Any], outlets: int, trust: float) -> float:
    """A story's number, 0 to 1, before an admin's pin (see the module's docstring)."""
    try:
        interest = min(1.0, max(0.0, float(verdict.get("interest") or 0)))
    except (TypeError, ValueError):
        interest = 0.0
    raw = INTEREST * interest + (POPULAR + LIKES) * outlets_part(outlets)
    if verdict.get("about_player") == "mention":
        raw *= MENTION
    return round(max(0.0, trust) * raw, 4)


def evergreen(features: dict[str, Any], published: datetime | None, now: datetime) -> bool:
    """An older share that's still being watched: "The ones everyone's seen"."""
    if published is None or now - published < EVERGREEN_AFTER:
        return False
    per_hour = growth(features)
    return per_hour is not None and per_hour >= EVERGREEN_PACE and (whole(features.get("views")) or 0) >= EVERGREEN_VIEWS


def tag(verdict: dict[str, Any], story: bool = False) -> str:
    """The Feed's pill for a row: a video's, or with story, an article's."""
    kind, by = verdict.get("kind"), verdict.get("by")
    if kind == "match_moment" and by == "fan_or_creator" and not story:
        return "Fan edit"
    if kind == "interview_or_analysis" and by in ("player", "official", "outlet"):
        return "Interview"
    return (STORY_TAGS if story else TAGS).get(str(kind), "News" if story else "Video")


def is_story(source: str) -> bool:
    """Whether a row's source (FeedCandidate.source) is one of stories (news.py) rather than of videos."""
    known = feed_source(source)
    return known is not None and known.kind == "article"


# ---------------------------------------------------------------- a channel's trust


def listed_channels() -> set[str]:
    """Every channel a running sport lists for its videos, and every feed it lists for its stories."""
    return {c for sport in sports.ENABLED for c in (*sport.video_channels, *sport.news_feeds)}


def feedback_weight(rows: Iterable[tuple[str, str]], channel_of: dict[str, str]) -> dict[str, float]:
    """Each channel's fans' feedback, weighed (FEEDBACK), from (item id, verdict) rows and which channel each item is from."""
    out: dict[str, float] = {}
    for item, verdict in rows:
        channel = channel_of.get(item)
        if channel:
            out[channel] = out.get(channel, 0.0) + FEEDBACK.get(verdict, 0.0)
    return out


def trust(channel: str, listed: set[str], set_by_admin: dict[str, float], weight: float = 0.0) -> float:
    """How far a channel's videos are trusted, 0 to 1: the admin's word for it as it is, else whether a running
    sport lists it, lowered (to FEEDBACK_FLOOR of it at most) by what signed-in fans said of its items."""
    if channel in set_by_admin:
        return max(0.0, min(1.0, set_by_admin[channel]))
    base = LISTED if channel in listed else UNLISTED
    return max(base * FEEDBACK_FLOOR, base / (1 + weight / FEEDBACK_SCALE))


def channel_feedback(session: Session) -> dict[str, float]:
    """Each channel's weighed feedback from signed-in fans (FEEDBACK): the feedback table read whole (it's a
    few rows a fan), matched to the channels of the candidates' addresses."""
    from .feed import item_id  # feed.py reads this module's helpers; this one of its, here only

    said = session.execute(select(FeedFeedback.item_id, FeedFeedback.verdict)
                           .where(FeedFeedback.fan_id.startswith(FAN_PREFIX), FeedFeedback.verdict.in_(FEEDBACK))).all()  # fmt: skip
    if not said:
        return {}
    channel_of = {item_id(url): str(cid or "") for url, cid in session.execute(select(FeedCandidate.url, FeedCandidate.candidate["channel_id"].astext))}
    return feedback_weight(((i, v) for i, v in said), channel_of)


# ---------------------------------------------------------------- moments


@dataclass(frozen=True)
class Head:
    """A moment a share may be grouped under."""

    id: int
    player_id: int
    at: datetime
    priority: int


def moment_for(published: datetime | None, heads: Sequence[Head]) -> int | None:
    """The moment a share published at this time is about: of the player's moments in the MOMENT_WINDOW before
    it, the biggest (a hundred over the result), the latest of equals."""
    if published is None:
        return None
    near = [h for h in heads if h.at <= published <= h.at + MOMENT_WINDOW]
    return max(near, key=lambda h: (h.priority, h.at)).id if near else None


def heads(session: Session, player_ids: Iterable[int], since: datetime) -> dict[int, list[Head]]:
    """The players' key moments since then that a share may be about: not a line-up, a start or a correction,
    and not one a correction took back."""
    ids = sorted(set(player_ids))
    if not ids:
        return {}
    taken_back = select(Moment.corrects).where(Moment.corrects.is_not(None))
    rows = session.execute(
        select(Moment.id, Moment.player_id, Moment.created_at, Moment.priority).where(
            Moment.player_id.in_(ids), Moment.level == "key", Moment.created_at >= since, Moment.kind.not_in(NOT_A_HEAD), Moment.id.not_in(taken_back)))
    out: dict[int, list[Head]] = {}
    for mid, pid, at, priority in rows:
        if pid is not None:
            out.setdefault(pid, []).append(Head(mid, pid, at, priority))
    return out


# ---------------------------------------------------------------- the judge


def as_candidate(source: str, url: str, kept: dict[str, Any], features: dict[str, Any], player: curate.PlayerContext) -> curate.Candidate:
    """A stored row as the judge reads it."""
    status = (kept.get("details") or {}).get("status") or {}
    known = feed_source(source)
    platform = known.platform if known else "other"
    if known is not None and known.kind == "article":
        return curate.Candidate(
            kind="article", source=cast(curate.Source, platform if platform in get_args(curate.Source) else "other"), url=url, author_kind="outlet",
            author_name=str(kept.get("channel") or ""), author_handle=str(kept.get("channel_id") or ""), title=str(kept.get("title") or ""),
            text=str(kept.get("description") or ""), published_at=kept.get("published") or None, player=player,
        )  # fmt: skip
    return curate.Candidate(
        kind="video", source=cast(curate.Source, platform if platform in get_args(curate.Source) else "other"), url=url,
        author_name=str(kept.get("channel") or ""), author_handle=str(kept.get("channel_id") or ""),
        title=str(kept.get("title") or ""), text=str(kept.get("description") or ""), published_at=kept.get("published") or None,
        engagement=curate.Engagement(views=whole(features.get("views")), likes=whole(features.get("likes"))),
        platform_flags=curate.PlatformFlags(paid_partnership=kept.get("paid") is True, embeddable=status.get("embeddable") is not False),
        duration_seconds=whole(features.get("seconds")), player=player,
    )  # fmt: skip


def players(session: Session, ids: Iterable[int]) -> dict[int, curate.PlayerContext]:
    """Who each row is about, as the judge is told: name, the names fans found them by, teams, sport, own accounts."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    aliases: dict[int, list[str]] = {}
    for pid, alias in session.execute(select(PlayerAlias.player_id, PlayerAlias.alias).where(PlayerAlias.player_id.in_(wanted))):
        aliases.setdefault(pid, []).append(alias.replace("-", " "))
    out = {}
    for p in session.scalars(select(Player).where(Player.id.in_(wanted))):
        handles = []
        for s in p.socials or []:
            try:
                handles.append(curate.Social.model_validate(s))
            except ValidationError:
                continue
        teams = [t if isinstance(t, str) else str((t or {}).get("name") or "") for t in p.teams or []]
        out[p.id] = curate.PlayerContext(name=p.name, aliases=aliases.get(p.id, []), teams=[t for t in teams if t], sport=p.sport or "", handles=handles)
    return out


def judge(now: datetime | None = None, limit: int | None = JUDGE_PER_RUN, again: bool = False, within: float | None = JUDGE_FOR) -> dict[str, int]:
    """Send unjudged rows (every row, with again) to the judge, newest first, and keep its verdicts, a chunk at a
    time. A row the judge couldn't answer for now stays as it was, for the next run; so do the rows left when
    the run has taken `within` seconds or the curator is stopping."""
    now = now or datetime.now(timezone.utc)
    started = time.monotonic()
    with db.session() as session:
        query = select(FeedCandidate.id, FeedCandidate.player_id, FeedCandidate.source, FeedCandidate.url, FeedCandidate.candidate, FeedCandidate.features)
        if not again:
            query = query.where(FeedCandidate.verdict.is_(None))
        rows = session.execute(query.order_by(FeedCandidate.fetched_at.desc(), FeedCandidate.id).limit(limit)).all()
        who = players(session, (r.player_id for r in rows))
    rows = [r for r in rows if r.player_id in who]
    judged = 0
    for i in range(0, len(rows), JUDGE_CHUNK):
        if shutdown.stopping.is_set() or (within is not None and time.monotonic() - started > within):
            log.info("feed: the judge stopped with %d candidate(s) left for the next run", len(rows) - i)
            break
        part = rows[i:i + JUDGE_CHUNK]
        verdicts = curate.judge_many(as_candidate(r.source, r.url, r.candidate or {}, r.features or {}, who[r.player_id]) for r in part)
        done = [{"id": r.id, "verdict": v.model_dump(mode="json"), "judged_at": now} for r, v in zip(part, verdicts) if v is not None]
        if done:
            with db.session() as session:
                session.execute(update(FeedCandidate), done)
        for r, v in zip(part, verdicts):
            if v is not None:
                for step in ("judged", "passed") if passes(v.model_dump()) else ("judged",):
                    metrics.feed_step(step, stream(r.source, r.candidate), [who[r.player_id].sport])
        judged += len(done)
    return {"judged": judged, "unjudged": len(rows) - judged}


# ---------------------------------------------------------------- the score


def rescore(now: datetime | None = None, channel: str | None = None, row: int | None = None) -> dict[str, int]:
    """Work out every judged row's moment and score again from what's stored (just one channel's rows, or one
    row, given either), and write the ones that changed. A row a moment's search found keeps that moment while it
    stands. A story is scored with the others of its player's about the same thing (_stories), so those are read
    too. No source, no model: cheap enough to run after every poll."""
    now = now or datetime.now(timezone.utc)
    with db.session() as session:
        columns = (FeedCandidate.id, FeedCandidate.player_id, FeedCandidate.source, FeedCandidate.url, FeedCandidate.candidate, FeedCandidate.features,
                   FeedCandidate.verdict, FeedCandidate.review, FeedCandidate.score, FeedCandidate.moment_id)  # fmt: skip
        query = select(*columns).where(FeedCandidate.verdict.is_not(None))
        if channel is not None:
            query = query.where(FeedCandidate.candidate["channel_id"].astext == channel)
        if row is not None:
            query = query.where(FeedCandidate.id == row)
        rows = list(session.execute(query).all())
        if not rows:
            return {"scored": 0, "served": 0, "changed": 0}
        storied = {r.player_id for r in rows if is_story(r.source)}
        if storied and (channel is not None or row is not None):
            have = {r.id for r in rows}
            more = session.execute(select(*columns).where(FeedCandidate.verdict.is_not(None), FeedCandidate.player_id.in_(storied),
                                                          FeedCandidate.source.in_([s for s in {r.source for r in rows} if is_story(s)]))).all()  # fmt: skip
            rows += [r for r in more if r.id not in have]
        published = {r.id: when((r.candidate or {}).get("published")) for r in rows}
        oldest = min((at for at in published.values() if at), default=now) - MOMENT_WINDOW
        moments = heads(session, (r.player_id for r in rows), oldest)
        weights = channel_feedback(session)
        listed, set_by_admin = listed_channels(), controls.current().channel_trust
        outlets = _stories(session, [r for r in rows if is_story(r.source)], published)
        values: dict[int, float | None] = {}
        for r in rows:
            cid = str((r.candidate or {}).get("channel_id") or "")
            how_far = trust(cid, listed, set_by_admin, weights.get(cid, 0.0))
            if r.id in outlets:
                worth = lambda far, r=r: story_score(r.verdict, outlets[r.id][0], far)  # noqa: E731
            else:
                worth = lambda far, r=r: score(r.verdict, r.features or {}, far, published[r.id])  # noqa: E731
            value: float | None = None
            if r.review in ("approved", "pinned"):
                # The admin's word on this one item stands over their word on its channel: served, if hidden there.
                value = worth(how_far or UNLISTED) + (PIN if r.review == "pinned" else 0.0)
            elif r.review != "hidden" and passes(r.verdict) and how_far > 0 and (r.id in outlets or is_story(r.source) or candidates.on_list(str((r.candidate or {}).get("sport") or ""), cid)):
                value = worth(how_far)
            values[r.id] = value
        # Of each story's group, only the best is served, but those an admin approved or pinned.
        reviewed = {r.id for r in rows if r.review in ("approved", "pinned")}
        for rid, (_, group) in outlets.items():
            scored = [g for g in group if values.get(g) is not None and g not in reviewed]
            if rid in scored and max(scored, key=lambda g: (values[g] or 0.0, -(published[g] or now).timestamp(), -g)) != rid:
                values[rid] = None
        changes, features, served = [], [], 0
        for r in rows:
            at = published[r.id]
            theirs = moments.get(r.player_id, [])
            searched = (r.candidate or {}).get("searched_for")
            # Found by a search for a moment: that moment, while it stands, over the biggest one near (searches.py).
            moment = searched if any(h.id == searched for h in theirs) else moment_for(at, theirs)
            value = values[r.id]
            if value is not None:
                served += 1
                if r.score is None:
                    metrics.feed_step("served", stream(r.source, r.candidate), [(r.candidate or {}).get("sport")])
            if value != r.score or moment != r.moment_id:
                changes.append({"id": r.id, "score": value, "moment_id": moment})
            if r.id in outlets and (r.features or {}).get("outlets") != outlets[r.id][0]:
                features.append({"id": r.id, "features": {**(r.features or {}), "outlets": outlets[r.id][0]}})
        if changes:
            session.execute(update(FeedCandidate), changes)
        if features:
            session.execute(update(FeedCandidate), features)
    return {"scored": len(rows), "served": served, "changed": len(changes)}


def _stories(session: Session, rows: Sequence[Any], published: dict[int, datetime | None]) -> dict[int, tuple[int, list[int]]]:
    """Each story row's group (news.clusters: its player's stories about the same thing within a day) and how many
    outlets ran it, by row id: (outlets, the group's row ids)."""
    if not rows:
        return {}
    drop: dict[int, list[str]] = {}
    for pid, name in session.execute(select(Player.id, Player.name).where(Player.id.in_({r.player_id for r in rows}))):
        drop.setdefault(pid, []).append(name)
    for pid, alias in session.execute(select(PlayerAlias.player_id, PlayerAlias.alias).where(PlayerAlias.player_id.in_({r.player_id for r in rows}))):
        drop.setdefault(pid, []).append(alias.replace("-", " "))
    by_player: dict[int, list[news.Story]] = {}
    outlet: dict[int, str] = {}
    for r in rows:
        kept = r.candidate or {}
        words = news.story_words(str(kept.get("title") or ""), str(kept.get("description") or ""), drop.get(r.player_id, []))
        by_player.setdefault(r.player_id, []).append(news.Story(r.id, published[r.id], frozenset(words)))
        outlet[r.id] = naming.fold(str(kept.get("channel") or "")) or str(kept.get("channel_id") or r.url)
    out = {}
    for stories in by_player.values():
        for group in news.clusters(stories):
            n = len({outlet[g] for g in group})
            out |= {g: (n, group) for g in group}
    return out


def cards(now: datetime | None = None) -> dict[str, int]:
    """After ranking: each player's served stories (news.py) onto their card as "stories" (feed.news_for), so every
    card that leaves the server carries them, a page's, a stream's or the Android app's, beside the research agent's
    news, which stays under "news"; taken off once none is served. A card is written only where they differ, under
    its row's lock, its version moved on, and published as any card is; one another writer replaced meanwhile gets
    them back at the next run."""
    from . import bus
    from .feed import news_for  # feed.py reads this module's helpers; this one of its, here only

    changed: list[tuple[int, dict[str, Any]]] = []
    with db.session() as session:
        ids = set(session.scalars(select(FeedCandidate.player_id).where(FeedCandidate.source.in_(list(NEWS)), FeedCandidate.score.is_not(None)).distinct()))
        ids |= set(session.scalars(select(PlayerCard.player_id).where(PlayerCard.card.has_key("stories"))))
        served = news_for(session, sorted(ids), now, strict=True)  # a failed read is an error, not every story gone
        for pid in sorted(ids):
            row = session.get(PlayerCard, pid, with_for_update=True)
            want = served.get(pid)
            if row is None or row.status != "ready" or (row.card or {}).get("stories") == want:
                continue
            card = {k: v for k, v in (row.card or {}).items() if k != "stories"} | ({"stories": want} if want else {})
            card["version"] = row.version = (row.version or 0) + 1
            row.card = card
            changed.append((pid, card))
    for pid, card in changed:
        bus.store_card(pid, card)
    return {"cards": len(changed)}


def review(session: Session, row_id: int, said: str | None) -> bool:
    """An admin's word on a row (REVIEWS, or None to take it back); False for no such row. Its score follows
    at the row's next rescore, which the caller runs. Approving or pinning one a fan's app said won't play inside
    it (feed.unplayable) says it does: every row of the video is served again."""
    if said is not None and said not in REVIEWS:
        raise ValueError(f"not a review: {said!r}")
    found = session.execute(update(FeedCandidate).where(FeedCandidate.id == row_id).values(review=said)
                            .returning(FeedCandidate.source, FeedCandidate.external_id)).first()  # fmt: skip
    if found is not None and said in ("approved", "pinned"):
        session.execute(update(FeedCandidate).where(FeedCandidate.source == found.source, FeedCandidate.external_id == found.external_id,
                                                    FeedCandidate.unplayable_at.is_not(None)).values(unplayable_at=None))  # fmt: skip
    return found is not None


def unserved() -> Any:
    """The SQL condition for a row the Feed never shows (FeedCandidate.served's opposite)."""
    return not_(FeedCandidate.served())
