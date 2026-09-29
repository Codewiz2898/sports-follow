"""Structured data sources, one adapter per source system (design section 4)."""

from __future__ import annotations

from .base import Adapter, AdapterError, Fixture, Line, PlayerRef, Snapshot
from .espn_cricket import EspnCricket
from .espn_soccer import EspnSoccer

ADAPTERS: dict[str, Adapter] = {a.system: a for a in (EspnSoccer(), EspnCricket())}


def for_sport(sport: str | None) -> Adapter | None:
    """The adapter for a sport as the research agent names it ("Cricket", "Football", "Soccer")."""
    wanted = (sport or "").strip().lower()
    return next((a for a in ADAPTERS.values() if wanted in a.sports), None)


__all__ = ["ADAPTERS", "Adapter", "AdapterError", "Fixture", "Line", "PlayerRef", "Snapshot", "for_sport"]
