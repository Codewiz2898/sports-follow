"""Where a game can be watched: its match page's Watch live (match.view, web/src/pages/MatchPage.tsx).

A source that lists a game's broadcasts puts them on its fixture (Fixture.streams: each one's page, the language
it's called in, whether the event's organiser runs it and whether it's their main one), and the game's read keeps
them (match.read). Here each is named by the platform it's on, which its address says, and they're put in the
order a fan wants them: the organiser's main broadcasts first, then those in English, then the rest, the
organiser's before others', each as the source listed it.

Where the app can play a platform's broadcast inside the page, a stream says which of its players (`embed.player`)
and what that plays (`embed.id`: a Twitch or Kick channel, a YouTube video). The web app makes the player's address
itself, as only it knows the page's host, which Twitch's player must be told. The Android app can't be that host,
so it shows the site's own page of the player alone (page, at /watch/<player>/<id>) in a WebView. Every other is a
link: "Watch on Douyu". A platform is one entry in PLATFORMS; an address on none is named by its host.

Till a fan plays it, a stream the app plays shows the platform's own picture of it where the platform has one
(`embed.poster`, the API's address for it: Player.poster): a live Twitch channel's latest frame, a YouTube video's
thumbnail. Twitch answers for a channel that isn't live with its grey "offline" picture, so the API sends the page
there only once the platform has said there's a picture (pictured), and else the page keeps its own poster.
"""

from __future__ import annotations

import base64
import hashlib
import html
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import httpx

from .adapters import Stream
from .config import APP_NAME, USER_AGENT

SHOWN = 8  # the most of a game's streams a page lists: a world final has dozens of co-streams
POSTER_FOR = 300  # seconds a platform's word on whether a stream has a picture is kept: Twitch's is 5 minutes old at most


@dataclass(frozen=True)
class Player:
    """The app's own player for a platform's broadcasts, inside a page: what it plays, where it's served from, and
    its address, playing at once, in a page at a host (the web app makes the same address, components/Watch.tsx)."""
    name: str  # "twitch": a stream's embed.player, and its /watch page's
    ids: re.Pattern[str]  # what it plays (a channel's name, a video's id), as a stream's address and /watch take it
    origin: str  # "https://player.twitch.tv": the one frame its /watch page may hold
    address: Callable[[str, str], str]  # (what it plays, the page's host) -> the player's address
    # (what it plays) -> its picture on the platform's own image host, for the page till it's played; None for a
    # platform that has no address for one (Kick's is in its API alone)
    poster: Callable[[str], str] | None = None


# (its platform's player, and a stream's host, path and query) -> what that player plays
Play = Callable[[Player, str, list[str], dict[str, list[str]]], str | None]


def _channel(site: str, pages: set[str]) -> Play:
    """A platform whose player plays a channel by its name, from the channel's page on its own site ("twitch.tv/
    riotgames", with "www." or "m." or neither): not another of its sites' ("clips.twitch.tv/…"), one of the site's
    own pages (pages), nor any page deeper than a channel's ("twitch.tv/riotgames/videos")."""
    own = {f"{sub}{site}" for sub in ("", "www.", "m.")}

    def play(player: Player, host: str, path: list[str], query: dict[str, list[str]]) -> str | None:
        return path[0] if host in own and len(path) == 1 and player.ids.fullmatch(path[0]) and path[0].lower() not in pages else None
    return play


def _youtube(player: Player, host: str, path: list[str], query: dict[str, list[str]]) -> str | None:
    """A YouTube video's page ("watch?v=…", "/live/…", "youtu.be/…") -> the video; a channel's ("/@lolesports/live",
    or an old one by its name, "youtube.com/OGNEsports1"), which names no video, none."""
    if path == ["watch"]:
        found = (query.get("v") or [""])[0]
    elif len(path) == 2 and path[0] in ("live", "embed", "shorts"):
        found = path[1]
    elif len(path) == 1 and (host == "youtu.be" or host.endswith(".youtu.be")):
        found = path[0]
    else:
        return None
    return found if player.ids.fullmatch(found) else None


@dataclass(frozen=True)
class Platform:
    name: str  # "Twitch", as a fan knows it
    hosts: tuple[str, ...]  # its sites, a subdomain of any as well ("www.twitch.tv", "m.twitch.tv")
    player: Player | None = None  # the app's own player for its broadcasts inside the page; None for a link only
    # What that player plays, from a stream's address (its host, path and query); None where it can't (a link).
    play: Play | None = None


PLATFORMS = (
    Platform("Twitch", ("twitch.tv",), Player(
        "twitch", re.compile(r"[A-Za-z0-9_]{1,25}"), "https://player.twitch.tv",
        lambda plays, host: "https://player.twitch.tv/?" + urlencode({"channel": plays, "parent": host, "autoplay": "true"}),
        lambda plays: f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{plays.lower()}-640x360.jpg",
    ), _channel("twitch.tv", {
        "directory", "videos", "search", "settings", "downloads", "jobs", "turbo", "p", "subscriptions", "inventory", "wallet",
        "drops", "login", "signup", "friends", "messages", "store", "prime", "bits"})),
    Platform("YouTube", ("youtube.com", "youtu.be"), Player(
        "youtube", re.compile(r"[A-Za-z0-9_-]{11}"), "https://www.youtube-nocookie.com",
        lambda plays, host: f"https://www.youtube-nocookie.com/embed/{quote(plays)}?autoplay=1&rel=0&playsinline=1",
        lambda plays: f"https://i.ytimg.com/vi/{quote(plays)}/hqdefault.jpg",  # as a feed video's (feed.VideoSource.picture)
    ), _youtube),
    Platform("Kick", ("kick.com",), Player(
        "kick", re.compile(r"[A-Za-z0-9_-]{1,40}"), "https://player.kick.com",
        lambda plays, host: f"https://player.kick.com/{quote(plays)}?autoplay=true",
    ), _channel("kick.com", {
        "browse", "categories", "category", "following", "search", "video", "clips", "dashboard", "settings", "about", "help",
        "community-guidelines", "terms-of-service", "privacy-policy", "dmca-policy", "careers", "contact", "brand", "login", "signup"})),
    Platform("Bilibili", ("bilibili.com",)),
    Platform("SOOP", ("sooplive.co.kr", "afreecatv.com")),
    Platform("Huya", ("huya.com",)),
    Platform("Douyu", ("douyu.com",)),
    Platform("Facebook", ("facebook.com", "fb.gg")),
    Platform("TikTok", ("tiktok.com",)),
    Platform("Trovo", ("trovo.live",)),
)
PLAYERS = {p.player.name: p.player for p in PLATFORMS if p.player}  # the app's own players, by name

# The languages broadcasts are called in, by ISO 639-1 code; another shows as its code.
LANGUAGES = {
    "en": "English", "es": "Spanish", "pt": "Portuguese", "fr": "French", "de": "German", "it": "Italian", "nl": "Dutch",
    "ru": "Russian", "uk": "Ukrainian", "pl": "Polish", "cs": "Czech", "sk": "Slovak", "hu": "Hungarian", "ro": "Romanian",
    "bg": "Bulgarian", "sr": "Serbian", "hr": "Croatian", "el": "Greek", "tr": "Turkish", "ar": "Arabic", "he": "Hebrew",
    "fa": "Persian", "sv": "Swedish", "da": "Danish", "no": "Norwegian", "fi": "Finnish", "lt": "Lithuanian", "lv": "Latvian",
    "et": "Estonian", "kk": "Kazakh", "mn": "Mongolian", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "vi": "Vietnamese",
    "th": "Thai", "id": "Indonesian", "ms": "Malay", "tl": "Filipino", "fil": "Filipino", "hi": "Hindi", "bn": "Bengali",
    "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}


def language(code: str) -> str:
    """ "English" for "en" (or "en-GB"); an unknown code as itself, upper case; "" for none."""
    code = (code or "").strip().lower()
    return LANGUAGES.get(code) or LANGUAGES.get(code.split("-")[0]) or code.upper()


def platform(host: str) -> Platform | None:
    host = host.lower().rstrip(".")
    return next((p for p in PLATFORMS if any(host == h or host.endswith(f".{h}") for h in p.hosts)), None)


def ordered(streams: list[Stream]) -> list[Stream]:
    """The organiser's main broadcasts first, then those in English, then the rest; the organiser's before
    others' among each; otherwise as the source listed them."""
    return sorted(streams, key=lambda s: (not (s.official and s.main), s.language.strip().lower().split("-")[0] != "en", not s.official))


def offer(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A read's streams (Stream's fields) for the match page: those at a web address, each once, in order (ordered),
    SHOWN at most, each named by its platform and language and, where the app plays it in the page, with what, and
    the API's address of its picture where its platform has one (the page shows it only once it loads)."""
    seen, streams = set(), []
    for r in raw:
        url = str(r.get("url") or "").strip() if isinstance(r, dict) else ""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or url.lower() in seen:
            continue
        seen.add(url.lower())
        streams.append(Stream(url=url, language=str(r.get("language") or ""), official=bool(r.get("official")), main=bool(r.get("main"))))
    out = []
    for s in ordered(streams)[:SHOWN]:
        parts = urlsplit(s.url)
        host = parts.hostname or ""
        found = platform(host)
        play = found.play(found.player, host.lower(), [p for p in parts.path.split("/") if p], parse_qs(parts.query)) if found and found.player and found.play else None
        out.append({
            "url": s.url,
            "platform": found.name if found else host.removeprefix("www."),
            "language": s.language,
            "language_name": language(s.language),
            "official": s.official,
            "main": s.main,
            "embed": {"player": found.player.name, "id": play, "poster": poster_path(found.player, play)} if found and found.player and play else None,
        })
    return out


def poster_path(player: Player, plays: str) -> str | None:
    """The API's address of what a player plays' picture (server.watch_poster), where its platform has one."""
    return f"/api/watch/{player.name}/{quote(plays, safe='')}/poster" if player.poster else None


def poster(player: str, plays: str) -> str | None:
    """Where the platform serves a picture of what one of the app's players plays (Player.poster); None for a player
    the app doesn't have, one without pictures, or what isn't of the shape its player plays."""
    found = PLAYERS.get(player)
    return found.poster(plays) if found and found.poster and found.ids.fullmatch(plays) else None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"user-agent": USER_AGENT}, timeout=5)  # follows no redirect, which is the point


async def pictured(address: str) -> bool:
    """Whether the platform has a picture at a poster's address now: an image, there and not elsewhere. Twitch's for
    a channel that isn't live sends to its grey "offline" picture (ttv-static/404_preview), which isn't one of it; a
    platform that can't be reached has none for now."""
    try:
        async with _client() as client:
            got = await client.head(address)
    except httpx.HTTPError:
        return False
    return got.status_code == 200 and got.headers.get("content-type", "").startswith("image/")


# The /watch page: its player over the whole of it, on black. Its one style, allowed by its hash (POLICY).
STYLE = "html,body{margin:0;height:100%;background:#000;overflow:hidden}iframe{display:block;width:100%;height:100%;border:0}"
STYLE_HASH = base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()
HOST = re.compile(r"[A-Za-z0-9.:-]{1,253}")  # a page's host, as Twitch's player is told it (an IPv6 one has colons)


def page(player: str, plays: str, host: str) -> tuple[str, str] | None:
    """The site's /watch page of a stream the app plays (its `embed`: which player, and what that plays), served at
    `host`: the player alone, over the whole page, playing at once (with no full screen of its own: the app's Expand
    is the way to a bigger one, and its WebView's own full screen would turn the screen upright), and the page's
    Content-Security-Policy, which lets it hold that player's frame and nothing else (no script of its own, its one
    style by its hash, no other site's frame around it). The Android app shows it in a WebView in its match page, and
    over the whole screen once expanded, as Twitch's player must sit in a page on the site it's told (its `parent`),
    which an app isn't. None for a player the app doesn't have, what
    isn't of the shape its player plays, or a host that isn't one."""
    found = PLAYERS.get(player)
    if found is None or not found.ids.fullmatch(plays) or not HOST.fullmatch(host):
        return None
    frame = (f'<iframe src="{html.escape(found.address(plays, host))}" title="{html.escape(APP_NAME)}: watch live" '
             'allow="autoplay; encrypted-media; picture-in-picture" referrerpolicy="strict-origin-when-cross-origin"></iframe>')
    body = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="robots" content="noindex">'
            f"<title>Watch live · {html.escape(APP_NAME)}</title><style>{STYLE}</style></head><body>{frame}</body></html>")
    policy = (f"default-src 'none'; style-src 'sha256-{STYLE_HASH}'; frame-src {found.origin}; "
              "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    return body, policy
