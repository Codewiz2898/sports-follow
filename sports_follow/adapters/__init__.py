"""Structured data sources, one adapter per source system (design section 4)."""

from __future__ import annotations

from ..config import SOURCES
from .base import Adapter, AdapterError, Fixture, Line, PlayerRef, Snapshot
from .espn_basketball import EspnBasketball
from .espn_cricket import EspnCricket
from .espn_soccer import EspnSoccer
from .espn_tennis import EspnTennis
from .lichess_chess import LichessChess
from . import sportmonks
from .sportmonks_football import SportmonksFootball

# Licensed sources come first, so for_sport() picks them over ESPN; they run only with a token.
LICENSED: tuple[Adapter, ...] = (SportmonksFootball(),) if sportmonks.token() else ()
ALL: tuple[Adapter, ...] = (*LICENSED, EspnSoccer(), EspnCricket(), EspnBasketball(), EspnTennis(), LichessChess())


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
