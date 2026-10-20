"""Structured data sources, one adapter per source system (design section 4).

Adding a source is its module plus one entry in KNOWN; removing one is deleting both. Each adapter
describes itself (base.Adapter), so the rest of the app asks it rather than naming it.
"""

from __future__ import annotations

from collections.abc import ItemsView, Iterator, KeysView, Mapping, MutableMapping, Sequence, ValuesView
from typing import Any

from .. import controls, sports
from ..config import SOURCES
from .base import Absence, Adapter, AdapterError, Article, Fixture, Line, LiveGame, LiveSearch, NewsSource, PlayerRef, Provider, Roster, Rostered, SearchResult, Snapshot, SourceTeam, Upload, VideoFacts, VideoSource
from .espn_basketball import EspnBasketball
from .espn_cricket import EspnCricket
from .espn_soccer import EspnSoccer
from .espn_tennis import EspnTennis
from .lichess_chess import LichessChess
from .liquipedia_esports import LiquipediaEsports
from .pandascore_esports import PandascoreEsports
from .pubg_esports import PubgEsports
from .publisher_rss import PublisherRss
from .sportmonks_cricket import SportmonksCricket
from .sportmonks_football import SportmonksFootball
from .youtube import YouTube

# Every source, in order of preference where two serve a sport: for_sport() picks the first that runs.
# Licensed sources run only with a token, so where both run (a development build) football and cricket
# come from Sportmonks, as in a public build, which lists only licensed sources. Each esports game (a sport
# of its own) comes from one of three sources: PandaScore for team titles (Valorant, CS2…), Liquipedia for
# battle royale (BGMI, Free Fire, Apex Legends), PUBG's own API for PUBG.
KNOWN: dict[str, Adapter] = {a.system: a for a in (
    SportmonksFootball(),
    EspnSoccer(),
    SportmonksCricket(),
    EspnCricket(),
    EspnBasketball(),
    EspnTennis(),
    LichessChess(),
    PandascoreEsports(),
    LiquipediaEsports(),
    PubgEsports(),
)}
# The ones this build can run.
ALL: tuple[Adapter, ...] = tuple(a for a in KNOWN.values() if a.available())
# Every source of videos about players, for the feed (curator.py). Each takes the channels of its own shape
# that a sport lists (Sport.video_channels); like KNOWN, adding one is its module plus an entry here.
VIDEOS: dict[str, VideoSource] = {v.system: v for v in (
    YouTube(),
)}
# Every source of stories about players, for the feed (news.py). Each takes the feeds of its own shape that a sport
# lists (Sport.news_feeds); like VIDEOS, adding one is its module plus an entry here.
NEWS: dict[str, NewsSource] = {n.system: n for n in (
    PublisherRss(),
)}


def videos() -> list[VideoSource]:
    """The video sources that run now: this build can run them (their key), SPORTS_FOLLOW_SOURCES lists them
    (or lists none), and the admin panel hasn't turned them off. Which sports' channels they read is the
    curator's: those of the sports that run."""
    off = controls.current().sources_off
    return [v for v in VIDEOS.values() if v.available() and (not SOURCES or v.system in SOURCES) and v.system not in off]


def news() -> list[NewsSource]:
    """The news sources that run now, as videos() says of video sources. Which sports' feeds they read is the
    curator's: those of the sports that run."""
    off = controls.current().sources_off
    return [n for n in NEWS.values() if n.available() and (not SOURCES or n.system in SOURCES) and n.system not in off]


def feed_source(system: str) -> VideoSource | NewsSource | None:
    """The feed's source a stored row names (FeedCandidate.source): a video source or a news source, which says what
    its rows are (kind) and where (platform); None for one no longer known."""
    return VIDEOS.get(system) or NEWS.get(system)


def pick(adapters: tuple[Adapter, ...], sources: set[str], enabled: Sequence[sports.Sport] = sports.ALL) -> dict[str, Adapter]:
    """The adapters that run: all of them, or only those SPORTS_FOLLOW_SOURCES names, that serve a sport that
    runs (a source of several games runs while any of them does, and reads only theirs: Adapter.running)."""
    keys = {s.key for s in enabled}
    return {a.system: a for a in adapters if (not sources or a.system in sources) and keys.intersection(a.sports)}


class Running(MutableMapping[str, Adapter]):
    """The sources that run now (ADAPTERS), by system, in order of preference: those this build can run
    (ALL) and SPORTS_FOLLOW_SOURCES lists, of the sports that run now, less those the admin panel has
    turned off (controls.py). Built again when the panel changes something, so that needs no restart;
    each read sees one whole set, so a loop over it isn't cut short by a change. (A write, which only
    tests make, lasts until the next change.)"""

    def __init__(self) -> None:
        self._built: tuple[int, dict[str, Adapter]] = (-1, {})

    def now(self) -> dict[str, Adapter]:
        seen = controls.version()
        built, running = self._built
        if built != seen:
            off = controls.current().sources_off
            running = {system: a for system, a in pick(ALL, SOURCES, sports.ENABLED).items() if system not in off}
            self._built = (seen, running)
        return running

    def __getitem__(self, system: str) -> Adapter:
        return self.now()[system]

    def __setitem__(self, system: str, adapter: Adapter) -> None:
        self._built = (controls.version(), {**self.now(), system: adapter})

    def __delitem__(self, system: str) -> None:
        running = dict(self.now())
        del running[system]
        self._built = (controls.version(), running)

    def __iter__(self) -> Iterator[str]:
        return iter(self.now())

    def __len__(self) -> int:
        return len(self.now())

    def __contains__(self, system: object) -> bool:
        return system in self.now()

    def get(self, system: str, default: Any = None) -> Any:  # one read, not two
        return self.now().get(system, default)

    def keys(self) -> KeysView[str]:
        return self.now().keys()

    def values(self) -> ValuesView[Adapter]:
        return self.now().values()

    def items(self) -> ItemsView[str, Adapter]:
        return self.now().items()

    def __repr__(self) -> str:
        return f"Running({list(self.now())!r})"


ADAPTERS: Running = Running()


def serving(sport: sports.Sport) -> list[Adapter]:
    """The running sources of a sport, in order of preference; of a group, those of any of its sports."""
    keys = {sport.key, *(s.key for s in sports.members(sport))}
    return [a for a in ADAPTERS.values() if keys.intersection(a.sports)]


def for_sport(name: str | None) -> Adapter | None:
    """The adapter for a sport as the research agent names it ("Cricket", "Soccer", "Chess", "BGMI"): its
    first running source. For a group ("Esports": a pro whose game isn't said), the first of any of its
    sports', which looks the pro up by name across its games."""
    sport = sports.get(name)
    theirs = serving(sport) if sport else []
    return theirs[0] if theirs else None


def providers(adapters: Mapping[str, Adapter] | None = None) -> list[tuple[Provider, list[Adapter]]]:
    """The providers of the running sources, each with its running adapters, in order of preference."""
    out: dict[Provider, list[Adapter]] = {}
    for a in (ADAPTERS if adapters is None else adapters).values():
        out.setdefault(a.provider, []).append(a)
    return list(out.items())


def source_name(system: str) -> str:
    """A source as a page names it ("ESPNcricinfo"); a system no adapter has any more (an old binding) as itself."""
    adapter = KNOWN.get(system)
    return adapter.name if adapter else system


__all__ = [
    "ADAPTERS", "ALL", "KNOWN", "NEWS", "VIDEOS", "Absence", "Adapter", "AdapterError", "Article", "Fixture", "Line", "LiveGame", "LiveSearch", "NewsSource", "PlayerRef",
    "Provider", "SourceTeam", "Roster", "Rostered", "SearchResult", "Snapshot", "Upload", "VideoFacts", "VideoSource", "feed_source", "for_sport", "news", "pick",
    "providers", "serving", "source_name", "videos",
]
