"""YouTube, for the feed (curator.py): the latest videos of the channels each sport trusts, and what the
YouTube Data API v3 says of each one.

A channel's uploads come from its public feed (youtube.com/feeds/videos.xml), which costs no quota but often
answers 404 or 500 for a while, for no reason it gives. When it doesn't answer and there's a key, the channel's
uploads playlist is read from the API instead (playlistItems.list, 1 unit). What the feed's hard filters need
(how long a video runs, whether it may be embedded, where it plays, whether it's age-restricted, its views and likes) comes from
videos.list: 1 unit for up to 50 videos, whichever parts are asked for, so the same call also reads whether the
maker declared paid promotion in it (paidProductPlacementDetails), which the judge takes as sponsored, and its
shape (player, asked with maxWidth: the embedded player's size), which tells an upright video (a Short) from a wide
one. A big moment of a followed player is searched for (search.list:
searches.py), from any channel, twice: the curator keeps those searches to a cap in any 24 hours
(SPORTS_FOLLOW_FEED_MOMENT_SEARCHES). A followed player with nothing to watch this week is searched for once a day,
each one's six months' most watched once a week, and their most watched ever once a month (sweeps.py), under caps of
their own (SPORTS_FOLLOW_FEED_GAP_SEARCHES, SPORTS_FOLLOW_FEED_WIDE_SEARCHES, SPORTS_FOLLOW_FEED_FAMOUS_SEARCHES), and
none while what's left of the day's searches is what the moment searches' cap may still spend.

Runs only with a key: SPORTS_FOLLOW_YOUTUBE_KEY, or the file ~/.config/sports-follow/youtube-key (kept outside
the repo), an API key of a Google Cloud project with the YouTube Data API v3 turned on. Without one the source
is off: the admin panel's Sources tab says there's no key, and nothing else changes.

The free quota is 10,000 units a day, and, since 1 June 2026, search.list's own: 100 searches a day, 1 unit each,
which spend none of the 10,000 (the "Search Queries" bucket: developers.google.com/youtube/v3/determine_quota_cost; the
Cloud console's YouTube Data API v3 Quotas page lists both). Both reset at midnight Pacific time. Each day is counted in
Redis (budget.py), from midnight Pacific; when YouTube says one is spent, nothing is asked of it until it resets.

Terms (developers.google.com/youtube/terms): videos are shown in YouTube's own embedded player; data from the
API is kept at most 30 days without being read again (the curator's weekly check reads every stored video
again, and drops those gone).
"""

from __future__ import annotations

import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from ..config import USER_AGENT
from . import budget, cache
from .base import AdapterError, Provider, Upload, VideoFacts, VideoSource

API = "https://www.googleapis.com/youtube/v3"
FEEDS = "https://www.youtube.com/feeds/"
KEY_FILE = Path(os.environ.get("SPORTS_FOLLOW_YOUTUBE_KEY_FILE", Path.home() / ".config" / "sports-follow" / "youtube-key"))
PER_DAY = 10_000  # units; each request this module sends costs 1, but a search, which spends SEARCHES_PER_DAY instead
SEARCHES_PER_DAY = 100  # search.list calls, 1 unit each in a quota of their own, whatever they find
SEARCH_COST = 1
SEARCH_RESULTS = 50  # videos a search lists, the most watched of them: the most it may, for the same unit
# What every search leaves out (search.list's q takes a NOT, "-"): the Shorts tagged so. Most watched first, a
# search's results are mostly Shorts, and those under a minute are refused (candidates.MIN_SECONDS); its
# videoDuration can't help, as "short" is anything under four minutes, where 1-4 minute highlight clips are too.
NOT_SHORTS = "-#shorts"
# Never spent: another process may count a moment late, and a request YouTube counts may not be ours (one sent by hand).
MARGIN = 1_000
SEARCH_MARGIN = 5
PACIFIC = "America/Los_Angeles"  # where YouTube's quota day starts
# Not "youtube" alone: that's also a platform players have accounts on (curate.py), not only this source.
BUDGET = budget.Budget("youtube-data", windows=(budget.Window(86400, PER_DAY, margin=MARGIN, zone=PACIFIC),))
SEARCH_BUDGET = budget.Budget("youtube-search", windows=(budget.Window(86400, SEARCHES_PER_DAY, margin=SEARCH_MARGIN, zone=PACIFIC),))
FEED_BUDGET = budget.Budget("youtube-feeds", urls=(FEEDS,))  # the channels' public feeds: free, counted only
FEED_TTL = 20 * 60  # a channel's latest uploads, read again after this: the curator polls every half hour

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
_NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "media": "http://search.yahoo.com/mrss/"}
# What videos.list is asked of each video: 1 unit a request whichever parts it names.
PARTS = "snippet,contentDetails,statistics,status,paidProductPlacementDetails,player"
# Asked with the parts: the player part then gives the embedded player's size at the video's own shape (embedWidth,
# embedHeight), this wide for a wide video and as tall as its shape needs for an upright one.
PLAYER_WIDTH = "1280"
_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")

PROVIDER = Provider(
    name="YouTube",
    url="https://www.youtube.com",
    what="videos about players from channels each sport trusts, played in YouTube's own player.",
    budgets=(BUDGET, SEARCH_BUDGET, FEED_BUDGET),
)

_client = httpx.Client(headers={"user-agent": USER_AGENT}, timeout=20, follow_redirects=True)
_cache = cache.Cache(BUDGET.name, size=500)


@lru_cache(maxsize=1)
def key() -> str | None:
    value = os.environ.get("SPORTS_FOLLOW_YOUTUBE_KEY", "").strip()
    if not value and KEY_FILE.is_file():
        value = KEY_FILE.read_text().strip()
    return value or None


# ---------------------------------------------------------------- the channels' feeds


def _feed(channel_id: str) -> str:
    budget.take(FEED_BUDGET)
    try:
        res = _client.get(f"{FEEDS}videos.xml", params={"channel_id": channel_id})
    except httpx.HTTPError as exc:
        raise AdapterError(f"YouTube's feed unreachable: {exc}") from exc
    if res.status_code != 200:
        raise AdapterError(f"YouTube's feed answered HTTP {res.status_code} for {channel_id}")
    return res.text


def parse_feed(text: str) -> list[Upload]:
    """A channel's public feed (Atom) as its uploads, newest first."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise AdapterError(f"YouTube's feed isn't readable: {exc}") from exc
    out = []
    for entry in root.findall("atom:entry", _NS):
        video_id = entry.findtext("yt:videoId", "", _NS).strip()
        if not VIDEO_ID.fullmatch(video_id):
            continue
        thumb = entry.find("media:group/media:thumbnail", _NS)
        out.append(Upload(
            video_id=video_id,
            channel_id=entry.findtext("yt:channelId", "", _NS).strip(),
            channel=entry.findtext("atom:author/atom:name", "", _NS).strip(),
            title=entry.findtext("atom:title", "", _NS).strip(),
            description=entry.findtext("media:group/media:description", "", _NS).strip(),
            url=f"https://www.youtube.com/watch?v={video_id}",  # whichever way it was found, the same (a Short's feed link differs)
            published=_time(entry.findtext("atom:published", None, _NS)),
            thumbnail=thumb.get("url") if thumb is not None else None,
        ))
    return out


def _time(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


# ---------------------------------------------------------------- the Data API


def reset_in(now: datetime | None = None) -> float:
    """Seconds until YouTube's quota day starts again: midnight Pacific time."""
    now = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        pacific = now.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:  # no time zone database: Pacific standard time, an hour late in summer
        pacific = now.astimezone(timezone(timedelta(hours=-8)))
    midnight = (pacific + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - pacific).total_seconds()


def _fetch(path: str, params: dict[str, str], spends: budget.Budget = BUDGET, cost: int = 1) -> dict[str, Any]:
    """A Data API request, counted as cost of the quota it spends (BUDGET, or SEARCH_BUDGET for search.list)."""
    if not key():
        raise AdapterError("no YouTube key (~/.config/sports-follow/youtube-key)")
    budget.take(spends, cost=cost)
    try:
        # The key in a header, not the query string: it stays out of every URL a log line or a span may show.
        res = _client.get(f"{API}/{path}", params=params, headers={"x-goog-api-key": key() or ""})
    except httpx.HTTPError as exc:
        raise AdapterError(f"YouTube unreachable: {exc}") from exc
    try:
        data = res.json()
    except ValueError as exc:
        raise AdapterError(f"YouTube returned non-JSON for {path} (HTTP {res.status_code})") from exc
    if res.status_code != 200 or not isinstance(data, dict):
        reasons = {e.get("reason") for e in ((data.get("error") or {}).get("errors") or []) if isinstance(e, dict)} if isinstance(data, dict) else set()
        if reasons & {"quotaExceeded", "dailyLimitExceeded"}:
            budget.observe(spends, "", 0, reset_in())
            raise budget.RateLimited(f"YouTube says the day's {'searches are' if spends is SEARCH_BUDGET else 'quota is'} spent")
        if res.status_code == 429 or reasons & {"rateLimitExceeded", "userRateLimitExceeded"}:
            budget.pause(spends)
            raise budget.RateLimited("YouTube said slow down")
        if reasons & {"keyInvalid", "keyExpired", "accessNotConfigured", "forbidden"}:
            raise AdapterError(f"YouTube rejected the key ({', '.join(sorted(r for r in reasons if r))})")
        raise AdapterError(f"YouTube returned HTTP {res.status_code} for {path}")
    return data


def _get(path: str, params: dict[str, str], ttl: float) -> dict[str, Any]:
    return _cache.read(f"{path}?{urlencode(sorted(params.items()))}", ttl, lambda: _fetch(path, params))


def parse_playlist(data: dict[str, Any]) -> list[Upload]:
    """playlistItems.list's items (a channel's uploads playlist) as uploads, newest first."""
    out = []
    for item in data.get("items") or []:
        snippet = item.get("snippet") or {}
        video_id = str((snippet.get("resourceId") or {}).get("videoId") or "")
        if not VIDEO_ID.fullmatch(video_id):
            continue
        thumbs = snippet.get("thumbnails") or {}
        thumb = next((thumbs[k].get("url") for k in ("high", "medium", "default") if isinstance(thumbs.get(k), dict)), None)
        out.append(Upload(
            video_id=video_id,
            channel_id=str(snippet.get("videoOwnerChannelId") or snippet.get("channelId") or ""),
            channel=str(snippet.get("videoOwnerChannelTitle") or snippet.get("channelTitle") or ""),
            title=str(snippet.get("title") or ""),
            description=str(snippet.get("description") or ""),
            url=f"https://www.youtube.com/watch?v={video_id}",
            published=_time(snippet.get("publishedAt")),
            thumbnail=thumb,
        ))
    return out


def parse_search(data: dict[str, Any]) -> list[Upload]:
    """search.list's items as uploads, in the order it ranked them; its descriptions are cut short. Unlike every
    other part's, its titles come HTML-escaped ("Mbappe &amp; Bijan"): they're read as written."""
    out = []
    for item in data.get("items") or []:
        video_id = str((item.get("id") or {}).get("videoId") or "")
        if not VIDEO_ID.fullmatch(video_id):
            continue  # a channel or a playlist
        snippet = item.get("snippet") or {}
        thumbs = snippet.get("thumbnails") or {}
        out.append(Upload(
            video_id=video_id,
            channel_id=str(snippet.get("channelId") or ""),
            channel=html.unescape(str(snippet.get("channelTitle") or "")),
            title=html.unescape(str(snippet.get("title") or "")),
            description=html.unescape(str(snippet.get("description") or "")),
            url=f"https://www.youtube.com/watch?v={video_id}",
            published=_time(snippet.get("publishedAt")),
            thumbnail=next((thumbs[k].get("url") for k in ("high", "medium", "default") if isinstance(thumbs.get(k), dict)), None),
        ))
    return out


def seconds(duration: str | None) -> int | None:
    """An ISO 8601 duration as YouTube writes it ("PT4M13S", "P1DT2H") in seconds; None for "P0D" (a live
    stream) or one it can't read."""
    match = _DURATION.fullmatch(duration or "")
    if not match or not any(match.groups()):
        return None
    d, h, m, s = (int(g or 0) for g in match.groups())
    total = ((d * 24 + h) * 60 + m) * 60 + s
    return total or None


def _count(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _size(player: dict[str, Any]) -> dict[str, int] | None:
    """The embedded player's size at the video's shape, as the player part gives it when asked with maxWidth."""
    width, height = _count(player.get("embedWidth")), _count(player.get("embedHeight"))
    return {"width": width, "height": height} if width and height and width > 0 and height > 0 else None


def parse_videos(data: dict[str, Any]) -> dict[str, VideoFacts]:
    """videos.list's items as what the feed reads of each, by id."""
    out = {}
    for item in data.get("items") or []:
        video_id = str(item.get("id") or "")
        if not VIDEO_ID.fullmatch(video_id):
            continue
        details, stats, status, snippet, placement, player = (
            item.get(k) or {} for k in ("contentDetails", "statistics", "status", "snippet", "paidProductPlacementDetails", "player"))
        region = details.get("regionRestriction") or {}
        allowed = region.get("allowed")
        out[video_id] = VideoFacts(
            video_id=video_id,
            seconds=seconds(details.get("duration")),
            views=_count(stats.get("viewCount")),
            likes=_count(stats.get("likeCount")),
            embeddable=status.get("embeddable") is True,
            public=status.get("privacyStatus") == "public" and status.get("uploadStatus") in ("processed", None),
            live=snippet.get("liveBroadcastContent") in ("live", "upcoming"),
            allowed=tuple(allowed) if isinstance(allowed, list) else None,
            blocked=tuple(region.get("blocked") or ()),
            paid=placement.get("hasPaidProductPlacement") is True,
            age_restricted=(details.get("contentRating") or {}).get("ytRating") == "ytAgeRestricted",
            raw={
                "contentDetails": details,
                "statistics": stats,
                "status": status,
                "snippet": {k: snippet[k] for k in ("liveBroadcastContent", "categoryId", "defaultAudioLanguage", "tags") if k in snippet}
                | {"thumbnails": sorted(k for k, v in (snippet.get("thumbnails") or {}).items() if isinstance(v, dict))},  # the sizes it has (picture)
            } | ({"player": size} if (size := _size(player)) else {}),  # its shape (upright), not the player's own HTML
        )
    return out


class YouTube(VideoSource):
    system = "youtube"
    name = "YouTube"
    provider = PROVIDER
    covers = "Videos about players, for the feed (YouTube)"
    id_pattern = VIDEO_ID
    channel_pattern = CHANNEL_ID
    platform = "youtube"
    search_cost = SEARCH_COST

    def search_budgets(self) -> tuple[budget.Budget, ...]:
        """search.list spends a quota of its own, not the 10,000 units."""
        return (SEARCH_BUDGET,)

    def embed(self, video_id: str) -> dict[str, str] | None:
        """The app plays YouTube's own embedded player (youtube-nocookie.com), by the video's id."""
        return {"youtube_id": video_id} if VIDEO_ID.fullmatch(video_id) else None

    def picture(self, video_id: str, details: dict[str, Any] | None = None) -> str | None:
        """YouTube's own thumbnail of the video, from its image host (the Content-Security-Policy allows it), the
        biggest it has: maxresdefault (1280x720) or sddefault (640x480) where videos.list said the video has one
        (details: what it said, as the candidate keeps it), else hqdefault (480x360), which every video has. The
        4:3 ones letterbox the 16:9 picture, so a 16:9 frame that covers crops the bars off exactly."""
        if not VIDEO_ID.fullmatch(video_id):
            return None
        sizes = ((details or {}).get("snippet") or {}).get("thumbnails") or []
        name = "maxresdefault" if "maxres" in sizes else "sddefault" if "standard" in sizes else "hqdefault"
        return f"https://i.ytimg.com/vi/{video_id}/{name}.jpg"

    def upright(self, details: dict[str, Any] | None = None) -> bool:
        """Taller than it's wide (a Short), by the player's size videos.list gave; a video last read before that was
        asked is wide until it's read again."""
        size = (details or {}).get("player") or {}
        width, height = _count(size.get("width")), _count(size.get("height"))
        return bool(width and height and height > width)

    @classmethod
    def available(cls) -> bool:
        return bool(key())

    def uploads(self, channel_id: str) -> list[Upload]:
        """A channel's latest uploads (15 or so), newest first: from its public feed, or, while that doesn't
        answer, from its uploads playlist (1 unit)."""
        if not CHANNEL_ID.fullmatch(channel_id):
            raise AdapterError(f"not a YouTube channel id: {channel_id!r}")
        try:
            return parse_feed(_cache.read(f"feed:{channel_id}", FEED_TTL, lambda: _feed(channel_id)))
        except AdapterError:
            if not key():
                raise
        playlist = "UU" + channel_id[2:]  # every channel's uploads playlist
        return parse_playlist(_get("playlistItems", {"part": "snippet", "playlistId": playlist, "maxResults": "15"}, FEED_TTL))

    def facts(self, video_ids: list[str], max_age: float = 3600) -> dict[str, VideoFacts]:
        """What YouTube says of each video now, a request (1 unit) for each `batch` of them; a video it leaves
        out is gone. A read younger than max_age seconds may be answered from the cache."""
        ids = sorted({v for v in video_ids if VIDEO_ID.fullmatch(v)})
        out: dict[str, VideoFacts] = {}
        for i in range(0, len(ids), self.batch):
            part = ids[i:i + self.batch]
            out |= parse_videos(_get("videos", {"part": PARTS, "id": ",".join(part), "maxResults": str(self.batch), "maxWidth": PLAYER_WIDTH}, max_age))
        return out

    def search(self, query: str, after: datetime | None, country: str) -> list[Upload]:
        """search.list (1 of the day's SEARCHES_PER_DAY, never cached): videos published after then (or ever, for None), most viewed
        first, that may be embedded and played off YouTube (syndicated), as a viewer in this country is shown them,
        but for the Shorts tagged #shorts (NOT_SHORTS)."""
        params = {
            "part": "snippet", "type": "video", "q": f"{query} {NOT_SHORTS}", "order": "viewCount", "maxResults": str(SEARCH_RESULTS),
            "videoEmbeddable": "true", "videoSyndicated": "true", "regionCode": country,
        }  # fmt: skip
        if after is not None:
            params["publishedAfter"] = after.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return parse_search(_fetch("search", params, spends=SEARCH_BUDGET, cost=SEARCH_COST))
