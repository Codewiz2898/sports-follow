"""Every sport Sports Follow knows, one module each: what the sport is and every rule that's its own (base.Sport).

Adding a sport is its module plus one line in ALL; shared code asks the sport for what it needs
(sports.get(name)) and never branches on which sport it is. A sport no module knows (golf, from an
AI-built page) gets OTHER: the rules every sport shares. A group of sports that share their rules is one
module with an entry per sport (esports: a sport per game), and its own sport in GROUPS: a setting may
name the group for all of them, and it's the sport of an athlete whose game can't be told.

A source module may use what its sport's sources share from here (cricket's innings, football's result
label), so nothing in this package imports a source at load time.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from functools import lru_cache
from typing import Any, overload

from .. import controls
from ..config import SPORTS
from . import basketball, chess, cricket, esports, football, tennis
from .base import Fact, Found, Notable, Seen, Sport

# In the order the app lists them.
ALL: tuple[Sport, ...] = (football.SPORT, cricket.SPORT, basketball.SPORT, tennis.SPORT, chess.SPORT, *esports.GAMES)
# Groups of ALL's sports (Sport.group), each with a sport of its own that isn't listed: "esports", every game.
GROUPS: tuple[Sport, ...] = (esports.SPORT,)
BY_KEY: dict[str, Sport] = {s.key: s for s in (*ALL, *GROUPS)}
OTHER = Sport()


def named(sport: Sport, keys: set[str] | frozenset[str]) -> bool:
    """Whether a set of sport keys (a setting, the admin panel's) names this sport: by its key, or its group's
    ("esports" names every game)."""
    return sport.key in keys or bool(sport.group) and sport.group in keys


def pick(every: tuple[Sport, ...], keys: set[str]) -> tuple[Sport, ...]:
    """The sports that run: all of them, or only those SPORTS_FOLLOW_SPORTS names, by key or by group (unknown
    names are ignored)."""
    return tuple(s for s in every if not keys or named(s, keys))


def members(group: Sport) -> tuple[Sport, ...]:
    """The sports of a group ("esports": every game), in the app's order; none for a sport that isn't one."""
    return tuple(s for s in ALL if s.group and s.group == group.key)


def expand(keys: Iterable[str], among: Iterable[str] | None = None) -> list[str]:
    """Sport keys as given, a group's standing for each of its sports ("esports": every game's), or for those of
    them among some (the ones that run), in the order given. A group none of whose sports is among them stands
    for none."""
    known = None if among is None else set(among)
    out: list[str] = []
    for key in keys:
        group = members(BY_KEY[key]) if key in BY_KEY else ()
        out += [s.key for s in group if known is None or s.key in known] if group else [key]
    return list(dict.fromkeys(out))


def within(key: str) -> list[str]:
    """A sport's key, and for a group its sports' too: what a filter or a campaign of it takes in ("esports":
    itself and every game)."""
    return list(dict.fromkeys([key, *expand([key])]))


# The sports this build may run (SPORTS_FOLLOW_SPORTS): the admin panel turns them off and on within these.
ALLOWED: tuple[Sport, ...] = pick(ALL, SPORTS)


class Running(Sequence[Sport]):
    """The sports that run now (ENABLED): those this build may run, less those the admin panel has turned
    off (controls.py). Read again on each use, so turning one off or on needs no restart."""

    def now(self) -> tuple[Sport, ...]:
        off = controls.current().sports_off
        return tuple(s for s in ALLOWED if not named(s, off))

    @overload
    def __getitem__(self, i: int) -> Sport: ...
    @overload
    def __getitem__(self, i: slice) -> tuple[Sport, ...]: ...
    def __getitem__(self, i: int | slice) -> Sport | tuple[Sport, ...]:
        return self.now()[i]

    def __len__(self) -> int:
        return len(self.now())

    def __iter__(self) -> Iterator[Sport]:
        return iter(self.now())

    def __contains__(self, sport: object) -> bool:
        return sport in self.now()

    def __repr__(self) -> str:
        return f"Running{self.now()!r}"


ENABLED: Sequence[Sport] = Running()


def on(name: str | None) -> bool:
    """Whether a sport runs in this build; a group while any of its sports does (an esports pro whose game
    can't be told is findable while any game runs). A sport no module knows (golf) isn't one that can be
    turned off: its players' pages come from the agent either way."""
    sport = get(name)
    return sport is None or sport in ENABLED or any(s in ENABLED for s in members(sport))


def running_keys() -> list[str]:
    """The keys of the sports that run now, and of the groups any of them is in (the registry's rows of an
    esports pro whose game can't be told are "esports")."""
    now = list(ENABLED)
    return [s.key for s in now] + [g.key for g in GROUPS if any(s.group == g.key for s in now)]


@lru_cache(maxsize=1)
def _names() -> dict[str, Sport]:
    """Every name a sport goes by, lower case: its key, label, other names and scoreboard kinds; a group's
    after its sports' (a game's names are the game's)."""
    names: dict[str, Sport] = {}
    for sport in (*ALL, *GROUPS):
        for name in (sport.key, sport.label, *sport.names, *sport.kinds):
            names.setdefault(name.lower(), sport)
    return names


def get(name: str | None) -> Sport | None:
    """The sport a name means ("Soccer" is football, "CS2" is CS2, "Esports" the esports group), or None for
    one no module knows."""
    return _names().get((name or "").strip().lower())


def of(name: str | None) -> Sport:
    """The sport a name means, or OTHER: the rules every sport shares."""
    return get(name) or OTHER


def of_state(state: dict[str, Any] | None) -> Sport:
    """The sport whose rules a scoreboard is read by, by its kind ("battle_royale" is esports': every game's)."""
    return of((state or {}).get("kind"))


def family(name: str | None) -> str:
    """The sport a name is, as the registry keys it ("football" for "Soccer", "cs2" for "Counter-Strike",
    "esports" for a game no source covers); a sport no module knows as its own name, lower case."""
    sport = get(name)
    return sport.key if sport else (name or "").strip().lower()


def label(name: str | None) -> str:
    """A player's sport as the app shows it: "Cricket" for "cricket", a game's own name ("CS2")."""
    text = (name or "").strip()
    return of(text).label_of(text)


def shown(sport: Sport, live: bool) -> dict[str, Any]:
    """A sport as the app knows it (GET /api/sports): what it's called, which free-text names are it, what
    its alert levels say, whether this build runs it and has live scores for it, the group it's drawn as
    (its board and line art: "esports" for a game; "" for a sport drawn as itself), and its colours for an
    app that doesn't read /api/sports.css (the Android app): the block, text on it, softer text on it and
    the colour as text on ink, or None for a sport that wears ink."""
    return {
        "key": sport.key or "other",
        "label": sport.label,
        "on": sport in ENABLED or any(s in ENABLED for s in members(sport)),
        "group": sport.group,
        "live": live,
        "pattern": sport.pattern,
        "exclude": sport.exclude,
        "alerts": {level: list(words) for level, words in sport.alerts.items()},
        "stats_title": sport.stats_title,
        "team_results": sport.team_moments,
        "keeps_names": sport.keeps_names,
        "colour": {k: v for k, v in sport.colour.items() if k in ("sport", "on", "on_soft", "ink")} or None,
    }


def _rgb(colour: str) -> tuple[float, ...]:
    """ "#1f5bef" -> (0.12, 0.36, 0.94)."""
    return tuple(int(colour.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))


def photo_tones(sport: Sport) -> tuple[str, str]:
    """The two CSS colours a player's photo is recoloured with (web/src/styles.css, .duo): its darks become
    the sport's deep colour D, its lights the block colour L. The page multiplies the grey photo by the
    second colour and screens D over it, D + grey × L' × (1 − D), so L' = (L − D) / (1 − D) makes it exactly
    D + grey × (L − D)."""
    light, dark = sport.colour["sport"], sport.colour.get("deep") or sport.colour["on"]
    pre = [(lc - dc) / (1 - dc) if dc < 1 else 0 for lc, dc in zip(_rgb(light), _rgb(dark))]
    return dark, "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in pre)


def css() -> str:
    """Each sport's colour on [data-sport] (GET /api/sports.css): the block, text on it, softer text on it,
    the colour as text on ink, and the two a photo is recoloured with."""
    names = {"sport": "--sport", "on": "--on-sport", "on_soft": "--on-sport-soft", "ink": "--sport-on-ink"}
    rules = []
    for sport in (*ALL, *GROUPS):
        if sport.colour:
            dark, light = photo_tones(sport)
            props = "; ".join([*(f"{names[k]}: {v}" for k, v in sport.colour.items() if k in names), f"--photo-dark: {dark}", f"--photo-light: {light}"])
            rules.append(f"[data-sport='{sport.key}'] {{ {props}; }}")
    return "\n".join(rules) + "\n"


def names_of(sport: Sport) -> list[str]:
    """Every name, lower case, that means this sport (a player's sport may be any of them)."""
    return sorted(n for n, s in _names().items() if s is sport)


__all__ = [
    "ALL", "ALLOWED", "BY_KEY", "ENABLED", "GROUPS", "OTHER", "Fact", "Found", "Notable", "Seen", "Sport", "css", "expand", "family", "get", "label", "members",
    "named", "names_of", "of", "of_state", "on", "photo_tones", "pick", "running_keys", "shown", "within",
]
