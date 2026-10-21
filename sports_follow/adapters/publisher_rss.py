"""Publishers' RSS, for the feed (news.py): the latest stories in the feeds each sport reads (Sport.news_feeds), each
as its headline, a link to the publisher's page, when it was published and the feed's own short description.

A feed is read at most every FEED_TTL through the source cache (cache.py), and asked conditionally: with the ETag
and Last-Modified its last answer gave, so a feed that hasn't changed answers 304 and nothing. RSS 2.0, RSS 1.0
(RDF) and Atom are read alike. Of a story only what the feed says of it is kept, never the page itself: the
description is cut to a sentence or two (DESCRIPTION_MAX) and the full text some feeds carry (content:encoded) is
never read. The picture a feed attaches to a story is kept as its address (image), which the app loads from the
publisher as they serve it; nothing of it is fetched or copied here. Addresses are written one way (canonical), so a story two feeds list, or one feed lists with tracking
parameters, is one story.

No key and no published limit: each request is counted (the publisher-rss budget), in the curator's feed lane.
Each publisher's terms for showing headlines with links are in the pull request that added it (BHA-325).
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_tz
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from ..config import USER_AGENT
from . import budget, cache
from .base import AdapterError, Article, NewsSource, Provider

BUDGET = budget.Budget("publisher-rss")  # no published limit: counted, never refused
FEED_TTL = 15 * 60  # a feed's stories, read again after this: the curator polls every 20 minutes
MAX_BYTES = 5_000_000  # a feed bigger than this isn't read
DESCRIPTION_MAX = 300  # characters of a feed's description kept, cut at a word
FEED = re.compile(r"https://[a-z0-9.-]+\.[a-z]{2,}(?::\d+)?/[^\s]*")
# Query parameters that say where a click came from, not which story: dropped, so one story has one address.
TRACKING = re.compile(r"^(?:utm_.*|at_.*|mc_.*|ex_cid|ocid|cmp|icid|ito|fbclid|gclid|ref|ref_src|src|from|rss|partner|source|feed|ftag)$", re.I)
# Zones feeds write by name that email.utils doesn't know (it reads GMT, UT and the US ones): Sky's BST, VLR's IST.
ZONES = {"BST": 1.0, "IST": 5.5, "CET": 1.0, "CEST": 2.0, "EET": 2.0, "EEST": 3.0, "AEST": 10.0, "AEDT": 11.0, "JST": 9.0, "SGT": 8.0}
# A picture's address as kept: https (an http one is asked for over https, as the app's pages are), no spaces or quotes.
IMAGE = re.compile(r"https://[a-z0-9.-]+\.[a-z]{2,}(?::\d+)?/[^\s\"'<>]*", re.I)
IMAGE_MAX = 1000  # characters of a picture's address; a longer one isn't kept
_IMAGE_FILE = re.compile(r"\.(?:jpe?g|png|webp|gif|avif)(?:$|[?#])", re.I)
_IMG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = re.compile(r"""\b(src|width|height)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")

PROVIDER = Provider(
    name="Publishers' RSS",
    url="https://www.rssboard.org/rss-specification",
    what="headlines about players from the publishers' feeds each sport reads, each linked to the publisher's own page.",
    budgets=(BUDGET,),
)

_client = httpx.Client(headers={"user-agent": USER_AGENT, "accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8"},
                       timeout=20, follow_redirects=True)  # fmt: skip
_cache = cache.Cache(BUDGET.name, size=200)


def canonical(url: str) -> str:
    """A story's address one way, however a feed linked it: https, the host in lower case, no fragment, no tracking
    parameters (TRACKING), the rest sorted, and no slash at the end of the path."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower() + (f":{parts.port}" if parts.port else "")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)))
    path = parts.path or "/"
    return urlunsplit(("https", host, path.rstrip("/") if len(path) > 1 else path, query, ""))


def plain(text: str | None, limit: int | None = None) -> str:
    """A feed's text (which may be HTML, escaped or not) as plain words on one line, cut at a word to limit."""
    words = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", html.unescape(text or "")))).strip()
    if limit is None or len(words) <= limit:
        return words
    return (words[:limit].rsplit(" ", 1)[0] if " " in words[:limit] else words[:limit]).rstrip(" ,;:-–—") + "…"


def when(value: str | None) -> datetime | None:
    """A feed's date in UTC: RFC 822 as RSS writes it ("Tue, 20 Oct 2026 16:13:27 GMT", "… BST"), or ISO 8601 as Atom
    and some RSS feeds do. A time with no zone is taken as UTC; one that can't be read is None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    named = re.search(r"\b([A-Z]{3,4})$", value)
    offset = ZONES.get(named[1]) if named else None
    parsed = parsedate_tz(value[: named.start()].strip() if offset is not None and named else value)
    if parsed is None:
        return None
    try:
        at = datetime(*parsed[:6], tzinfo=timezone.utc)
    except ValueError:
        return None
    seconds = offset * 3600 if offset is not None else parsed[9] or 0
    return at - timedelta(seconds=seconds)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node: ET.Element, *names: str) -> ET.Element | None:
    for name in names:
        for child in node:
            if _local(child.tag) == name:
                return child
    return None


def _text(node: ET.Element, *names: str) -> str:
    child = _child(node, *names)
    return "".join(child.itertext()).strip() if child is not None else ""


def _link(item: ET.Element) -> str:
    """An item's link: RSS's <link>, else a <guid> that's a link; Atom's alternate <link href>."""
    for child in item:
        if _local(child.tag) == "link":
            href = child.get("href")
            if href is not None:
                if child.get("rel", "alternate") == "alternate":
                    return href.strip()
            elif (child.text or "").strip():
                return (child.text or "").strip()
    guid = _child(item, "guid", "id")
    text = (guid.text or "").strip() if guid is not None else ""
    return text if guid is not None and guid.get("isPermaLink") != "false" and text.startswith(("http://", "https://")) else ""


def _picture(url: str | None) -> str | None:
    """A picture's address as kept (IMAGE), or None: one written //host/… or http://… is asked for over https."""
    url = html.unescape((url or "").strip())
    if url.startswith("//"):
        url = "https:" + url
    elif url.lower().startswith("http://"):
        url = "https://" + url[7:]
    return url if len(url) <= IMAGE_MAX and IMAGE.fullmatch(url) else None


def _an_image(node: ET.Element, url: str) -> bool:
    """Whether a media:content or an enclosure is a picture: by what it says it is, else by its file's name."""
    medium, kind = (node.get("medium") or "").lower(), (node.get("type") or "").lower()
    if medium or kind:
        return medium == "image" or kind.startswith("image/")
    return bool(_IMAGE_FILE.search(url))


def _width(node: ET.Element) -> int:
    try:
        return int(node.get("width") or 0)
    except ValueError:
        return 0


def image(item: ET.Element, description: str = "") -> str | None:
    """The picture a feed attaches to a story, as an https address: Media RSS's media:content that's an image (the
    widest of several, a media:group's too), else its media:thumbnail (the widest), else an image <enclosure> (Atom's
    <link rel="enclosure">), else the first <img> in the description that isn't a 1-pixel counter. None without one."""
    contents: list[tuple[int, str]] = []
    thumbs: list[tuple[int, str]] = []
    enclosures: list[str] = []
    for node in item.iter():
        name = _local(node.tag)
        url = (node.get("url") or "").strip()
        if name == "content" and url and _an_image(node, url):
            contents.append((_width(node), url))
        elif name == "thumbnail" and url:
            thumbs.append((_width(node), url))
        elif name == "enclosure" and url and _an_image(node, url):
            enclosures.append(url)
        elif name == "link" and node.get("rel") == "enclosure" and node.get("href") and _an_image(node, node.get("href") or ""):
            enclosures.append(node.get("href") or "")
    ordered = [u for _, u in sorted(contents, key=lambda c: -c[0])] + [u for _, u in sorted(thumbs, key=lambda c: -c[0])] + enclosures
    for tag in _IMG.findall(html.unescape(description)):
        attrs = {m[1].lower(): m[2] or m[3] or m[4] for m in _ATTR.finditer(tag)}
        if attrs.get("width") == "1" or attrs.get("height") == "1":
            continue
        ordered.append(attrs.get("src") or "")
    return next((p for p in map(_picture, ordered) if p), None)


def parse(text: str, feed: str, outlet: str) -> list[Article]:
    """A feed (RSS 2.0, RSS 1.0 or Atom) as its stories, in the feed's order, each with the picture the feed attaches
    to it (image); a story with no headline or no web link is left out."""
    try:
        root = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", text))
    except ET.ParseError as exc:
        raise AdapterError(f"{outlet}'s feed isn't readable: {exc}") from exc
    if _local(root.tag) not in ("rss", "RDF", "feed"):
        raise AdapterError(f"{outlet}'s feed isn't RSS or Atom (<{_local(root.tag)}>)")
    items = [n for n in root.iter() if _local(n.tag) in ("item", "entry")]
    out = []
    for item in items:
        title, link = plain(_text(item, "title")), _link(item)
        if not title or not link.startswith(("http://", "https://")):
            continue
        description = _text(item, "description", "summary")
        out.append(Article(
            url=canonical(link), title=title, description=plain(description, DESCRIPTION_MAX),
            outlet=outlet, feed=feed, published=when(_text(item, "pubDate", "published", "date", "updated", "issued")), image=image(item, description),
        ))  # fmt: skip
    return out


def _fetch(feed: str) -> dict[str, str | None]:
    """The feed now, asked with what its last answer said (ETag, Last-Modified): a 304 is that answer again."""
    before = _cache.peek(feed, cache.KEEP)
    headers = {}
    if isinstance(before, dict):
        if before.get("etag"):
            headers["if-none-match"] = str(before["etag"])
        if before.get("modified"):
            headers["if-modified-since"] = str(before["modified"])
    budget.take(BUDGET)
    try:
        res = _client.get(feed, headers=headers)
    except httpx.HTTPError as exc:
        raise AdapterError(f"feed unreachable: {feed}: {type(exc).__name__}") from exc
    if res.status_code == 304 and isinstance(before, dict):
        return before
    if res.status_code != 200:
        raise AdapterError(f"feed answered HTTP {res.status_code}: {feed}")
    if len(res.content) > MAX_BYTES:
        raise AdapterError(f"feed too big ({len(res.content):,} bytes): {feed}")
    return {"etag": res.headers.get("etag"), "modified": res.headers.get("last-modified"), "text": decode(res.content, res.charset_encoding)}


def decode(body: bytes, charset: str | None) -> str:
    """A feed's text: by the charset its HTTP answer names, else the one its XML declaration does, else UTF-8 (XML's
    own default), so a feed that says its encoding only in <?xml encoding=…?> keeps its accents."""
    declared = re.match(rb"\s*<\?xml[^>]*encoding=[\"']([A-Za-z0-9._-]+)", body)
    for name in (charset, declared[1].decode() if declared else None, "utf-8"):
        if name:
            try:
                return body.decode(name)
            except (LookupError, UnicodeDecodeError):
                continue
    return body.decode("utf-8", errors="replace")


class PublisherRss(NewsSource):
    system = "publisher_rss"
    name = "Publishers' RSS"
    provider = PROVIDER
    covers = "Headlines about players, for the feed (publishers' RSS)"
    feed_pattern = FEED
    platform = "rss"

    def articles(self, feed: str, outlet: str = "") -> list[Article]:
        """A feed's stories, from a read at most FEED_TTL old."""
        if not FEED.fullmatch(feed):
            raise AdapterError(f"not a feed address: {feed!r}")
        doc = _cache.read(feed, FEED_TTL, lambda: _fetch(feed))
        return parse(str(doc.get("text") or ""), feed, outlet or (urlsplit(feed).hostname or feed))
