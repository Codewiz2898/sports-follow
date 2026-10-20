"""A player's Feed (BHA-296): what fans, creators and the player share about them, judged before a fan sees it.

Three layers. L0 fetches candidates from the sources (YouTube first) into feed_candidates; L1, the judge, keeps
the ones really about the player and says why in one line; L2, here, puts what is left in front of one fan at
serve time: their hidden items out, grouped around the player's moments, newest first. This layer never waits on
a model or a source: it reads rows, so a feed is as fast as a follow list, and with nothing judged it is just
the news the player's page already has.

`judged` is where the pipeline plugs in: the rows the curator judged and scored (rank.py), served as videos,
and stories from the publishers' feeds (news.py) as news, each with the number of outlets that ran it. While a
player has any of those stories they are the feed's news, and the curator puts them on the card too (news_for,
rank.cards), so the player page's news is theirs; without any, the card's news is. Their score fades with age (HALF_LIFE); a page holds one video from each channel and
PER_MOMENT around each moment; older ones still being watched are "the ones everyone's seen" (evergreen). If
reading them fails, a feed is the card's news alone (never emptier than the News tab was), and Today's rail,
which takes no news (the Wire has it), is empty.

The web app draws it (web/src/components/Feed.tsx): the Feed tab, Overview's "From the fans" and Today's
"From your players". A fan's "…" sheet sends feedback (`feedback`), kept per fan as follows and mutes are.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
THIS_WEEK = "This week"
EARLIER = "Earlier"
HALF_LIFE = timedelta(days=3)  # a video's score halves over this, so last week's best gives way to today's
PER_MOMENT = 2  # videos on a page around one moment: the best two of the many edits of one goal
NEWS_ON_CARD = 10  # stories a player page's news holds (news_for), newest first


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
    video: Video | None = None
    post: Post | None = None


class Group(BaseModel):
    head: str  # the moment's title, or "This week" / "Earlier"
    moment: bool  # around a moment of the player's: the line opens its game
    when: datetime | None = None
    match: str | None = None  # the game's page in the app ("/match/<source>/<id>"), when the moment had one
    items: list[Item]


class Feed(BaseModel):
    player_id: int
    groups: list[Group]
    evergreen: list[Item]  # "The ones everyone's seen": the famous ones, apart from the week
    count: int  # every item in groups and evergreen


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
                       FeedCandidate.score, FeedCandidate.moment_id, FeedCandidate.review)
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
                    c = _video(embed, source.name if source else "", r, now)
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


def _video(embed: dict[str, str] | None, source: str, r: Any, now: datetime) -> Candidate | None:
    """A scored row as a video item, its score faded by age: an evergreen one keeps its own, and a pinned one
    fades only past its pin, so it leads however old, and is never put among the evergreen."""
    if not embed:
        return None  # a video the app's player can't play
    kept, features, verdict = r.candidate or {}, r.features or {}, r.verdict or {}
    pinned = r.review == "pinned"
    score = (r.score or 0.0) - (rank.PIN if pinned else 0.0)
    published = rank.when(kept.get("published"))
    evergreen = not pinned and rank.evergreen(features, published, now)
    age = (now - published) if published else HALF_LIFE * 4
    faded = (score if evergreen else score * 0.5 ** (max(age, timedelta(0)) / HALF_LIFE)) + (rank.PIN if pinned else 0.0)
    item = Item(
        id=item_id(r.url), kind="video", tag=rank.tag(verdict), title=str(kept.get("title") or "").strip() or "A video", url=r.url,
        source=str(kept.get("channel") or source), published_at=str(kept.get("published") or "") or None,
        reason=str(verdict.get("reason") or "").strip() or "A video from a channel this sport trusts.",
        video=Video(**embed, duration=rank.whole(features.get("seconds")), views=rank.whole(features.get("views"))),
    )  # fmt: skip
    channel = str(kept.get("channel_id") or kept.get("channel") or r.url)
    return Candidate(item=item, moment_id=None if evergreen else r.moment_id, evergreen=evergreen, score=round(faded, 6), channel=channel, pinned=pinned)


def _faded(score: float, pinned: bool, published: datetime | None, now: datetime) -> float:
    """A row's score faded by age, a pinned one only past its pin."""
    age = (now - published) if published else HALF_LIFE * 4
    return score * 0.5 ** (max(age, timedelta(0)) / HALF_LIFE) + (rank.PIN if pinned else 0.0)


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
    )  # fmt: skip
    faded = _faded((r.score or 0.0) - (rank.PIN if pinned else 0.0), pinned, published, now)
    return Candidate(item=item, moment_id=r.moment_id, score=round(faded, 6), channel=str(kept.get("channel_id") or ""), pinned=pinned)


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


def _page(candidates: list[Candidate]) -> list[Candidate]:
    """What one page shows of a player's ranked candidates: one video from each channel (and one more among the
    evergreen ones, a list apart), and PER_MOMENT around each moment, the best of each kept. News and posts
    aren't thinned."""
    channels: set[tuple[bool, str]] = set()  # (evergreen, channel id)
    per_moment: dict[int, int] = {}
    out = []
    for c in _ranked(candidates):
        if c.item.kind == "video":
            if (c.evergreen, c.channel or c.item.source) in channels:
                continue
            if c.moment_id is not None:
                if per_moment.get(c.moment_id, 0) >= PER_MOMENT:
                    continue
                per_moment[c.moment_id] = per_moment.get(c.moment_id, 0) + 1
            channels.add((c.evergreen, c.channel or c.item.source))
        out.append(c)
    return out


def feed_for(db: Session, player_id: int, fan: str, now: datetime | None = None) -> Feed | None:
    """The player's Feed for this fan, or None for no such player."""
    card = db.get(PlayerCard, player_id)
    if card is None:
        return None
    now = now or datetime.now(timezone.utc)
    shown = _page(_unhidden(db, fan, player_id, _candidates(db, player_id, card.card, now)))
    moments = _moments(db, {c.moment_id for c in shown if c.moment_id is not None and not c.evergreen})
    groups = _arrange([c for c in shown if not c.evergreen], moments, now)
    evergreen = [c.item for c in _ranked(c for c in shown if c.evergreen)]
    return Feed(player_id=player_id, groups=groups, evergreen=evergreen, count=sum(len(g.items) for g in groups) + len(evergreen))


def shares_for(db: Session, fan: str) -> list[Share]:
    """Each followed player's top video or post, in the order they were followed. News never comes here:
    the Wire carries it. With nothing judged for any of them the rail is absent."""
    ids = list(db.scalars(select(Follow.player_id).where(Follow.fan_id == fan).order_by(Follow.created_at)))
    kept = judged(db, ids) if ids else {}
    out = []
    for player_id in ids:
        top = _ranked(_unhidden(db, fan, player_id, [c for c in kept.get(player_id, []) if c.item.kind != "news" and not c.evergreen]))
        if top:
            out.append(Share(player_id=player_id, item=top[0].item))
    return out


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
    hidden = set(db.scalars(select(FeedFeedback.item_id).where(FeedFeedback.fan_id == fan, FeedFeedback.player_id == player_id)))
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
    """A group per moment, the latest first, then This week and Earlier."""
    around: dict[int, list[Candidate]] = {}
    week: list[Candidate] = []
    earlier: list[Candidate] = []
    for c in candidates:
        if c.moment_id in moments:
            around.setdefault(c.moment_id, []).append(c)
        elif c.pinned or ((at := _when(c.item.published_at)) and now - at <= WEEK):
            week.append(c)
        else:
            earlier.append(c)
    groups = []
    for moment_id in sorted(around, key=lambda i: moments[i].created_at, reverse=True):
        m = moments[moment_id]
        match = m.url if m.url.startswith("/match/") else None
        groups.append(Group(head=m.title, moment=True, when=m.created_at, match=match, items=[c.item for c in _ranked(around[moment_id])]))
    for head, these in ((THIS_WEEK, week), (EARLIER, earlier)):
        if these:
            groups.append(Group(head=head, moment=False, items=[c.item for c in _ranked(these)]))
    return groups
