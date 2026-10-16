"""Structured data sources, one adapter per source system (design section 4).

Adding a source is its module plus one entry in KNOWN; removing one is deleting both. Each adapter
describes itself (base.Adapter), so the rest of the app asks it rather than naming it.
"""

from __future__ import annotations

from collections.abc import ItemsView, Iterator, KeysView, Mapping, MutableMapping, Sequence, ValuesView
from typing import Any

from .. import controls, sports
from ..config import SOURCES
from .base import Absence, Adapter, AdapterError, Fixture, Line, LiveGame, LiveSearch, PlayerRef, Provider, Roster, Rostered, SearchResult, Snapshot
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


def pick(adapters: tuple[Adapter, ...], sources: set[str], enabled: Sequence[sports.Sport] = sports.ALL) -> dict[str, Adapter]:
    """The adapters that run: all of them, or only those SPORTS_FOLLOW_SOURCES names, of the sports that run."""
    keys = {s.key for s in enabled}
    return {a.system: a for a in adapters if (not sources or a.system in sources) and a.sport in keys}


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
    "ADAPTERS", "ALL", "KNOWN", "Absence", "Adapter", "AdapterError", "Fixture", "Line", "LiveGame", "LiveSearch", "PlayerRef", "Provider",
    "Roster", "Rostered", "SearchResult", "Snapshot", "for_sport", "pick", "providers", "source_name",
]
