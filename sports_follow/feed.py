"""A player's Feed (BHA-296): what fans, creators and the player share about them, judged before a fan sees it.

Three layers. L0 fetches candidates from the sources (YouTube first) into feed_candidates; L1, the judge, keeps
the ones really about the player and says why in one line; L2, here, puts what is left in front of one fan at
serve time: their hidden items out, grouped around the player's moments, newest first. This layer never waits on
a model or a source: it reads rows, so a feed is as fast as a follow list, and with nothing judged it is just
the news the player's page already has.

`judged` is where the pipeline plugs in: the rows the curator judged and scored (rank.py), served as videos,
and stories from the publishers' feeds (news.py) as news, each with the number of outlets that ran it. While a
player has any of those stories they are the feed's news, and the curator puts them on the card too (news_for,
rank.cards), so the player page's news is theirs; without any, the card's news is. Their score, age-fair as the curator stores it (rank.py),
fades with age only here (fade); a page holds one video from each channel and
PER_MOMENT around each moment; older ones still being watched are "the ones everyone's seen" (evergreen). If
reading them fails, a feed is the card's news alone (never emptier than the News tab was), and Today's rail,
which takes no news (the Wire has it), is empty.

The fan's own Feed (`page`, GET /api/me/feed/items) is every followed player's at once: newest day first (Today,
Yesterday, This week, This month, Earlier, in the fan's own days), the ranking deciding within a day, and the
players taking turns, so no two items in a row are about one player while another has one to show. It pages
with a cursor that keeps the order it was read in, so a page loaded later carries on where the last one ended.

The web app draws it (web/src/components/Feed.tsx): a player page's Feed tab and Overview's "From the fans", the
Feed tab, and Today's "From your players" (the top two of the fan's Feed). A fan's "…" sheet sends feedback
(`feedback`), kept per fan as follows and mutes are.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import rank
from .adapters import VIDEOS, feed_source
from .models import FeedCandidate, FeedFeedback, Follow, Moment, PlayerCard

log = logging.getLogger("sports_follow.feed")

# What a fan can say about an item from its "…" sheet. Each hides it from them; the judge reads all three later.
VERDICTS = ("not_interested", "not_about_player", "report")
ITEM_ID = re.compile(r"[0-9a-f]{16}")
YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
WEEK = timedelta(days=7)
MONTH = timedelta(days=30)
TODAY = "Today"
YESTERDAY = "Yesterday"
THIS_WEEK = "This week"
THIS_MONTH = "This month"  # 8 to 30 days old
EARLIER = "Earlier"
# Recency, weighed only as the Feed serves (the stored score is age-fair, rank.py): a share's score is multiplied by
#
#     FADE_FLOOR + (1 - FADE_FLOOR) x 0.5 ^ (age / HALF_LIFE)
#
# which falls fast over the first days and levels off at FADE_FLOOR. So this week's good ones lead (an interesting,
# well-watched video two days old keeps about 80% of its score), and a strong one from three months ago (45%) still
# outranks a weak one from yesterday (89% of a much lower score): tests/test_feed.py pins the order of a few.
HALF_LIFE = timedelta(days=3)
FADE_FLOOR = 0.45
PER_MOMENT = 2  # videos on a page around one moment: the best two of the many edits of one goal
NEWS_ON_CARD = 10  # stories a player page's news holds (news_for), newest first
DAYS = (TODAY, YESTERDAY, THIS_WEEK, THIS_MONTH, EARLIER)  # the fan's Feed's groups, in order (page)
PAGE = 20  # items on a page of the fan's Feed, unless the app asks for fewer (or up to MAX_PAGE)
MAX_PAGE = 50
CURSOR_AGE = timedelta(days=2)  # a cursor older than this carries on in today's order, from its last item


class Video(BaseModel):
    youtube_id: str  # the player loads from youtube-nocookie.com, and only once a fan taps play
    duration: int | None = None  # seconds
    views: int | None = None


class Post(BaseModel):
    platform: str  # where it was posted, as fans call it ("X", "Instagram"): the card says "Open on X"
    own: bool  # the player's own account (an ink pill) rather than a fan's or a creator's


class Item(BaseModel):
    id: str  # item_id(url): stable, so a fan's feedback finds it again
    kind: Literal["video", "post", "news"]
    tag: str  # what it is, in a word or two: Analysis, Fan edit, Interview, Fun, News
    title: str  # a video's or story's title; a post's words, a snippet of them
    url: str
    source: str  # the channel, account or outlet
    # As the source gave it: an instant, or a bare day ("2026-10-12") for a story known only by its day, which
    # the app shows as that day wherever the fan is, never as a time.
    published_at: str | None = None
    reason: str  # "Why this is here", one line, shown as it is
    summary: str | None = None  # a story's first lines: the feed's own short description, never the story
    outlets: int | None = None  # how many outlets ran the story, where the curator counted them (news.py)
    # The card's picture, an https address loaded as it is: a video's still from its source (YouTube's thumbnail), a
    # story's image from the publisher's feed. None (a story without one, every post): the card draws the headline
    # on the sport's colour, and a post is words only.
    picture: str | None = None
    video: Video | None = None
    post: Post | None = None


class Group(BaseModel):
    head: str  # the moment's title, or "This week" / "This month" / "Earlier"
    moment: bool  # around a moment of the player's: the line opens its game
    when: datetime | None = None
    match: str | None = None  # the game's page in the app ("/match/<source>/<id>"), when the moment had one
    items: list[Item]


class Feed(BaseModel):
    player_id: int
    groups: list[Group]
    evergreen: list[Item]  # "The ones everyone's seen": the famous ones, apart from the week
    count: int  # every item in groups and evergreen


class Who(BaseModel):
    """The player an item of the fan's Feed is about, as its card's head shows them."""

    id: int
    name: str
    sport: str | None = None  # the sport's key, for its colour
    photo: dict[str, Any] | None = None  # the card's photo (photos.py): url, credit, page, author, licence, crop


class Circle(Who):
    """A player across the top of the fan's Feed: a tap shows only theirs."""

    count: int  # their items in the fan's Feed
    newest: str | None = None  # when the newest of them came in (ISO): the app rings them while that's after they looked


class Entry(BaseModel):
    """An item of the fan's Feed, with who it's about and the day it's under."""

    day: str  # one of DAYS: the group it's in
    player: Who
    item: Item
    arrived: str | None = None  # when it came into the feed (ISO), else when it was published
    moment: str | None = None  # the title of the player's moment it's about ("Kohli's hundred"), if it is
    match: str | None = None  # that moment's game in the app ("/match/<source>/<id>"), when it had one


class Page(BaseModel):
    """A page of the fan's Feed (GET /api/me/feed/items)."""

    items: list[Entry]
    cursor: str | None  # the next page's (?cursor=…); None: this was the last
    count: int  # every item in the feed, of the one player shown when one is
    newest: str | None  # when the newest item of any player's came in: the app's Feed tab has a dot while it's newer than the fan's last look
    players: list[Circle]  # every followed player with something in their feed, the latest to have something new first


class Share(BaseModel):
    """Today's "From your players": a followed player's top video or post."""

    player_id: int
    item: Item


@dataclass(frozen=True)
class Candidate:
    """An item the judge kept for a player, with what L2 ranks and groups it by."""

    item: Item
    moment_id: int | None = None  # the moment it's about (a fan edit of a hundred): grouped under it
    evergreen: bool = False
    score: float = 0.0  # the judge's: higher first within a group, then newer
    channel: str = ""  # a video's channel, by the source's id for it: a page holds one video from each
    pinned: bool = False  # an admin pinned it: at the top of This week (or of its moment), however old
    arrived: datetime | None = None  # when it came into the feed: the judge's verdict on it (else its fetch)


def item_id(url: str) -> str:
    return hashlib.blake2b(url.encode(), digest_size=8).hexdigest()


def judged(db: Session, player_ids: Sequence[int], now: datetime | None = None, strict: bool = False) -> dict[int, list[Candidate]]:
    """The judge's kept items for each player, best first: the rows the curator scored and nobody hid (rank.py),
    their score faded by age. {} if they can't be read: a feed is then the news alone, and never an error; with
    strict, the error is raised instead (the curator mustn't take a failed read for nothing served)."""
    if not player_ids:
        return {}
    now = now or datetime.now(timezone.utc)
    try:
        with db.begin_nested():
            rows = db.execute(
                select(FeedCandidate.player_id, FeedCandidate.source, FeedCandidate.external_id, FeedCandidate.url, FeedCandidate.candidate, FeedCandidate.features, FeedCandidate.verdict,
                       FeedCandidate.score, FeedCandidate.moment_id, FeedCandidate.review, FeedCandidate.judged_at, FeedCandidate.fetched_at)
                .where(FeedCandidate.player_id.in_(sorted(set(player_ids))), FeedCandidate.score.is_not(None), FeedCandidate.review.is_distinct_from("hidden"))
            ).all()  # fmt: skip
        out: dict[int, list[Candidate]] = {}
        for r in rows:
            known = feed_source(r.source)
            source = VIDEOS.get(r.source)
            try:
                if known is not None and known.kind == "article":
                    c = _story(r, now)
                else:
                    embed = source.embed(r.external_id) if source else None
                    picture = source.picture(r.external_id) if source else None
                    c = _video(embed, source.name if source else "", r, now, picture)
            except ValueError:  # pydantic's too: a row the fetch stored oddly is left out, not the feed
                continue
            if c is not None:
                out.setdefault(r.player_id, []).append(c)
        return {pid: _ranked(theirs) for pid, theirs in out.items()}
    except Exception:
        if strict:
            raise
        log.exception("feed: the judged videos couldn't be read; serving the news alone")
        return {}


def fade(published: datetime | None, now: datetime) -> float:
    """What of a share's score is left at its age (the curve above); one with no date counts as HALF_LIFE x 4 old."""
    age = (now - published) if published else HALF_LIFE * 4
    return FADE_FLOOR + (1 - FADE_FLOOR) * 0.5 ** (max(age, timedelta(0)) / HALF_LIFE)


def _video(embed: dict[str, str] | None, source: str, r: Any, now: datetime, picture: str | None = None) -> Candidate | None:
    """A scored row as a video item, its score faded by age: an evergreen one keeps its own, and a pinned one
    fades only past its pin, so it leads however old, and is never put among the evergreen."""
    if not embed:
        return None  # a video the app's player can't play
    kept, features, verdict = r.candidate or {}, r.features or {}, r.verdict or {}
    pinned = r.review == "pinned"
    score = (r.score or 0.0) - (rank.PIN if pinned else 0.0)
    published = rank.when(kept.get("published"))
    evergreen = not pinned and rank.evergreen(features, published, now)
    faded = (score if evergreen else score * fade(published, now)) + (rank.PIN if pinned else 0.0)
    item = Item(
        id=item_id(r.url), kind="video", tag=rank.tag(verdict), title=str(kept.get("title") or "").strip() or "A video", url=r.url,
        source=str(kept.get("channel") or source), published_at=str(kept.get("published") or "") or None,
        reason=str(verdict.get("reason") or "").strip() or "A video from a channel this sport trusts.", picture=picture,
        video=Video(**embed, duration=rank.whole(features.get("seconds")), views=rank.whole(features.get("views"))),
    )  # fmt: skip
    channel = str(kept.get("channel_id") or kept.get("channel") or r.url)
    return Candidate(item=item, moment_id=None if evergreen else r.moment_id, evergreen=evergreen, score=round(faded, 6), channel=channel, pinned=pinned,
                     arrived=_arrived(r))  # fmt: skip


def _faded(score: float, pinned: bool, published: datetime | None, now: datetime) -> float:
    """A row's score faded by age, a pinned one only past its pin."""
    return score * fade(published, now) + (rank.PIN if pinned else 0.0)


def _story(r: Any, now: datetime) -> Candidate | None:
    """A scored story (news.py) as a news item: its headline, outlet, time and link, the feed's own short description,
    and how many outlets ran it; its score faded by age as a video's is."""
    kept, verdict = r.candidate or {}, r.verdict or {}
    title = str(kept.get("title") or "").strip()
    if not title:
        return None
    pinned = r.review == "pinned"
    published = rank.when(kept.get("published"))
    outlet = str(kept.get("channel") or "").strip()
    outlets = rank.whole((r.features or {}).get("outlets"))
    item = Item(
        id=item_id(r.url), kind="news", tag=rank.tag(verdict, story=True), title=title, url=r.url, source=outlet,
        published_at=str(kept.get("published") or "") or None,
        reason=str(verdict.get("reason") or "").strip() or (f"A story from {outlet}." if outlet else "A story from a publisher this sport reads."),
        summary=str(kept.get("description") or "").strip() or None, outlets=outlets if outlets and outlets > 1 else None,
        picture=_https(kept.get("image")),
    )  # fmt: skip
    faded = _faded((r.score or 0.0) - (rank.PIN if pinned else 0.0), pinned, published, now)
    return Candidate(item=item, moment_id=r.moment_id, score=round(faded, 6), channel=str(kept.get("channel_id") or ""), pinned=pinned, arrived=_arrived(r))


def _arrived(r: Any) -> datetime | None:
    """When a row came into the feed: the judge's verdict on it, else its fetch."""
    at = getattr(r, "judged_at", None) or getattr(r, "fetched_at", None)
    return at if isinstance(at, datetime) else None


def _https(url: Any) -> str | None:
    """A picture's address the app may load: https, no spaces or quotes; else None."""
    return url if isinstance(url, str) and len(url) <= 1000 and re.fullmatch(r"https://[^\s\"'<>]+", url) else None


def news_for(db: Session, player_ids: Sequence[int], now: datetime | None = None, strict: bool = False) -> dict[int, list[dict[str, Any]]]:
    """Each player's stories the curator served (news.py), newest first, NEWS_ON_CARD at most, in the shape of a card's
    news (headline, url, source, published, summary, outlets): what the curator keeps on the card as "stories"
    (rank.cards), which a player page shows in place of its news. A player with none is left out, and so is
    everyone if they can't be read (with strict, an error)."""
    out: dict[int, list[dict[str, Any]]] = {}
    for pid, theirs in judged(db, player_ids, now, strict).items():
        stories = sorted((c.item for c in theirs if c.item.kind == "news"), key=lambda i: -(_when(i.published_at) or datetime.min.replace(tzinfo=timezone.utc)).timestamp())
        if stories:
            out[pid] = [{"headline": i.title, "url": i.url, "source": i.source, "published": i.published_at, "summary": i.summary or "", "outlets": i.outlets}
                        for i in stories[:NEWS_ON_CARD]]  # fmt: skip
    return out


def _when_group(c: Candidate, now: datetime) -> str:
    """The dated group a share not about a moment goes in: This week (or pinned), This month, or Earlier."""
    at = _when(c.item.published_at)
    if c.pinned or (at and now - at <= WEEK):
        return THIS_WEEK
    return THIS_MONTH if at and now - at <= MONTH else EARLIER


def _page(candidates: list[Candidate], now: datetime) -> list[Candidate]:
    """What one page shows of a player's ranked candidates: one video from each channel of each age (this week's,
    around a moment or not; this month's; earlier; the evergreen ones), so a channel's strong old video never takes
    its place from this week's, and PER_MOMENT around each moment, the best of each kept. News and posts aren't
    thinned."""
    channels: set[tuple[bool, str, str]] = set()  # (evergreen, its age's group, channel id)
    per_moment: dict[int, int] = {}
    out = []
    for c in _ranked(candidates):
        if c.item.kind == "video":
            key = (c.evergreen, _when_group(c, now), c.channel or c.item.source)
            if key in channels:
                continue
            if c.moment_id is not None:
                if per_moment.get(c.moment_id, 0) >= PER_MOMENT:
                    continue
                per_moment[c.moment_id] = per_moment.get(c.moment_id, 0) + 1
            channels.add(key)
        out.append(c)
    return out


def feed_for(db: Session, player_id: int, fan: str, now: datetime | None = None) -> Feed | None:
    """The player's Feed for this fan, or None for no such player."""
    card = db.get(PlayerCard, player_id)
    if card is None:
        return None
    now = now or datetime.now(timezone.utc)
    shown = _page(_unhidden(db, fan, player_id, _candidates(db, player_id, card.card, now)), now)
    moments = _moments(db, {c.moment_id for c in shown if c.moment_id is not None and not c.evergreen})
    groups = _arrange([c for c in shown if not c.evergreen], moments, now)
    evergreen = [c.item for c in _ranked(c for c in shown if c.evergreen)]
    return Feed(player_id=player_id, groups=groups, evergreen=evergreen, count=sum(len(g.items) for g in groups) + len(evergreen))


def _rail(candidates: list[Candidate], now: datetime) -> list[Candidate]:
    """A player's shares as Today's rail picks among them: this week's first, best first, then the rest."""
    return sorted(candidates, key=lambda c: (_when_group(c, now) != THIS_WEEK, _newest(c)))


def shares_for(db: Session, fan: str) -> list[Share]:
    """Each followed player's top video or post, in the order they were followed: this week's best if they have
    one this week, however strong an older one is (Today is about now). News never comes here: the Wire carries
    it. With nothing judged for any of them the rail is absent."""
    now = datetime.now(timezone.utc)
    ids = list(db.scalars(select(Follow.player_id).where(Follow.fan_id == fan).order_by(Follow.created_at)))
    kept = judged(db, ids, now) if ids else {}
    out = []
    for player_id in ids:
        top = _rail(_unhidden(db, fan, player_id, [c for c in kept.get(player_id, []) if c.item.kind != "news" and not c.evergreen]), now)
        if top:
            out.append(Share(player_id=player_id, item=top[0].item))
    return out


def page(db: Session, fan: str, player: int | None = None, cursor: str | None = None, limit: int = PAGE, tz: int = 0, now: datetime | None = None) -> Page:
    """A page of the fan's Feed: every followed player's (or the one player's, when the fan picked one), each as their
    own Feed tab has them (the judge's kept items, else the card's news; what the fan hid left out; a channel's best
    and PER_MOMENT around a moment), under the day each was published in the fan's days (tz: minutes east of UTC),
    the newest day first. Within a day the ranking decides, but players take turns: the next item is the best of a
    player other than the last one's, while there is one (a day carries on from the one before). A story two
    players' feeds hold is there once, with the first of them. Read from rows only, never a model or a source.

    cursor: where the last page ended (Page.cursor), which keeps that page's order and carries on after its last
    item: the order is the one at the cursor's time (the days, the fading, and what the fan had hidden by then), so
    an item hidden since keeps its place, unserved, and nothing slides past the fan between pages. ValueError for
    a cursor this never made."""
    real = now or datetime.now(timezone.utc)
    at, start, after = _read_cursor(cursor, real) if cursor else (real, 0, None)
    limit = max(0, min(limit, MAX_PAGE))
    everyone, circles = _everyone(db, fan, at, tz)
    ordered = [(e, m, gone) for e, m, gone in everyone if player is None or e.player.id == player]
    if after is not None:
        start = next((i + 1 for i, (e, _, _) in enumerate(ordered) if e.item.id == after), start)
    chunk: list[tuple[Entry, int | None]] = []
    end = start
    while end < len(ordered) and len(chunk) < limit:
        e, m, gone = ordered[end]
        end += 1
        if not gone:
            chunk.append((e, m))
    moments = _moments(db, {m for _, m in chunk if m is not None})
    items = []
    for e, m in chunk:
        if m in moments:
            e = e.model_copy(update={"moment": moments[m].title, "match": moments[m].url if moments[m].url.startswith("/match/") else None})
        items.append(e)
    more = any(not gone for _, _, gone in ordered[end:])
    nxt = _write_cursor(at, end, items[-1].item.id) if items and more else None
    newest = max((c.newest for c in circles if c.newest), default=None)
    return Page(items=items, cursor=nxt, count=sum(not gone for _, _, gone in ordered), newest=newest, players=circles)


def _everyone(db: Session, fan: str, now: datetime, tz: int) -> tuple[list[tuple[Entry, int | None, bool]], list[Circle]]:
    """The fan's whole Feed in its order at now, each item with the moment it's about (by id) and whether the fan has
    hidden it since (in its place, not served), and the players' circles."""
    ids = list(db.scalars(select(Follow.player_id).where(Follow.fan_id == fan).order_by(Follow.created_at)))
    if not ids:
        return [], []
    cards = {pid: card for pid, card in db.execute(select(PlayerCard.player_id, PlayerCard.card).where(PlayerCard.player_id.in_(ids))).all()}
    kept = judged(db, ids, now)
    hidden: dict[int, set[str]] = {}  # by now: out of the order
    since: set[tuple[int, str]] = set()  # after now (a page read earlier): in their places, not served
    for pid, item, said in db.execute(select(FeedFeedback.player_id, FeedFeedback.item_id, FeedFeedback.created_at).where(FeedFeedback.fan_id == fan, FeedFeedback.player_id.in_(ids))).all():
        if said is None or said <= now:
            hidden.setdefault(pid, set()).add(item)
        else:
            since.add((pid, item))
    whose: dict[int, Who] = {}
    days: dict[str, list[tuple[int, Candidate]]] = {d: [] for d in DAYS}
    for pid in ids:
        card = cards.get(pid)
        if card is None:
            continue
        theirs = kept.get(pid, [])
        candidates = [*theirs, *([] if any(c.item.kind == "news" for c in theirs) else _news(card))]
        about = card.get("player") or {}
        photo = about.get("photo")
        whose[pid] = Who(id=pid, name=str(about.get("name") or ""), sport=about.get("sport") or None, photo=photo if isinstance(photo, dict) and photo.get("url") else None)
        for c in _page(_unseen(candidates, hidden.get(pid, set())), now):
            days[_day(c, now, tz)].append((pid, c))
    entries: list[tuple[Entry, int | None, bool]] = []
    counts: dict[int, int] = {}
    newest: dict[int, datetime] = {}
    seen: set[str] = set()
    last = None
    for day in DAYS:
        ranked = []
        for pid, c in sorted(days[day], key=lambda pc: _newest(pc[1])):  # a story two players' feeds hold: the first's
            if c.item.id not in seen:
                seen.add(c.item.id)
                ranked.append((pid, c))
        for pid, c in _turns(ranked, last):
            last = pid
            came = c.arrived or _when(c.item.published_at)
            gone = (pid, c.item.id) in since
            if not gone:
                if came and (pid not in newest or came > newest[pid]):
                    newest[pid] = came
                counts[pid] = counts.get(pid, 0) + 1
            entries.append((Entry(day=day, player=whose[pid], item=c.item, arrived=came.isoformat() if came else None), c.moment_id, gone))
    order = {pid: i for i, pid in enumerate(ids)}
    lead = sorted(counts, key=lambda pid: (-(newest[pid].timestamp() if pid in newest else 0), order[pid]))
    circles = [Circle(**whose[pid].model_dump(), count=counts[pid], newest=newest[pid].isoformat() if pid in newest else None) for pid in lead]
    return entries, circles


def _turns(ranked: list[tuple[int, Candidate]], last: int | None) -> list[tuple[int, Candidate]]:
    """Players taking turns down a ranked list: each next item is the best one left of a player other than the last
    one's, and only when nobody else has one left, the same player's again."""
    rest = list(ranked)
    out = []
    while rest:
        i = next((k for k, (pid, _) in enumerate(rest) if pid != last), 0)
        last, c = rest.pop(i)
        out.append((last, c))
    return out


def _day(c: Candidate, now: datetime, tz: int) -> str:
    """The day group of the fan's Feed an item is in, by the day it was published where the fan is (tz: minutes east
    of UTC; a story known only by its day is under that day): Today (or later), Yesterday, This week (up to six days
    ago, and a pinned one however old), This month (up to 30), else Earlier, and an undated one Earlier."""
    published = c.item.published_at
    at = _when(published)
    if at is None or published is None:
        return THIS_WEEK if c.pinned else EARLIER
    local = timezone(timedelta(minutes=tz))
    on = date.fromisoformat(published[:10]) if len(published) == 10 else at.astimezone(local).date()
    back = (now.astimezone(local).date() - on).days
    if back <= 0:
        return TODAY
    if back == 1:
        return YESTERDAY
    if back < 7 or c.pinned:
        return THIS_WEEK
    return THIS_MONTH if back <= 30 else EARLIER


def _write_cursor(at: datetime, offset: int, last: str) -> str:
    """A next page's cursor: the time the feed was put in order at, how many items came before, and the last one."""
    raw = json.dumps([int(at.timestamp()), offset, last], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _read_cursor(cursor: str, now: datetime) -> tuple[datetime, int, str | None]:
    """A cursor's time (now instead, once it's over CURSOR_AGE old or ahead of now), offset and last item; ValueError
    for one this never made."""
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("That isn't a page of the feed.") from exc
    if not (isinstance(raw, list) and len(raw) == 3 and all(isinstance(v, int) and not isinstance(v, bool) for v in raw[:2])
            and isinstance(raw[2], str) and ITEM_ID.fullmatch(raw[2]) and 0 <= raw[1] <= 100_000):  # fmt: skip
        raise ValueError("That isn't a page of the feed.")
    try:
        at = datetime.fromtimestamp(raw[0], timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("That isn't a page of the feed.") from exc
    if not now - CURSOR_AGE <= at <= now + timedelta(minutes=1):
        at = now
    return at, raw[1], raw[2]


def feedback(db: Session, fan: str, player_id: int, item: str, verdict: str) -> None:
    """Keep what a fan said about an item: it leaves their feed at once. A second word on it replaces the
    first. Only about an item the player's feed has now, so a fan's rows are never more than its items.
    ValueError for an item id or verdict this never makes; LookupError for no such player or item."""
    if verdict not in VERDICTS or not ITEM_ID.fullmatch(item):
        raise ValueError("No such item or feedback.")
    card = db.get(PlayerCard, player_id)
    if card is None:
        raise LookupError("No such player.")
    if item not in {c.item.id for c in _candidates(db, player_id, card.card)}:
        raise LookupError("That item isn't in this player's feed any more.")
    row = insert(FeedFeedback).values(fan_id=fan, player_id=player_id, item_id=item, verdict=verdict)
    db.execute(row.on_conflict_do_update(index_elements=["fan_id", "player_id", "item_id"], set_={"verdict": verdict}))


# ---------------------------------------------------------------- inside


def _candidates(db: Session, player_id: int, card: dict[str, Any], now: datetime | None = None) -> list[Candidate]:
    """Everything the player's feed could show anyone: the judge's kept items, then the card's news, unless the
    judge kept stories of theirs (news.py), which are the news then."""
    kept = judged(db, [player_id], now).get(player_id, [])
    return [*kept, *([] if any(c.item.kind == "news" for c in kept) else _news(card))]


def _unhidden(db: Session, fan: str, player_id: int, candidates: list[Candidate]) -> list[Candidate]:
    """The candidates this fan hasn't hidden, each once, safe to link to."""
    if not candidates:
        return []
    return _unseen(candidates, set(db.scalars(select(FeedFeedback.item_id).where(FeedFeedback.fan_id == fan, FeedFeedback.player_id == player_id))))


def _unseen(candidates: list[Candidate], hidden: set[str]) -> list[Candidate]:
    """The candidates not hidden, each once, safe to link to."""
    seen: set[str] = set()
    out = []
    for c in candidates:
        if c.item.id in hidden or c.item.id in seen or not _safe(c.item):
            continue
        seen.add(c.item.id)
        out.append(c)
    return out


def _safe(item: Item) -> bool:
    """A link the app may open, and a video the player can load."""
    if not re.match(r"https?://", item.url):
        return False
    return item.kind != "video" or (item.video is not None and bool(YOUTUBE_ID.fullmatch(item.video.youtube_id)))


def _news(card: dict[str, Any]) -> list[Candidate]:
    """The card's news as feed items, newest first as the card keeps them."""
    name = (card.get("player") or {}).get("name") or "this player"
    out = []
    for n in card.get("news") or []:
        url, title = (n.get("url") or "").strip(), (n.get("headline") or "").strip()
        if not url or not title:
            continue
        source = (n.get("source") or "").strip()
        reason = f"A story about {name} from {source}, from the news on their page." if source else f"A story about {name}, from the news on their page."
        published = n.get("published") if isinstance(n.get("published"), str) and _when(n.get("published")) else None
        summary = (n.get("summary") or "").strip() or None
        out.append(Candidate(Item(id=item_id(url), kind="news", tag="News", title=title, url=url, source=source, published_at=published, reason=reason, summary=summary)))
    return out


def _when(value: Any) -> datetime | None:
    """An ISO date or time a source gave, in UTC, for sorting and the week's cut-off; a bare day is its UTC midnight."""
    if not isinstance(value, str) or not value:
        return None
    try:
        found = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def _moments(db: Session, ids: set[int]) -> dict[int, Moment]:
    return {m.id: m for m in db.scalars(select(Moment).where(Moment.id.in_(ids)))} if ids else {}


def _newest(c: Candidate) -> tuple[float, float]:
    at = _when(c.item.published_at)
    return (-c.score, -(at.timestamp() if at else 0))


def _ranked(candidates: Iterable[Candidate]) -> list[Candidate]:
    """L2 within a group: the judge's score first, then newest. Per-fan signals (what they watch, what they
    hide) join here once there are any beyond their hidden items."""
    return sorted(candidates, key=_newest)


def _arrange(candidates: list[Candidate], moments: dict[int, Moment], now: datetime) -> list[Group]:
    """A group per moment, the latest first, then This week, This month (8 to 30 days old) and Earlier (older, or
    undated). A pinned item is at the top of This week however old."""
    around: dict[int, list[Candidate]] = {}
    week: list[Candidate] = []
    month: list[Candidate] = []
    earlier: list[Candidate] = []
    for c in candidates:
        if c.moment_id in moments:
            around.setdefault(c.moment_id, []).append(c)
        else:
            {THIS_WEEK: week, THIS_MONTH: month, EARLIER: earlier}[_when_group(c, now)].append(c)
    groups = []
    for moment_id in sorted(around, key=lambda i: moments[i].created_at, reverse=True):
        m = moments[moment_id]
        match = m.url if m.url.startswith("/match/") else None
        groups.append(Group(head=m.title, moment=True, when=m.created_at, match=match, items=[c.item for c in _ranked(around[moment_id])]))
    for head, these in ((THIS_WEEK, week), (THIS_MONTH, month), (EARLIER, earlier)):
        if these:
            groups.append(Group(head=head, moment=False, items=[c.item for c in _ranked(these)]))
    return groups
