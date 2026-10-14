"""Structured data sources, one adapter per source system (design section 4).

Adding a source is its module plus one entry in KNOWN; removing one is deleting both. Each adapter
describes itself (base.Adapter), so the rest of the app asks it rather than naming it.
"""

from __future__ import annotations

from .. import sports
from ..config import SOURCES
from .base import Adapter, AdapterError, Fixture, Line, LiveSearch, PlayerRef, Provider, Roster, Rostered, SearchResult, Snapshot
from .espn_basketball import EspnBasketball
from .espn_cricket import EspnCricket
from .espn_soccer import EspnSoccer
from .espn_tennis import EspnTennis
from .lichess_chess import LichessChess
from .liquipedia_esports import LiquipediaEsports
from .pandascore_esports import PandascoreEsports
from .pubg_esports import PubgEsports
from .sportmonks_cricket import SportmonksCricket
from .sportmonks_football import SportmonksFootball

# Every source, in order of preference where two serve a sport: for_sport() picks the first that runs.
# Licensed sources run only with a token, so where both run (a development build) football and cricket
# come from Sportmonks, as in a public build, which lists only licensed sources. Esports comes from three
# sources side by side, each for its own games: PandaScore for team titles (Valorant, CS2…), Liquipedia
# for battle royale (BGMI, Free Fire, Apex Legends), PUBG's own API for PUBG.
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


def pick(adapters: tuple[Adapter, ...], sources: set[str]) -> dict[str, Adapter]:
    """The adapters that run: all of them, or only those SPORTS_FOLLOW_SOURCES names."""
    return {a.system: a for a in adapters if not sources or a.system in sources}


ADAPTERS: dict[str, Adapter] = pick(ALL, SOURCES)


def for_sport(name: str | None) -> Adapter | None:
    """The adapter for a sport as the research agent names it ("Cricket", "Soccer", "Chess"): its first
    running source, or for a game, the source that covers that game ("Valorant", "BGMI"). A name of a game
    no source covers ("LoL") has none."""
    sport = sports.get(name)
    if sport is None:
        return None
    wanted = (name or "").strip().lower()
    theirs = [a for a in ADAPTERS.values() if a.sport == sport.key]
    claimed = next((a for a in theirs if wanted in {g.lower() for g in getattr(a, "game_names", ())}), None)
    if claimed or wanted not in (sport.key, sport.label.lower(), *sport.names):
        return claimed
    return theirs[0] if theirs else None


def providers(adapters: dict[str, Adapter] | None = None) -> list[tuple[Provider, list[Adapter]]]:
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
    "ADAPTERS", "ALL", "KNOWN", "Adapter", "AdapterError", "Fixture", "Line", "LiveSearch", "PlayerRef", "Provider",
    "Roster", "Rostered", "SearchResult", "Snapshot", "for_sport", "pick", "providers", "source_name",
]
