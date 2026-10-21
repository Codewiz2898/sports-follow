"""Where a game can be watched: its match page's Watch live (match.view, web/src/pages/MatchPage.tsx).

A source that lists a game's broadcasts puts them on its fixture (Fixture.streams: each one's page, the language
it's called in, whether the event's organiser runs it and whether it's their main one), and the game's read keeps
them (match.read). Here each is named by the platform it's on, which its address says, and they're put in the
order a fan wants them: the organiser's main broadcasts first, then those in English, then the rest, the
organiser's before others', each as the source listed it.

Where the app can play a platform's broadcast inside the page, a stream says which of its players (`embed.player`)
and what that plays (`embed.id`: a Twitch or Kick channel, a YouTube video). The app makes the player's address
itself, as only it knows the page's host, which Twitch's player must be told. Every other is a link: "Watch on
Douyu". A platform is one entry in PLATFORMS; an address on none is named by its host.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .adapters import Stream

SHOWN = 8  # the most of a game's streams a page lists: a world final has dozens of co-streams

YOUTUBE_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")
Play = Callable[[str, list[str], dict[str, list[str]]], str | None]  # (host, path, query) -> what the player plays


def _channel(site: str, name: re.Pattern[str], pages: set[str]) -> Play:
    """A platform whose player plays a channel by its name, from the channel's page on its own site ("twitch.tv/
    riotgames", with "www." or "m." or neither): not another of its sites' ("clips.twitch.tv/…"), one of the site's
    own pages (pages), nor any page deeper than a channel's ("twitch.tv/riotgames/videos")."""
    own = {f"{sub}{site}" for sub in ("", "www.", "m.")}

    def play(host: str, path: list[str], query: dict[str, list[str]]) -> str | None:
        return path[0] if host in own and len(path) == 1 and name.fullmatch(path[0]) and path[0].lower() not in pages else None
    return play


def _youtube(host: str, path: list[str], query: dict[str, list[str]]) -> str | None:
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
    return found if YOUTUBE_VIDEO.fullmatch(found) else None


@dataclass(frozen=True)
class Platform:
    name: str  # "Twitch", as a fan knows it
    hosts: tuple[str, ...]  # its sites, a subdomain of any as well ("www.twitch.tv", "m.twitch.tv")
    player: str = ""  # the app's own player for its broadcasts inside the page ("twitch"); "" for a link only
    # What that player plays, from a stream's address (its host, path and query); None where it can't (a link).
    play: Play | None = None


PLATFORMS = (
    Platform("Twitch", ("twitch.tv",), "twitch", _channel("twitch.tv", re.compile(r"[A-Za-z0-9_]{1,25}"), {
        "directory", "videos", "search", "settings", "downloads", "jobs", "turbo", "p", "subscriptions", "inventory", "wallet",
        "drops", "login", "signup", "friends", "messages", "store", "prime", "bits"})),
    Platform("YouTube", ("youtube.com", "youtu.be"), "youtube", _youtube),
    Platform("Kick", ("kick.com",), "kick", _channel("kick.com", re.compile(r"[A-Za-z0-9_-]{1,40}"), {
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
    SHOWN at most, each named by its platform and language and, where the app plays it in the page, with what."""
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
        play = found.play(host.lower(), [p for p in parts.path.split("/") if p], parse_qs(parts.query)) if found and found.play else None
        out.append({
            "url": s.url,
            "platform": found.name if found else host.removeprefix("www."),
            "language": s.language,
            "language_name": language(s.language),
            "official": s.official,
            "main": s.main,
            "embed": {"player": found.player, "id": play} if found and found.player and play else None,
        })
    return out
