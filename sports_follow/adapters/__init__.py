"""Structured data sources, one adapter per source system (design section 4).

Adding a source is its module plus one entry in KNOWN; removing one is deleting both. Each adapter
describes itself (base.Adapter), so the rest of the app asks it rather than naming it.
"""

from __future__ import annotations

from ..config import SOURCES
from .base import Adapter, AdapterError, Fixture, Line, LiveSearch, PlayerRef, Provider, SearchResult, Snapshot
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
# Licensed sources run only with a token, so where both run (a development build) football comes from
# Sportmonks, while cricket stays on ESPN until the Sportmonks plan covers ODIs, Tests and the IPL (the
# free plan has three T20 competitions). A public build lists only licensed sources and gets Sportmonks
# for both. Esports comes from three sources side by side, each for its own games: PandaScore for team
# titles (Valorant, CS2…), Liquipedia for battle royale (BGMI, Free Fire, Apex Legends), PUBG's own API
# for PUBG.
KNOWN: dict[str, Adapter] = {a.system: a for a in (
    SportmonksFootball(),
    EspnSoccer(),
    EspnCricket(),
    SportmonksCricket(),
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


def for_sport(sport: str | None) -> Adapter | None:
    """The adapter for a sport as the research agent names it ("Cricket", "Football", "Basketball", "Chess")."""
    wanted = (sport or "").strip().lower()
    return next((a for a in ADAPTERS.values() if wanted in a.sports), None)


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
    "SearchResult", "Snapshot", "for_sport", "pick", "providers", "source_name",
]
