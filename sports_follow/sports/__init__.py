"""Every sport Sports Follow knows, one module each: what the sport is and every rule that's its own (base.Sport).

Adding a sport is its module plus one line in ALL; shared code asks the sport for what it needs
(sports.get(name)) and never branches on which sport it is. A sport no module knows (golf, from an
AI-built page) gets OTHER: the rules every sport shares.

A source module may use what its sport's sources share from here (cricket's innings, football's result
label), so nothing in this package imports a source at load time.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from . import basketball, chess, cricket, esports, football, tennis
from .base import Found, Seen, Sport

# In the order the app lists them.
ALL: tuple[Sport, ...] = (football.SPORT, cricket.SPORT, basketball.SPORT, tennis.SPORT, chess.SPORT, esports.SPORT)
BY_KEY: dict[str, Sport] = {s.key: s for s in ALL}
OTHER = Sport()


@lru_cache(maxsize=1)
def _names() -> dict[str, Sport]:
    """Every name a sport goes by, lower case: its key, label, other names and scoreboard kinds, and the
    names its sources give what they cover (esports games: "Valorant", "BGMI")."""
    from ..adapters import KNOWN  # here, not at the top: sources import this package

    names: dict[str, Sport] = {}
    for sport in ALL:
        for name in (sport.key, sport.label, *sport.names, *sport.kinds, *sport.other_games):
            names.setdefault(name.lower(), sport)
    for adapter in KNOWN.values():
        for name in getattr(adapter, "game_names", ()):
            names.setdefault(name.lower(), BY_KEY[adapter.sport])
    return names


def get(name: str | None) -> Sport | None:
    """The sport a name means ("Soccer" is football, "CS2" esports), or None for one no module knows."""
    return _names().get((name or "").strip().lower())


def of(name: str | None) -> Sport:
    """The sport a name means, or OTHER: the rules every sport shares."""
    return get(name) or OTHER


def of_state(state: dict[str, Any] | None) -> Sport:
    """The sport a scoreboard is of, by its kind ("battle_royale" is esports)."""
    return of((state or {}).get("kind"))


def family(name: str | None) -> str:
    """The sport a name belongs to, as the registry keys it ("esports" for any game, "football" for
    "Soccer"); a sport no module knows as its own name, lower case."""
    sport = get(name)
    return sport.key if sport else (name or "").strip().lower()


def label(name: str | None) -> str:
    """A player's sport as the app shows it: "Cricket" for "cricket", a game's own name ("CS2")."""
    text = (name or "").strip()
    return of(text).label_of(text)


def names_of(sport: Sport) -> list[str]:
    """Every name, lower case, that means this sport (a player's sport may be any of them)."""
    return sorted(n for n, s in _names().items() if s is sport)


__all__ = ["ALL", "BY_KEY", "OTHER", "Found", "Seen", "Sport", "family", "get", "label", "names_of", "of", "of_state"]
