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

from .. import sports

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
    # esports pro's game as the source names it ("valorant", "cs-go", "pubgmobile").
    league: str | None = None
    # Birth date as the source gives it, ISO ("1988-11-05"); how the registry confirms a name match.
    born: str | None = None
    # The game an esports pro plays, as the app names it ("Valorant", "BGMI"): their sport on Sports Follow
    # (Adapter.sport_of).
    game: str | None = None


@dataclass
class Stream:
    """One broadcast of a game, where its source lists them (an esports series' streams): its page, in the
    language it's called in (ISO 639-1, "en"; "" where the source doesn't say), whether the event's organiser
    runs it, and whether it's the organiser's main one. Which platform it's on, and whether the app can play it
    in the page, its address says (watch.py)."""

    url: str
    language: str = ""
    official: bool = False
    main: bool = False


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
    # How big the source rates the game's event, where it rates them (an esports tournament's tier), on
    # LiveGame.tier's scale: 3 a world event, 2 a top league, 1 a smaller one; 0 unrated. A league-wide
    # schedule's games are ranked by their sport (Sport.notable), which reads it.
    tier: int = 0
    # What else the source knows that its sport ranks a game by (Sport.notable), in the shape the sport's
    # sources share: football's {"table": [home's place, away's]}, cricket's {"super_over": True}.
    facts: dict[str, Any] = field(default_factory=dict)
    # The sport it's of, from a source of several (an esports game's: "valorant"); "" for the source's one.
    sport: str = ""
    # Where it can be watched, where the source lists its broadcasts, in the source's order.
    streams: list[Stream] = field(default_factory=list)


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
    # Who its lines are, by the same ids, where the source names them (a scorecard's batters and bowlers):
    # a game's own moments name its players (Sport.game_moments). Optional; a line of someone unnamed isn't told.
    names: dict[str, str] = field(default_factory=dict)


@dataclass
class LiveGame:
    """One game a source has on right now, whoever follows it (an adapter's live_now()): its score as a
    followed player's card would show it, and who's playing, followable by the source's own ids, the
    source's order (a starting eleven, the top boards)."""

    source_id: str  # the source's event id, as Fixture.source_id
    title: str  # "Arsenal vs Chelsea", "India v West Indies", "Gukesh D v Carlsen, Magnus"
    competition: str
    start_utc: datetime | None
    snapshot: Snapshot
    players: list[PlayerRef] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)  # its sides' names, when it has teams: whose players the registry knows
    league: str | None = None  # where a source covers several games (esports), this one's, as the registry's league
    # The Fixture.source_ids a followed player's card has this game under, where that isn't source_id (a chess
    # board is a fixture of each of its players): how Today knows it's one of the fan's games (live.offer).
    fixtures: list[str] = field(default_factory=list)
    # How big the source rates the game's event, where it rates them (an esports tournament's tier): a bigger
    # tier's games come first in Live now, before the most followed and the best known (live.offer). Compared
    # across a sport's sources: 3 a world event, 2 a top league, 1 a smaller one; 0 unrated.
    tier: int = 0
    # The sport it's of, from a source of several (an esports game's: "valorant"); "" for the source's one.
    sport: str = ""
    # The rest of its line-ups (substitutes, an impact player), by the source's ids, where a source's match()
    # names them: whose lines its match page names (a scorecard's), though only `players` are offered to follow.
    bench: list[PlayerRef] = field(default_factory=list)
    # A finished game's match() read that isn't all its source will have, or may yet change: a chess board's moves
    # it couldn't read, or a round with more games to come; a Dota 2 map OpenDota hasn't parsed yet. Its match
    # page reads it again later instead of keeping it as the game's last word (match.read).
    partial: bool = False
    # Where it can be watched (Fixture.streams), as its fixture has them: its match page's Watch live (watch.py).
    streams: list[Stream] = field(default_factory=list)


@dataclass
class SourceTeam:
    """A team as a source knows it (an adapter's teams(), team() and find_teams()): the team registry's row of it
    (teams.py), which fans follow, and with its roster today a campaign page follows every player on it in one
    tap (campaigns.py)."""

    system: str  # the source's system, as PlayerRef.system
    team_id: str  # the source's id for the team
    name: str  # "T1"
    game: str | None = None  # where a sport's sources cover several games (esports), its game as fans name it ("League of Legends")
    players: list[PlayerRef] = field(default_factory=list)  # who plays for it now, followable by the source's own ids
    # The sport it plays (sports.Sport.key), from a source of several (an esports team's game: "valorant"); "" for
    # the source's one.
    sport: str = ""
    short: str = ""  # the source's short name or code for it, which fans type too: "ARS", "LAL", "MI"
    competition: str = ""  # what it plays in, as a search result's line says it: "Premier League", "NBA", "International"
    country: str = ""  # where it's from, where the source says: "England", "India"
    # How big a team the source makes it, where it rates them (the tier of its tournaments; a top league's), on
    # Fixture.tier's scale: 3 a world event's, 2 a top league's, 1 a smaller one's; 0 unrated. Search's tie-break.
    tier: int = 0


@dataclass
class Absence:
    """A player out of their team's games, injured or suspended, as a source lists them (an adapter's
    sidelined()): one spell, by the source's own id for it, whoever follows the player."""

    source_id: str  # the source's id for the spell: one player may have two (an injury and a ban)
    ref: PlayerRef  # who, by the source's id for them, with their team and birth date as it has them
    kind: str  # "injury", "suspension", or "other" (not available for another reason): its sport words it
    what: str  # as the source says it: "Back Injury", "Red Card Suspension"
    since: date | None = None
    until: date | None = None  # when they're expected back, if the source says


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
    snapshots(locators) (below), warm() (a shared index kept fresh); for esports, games (its own names for
    the games it covers: PandaScore's slugs, Liquipedia's wikis); and a roster for the registry:
    roster_prefix (its registry ids' prefix, "pandascore:"), roster(keep, sports) -> Roster, which hands
    keep() its athletes of those sports (Rostered) a page at a time, and roster_every (seconds) for a
    source whose list is read more often than the weekly import; and for a source that has changed how it names its
    athletes, rekey(ref) -> PlayerRef | None: a bound player under the id it gives them now (None: the
    same), which the worker moves every binding to (structured.rekey_bound). Its id_pattern then refuses
    the old ids, and the registry drops its rows holding one. A source that lists teams' rosters has
    team(team_id) -> SourceTeam | None and find_teams(name) -> [SourceTeam], with team_id_pattern the shape
    of its team ids: a campaign page follows a whole team through them (campaigns.py).

    A source fans can follow teams from (teams.py) has, for its sports whose athletes turn out for teams
    (Sport.team): teams() -> [SourceTeam], every team it covers in its sports that run (running()), players
    left empty, read weekly for the team registry in a few requests; and team_fixtures(team_id) -> [Fixture],
    one team's games, finished and to come, over the window fixtures() reads for a player of theirs, each with
    the teams' ids (Fixture.team_ids), the follow's refresh. Its team(team_id) gives the team page its squad
    (players), and its result_label(fixture, [team_id]) says each result from the team's side. One whose team ids
    say more than its boards do (ESPN basketball's carry the league) has board_id(team_id) -> str, the id its
    boards (Snapshot.state) give that team, so a result is told from the team's side (Sport.side_final)."""

    system: str  # "espn_cricket": the key of its bindings, events and athlete ids
    # The sports it serves (sports.Sport.key): ("cricket",); a source of several esports games, each game's.
    sports: tuple[str, ...]
    live_cadence: int  # seconds between polls while an event is live
    name: str  # the source as a player's page names it: "ESPNcricinfo"
    provider: Provider
    covers: str  # what it covers, on the Credits page: "Cricket (ESPNcricinfo)"
    id_pattern: re.Pattern[str] = DIGITS  # the shape of its athlete ids
    fixture_pattern: re.Pattern[str] = DIGITS  # and of its games' (Fixture.source_id), for match(): refused before anything is sent
    preview_fixtures = True  # False: a preview before following shows stats only (fixtures take many reads)
    # How a player's page words this source's live scores, where the app's general sentences don't fit (a
    # source whose standings come after each match): live_note under the live board, lag_note at the page's
    # foot. Sent in the card's live block; None leaves the app's own.
    live_note: str | None = None
    lag_note: str | None = None
    # The Wikidata property that holds its athlete ids, where Wikidata keeps them (Liquipedia's pages,
    # P10918): a pro from its roster is then matched to their Wikidata person by id (roster_wikidata.py).
    wikidata_property: str | None = None

    @classmethod
    def available(cls) -> bool:
        """Whether this build can run it: a licensed source needs its token."""
        return True

    def wikidata_id(self, athlete_id: str) -> str:
        """One of its athlete ids as Wikidata writes it, under wikidata_property."""
        return athlete_id

    def sport_of(self, ref: PlayerRef) -> str:
        """The sport one of its athletes plays: its one sport; for a source of several, the athlete's game's
        (one_of)."""
        return self.sports[0]

    def one_of(self, found: str | None) -> str:
        """One of its sports, as found for an athlete; where it can't tell which (a game it doesn't cover, or
        none named), the group its sports are of: an esports pro whose game is unknown is "esports", never
        another game's."""
        if found and found in self.sports:
            return found
        return sports.of(self.sports[0]).group or self.sports[0]

    def running(self) -> tuple[str, ...]:
        """Its sports that run now (sports.ENABLED): a source of several reads only their games."""
        on = {s.key for s in sports.ENABLED}
        return tuple(s for s in self.sports if s in on)

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
        """The event now. final=True may serve a cached copy: a finished match doesn't change. Called from
        several threads at once: a refresh reads a player's recent finals side by side."""
        raise NotImplementedError

    # Optional, for a source that fetches many events in one request (Sportmonks football):
    #   def snapshots(self, locators: dict[Any, dict[str, Any]]) -> dict[Any, Snapshot]
    # keyed as given, leaving out events the source didn't return. An adapter with it has all its
    # armed and live events polled together, one request a round (structured.poll_lane).
    #
    # Optional, for a source that can list everything it has on now, league-wide, in a request or a few:
    #   def live_now(self) -> list[LiveGame]
    # the games in play now, whoever follows them, each game's players in the order the source would put
    # them forward, for "Live now" (live.py). Asked at most once a minute, and only while fans are looking.
    # Where a game's source_id isn't the Fixture.source_id a followed player's card has it under, set its
    # fixtures to those (a chess board is a fixture of each player's), or Today shows that game twice.
    # Live now is for the big games: where the source rates its events (an esports tournament's tier), leave
    # out the small ones and set each game's tier, so the biggest come first.
    #
    # Optional, for a source that can read one game by its id in a request or two:
    #   def match(self, source_id: str) -> LiveGame | None
    # the game a fixture of it names (Fixture.source_id, of the shape fixture_pattern says), whoever follows
    # it, live, to come or over, as live_now() gives one: its snapshot with every player's line the source
    # has, and both sides' players (the line-ups, one side's then the other's), for its match page (match.py);
    # a source of several sports says the game's (LiveGame.sport). None: the source has no such game, or it's of
    # a sport turned off. Asked at most once a minute a game, only while someone has its page open, and only for
    # a game a source named first (on Live now, or a followed player's). A finished game whose read lacks a part
    # the source will have later (a second request that failed), or that may still change, says so
    # (LiveGame.partial): it's read again later, not kept a week.
    #
    # Optional, for a source whose live polls need nothing but a game's id (a fixture's source_id):
    #   def locator(self, source_id: str) -> dict[str, Any] | None
    # the locator snapshot() takes for that game (None for an id it can't poll), so a fan can follow a game by
    # itself, from its match page, though they follow nobody in it (matches.py): the game is then polled in
    # the live lane like a followed player's, and stops when it ends. A source without it has only the games
    # a followed player's fixtures bring followed that way; its others say alerts aren't available.
    #
    # Optional, for a source that can list its competitions' games between two days, league-wide, in a few
    # requests:
    #   def schedule(self, start: date, end: date) -> list[Fixture]
    # every game it covers that starts on those days (UTC, both included), finished or to come, whoever
    # follows it, as fixtures() gives a player's: Today's big results and big games coming up for every fan
    # (league.py), each ranked by its sport (Sport.notable) from its tier, facts, stage and result. Asked a
    # few times a day at most, and only while fans are looking. Where the source rates its events, leave out
    # the small ones and set each game's tier.
    #
    # Optional, for a source that lists every player out of its competitions' games, injured or suspended,
    # in a few requests:
    #   def sidelined(self) -> dict[str, list[Absence]]
    # each team it covers, by its id, with every spell the source has a player of it out for now (a team
    # with nobody out has an empty list): Today's Wire tells who's out and who's back (sidelined.py), each
    # read compared with the last, so a spell missing from a team listed here is over. Raise rather than
    # leave a team out: a read that fails changes nothing. Asked twice a day.

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


@dataclass
class Upload:
    """One video a channel posted, as its feed lists it (a video source's uploads()): what the feed's fetch
    (curator.py) reads a followed player's name in, before asking anything else of it."""

    video_id: str  # the source's id for it, of the shape VideoSource.id_pattern says
    channel_id: str
    channel: str  # the channel's name, as the source gives it
    title: str
    description: str
    url: str  # where it plays, on the source's own site
    published: datetime | None = None
    thumbnail: str | None = None


@dataclass
class VideoFacts:
    """What a video source says of one video now (a video source's facts()): what the feed's hard filters
    read, and the numbers it keeps for ranking later."""

    video_id: str
    seconds: int | None  # how long it runs; None for a live stream or one still being processed
    views: int | None = None
    likes: int | None = None
    embeddable: bool = False  # it may play inside another site's page
    public: bool = False  # anyone can watch it: not private or unlisted, and its upload has been processed
    live: bool = False  # a live stream, now or to come, rather than a video
    allowed: tuple[str, ...] | None = None  # the only countries it plays in (ISO 3166-1 alpha-2), where the source limits it
    blocked: tuple[str, ...] = ()  # countries it doesn't play in
    paid: bool = False  # its maker declared paid promotion in it (YouTube's "Includes paid promotion")
    raw: dict[str, Any] = field(default_factory=dict)  # the parts the source sent, as it sent them

    def plays_in(self, country: str) -> bool:
        """Whether it plays for a viewer in this country."""
        return country not in self.blocked and (self.allowed is None or country in self.allowed)


class VideoSource:
    """A source of videos about players, for the feed (curator.py): each sport names the channels it trusts
    (Sport.video_channels), and a source takes those of the shape its channel ids have (channel_pattern).
    Like an adapter it describes itself, so nothing else names it: adding one is its module plus one entry in
    adapters.VIDEOS.

    uploads(channel_id) lists a channel's latest videos, cheaply (the source's public feed), and facts(ids)
    what the source says of each of up to `batch` videos now; a video it leaves out is gone (removed, made
    private). A source that can search (search_cost set) finds videos from any channel too, for a moment's search
    (searches.py), and a player's with nothing to watch, or their most watched ever (sweeps.py). Each raises AdapterError when the source can't be read, and budget.RateLimited when its
    allowance is spent."""

    system: str  # "youtube": the feed's rows name their source by it
    name: str
    provider: Provider
    covers: str  # what it covers, as the admin panel lists it
    id_pattern: re.Pattern[str]  # the shape of its video ids
    channel_pattern: re.Pattern[str]  # and of its channels', as a sport lists them
    platform = "other"  # where its videos are, as the Feed's judge knows platforms (curate.Source)
    kind = "video"  # what its rows are, as the Feed's judge knows kinds (curate.Kind)
    batch = 50  # the most videos one facts() call asks about
    search_cost: int | None = None  # what one search() spends of each of its search_budgets(); None: it can't search

    def embed(self, video_id: str) -> dict[str, str] | None:
        """How the app's video player plays one of its videos (feed.Video's fields, past duration and views), or
        None if it can't: a video the player can't play isn't served."""
        return None

    @classmethod
    def available(cls) -> bool:
        """Whether this build can run it: a source with a key needs its key."""
        return True

    def budgets(self) -> tuple[Budget, ...]:
        """What its requests spend (adapters/budget.py)."""
        return self.provider.budgets

    def search_budgets(self) -> tuple[Budget, ...]:
        """What a search() spends (search_cost of each): all its budgets, unless the source counts searches apart. The
        gates (searches.room, sweeps._room) take a search's reads of what it found to spend the other budgets."""
        return self.budgets()

    def uploads(self, channel_id: str) -> list[Upload]:
        raise NotImplementedError

    def facts(self, video_ids: list[str], max_age: float = 3600) -> dict[str, VideoFacts]:
        """max_age: the oldest read (seconds) the cache may answer with; 0 asks the source."""
        raise NotImplementedError

    def search(self, query: str, after: datetime | None, country: str) -> list[Upload]:
        """Videos from any channel that the query finds, published after then (or ever, for None), most watched
        first: only ones that may be embedded and played outside the source's own site, as a viewer in this country
        finds them."""
        raise NotImplementedError


@dataclass
class Article:
    """One story a publisher's feed lists (a news source's articles()): what the feed's news poll (news.py) reads
    a followed player's name in. Never the story itself: its headline, a link to it and the feed's own short
    description."""

    url: str  # where it is, on the publisher's site, one way however it was linked (no tracking parameters, no fragment)
    title: str
    description: str  # the feed's summary as plain text, cut short (a sentence or two)
    outlet: str  # the publisher, as the feed names itself ("BBC Sport")
    feed: str  # the feed it was listed in, as a sport lists it
    published: datetime | None = None


class NewsSource:
    """A source of stories about players, for the feed (news.py): each sport names the publishers' feeds it reads
    (Sport.news_feeds), and a source takes those of the shape its feed addresses have (feed_pattern). Like a video
    source it describes itself, so nothing else names it: adding one is its module plus one entry in adapters.NEWS.

    articles(feed, outlet) lists a feed's latest stories, in the feed's order, each credited to the outlet (the name
    the sport gives the publisher); it raises AdapterError when the feed can't be read,
    and budget.RateLimited when its allowance is spent."""

    system: str  # the feed's rows name their source by it
    name: str
    provider: Provider
    covers: str  # what it covers, as the admin panel lists it
    feed_pattern: re.Pattern[str]  # the shape of the feed addresses it reads, as a sport lists them
    platform = "other"  # where its stories are, as the Feed's judge knows sources (curate.Source)
    kind = "article"  # what its rows are, as the Feed's judge knows kinds (curate.Kind)

    @classmethod
    def available(cls) -> bool:
        """Whether this build can run it: a source with a key needs its key."""
        return True

    def budgets(self) -> tuple[Budget, ...]:
        """What its requests spend (adapters/budget.py)."""
        return self.provider.budgets

    def articles(self, feed: str, outlet: str = "") -> list[Article]:
        raise NotImplementedError


class AdapterError(RuntimeError):
    pass
