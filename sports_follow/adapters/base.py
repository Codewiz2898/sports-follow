"""What every adapter is and returns (design section 4: an id plus an adapter is a binding).

An adapter is code for one source system. It turns a player's external id into fixtures, live
snapshots and stats. Parsing is kept in pure functions over the source's JSON, so adapters are
tested against recorded responses without the network.

Each adapter also describes itself (its name, provider, ids, whether this build can run it), so the
rest of the app asks it instead of naming it: adding a source is its module plus one entry in
adapters.KNOWN, and removing one is deleting both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .budget import Budget

# What most sources' athlete ids look like. An id goes into the source's URL or query as it is, so one
# of any other shape is refused before anything is sent.
DIGITS = re.compile(r"[0-9]{1,12}")


@dataclass
class PlayerRef:
    """A player as one source system knows them."""

    system: str  # "espn_soccer", "espn_cricket", "espn_basketball", "espn_tennis", "lichess_chess", "pandascore_esports"…
    athlete_id: str
    name: str
    team_ids: list[str]
    team_names: list[str]
    profile_url: str | None = None
    # Other sides the player turns out for, by name, from the research agent ("Australia A",
    # "Melbourne Stars"). A source's athlete record names one team; fixtures for the rest are
    # found by matching these against the competitors' names.
    other_teams: list[str] = field(default_factory=list)
    # The competition family the id lives in, where the source needs it: "nba", "wnba", "atp", "wta"; an
    # esports pro's game ("valorant", "cs-go").
    league: str | None = None
    # Birth date as the source gives it, ISO ("1988-11-05"); how the registry confirms a name match.
    born: str | None = None
    # The game an esports pro plays, as fans name it ("Valorant", "BGMI"): their sport on Sports Follow.
    game: str | None = None


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
    # The published team sheet, before kick-off as well as during: athlete_id -> "starting" | "bench".
    # Empty until the source has one (and for sports without line-ups).
    squad: dict[str, str] = field(default_factory=dict)


@dataclass
class Rostered:
    """An athlete as a source's own list has them, for the registry: esports pros come from their
    sources, not Wikidata (registry.import_roster)."""

    key: str  # their registry id: "pandascore:123", "lp:pubgm:4567"
    ref: PlayerRef
    aliases: list[str] = field(default_factory=list)  # other names (a real name beside the handle)
    country: str | None = None
    current: bool = True  # on a team, or playing, today


@dataclass
class Roster:
    """How a source's roster run went. A complete one lets the registry drop the rows of the source it
    didn't see again (retired, moved on)."""

    complete: bool
    leagues: list[str] | None = None  # the leagues (games, wikis) it read in full; None: all of them
    grace: timedelta = timedelta(0)  # how long a row not seen is kept anyway


@dataclass
class SearchResult:
    """One athlete a search found: on Sports Follow, in the registry, or in a source's own search."""

    key: str  # "espn_basketball:4433403"; "player:12" for a player no source knows
    name: str
    sport: str  # "Basketball", "MMA"
    detail: str  # "Indiana Fever · WNBA", "GM · Norway · 2823 classical"
    system: str | None = None
    athlete_id: str | None = None
    league: str | None = None
    player_id: int | None = None  # set when the athlete is already on Sports Follow
    following: bool = False
    followers: int = 0
    live_scores: bool = False  # a source we follow live covers them; otherwise the agent builds the page
    inactive: bool = False
    exact: bool = False  # the name is exactly what was typed
    aka: str | None = None  # the alias it was found by, when the name doesn't start with what was typed
    status: str | None = None  # the card's status, for players on Sports Follow
    next: dict[str, Any] | None = None  # {"title", "start_utc", "competition"} or {"live": "headline"}
    qid: str | None = None  # the athlete's Wikidata id, when the registry knows them
    born: int | None = None  # birth year, shown to tell namesakes apart
    matched: bool = True  # False: only alike ("catalin clark" ~ Caitlin Clark), offered as "did you mean"
    popularity: int = 0  # Wikipedia editions for registry athletes; a headshot or title for live hits
    _rank: tuple = ()


@dataclass(frozen=True)
class LiveSearch:
    """A provider's own player search, asked when the registry has few matches for what a fan typed.

    find(query, sport, adapters) returns results best first; sport is the fan's filter (None for every
    sport), adapters are the provider's running ones. It's asked under a filter its adapters serve."""

    find: Callable[[str, str | None, list["Adapter"]], list[SearchResult]]
    unfiltered: bool = True  # asked when the fan hasn't picked a sport, too
    min_length: int = 2  # letters typed before it's asked
    by_word: bool = False  # asked word by word for "did you mean" (its search has no typo tolerance)


@dataclass(frozen=True)
class Provider:
    """Who runs a source, as the Credits page names them; several adapters can share one (ESPN's four).
    A provider may also offer what its adapters share: a player search, and a way to recognise a typed
    name without the research agent."""

    name: str  # "ESPN": on the Credits page, and named when its search doesn't answer in time
    url: str
    what: str  # what it gives, after the sports it covers: "schedules, live scores and player stats."
    note: str | None = None
    licence: str | None = None
    licence_url: str | None = None
    licensed: bool = True  # False: unofficial endpoints, development builds only (credited last)
    search: LiveSearch | None = None
    # recognise(query, adapters): the (adapter, ref) a full typed name surely means; False when the name
    # is ambiguous, which ends the lookup (the agent decides); None to leave it to the next provider.
    recognise: Callable[[str, list["Adapter"]], "tuple[Adapter, PlayerRef] | bool | None"] | None = None
    # research(adapters): its public API as the research agent's prompt points to it, given its running
    # adapters ("https://lichess.org/api/broadcast/top"), for an API the agent can read without a key of
    # ours (tools.fetch_url spends its budget). agent.py lists each running provider's.
    research: Callable[[list["Adapter"]], str] | None = None
    budgets: tuple[Budget, ...] = ()  # what its API key may spend (adapters/budget.py)


class Adapter:
    """One source system. Subclasses set the class attributes and implement every method below.

    Optional, where a source has it (asked for with getattr): search(name, league) -> [PlayerRef] (the
    registry's name lookup), spare() (whether background reads leave live games their share),
    snapshots(locators) (below), warm() (a shared index kept fresh); for esports, games and game_of(ref),
    game_names (every name its games go by) and game_of_league(league, country); and a roster for the
    registry: roster_prefix (its registry ids' prefix, "pandascore:"), roster(keep) -> Roster, which
    hands keep() its athletes (Rostered) a page at a time, and roster_every (seconds) for a source whose
    list is read more often than the weekly import."""

    system: str  # "espn_cricket": the key of its bindings, events and athlete ids
    sport: str  # the sport it serves (sports.Sport.key): "cricket"
    live_cadence: int  # seconds between polls while an event is live
    name: str  # the source as a player's page names it: "ESPNcricinfo"
    provider: Provider
    covers: str  # what it covers, on the Credits page: "Cricket (ESPNcricinfo)"
    id_pattern: re.Pattern[str] = DIGITS  # the shape of its athlete ids
    preview_fixtures = True  # False: a preview before following shows stats only (fixtures take many reads)

    @classmethod
    def available(cls) -> bool:
        """Whether this build can run it: a licensed source needs its token."""
        return True

    def budgets(self) -> tuple[Budget, ...]:
        """What its requests spend (adapters/budget.py): its provider's, and any other source's it reads."""
        return self.provider.budgets

    def credits_also(self) -> list[dict[str, Any]]:
        """Others whose data comes in through this source, for the Credits page (FIDE's ratings via Lichess)."""
        return []

    def credit(self, snap: Snapshot) -> str:
        """Who a live card credits for this snapshot: the source, and anyone else whose data it carries."""
        return self.name

    def find_player(self, name: str) -> PlayerRef | None:
        raise NotImplementedError

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        """The athlete with this id in the source (a search result a fan picked), or None."""
        raise NotImplementedError

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        raise NotImplementedError

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        """The event now. final=True may serve a cached copy: a finished match doesn't change."""
        raise NotImplementedError

    # Optional, for a source that fetches many events in one request (Sportmonks football):
    #   def snapshots(self, locators: dict[Any, dict[str, Any]]) -> dict[Any, Snapshot]
    # keyed as given, leaving out events the source didn't return. An adapter with it has all its
    # armed and live events polled together, one request a round (structured.poll_lane).

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        """A finished fixture's result from the player's side: "Won 2–1", "India won by 57 runs"."""
        raise NotImplementedError

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        """Season or recent-form stats, plus a short note on where they come from."""
        raise NotImplementedError

    def history_pages(self, ref: PlayerRef, anchor: date) -> list[dict[str, Any]]:
        """Stretches of the player's past older than fixtures() reaches (last season's schedule, a
        month of draws), each read once by history.py. A page's "cost" is its requests (default 1)."""
        raise NotImplementedError

    def history(self, ref: PlayerRef, page: dict[str, Any]) -> list[Fixture]:
        """The finished fixtures in one page from history_pages()."""
        raise NotImplementedError


class AdapterError(RuntimeError):
    pass
