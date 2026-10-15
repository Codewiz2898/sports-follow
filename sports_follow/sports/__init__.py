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

from ..config import SPORTS
from . import basketball, chess, cricket, esports, football, tennis
from .base import Found, Seen, Sport

# In the order the app lists them.
ALL: tuple[Sport, ...] = (football.SPORT, cricket.SPORT, basketball.SPORT, tennis.SPORT, chess.SPORT, esports.SPORT)
BY_KEY: dict[str, Sport] = {s.key: s for s in ALL}
OTHER = Sport()


def pick(every: tuple[Sport, ...], keys: set[str]) -> tuple[Sport, ...]:
    """The sports that run: all of them, or only those SPORTS_FOLLOW_SPORTS names (unknown names are ignored)."""
    return tuple(s for s in every if not keys or s.key in keys)


# The sports this build runs.
ENABLED: tuple[Sport, ...] = pick(ALL, SPORTS)


def on(name: str | None) -> bool:
    """Whether a sport runs in this build. A sport no module knows (golf) isn't one that can be turned off:
    its players' pages come from the agent either way."""
    sport = get(name)
    return sport is None or sport in ENABLED


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


def shown(sport: Sport, live: bool) -> dict[str, Any]:
    """A sport as the app knows it (GET /api/sports): what it's called, which free-text names are it, what
    its alert levels say, and whether this build runs it and has live scores for it."""
    return {
        "key": sport.key or "other",
        "label": sport.label,
        "on": sport in ENABLED,
        "live": live,
        "pattern": sport.pattern,
        "exclude": sport.exclude,
        "alerts": {level: list(words) for level, words in sport.alerts.items()},
        "stats_title": sport.stats_title,
        "team_results": sport.team_moments,
        "keeps_names": sport.keeps_names,
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
    for sport in ALL:
        if sport.colour:
            dark, light = photo_tones(sport)
            props = "; ".join([*(f"{names[k]}: {v}" for k, v in sport.colour.items() if k in names), f"--photo-dark: {dark}", f"--photo-light: {light}"])
            rules.append(f"[data-sport='{sport.key}'] {{ {props}; }}")
    return "\n".join(rules) + "\n"


def names_of(sport: Sport) -> list[str]:
    """Every name, lower case, that means this sport (a player's sport may be any of them)."""
    return sorted(n for n, s in _names().items() if s is sport)


__all__ = ["ALL", "BY_KEY", "ENABLED", "OTHER", "Found", "Seen", "Sport", "css", "family", "get", "label", "names_of", "of", "of_state", "on", "photo_tones", "pick", "shown"]
