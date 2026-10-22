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
That's its Following tab; For you (BHA-349) mixes in videos of players the fan doesn't follow, MIX of them in
every few items (others): the most followed on the app, those with a big moment today and the pool's (feedpool.py:
each sport's best known, fetched for For you), the fan's sports first. Their last OTHERS_WINDOW (two months) is one
list, ranked by the faded score whatever the day (BHA-382), so a strong official video of three weeks ago comes
before a weak one of today's.
Each entry says whether the fan follows its player, and the page which of the two layouts the apps draw (layout:
a setting, FEED_LAYOUT, that the admin panel changes live).

A video's title is shown as YouTube gives it, its HTML escapes read as it's served (titles.py: YouTube's API policies
want it unmodified). Its picture is the biggest its source has (VideoSource.picture).

The web app draws it (web/src/components/Feed.tsx): a player page's Feed tab and Overview's "From the fans", the
Feed tab, and Today's "From your players" (the top two of the fan's Feed). A fan's "…" sheet sends feedback
(`feedback`), kept per fan as follows and mutes are.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html
import json
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import controls, feedpool, models, rank, sports, titles
from .adapters import VIDEOS, feed_source
from .adapters import Mark as SourceMark
from .config import FEED_LAYOUT, FEED_ZONES
from .models import FeedCandidate, FeedFeedback, Follow, Moment, Player, PlayerCard

log = logging.getLogger("sports_follow.feed")

# What a fan can say about an item from its "…" sheet. Each hides it from them; the judge reads all three later.
VERDICTS = ("not_interested", "not_about_player", "report")
# What's kept for a video a fan's app said won't play inside it (said_unplayable): it's hidden from them, as their
# words from the sheet are, and the judge doesn't read it (rank.FEEDBACK).
UNPLAYABLE = "unplayable"
# Votes a fan's app may cast in an hour that a video won't play inside it (said_unplayable), from one fan and from one
# address. Past either, the video still leaves that fan's Feed; only their vote isn't counted.
UNPLAYABLE_PER_FAN, UNPLAYABLE_PER_ADDRESS = 10, 30
# Networks whose apps must say a video won't play inside them before it leaves every fan's Feed (unplayable); until
# then it leaves only the Feeds of the fans who said so. One fan, or one network with any number of new cookies,
# can't take a video from anyone else, while one that really won't play does so for every fan who tries it, so it's
# soon gone for all. Three: a fan has a home and a phone network at most. The admin panel's Feed tab lists every video
# taken out this way (admin.taken_out), where Put back undoes it (put_back). A network's word is kept for
# UNPLAYABLE_VOTES_FOR seconds (a month). Only an app in FEED_COUNTRY votes (counts_everywhere): fans abroad would soon
# be three networks too.
UNPLAYABLE_VOTES, UNPLAYABLE_VOTES_FOR = 3, 30 * 24 * 3600
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
# The last items served that a page's cursor keeps (_served): the next page carries on after each player's last of them.
CURSOR_TAIL = PAGE
CURSOR_LENGTH = 600  # a cursor's characters at most (the route's limit): CURSOR_TAIL items' ids and its time and place fit
FOLLOWING, FOR_YOU = "following", "for_you"  # the fan's Feed's tabs (page): their players only, or mixed with others
TABS = (FOLLOWING, FOR_YOU)
# For you: of every MIX[0] + MIX[1] items, MIX[0] from players the fan follows and MIX[1] from players they don't, while
# both have some left; then whichever has. A fan who follows nobody gets For you all from the others.
MIX = (3, 2)
OTHERS = 50  # players the fan doesn't follow whose videos For you mixes in, at most
# For you's others' videos this recent are one list, best first by their score faded for age (fade), whatever their day;
# older ones come after, newest day first. The fan's own players' stay in their days.
OTHERS_WINDOW = timedelta(days=60)
BIG_MOMENT = 85  # a key moment this big (moments.PRIORITY: a goal, a hundred, an upset) in the last BIG_FOR puts its player among them
BIG_FOR = timedelta(hours=24)
LAYOUTS = controls.FEED_LAYOUTS  # how the apps draw the fan's Feed: "edge", posts edge to edge, or "full", a video a screen (layout)


class MarkPath(BaseModel):
    fill: str  # its colour, as the source draws it
    d: str  # an SVG path's drawing


class Mark(BaseModel):
    """The icon of the source a video is from (VideoSource.mark), which its card shows linked to the video where it
    plays (item.url), so a fan can tell where it's from: its name, for the link's label, and its drawing in a
    width x height box, at least min_height high."""

    name: str
    width: float
    height: float
    min_height: int
    paths: list[MarkPath]


class Video(BaseModel):
    youtube_id: str  # the player loads from youtube-nocookie.com, and only once a fan taps play
    duration: int | None = None  # seconds
    views: int | None = None
    upright: bool = False  # taller than it's wide (a Short): the apps frame and play it upright
    mark: Mark | None = None  # its source's icon, linked to the video on the source's site (YouTube's III.F.2)


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
    following: bool = True  # whether the fan follows its player: For you's others don't (their cards offer Follow)


class Page(BaseModel):
    """A page of the fan's Feed (GET /api/me/feed/items)."""

    items: list[Entry]
    cursor: str | None  # the next page's (?cursor=…); None: this was the last
    count: int  # every item in the feed, of the one player shown when one is
    newest: str | None  # when the newest item of any player's came in: the app's Feed tab has a dot while it's newer than the fan's last look
    players: list[Circle]  # every followed player with something in their feed, the latest to have something new first
    tab: str = FOLLOWING  # which of TABS this is a page of
    layout: str = LAYOUTS[0]  # how the apps draw it, one of LAYOUTS (layout(): the admin panel's, else FEED_LAYOUT)


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
    their score faded by age, a video's title as YouTube gives it (titles.shown). None for a player whose sport is turned off: their rows stay, served again once it's back on.
    {} if they can't be read: a feed is then the news alone, and never an error; with strict, the error is raised
    instead (the curator mustn't take a failed read for nothing served)."""
    if not player_ids:
        return {}
    now = now or datetime.now(timezone.utc)
    try:
        with db.begin_nested():
            players = db.execute(select(Player.id, Player.sport).where(Player.id.in_(sorted(set(player_ids))))).all()
            running = sorted(pid for pid, sport in players if sports.on(sport))
            rows = db.execute(
                select(FeedCandidate.player_id, FeedCandidate.source, FeedCandidate.external_id, FeedCandidate.url, FeedCandidate.candidate, FeedCandidate.features, FeedCandidate.verdict,
                       FeedCandidate.score, FeedCandidate.moment_id, FeedCandidate.review, FeedCandidate.judged_at, FeedCandidate.fetched_at)
                .where(FeedCandidate.player_id.in_(running), FeedCandidate.served())
            ).all() if running else []  # fmt: skip
        out: dict[int, list[Candidate]] = {}
        for r in rows:
            known = feed_source(r.source)
            source = VIDEOS.get(r.source)
            try:
                if known is not None and known.kind == "article":
                    c = _story(r, now)
                else:
                    details = (r.candidate or {}).get("details")
                    if source and not source.allowed(details):
                        continue  # its source's terms keep it off the feed (YouTube's made for kids); its next read drops it
                    embed = source.embed(r.external_id) if source else None
                    picture = source.picture(r.external_id, details) if source else None
                    c = _video(embed, source.name if source else "", r, now, picture, upright=bool(source and source.upright(details)),
                               mark=_mark(source.mark) if source and source.mark else None)
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


def _mark(mark: SourceMark) -> Mark:
    """A source's icon as the API sends it."""
    return Mark(name=mark.name, width=mark.width, height=mark.height, min_height=mark.min_height, paths=[MarkPath(fill=f, d=d) for f, d in mark.paths])


def _video(embed: dict[str, str] | None, source: str, r: Any, now: datetime, picture: str | None = None, upright: bool = False,
           mark: Mark | None = None) -> Candidate | None:
    """A scored row as a video item, its score faded by age: an evergreen one keeps its own, and a pinned one
    fades only past its pin, so it leads however old, and is never put among the evergreen. Its title as YouTube
    gives it (titles.shown). upright: taller than it's
    wide (VideoSource.upright); mark: its source's icon (VideoSource.mark)."""
    if not embed:
        return None  # a video the app's player can't play
    kept, features, verdict = r.candidate or {}, r.features or {}, r.verdict or {}
    pinned = r.review == "pinned"
    score = (r.score or 0.0) - (rank.PIN if pinned else 0.0)
    published = rank.when(kept.get("published"))
    evergreen = not pinned and rank.evergreen(features, published, now)
    faded = (score if evergreen else score * fade(published, now)) + (rank.PIN if pinned else 0.0)
    channel_name = html.unescape(str(kept.get("channel") or source))  # a search's came escaped before it read them as written
    title = titles.shown(str(kept.get("title") or ""))
    item = Item(
        id=item_id(r.url), kind="video", tag=rank.tag(verdict), title=title or "A video", url=r.url,
        source=channel_name, published_at=str(kept.get("published") or "") or None,
        reason=str(verdict.get("reason") or "").strip() or "A video from a channel this sport trusts.", picture=picture,
        video=Video(**embed, duration=rank.whole(features.get("seconds")), views=rank.whole(features.get("views")), upright=upright, mark=mark),
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


def page(db: Session, fan: str, player: int | None = None, cursor: str | None = None, limit: int = PAGE, tz: int = 0, now: datetime | None = None,
         tab: str = FOLLOWING) -> Page:
    """A page of the fan's Feed: every followed player's (or the one player's, when the fan picked one), each as their
    own Feed tab has them (the judge's kept items, else the card's news; what the fan hid left out; a channel's best
    and PER_MOMENT around a moment), under the day each was published in the fan's days (tz: minutes east of UTC),
    the newest day first. Within a day the ranking decides, but players take turns: the next item is the best of a
    player other than the last one's, while there is one (a day carries on from the one before). A story two
    players' feeds hold is there once, with the first of them (the picked player's, when one is), and hiding it from
    either hides it. Read from rows only, never a model or a source.

    tab: FOLLOWING is that; FOR_YOU mixes in the videos of players the fan doesn't follow (others), MIX of them in
    every few items: those of the last OTHERS_WINDOW best first whatever their day, in turns, then older ones by day;
    with a player picked it's FOLLOWING's.

    cursor: where the last page ended (Page.cursor), which carries on with what the pages before didn't serve
    (_served: each player's items after their last one served, so one gone from every fan's feed since doesn't take
    the item that moves up into its place with it) in the order as it was at the cursor's time: its days and fading,
    what the fan had hidden by then (an item hidden since keeps its place, unserved), and only the rows that had come
    in by then (a newer one waits for the next first page). The judge's scores aren't kept with it, so a rescore
    meanwhile (views growing) can still move an item across the page's end, and For you's others are chosen again (a
    follow meanwhile moves a player across). ValueError for a cursor this never made, or a tab that isn't one."""
    if tab not in TABS:
        raise ValueError(f"The feed's tabs are {', '.join(TABS)}.")
    real = now or datetime.now(timezone.utc)
    at, offset, after = _read_cursor(cursor, real) if cursor else (real, 0, ())
    limit = max(0, min(limit, MAX_PAGE))
    everyone, circles = _everyone(db, fan, at, tz, player, cursor is not None, mix=tab == FOR_YOU and player is None)
    ordered = [(e, m, gone) for e, m, gone in everyone if player is None or e.player.id == player]
    served = _served(ordered, after, offset) if after else set()  # places in it of what the earlier pages served
    chunk: list[tuple[Entry, int | None]] = []
    end = 0
    while end < len(ordered) and len(chunk) < limit:
        e, m, gone = ordered[end]
        if not gone and end not in served:
            chunk.append((e, m))
            served.add(end)
        end += 1
    moments = _moments(db, {m for _, m in chunk if m is not None})
    items = []
    for e, m in chunk:
        if m in moments:
            e = e.model_copy(update={"moment": moments[m].title, "match": moments[m].url if moments[m].url.startswith("/match/") else None})
        items.append(e)
    more = any(not gone and i not in served for i, (_, _, gone) in enumerate(ordered) if i >= end)
    nxt = _write_cursor(at, end, [ordered[i][0].item.id for i in sorted(served)[-CURSOR_TAIL:]]) if items and more else None
    newest = max((c.newest for c in circles if c.newest), default=None)
    return Page(items=items, cursor=nxt, count=sum(not gone for _, _, gone in ordered), newest=newest, players=circles, tab=tab, layout=layout())


def _served(ordered: list[tuple[Entry, int | None, bool]], last: Sequence[str], offset: int) -> set[int]:
    """The places in the feed's order of what the pages before served, from the last few they served (a cursor's, in
    their order) and how far they read (offset). Something gone from every feed since (an admin's hide, the weekly
    check, fans' apps saying it won't play) closes the players' turns up round its gap (_turns, _mix), so items move
    up past where the last page ended; but each player's own items keep their order. So it's each player's items up
    to their last one among those last few still there, and for a player with none of them, whatever comes before
    the first of them (less the places of those ahead of it now gone). None of them still there: the first offset
    less as many places as there were of them."""
    place = {e.item.id: i for i, (e, _, _) in enumerate(ordered)}
    there = [place[i] for i in last if i in place]
    if not there:
        return set(range(max(0, offset - len(last))))
    # Those gone ahead of the first still there held places a player with none of them left may have moved up into.
    first = min(there) - next(n for n, i in enumerate(last) if i in place)
    upto: dict[int, int] = {}  # each player's last place among them
    for i in there:
        who = ordered[i][0].player.id
        upto[who] = max(upto.get(who, i), i)
    return {i for i, (e, _, _) in enumerate(ordered) if i <= upto.get(e.player.id, first - 1)}


def layout() -> str:
    """How the apps draw the fan's Feed, one of LAYOUTS: what the admin panel set (controls.feed_layout, live in
    every process within controls.REFRESH), else the server's FEED_LAYOUT."""
    chosen = controls.current().feed_layout
    return chosen if chosen in LAYOUTS else FEED_LAYOUT if FEED_LAYOUT in LAYOUTS else LAYOUTS[0]


def others(db: Session, followed: Sequence[int], now: datetime) -> dict[int, int]:
    """The players For you mixes in for a fan who follows these, OTHERS at most, each with its tier, in order: of the
    players the Feed serves a video of, in a sport that runs, those the fan doesn't follow; the fan's sports first
    (every sport alike for one who follows nobody), then those with a big moment in the last BIG_FOR (a key one of
    BIG_MOMENT or more, not taken back), then the most followed on the app (by signed-in fans, then by anyone:
    models.followers), then by the pool's order (feedpool.ids:
    its players nobody follows, the best known first). The tier is the first two of those
    (0: the fan's sport with a big moment, up to 3: neither): For you serves a tier's videos before the next's."""
    served = select(FeedCandidate.player_id).where(FeedCandidate.served(), FeedCandidate.source.in_(list(VIDEOS))).distinct().subquery()
    # Their followers: signed-in fans rank, anyone's follows break a tie.
    fans = models.followers(Follow.player_id, Follow.fan_id).where(Follow.player_id.in_(select(served.c.player_id))).subquery()
    rows = db.execute(select(Player.id, Player.sport, func.coalesce(fans.c.accounts, 0), func.coalesce(fans.c.follows, 0)).join(served, served.c.player_id == Player.id)
                      .outerjoin(fans, fans.c.id == Player.id)).all()  # fmt: skip
    mine = set(followed)
    theirs = {sports.family(s) for s in db.scalars(select(Player.sport).where(Player.id.in_(mine))) if s} if mine else set()
    taken_back = select(Moment.corrects).where(Moment.corrects.is_not(None))
    big = set(db.scalars(select(Moment.player_id).where(Moment.level == "key", Moment.priority >= BIG_MOMENT, Moment.created_at >= now - BIG_FOR,
                                                        Moment.created_at <= now, Moment.kind.not_in(rank.NOT_A_HEAD), Moment.id.not_in(taken_back))))  # fmt: skip
    place = {pid: i for i, pid in enumerate(feedpool.ids())}
    pool = [(2 * (bool(theirs) and sports.family(sport) not in theirs) + (pid not in big), -accounts, -follows, place.get(pid, len(place)), pid)
            for pid, sport, accounts, follows in rows if pid not in mine and sports.on(sport)]  # fmt: skip
    return {pid: tier for tier, *_, pid in sorted(pool)[:OTHERS]}


def _mix(mine: list[Any], theirs: list[Any]) -> list[Any]:
    """Two ordered lists as one: MIX[0] of mine, then MIX[1] of theirs, over and over, each keeping its own order;
    once one runs out, the rest of the other."""
    out: list[Any] = []
    i = j = 0
    while i < len(mine) or j < len(theirs):
        want_mine = len(out) % (MIX[0] + MIX[1]) < MIX[0]
        if (want_mine and i < len(mine)) or j >= len(theirs):
            out.append(mine[i])
            i += 1
        else:
            out.append(theirs[j])
            j += 1
    return out


def _everyone(db: Session, fan: str, now: datetime, tz: int, player: int | None = None, then: bool = False,
              mix: bool = False) -> tuple[list[tuple[Entry, int | None, bool]], list[Circle]]:
    """The fan's whole Feed in its order at now, each item with the moment it's about (by id) and whether the fan has
    hidden it since (in its place, not served), and the followed players' circles. A story two players hold goes with
    the picked player when there is one; then: the order as a page read at now had it, without what came in since.
    mix: For you, the videos of others() mixed in (_mix), each entry saying it isn't a followed player's."""
    ids = list(db.scalars(select(Follow.player_id).where(Follow.fan_id == fan).order_by(Follow.created_at)))
    strangers = others(db, ids, now) if mix else {}
    if not ids and not strangers:
        return [], []
    everyone = [*ids, *strangers]
    off = _off(db, ids)  # a turned-off sport's: not even their card's news (others() takes none of theirs)
    cards = {pid: card for pid, card in db.execute(select(PlayerCard.player_id, PlayerCard.card).where(PlayerCard.player_id.in_(everyone))).all()}
    kept = judged(db, everyone, now)
    # By item, whichever player it was hidden from: a story two players hold is one card.
    hidden: set[str] = set()  # by now: out of the order
    since: set[str] = set()  # after now (a page read earlier): in their places, not served
    for item, said in db.execute(select(FeedFeedback.item_id, FeedFeedback.created_at).where(FeedFeedback.fan_id == fan, FeedFeedback.player_id.in_(everyone))).all():
        if said is None or said <= now:
            hidden.add(item)
        else:
            since.add(item)
    whose: dict[int, Who] = {}
    mine: dict[str, list[tuple[int, Candidate]]] = {d: [] for d in DAYS}
    theirs: dict[str, list[tuple[int, Candidate]]] = {d: [] for d in DAYS}
    followed = set(ids)
    for pid in everyone:
        card = cards.get(pid)
        if card is None or pid in whose or pid in off:
            continue
        held = kept.get(pid, [])
        if pid in followed:
            candidates = [*held, *([] if any(c.item.kind == "news" for c in held) else _news(card))]
        else:
            candidates = [c for c in held if c.item.kind == "video"]  # For you's others: their videos alone
        about = card.get("player") or {}
        photo = about.get("photo")
        whose[pid] = Who(id=pid, name=str(about.get("name") or ""), sport=about.get("sport") or None, photo=photo if isinstance(photo, dict) and photo.get("url") else None)
        for c in _page(_unseen(candidates, hidden), now):
            if then and c.arrived is not None and c.arrived > now:
                continue
            (mine if pid in followed else theirs)[_day(c, now, tz)].append((pid, c))
    seen: set[str] = set()
    ordered = _in_turns(mine, player, seen)  # first: a video a followed player holds too is theirs, in its place
    if strangers:
        # Others a tier at a time (others: the fan's sports first, then a big moment today); within a tier, the last
        # OTHERS_WINDOW's best first whatever the day, in turns, then the older ones by day.
        tiered: list[tuple[str, int, Candidate]] = []
        for tier in sorted(set(strangers.values())):
            these = {d: [(pid, c) for pid, c in theirs[d] if strangers[pid] == tier] for d in DAYS}
            recent = [(d, pid, c) for d in DAYS for pid, c in these[d] if _within(c, now, OTHERS_WINDOW)]
            older = {d: [(pid, c) for pid, c in these[d] if not _within(c, now, OTHERS_WINDOW)] for d in DAYS}
            tiered += _best_in_turns(recent, seen) + _in_turns(older, None, seen)
        ordered = _mix(ordered, tiered)
    entries: list[tuple[Entry, int | None, bool]] = []
    counts: dict[int, int] = {}
    newest: dict[int, datetime] = {}
    for day, pid, c in ordered:
        came = c.arrived or _when(c.item.published_at)
        gone = c.item.id in since
        if not gone and pid in followed:
            # New for the dot and the ring only when it's of the last two days: a months-old video the sweep only
            # now found (or a rejudge) comes in fresh, but under Earlier, where nothing new is.
            if came and day in (TODAY, YESTERDAY) and (pid not in newest or came > newest[pid]):
                newest[pid] = came
            counts[pid] = counts.get(pid, 0) + 1
        entries.append((Entry(day=day, player=whose[pid], item=c.item, arrived=came.isoformat() if came else None, following=pid in followed), c.moment_id, gone))
    order = {pid: i for i, pid in enumerate(ids)}
    lead = sorted(counts, key=lambda pid: (-(newest[pid].timestamp() if pid in newest else 0), order[pid]))
    circles = [Circle(**whose[pid].model_dump(), count=counts[pid], newest=newest[pid].isoformat() if pid in newest else None) for pid in lead]
    return entries, circles


def _in_turns(days: dict[str, list[tuple[int, Candidate]]], player: int | None, seen: set[str]) -> list[tuple[str, int, Candidate]]:
    """Each day's items, the newest day first, the players taking turns down the ranking within it (_turns, carrying
    on from the day before); an item already in seen (a story two players hold) only with the first (the picked
    player's first), and noted there."""
    out: list[tuple[str, int, Candidate]] = []
    last = None
    for day in DAYS:
        ranked = []
        for pid, c in sorted(days[day], key=lambda pc: (pc[0] != player, _newest(pc[1]))):
            if c.item.id not in seen:
                seen.add(c.item.id)
                ranked.append((pid, c))
        for pid, c in _turns(ranked, last):
            last = pid
            out.append((day, pid, c))
    return out


def _best_in_turns(items: list[tuple[str, int, Candidate]], seen: set[str]) -> list[tuple[str, int, Candidate]]:
    """Items of any day as one list, the best first (their faded score, then the newest), the players taking turns
    down it (_turns); each keeps its day. An item already in seen only with the first, and noted there."""
    ranked: list[tuple[str, int, Candidate]] = []
    for day, pid, c in sorted(items, key=lambda x: _newest(x[2])):
        if c.item.id not in seen:
            seen.add(c.item.id)
            ranked.append((day, pid, c))
    day_of = {c.item.id: day for day, _, c in ranked}
    return [(day_of[c.item.id], pid, c) for pid, c in _turns([(pid, c) for _, pid, c in ranked], None)]


def _within(c: Candidate, now: datetime, window: timedelta) -> bool:
    """Whether an item was published in the last window (a pinned one always is; an undated one never)."""
    at = _when(c.item.published_at)
    return c.pinned or (at is not None and now - at <= window)


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


def _write_cursor(at: datetime, offset: int, last: Sequence[str]) -> str:
    """A next page's cursor: the time the feed was put in order at, how far it was read, and the last items served
    (CURSOR_TAIL), in their order."""
    raw = json.dumps([int(at.timestamp()), offset, list(last[-CURSOR_TAIL:])], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _read_cursor(cursor: str, now: datetime) -> tuple[datetime, int, tuple[str, ...]]:
    """A cursor's time (now instead, once it's over CURSOR_AGE old or ahead of now), offset and the last items served,
    in their order (one made before it kept them: its last alone); ValueError for one this never made."""
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("That isn't a page of the feed.") from exc
    last = raw[2] if isinstance(raw, list) and len(raw) == 3 else None
    last = [last] if isinstance(last, str) else last  # one made before cursors kept a page's last few
    if not (isinstance(raw, list) and len(raw) == 3 and all(isinstance(v, int) and not isinstance(v, bool) for v in raw[:2]) and 0 <= raw[1] <= 100_000
            and isinstance(last, list) and 1 <= len(last) <= CURSOR_TAIL and all(isinstance(i, str) and ITEM_ID.fullmatch(i) for i in last)):  # fmt: skip
        raise ValueError("That isn't a page of the feed.")
    try:
        at = datetime.fromtimestamp(raw[0], timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("That isn't a page of the feed.") from exc
    if not now - CURSOR_AGE <= at <= now + timedelta(minutes=1):
        at = now
    return at, raw[1], tuple(last)


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


def said_unplayable(db: Session, fan: str, player_id: int, item: str) -> str | None:
    """A fan's app said this video of the player's feed won't play inside it: its source's player refused it. It
    leaves this fan's Feed at once (UNPLAYABLE, kept as their words from the sheet are; said again, nothing changes),
    and the video comes back as "source:id", for their network's vote on whether it leaves everyone's
    (UNPLAYABLE_VOTES, then unplayable). None, and nothing kept, when an admin approved or pinned it: their word
    that it plays stands over any app's. ValueError for an item id this never makes; LookupError for no such video
    of the player's."""
    found, vouched = _video_of(db, player_id, item)
    if vouched:
        return None
    said = insert(FeedFeedback).values(fan_id=fan, player_id=player_id, item_id=item, verdict=UNPLAYABLE)
    db.execute(said.on_conflict_do_nothing(index_elements=["fan_id", "player_id", "item_id"]))  # their words from the sheet stand
    return f"{found[0]}:{found[1]}"


def counts_everywhere(zone: Any) -> bool:
    """Whether a fan's app saying a video won't play is a vote on whether it leaves every fan's Feed (UNPLAYABLE_VOTES):
    only from a device in FEED_COUNTRY, by the time zone it says it's in (FEED_ZONES). A video is kept because it plays
    there (candidates.refused), and may play there alone (a broadcaster's rights there), so an app abroad that's refused
    it says nothing of how it plays for the fans the Feed is for; it leaves that fan's Feed all the same. An app that
    names no zone (one from before it said) doesn't vote. A device can name any zone, but one network's vote is all it
    gets, as before. With FEED_ZONES empty, every app votes."""
    return not FEED_ZONES or (isinstance(zone, str) and zone in FEED_ZONES)


def unplayable_votes_key(video: str) -> str:
    """Redis's set of the networks whose apps said this video ("source:id") won't play inside them (UNPLAYABLE_VOTES): an
    admin's word on the video empties it (server.admin_review_candidate)."""
    return f"feed:unplayable:votes:{video}"


def unplayable(db: Session, video: str, now: datetime | None = None) -> bool:
    """Enough fans' apps said this video ("source:id", as said_unplayable gives it) won't play inside them
    (UNPLAYABLE_VOTES): its source's player refused it (its owner keeps it off other sites, which the source's facts
    didn't say). It's marked on every player's row of it, so no Feed serves it again, and a fetch that finds it again
    for someone else keeps the mark (candidates.keep). The admin panel's Feed tab lists it (admin.taken_out), where Put
    back undoes the mark (put_back), and an admin's approving or pinning it stands over the mark (rank.review): such a
    video is never marked. One the source
    says is gone, private or no longer embeddable goes at its weekly check (candidates.check), marked or not. True
    when this marked it; False when it was already, an admin vouched for it, or there's no such video."""
    source, _, external_id = video.partition(":")
    found = (source, external_id)
    if _vouched(db, found):
        return False
    rows = (FeedCandidate.source == found[0]) & (FeedCandidate.external_id == found[1])
    marked = db.execute(update(FeedCandidate).where(rows, FeedCandidate.unplayable_at.is_(None))
                        .values(unplayable_at=now or datetime.now(timezone.utc)).returning(FeedCandidate.id)).all()  # fmt: skip
    if marked:
        log.info("feed: %s %s won't play inside the app, fans' apps said: no longer served (%d row(s))", found[0], found[1], len(marked))
    return bool(marked)


def put_back(db: Session, row_id: int) -> str | None:
    """An admin says the video a candidate row is plays after all, from the Feed tab's list of those fans' apps took
    out (admin.taken_out): every player's row of it loses the mark (unplayable), and each is served again or not as
    the judge's and any admin's word on it say: nothing is approved. Its votes are the caller's to clear
    (unplayable_votes_key); fans' apps on as many networks as ever take it out again. The video ("source:id"), or
    None for no such row."""
    video = video_of_row(db, row_id)
    if video is None:
        return None
    source, _, external_id = video.partition(":")
    put = db.execute(update(FeedCandidate).where(FeedCandidate.source == source, FeedCandidate.external_id == external_id, FeedCandidate.unplayable_at.is_not(None))
                     .values(unplayable_at=None).returning(FeedCandidate.id)).all()  # fmt: skip
    if put:
        log.info("feed: %s %s put back by an admin: it plays after all (%d row(s))", source, external_id, len(put))
    return video


def video_of_row(db: Session, row_id: int) -> str | None:
    """The video ("source:id", as said_unplayable names it) a candidate row is; None for no such row."""
    found = db.execute(select(FeedCandidate.source, FeedCandidate.external_id).where(FeedCandidate.id == row_id)).first()
    return f"{found.source}:{found.external_id}" if found else None


def _video_of(db: Session, player_id: int, item: str) -> tuple[tuple[str, str], bool]:
    """The video (source, its id) an item of the player's feed is, and whether an admin approved or pinned it.
    ValueError for an item id this never makes; LookupError for no such video of the player's."""
    if not ITEM_ID.fullmatch(item):
        raise ValueError("No such item.")
    rows = db.execute(select(FeedCandidate.source, FeedCandidate.external_id, FeedCandidate.url)
                      .where(FeedCandidate.player_id == player_id, FeedCandidate.source.in_(list(VIDEOS)))).all()  # fmt: skip
    found = next(((r.source, r.external_id) for r in rows if item_id(r.url) == item), None)
    if found is None:
        raise LookupError("That video isn't in this player's feed.")
    return found, _vouched(db, found)


def _vouched(db: Session, found: tuple[str, str]) -> bool:
    """Whether an admin approved or pinned this video (source, its id) on any player's row of it."""
    video = (FeedCandidate.source == found[0]) & (FeedCandidate.external_id == found[1])
    return bool(db.scalar(select(func.count()).where(video, FeedCandidate.review.in_(("approved", "pinned")))))


# ---------------------------------------------------------------- inside


def _candidates(db: Session, player_id: int, card: dict[str, Any], now: datetime | None = None) -> list[Candidate]:
    """Everything the player's feed could show anyone: the judge's kept items, then the card's news, unless the
    judge kept stories of theirs (news.py), which are the news then. Nothing while their sport is turned off."""
    if _off(db, [player_id]):
        return []
    kept = judged(db, [player_id], now).get(player_id, [])
    return [*kept, *([] if any(c.item.kind == "news" for c in kept) else _news(card))]


def _off(db: Session, player_ids: Iterable[int]) -> set[int]:
    """The players among these whose sport is turned off (sports.on): their feed is empty, the card's news too, until
    it's back on. judged() has nothing of theirs, which mustn't be read as nothing judged yet."""
    ids = sorted(set(player_ids))
    if not ids:
        return set()
    return {pid for pid, sport in db.execute(select(Player.id, Player.sport).where(Player.id.in_(ids))).all() if not sports.on(sport)}


def _unhidden(db: Session, fan: str, player_id: int, candidates: list[Candidate]) -> list[Candidate]:
    """The candidates this fan hasn't hidden, each once, safe to link to. A video two players hold is one video: what
    the fan said of it under either (not interested, a report, won't play) hides it under both, as their Feed does;
    "not about this player" only under the player it was said of, whose teammate it may well be about."""
    if not candidates:
        return []
    said = or_(FeedFeedback.player_id == player_id, FeedFeedback.verdict != "not_about_player")
    return _unseen(candidates, set(db.scalars(select(FeedFeedback.item_id).where(FeedFeedback.fan_id == fan, said))))


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
