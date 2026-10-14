"""Structured data sources, one adapter per source system (design section 4)."""

from __future__ import annotations

from ..config import SOURCES
from .base import Adapter, AdapterError, Fixture, Line, PlayerRef, Snapshot
from .espn_basketball import EspnBasketball
from .espn_cricket import EspnCricket
from .espn_soccer import EspnSoccer
from .espn_tennis import EspnTennis
from .lichess_chess import LichessChess
from . import liquipedia, pandascore, pubg, sportmonks
from .liquipedia_esports import LiquipediaEsports
from .pandascore_esports import PandascoreEsports
from .pubg_esports import PubgEsports
from .sportmonks_cricket import SportmonksCricket
from .sportmonks_football import SportmonksFootball

# Licensed sources run only with a token. for_sport() picks the first adapter for a sport, so where
# both run (a development build) football and cricket come from Sportmonks, as in a public build, which
# lists only licensed sources. Esports comes from three sources side by side, each for its own games:
# PandaScore for team titles (Valorant, CS2…), Liquipedia for battle royale (BGMI, Free Fire, Apex
# Legends), PUBG's own API for PUBG.
_TOKEN = bool(sportmonks.token())
ALL: tuple[Adapter, ...] = (
    *((SportmonksFootball(),) if _TOKEN else ()),
    EspnSoccer(),
    *((SportmonksCricket(),) if _TOKEN else ()),
    EspnCricket(),
    EspnBasketball(),
    EspnTennis(),
    LichessChess(),
    *((PandascoreEsports(),) if pandascore.token() else ()),
    *((LiquipediaEsports(),) if liquipedia.token() else ()),
    *((PubgEsports(),) if pubg.token() else ()),
)


def pick(adapters: tuple[Adapter, ...], sources: set[str]) -> dict[str, Adapter]:
    """The adapters that run: all of them, or only those SPORTS_FOLLOW_SOURCES names."""
    return {a.system: a for a in adapters if not sources or a.system in sources}


ADAPTERS: dict[str, Adapter] = pick(ALL, SOURCES)


def espn_enabled() -> bool:
    """Whether any ESPN source runs (its search is shared by all of them)."""
    return any(system.startswith("espn_") for system in ADAPTERS)


def for_sport(sport: str | None) -> Adapter | None:
    """The adapter for a sport as the research agent names it ("Cricket", "Football", "Basketball", "Chess")."""
    wanted = (sport or "").strip().lower()
    return next((a for a in ADAPTERS.values() if wanted in a.sports), None)


__all__ = ["ADAPTERS", "Adapter", "AdapterError", "Fixture", "Line", "PlayerRef", "Snapshot", "espn_enabled", "for_sport", "pick"]
