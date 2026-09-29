"""What every adapter returns (design section 4: an id plus an adapter is a binding).

An adapter is code for one source system. It turns a player's external id into fixtures, live
snapshots and stats. Parsing is kept in pure functions over the source's JSON, so adapters are
tested against recorded responses without the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass
class PlayerRef:
    """A player as one source system knows them."""

    system: str  # "espn_soccer", "espn_cricket", "espn_basketball", "espn_tennis", "lichess_chess"
    athlete_id: str
    name: str
    team_ids: list[str]
    team_names: list[str]
    profile_url: str | None = None
    # Other sides the player turns out for, by name, from the research agent ("Australia A",
    # "Melbourne Stars"). A source's athlete record names one team; fixtures for the rest are
    # found by matching these against the competitors' names.
    other_teams: list[str] = field(default_factory=list)
    # The competition family the id lives in, where the source needs it: "nba", "wnba", "atp", "wta".
    league: str | None = None
    # Birth date as the source gives it, ISO ("1988-11-05"); how the registry confirms a name match.
    born: str | None = None


@dataclass
class Fixture:
    """One event involving one of the player's teams, upcoming or finished."""

    source_id: str  # the source's event id; with the system it is the event's dedupe key
    title: str  # "India v West Indies", "Al Khaleej vs Al Nassr"
    competition: str
    start_utc: datetime | None
    venue: str | None
    status: str  # scheduled | live | final | postponed
    team_ids: list[str]
    locator: dict[str, Any]  # everything snapshot() needs
    team_names: list[str] = field(default_factory=list)  # in the same order as team_ids
    detail: str | None = None  # "2nd ODI", "Matchday 7"
    result: dict[str, Any] | None = None  # scores and winner for finished events


@dataclass
class Line:
    """One player's numbers in one event."""

    headline: str  # "67* (71)", "1 goal"
    stats: list[dict[str, str]]  # [{"label": "Balls", "value": "71"}, …] in display order


@dataclass
class Snapshot:
    """The state of one event at one moment."""

    status: str  # scheduled | live | final | postponed
    score_label: str
    clock_label: str
    state: dict[str, Any]  # sport-specific structure the app renders (teams, innings, scorers)
    lines: dict[str, Line] = field(default_factory=dict)  # athlete_id -> line
    moments: list[dict[str, Any]] = field(default_factory=list)  # newest first
    source_url: str | None = None


class Adapter(Protocol):
    system: str
    sports: tuple[str, ...]  # sport names this adapter answers for, lower case
    live_cadence: int  # seconds between polls while an event is live

    def find_player(self, name: str) -> PlayerRef | None: ...

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        """The athlete with this id in the source (a search result a fan picked), or None."""
        ...

    def fixtures(self, ref: PlayerRef) -> list[Fixture]: ...

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        """The event now. final=True may serve a cached copy: a finished match doesn't change."""
        ...

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        """A finished fixture's result from the player's side: "Won 2–1", "India won by 57 runs"."""
        ...

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        """Season or recent-form stats, plus a short note on where they come from."""
        ...


class AdapterError(RuntimeError):
    pass
