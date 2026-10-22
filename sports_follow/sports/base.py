"""What a sport is to Sports Follow: its names, how its scoreboards and lines read, what's worth telling its
fans, how its form is summed up, and who counts as one of its athletes on Wikidata.

Sport holds what every sport shares; each sport's module subclasses it and changes what's its own, so
shared code asks the sport (sports.get(name)) instead of branching on it. A sport no module knows (golf,
from an AI-built page) gets Sport itself: start and result moments, a W–L record, nothing else.

Seen and Found are the words moments are found in (moments.detect): an event and a player's line at one
poll, and what happened since the last. Played is a whole game at one poll, both sides, which a game's own
moments are found in (Sport.game_moments), for fans who follow the game itself (matches.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from .. import naming

if TYPE_CHECKING:
    from ..adapters.base import Fixture, Snapshot
    from ..models import Athlete

ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}


def ordinal(n: int | None) -> str:
    """ "1st", "12th", "23rd"; "?" for no place."""
    if not n:
        return "?"
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
Line = dict[str, Any]  # {"headline": "38 PTS · 16 REB · 2 AST", "stats": [{"label", "value"}]}


# ---------------------------------------------------------------- moments: what sports' rules read and say


@dataclass
class Seen:
    """An event and one player's line in it, at one poll."""

    status: str | None  # scheduled | armed | live | final | postponed
    state: dict[str, Any] = field(default_factory=dict)
    line: dict[str, Any] | None = None  # {"headline": "52* (41)", "stats": [{"label", "value"}]}
    score: str = ""
    clock: str = ""
    lineups: bool = False  # the source lists who's playing (any player has a line)
    # The published team sheet's word on this player, before or during the game: starting | bench.
    role: str | None = None
    # The source's key events, newest first (Snapshot.moments): football goals with minute, team and assist.
    events: list[dict[str, Any]] = field(default_factory=list)
    # The player's team ids, where moments are told from the team's side (esports: no player lines).
    teams: list[str] = field(default_factory=list)


@dataclass
class Found:
    kind: str
    level: str  # key | minor
    title: str
    body: str
    # Tells two moments of one kind apart in the key ("50", "i2:100", "goal2", "set3"). A correction's is
    # the kind and detail of the moment it takes back ("goal:goal@101").
    detail: str = ""
    priority: int = 0  # 0 to 100; 0 takes the kind's PRIORITY (moments.py)
    # A game's own moment (Sport.game_moments): the players it's about, by their ids in the source (a goal's
    # scorer, both players of a chess board), or "*", the whole game (its start, its result, a map).
    about: tuple[str, ...] = ()
    # A player's moment of a game under several fixtures (a chess board is one of each player's, and each finds
    # the moment from its own side): the player, by their id in the source, whose side tells it to a fan who
    # follows both (the side the game went toward, the winner; White where it went neither way, the start or a
    # draw). The other side's word for it isn't sent them where this side's fixture is being polled to say it
    # (alerts.voiced). "" where only its player says it.
    voice: str = ""


@dataclass(frozen=True)
class Fired:
    """A player's moment as the feed's moment search reads it (searches.py, Sport.search_words): the moment as it
    was stored, how many of its kind the player has in this game so far (this one too: a second goal is 2), and
    the game it was in, as its fixture names it."""

    kind: str
    detail: str
    title: str
    body: str
    priority: int
    count: int = 1
    game: str = ""  # the game's title: "India vs England"
    competition: str = ""
    venue: str = ""


# The statuses of a game not under way yet.
PRE_MATCH = ("scheduled", "armed", "postponed")
EVERYONE = ("*",)


@dataclass
class Played:
    """A whole game at one poll, both sides, for fans who follow the game itself (Sport.game_moments)."""

    status: str | None  # scheduled | armed | live | final | postponed
    state: dict[str, Any] = field(default_factory=dict)
    score: str = ""
    clock: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)  # the source's key events, newest first
    lines: dict[str, Line] = field(default_factory=dict)  # every player's line the source gave, by their id there
    names: dict[str, str] = field(default_factory=dict)  # who those are, where known: a line of someone unnamed isn't told

    def seen(self, athlete_id: str) -> Seen:
        """One player's view of the game, for the rules that find a player's moments (Sport.during)."""
        return Seen(self.status, self.state, self.lines.get(athlete_id), self.score, self.clock, lineups=bool(self.lines), events=self.events)


@dataclass
class Poster:
    """A player's game this week as Today's "This week" rail draws it (week.py): a word or two in big type
    ("Hundred", "Hat-trick"), a short line under the player's name ("112 (98) v Australia"), and the moment
    it was read from, which dates it. One read off a finished game's line instead (Sport.finished_poster)
    has no moment, and says how big it is: the priority its moment would have had."""

    word: str
    line: str = ""
    of: Found | None = field(default=None, compare=False)
    priority: int = field(default=0, compare=False)


@dataclass
class Notable:
    """A game no fan's player need be in, as big as its sport rates it (Sport.notable), for Today's "Big
    results" and "Big games coming up" (league.py): how big, on one scale for every sport, so the biggest of
    all of them come first; and what its card says."""

    rank: int  # 1 to 100: 90 a final, 75 a semi-final or a top-of-the-table game, 40 a game of a big league
    word: str = ""  # a finished game's big word: its score ("4–1"), its margin ("By 6 wickets"), its winner
    line: str = ""  # under it, who played and how it went: "Arsenal beat Chelsea"; the game's title if ""
    why: str = ""  # what makes it big, in a word or two: "Final", "1st v 2nd", "Last ball"


@dataclass
class Fact:
    """Something a source says about a player that the Wire tells (week.py, from sidelined.py): a tag ("Out",
    "Back") and a line ("William Saliba · back injury · until 11 Dec"), in the player's sport's words."""

    tag: str
    line: str


@dataclass
class Glance:
    """A game at a glance, as an Android notification draws it (livecards.py): its two sides, each a short name
    and its score (none for a lobby of more), where it's at in a word or two ("67'", "38.2 ov", "Set 2", "Full
    time"), how far along it is (done, of) where its sport can say, and where along that it turned, for which
    side (0 the first, 1 the second): a goal at 23' for the away side is (23, 1)."""

    sides: list[tuple[str, str]] = field(default_factory=list)
    status: str = ""
    progress: tuple[float, float] | None = None
    marks: list[tuple[float, int]] = field(default_factory=list)


# The answer to a question that won't have one (Sport.answer): a map never played, a game called off.
VOID = "void"
# A question's answer beside its two sides' ids where a game can end level (Sport.asks): a chess game, a football match.
DRAW = "draw"


@dataclass(frozen=True)
class Ask:
    """A question fans call before a game, or a part of it, starts (predictions.py, Sport.asks): which one it is in
    the game ("result", "map2"), its words ("Who wins?"), the answers to pick from as (key, label), each key a side's
    id in the game's source or a word ("draw"), and where it comes in the game's list. A `first` question closes
    when the game starts (its start time closes it too, should a poll be late); a later one (map 2) when its part
    of the game does."""

    key: str
    title: str
    options: tuple[tuple[str, str], ...]
    order: int = 0
    first: bool = True


# How late in a competition a game is, from its stage's words, latest first: "Grand final", "Upper bracket
# final: SEN vs 100T", "Semi-finals", "Qualifier 1". A semi's or a quarter's "finals" isn't a final, so
# those are read first.
_STAGES = (
    (re.compile(r"\bgrand[ -]?finals?\b", re.I), 3, "Grand final"),
    (re.compile(r"\b(?:upper|lower|winners'?|losers'?)[ -]bracket[ -]finals?\b", re.I), 2, "Bracket final"),
    (re.compile(r"\bsemi[ -]?finals?\b|\bsemis\b", re.I), 2, "Semi-final"),
    (re.compile(r"\bquarter[ -]?finals?\b", re.I), 1, "Quarter-final"),
    (re.compile(r"\bfinals?\b", re.I), 3, "Final"),
    (re.compile(r"\bplay-?offs?\b|\bknock-?outs?\b|\beliminator\b|\bqualifier \d\b|\bround of (?:16|32)\b|\blast (?:16|8)\b", re.I), 1, "Playoffs"),
)


def stage(*texts: str | None) -> tuple[int, str]:
    """How late in its competition a game is, from what its source calls its stage or round (several
    texts: the first that says wins): 3 a final, 2 a semi-final (or a bracket's final), 1 another knockout
    round, 0 a league or group game; and the word for it on a card ("Final", "Semi-final")."""
    for text in texts:
        for pattern, level, word in _STAGES:
            if text and pattern.search(text):
                return level, word
    return 0, ""


# What a source names a side it hasn't decided yet (a final's, before the semi-finals): "TBC", "TBA", "TBD", "".
_UNDECIDED = re.compile(r"\s*(?:tb[acd]|to be (?:confirmed|announced|decided|determined))?\s*", re.I)


def undecided(sides: list[str]) -> bool:
    """Whether a source lists a game's sides and names none of them yet ("TBC v TBC"): a sport may leave such
    a game out of the big games until it has them. False when the source lists no sides."""
    return bool(sides) and all(_UNDECIDED.fullmatch(s or "") for s in sides)


def sides(state: dict[str, Any]) -> list[dict[str, Any]]:
    """A game's two sides as its board has them ({id, name, score, winner}): home and away (football,
    basketball), or its two teams (cricket, an esports series); [] for a board of more or fewer (a lobby)."""
    pair: list[Any] = [state.get("home"), state.get("away")] if state.get("home") or state.get("away") else list(state.get("teams") or [])
    return pair if len(pair) == 2 and all(isinstance(s, dict) for s in pair) else []


def short(side: dict[str, Any]) -> str:
    """A side's name as a few letters where its board has them: its abbreviation ("ARS"), else its short name, else
    its name."""
    return str(side.get("abbr") or side.get("short") or side.get("name") or "")


def stat(line: dict[str, Any] | None, label: str) -> str | None:
    for s in (line or {}).get("stats") or []:
        if s.get("label") == label:
            return str(s.get("value"))
    return None


def number(value: str | None) -> int:
    match = re.match(r"\s*(\d+)", value or "")
    return int(match.group(1)) if match else 0


def words(*parts: str | None) -> str:
    """The parts that say something, in one line: a moment search's words (Sport.search_words)."""
    return " ".join(p.strip() for p in parts if p and p.strip())


def ground(venue: str | None) -> str:
    """A venue as a search names it: "Lord's", not "Lord's, London"."""
    return (venue or "").split(",")[0].strip()


def crossed(before: int, now: int, marks: tuple[int, ...]) -> list[int]:
    return [m for m in marks if before < m <= now]


def joined(*parts: str | None) -> str:
    return " · ".join(p for p in parts if p)


def spaced(*parts: str | None) -> str:
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------- form: what sports' summaries read


def values(line: Line) -> dict[str, str]:
    return {s.get("label"): str(s.get("value")) for s in line.get("stats") or []}


def num(value: str | None) -> float:
    match = re.match(r"\s*([+-]?\d+(?:\.\d+)?)", value or "")
    return float(match.group(1)) if match else 0.0


def outcome(result: str) -> str | None:
    """W, D or L from a result put from the player's side ("Won 2–1"); None if it doesn't say."""
    if "(w/o)" in (result or ""):
        return None  # a walkover wasn't played, so it isn't form (as the tennis Win–loss leaves it out)
    word = (result or "").split(" ", 1)[0]
    return {"Won": "W", "Drew": "D", "Lost": "L"}.get(word)


# ---------------------------------------------------------------- the sport


class Sport:
    """A sport, and the rules every sport shares; a sport's module overrides what's its own."""

    key = ""  # "cricket": the registry's name for it, the search filter's, and the settings'
    label = ""  # "Cricket", as the app shows it
    names: tuple[str, ...] = ()  # other names a page or a source may give it ("soccer")
    kinds: tuple[str, ...] = ()  # its scoreboards' kinds (Snapshot.state["kind"])
    # The group of sports it's one of, whose rules and look it shares and whose name a setting may give for
    # all of them at once ("esports": each esports game is a sport of its own); "" for a sport on its own.
    group = ""
    team = False  # its athletes turn out for teams: clubs, countries
    # Fans can follow its teams (teams.py), where a running source of it lists them (Adapter.teams): a club, a
    # country's side, an esports team. Not a sport whose sides are one player (tennis, chess).
    follow_teams = False
    # Who counts as one of its athletes on Wikidata (anyone with any of these ids), and the ids kept,
    # keyed by the source that reads each one where one does ("espn_cricket"), or the database's name.
    wikidata: dict[str, str] = {}
    athletes = 0  # about how many Wikidata has (29 Sep 2026): the registry imports the smallest sports first
    roster = False  # its athletes come from its sources' own lists instead (adapters' roster())
    no_line = "No stats"  # a finished game's word for a player its line-up doesn't have
    games = ("game", "games")  # a form note's word: "Last 5 games"
    # How long before the start its team sheets are published (seconds): its games are armed, and
    # polled, at least this early, so "starts" or "on the bench" is told when the line-up is out.
    team_sheet_before = 0

    # ---- how the app shows it (GET /api/sports, /api/sports.css)

    # Its colour, as CSS custom properties on [data-sport]: the block colour, text on it, softer text on
    # it, and the colour itself as text on the dark ink. A sport without one wears ink. A link preview's
    # picture is the block colour with the text on it (share.py).
    colour: dict[str, str] = {}
    # Which free-text sport names it is (a page's "Women's football", the agent's "T20 cricket"): a
    # regular expression over the lower-case name, and one that rules a name out ("american football").
    pattern = ""
    exclude = ""
    # What a fan hears about at each alert level: key moments, and what "everything" adds; a line and in full.
    alerts = {
        "key": ("Big news", "Injuries, transfers and retirements, from the news: no live source covers this sport yet"),
        "more": ("milestones", "milestones in the news"),
    }
    # What a fan following a whole game hears at key moments, and what everything adds (game_moments): a line
    # and in full; "" adds nothing.
    game_alerts = {
        "key": ("The start and the result", "The game starting and its result"),
        "more": ("", ""),
    }
    # The big word an Android scorecard gives a moment of one of its own kinds (notify.word_and_line), where neither
    # high() nor the moment's title says one: football's "goal" is "Goal". The kinds every sport has are notify's.
    kind_words: dict[str, str] = {}
    stats_title = "Season"  # the Stats tab's first view: a season's numbers, or an overview (ranking, form)
    keeps_names = False  # a player's sport is shown as written ("CS2"), not title-cased (esports' games)

    # ---- names

    def label_of(self, name: str) -> str:
        """A player's sport as the app shows it ("Cricket" for "cricket")."""
        return name.title()

    # ---- the registry (registry.build): who's current, their league and title

    wikidata_title: str | None = None  # the property for a title worth showing (chess: GM, IM…)
    nation_first = False  # a player's country before their club (cricket)
    retire_age = 75  # an individual sport's athletes count as current to this age

    def team_ok(self, label: str) -> bool:
        """Whether a team Wikidata lists for a player is one of this sport's."""
        return True

    leagues: tuple[str, ...] = ()  # the leagues or tours its sources read, named to the agent ("nba")

    def league_of(self, ids: dict[str, str]) -> str | None:
        """The league or tour a registry athlete plays in, from the ids Wikidata has for them."""
        return None

    def title_of(self, label: str) -> str | None:
        """A title's short form ("Grandmaster" -> "GM"), for a sport with titles."""
        return None

    def better_title(self, a: str, b: str) -> bool:
        return False

    def current(self, age: int | None, teams: bool, latest_start: date | None, today: date) -> bool:
        """Whether a registry athlete still plays: on a team now, and either started there in the last ten
        years or aged 38 or under (or 30 or under, team or not), for a team sport; else under retire_age."""
        if not self.team:
            return age is None or age <= self.retire_age
        current = teams and ((latest_start is not None and latest_start.year >= today.year - 10) or (age is not None and age <= 38))
        return current or (age is not None and age <= 30)

    # ---- the profile on a player's page, from their Wikidata person (wiki.py)

    profile_nation: tuple[str, ...] = ("P1532", "P27")  # the country they compete for, else their citizenship
    profile_role: tuple[str, ...] = ("P413",)  # what the page calls their role: position played ("forward", "batter")
    # Awards worth naming on the page (Wikidata's "award received"), by item, and what the page calls each.
    # Two items with one name (FIFA Ballon d'Or and Ballon d'Or) are counted together. Only awards Wikidata
    # records reliably, with the year of each, go here: a list of every honour is the article's to give.
    honours: dict[str, str] = {}

    # ---- the registry, for a sport whose athletes come from its sources' lists (roster): who on Wikidata
    # is one of its people, so each athlete's Wikidata person, and their photo, can be found (roster_wikidata.py)

    people_ids: tuple[str, ...] = ()  # anyone with any of these ids
    people_occupations: tuple[str, ...] = ()  # or any of these occupations
    team_filler: frozenset[str] = frozenset({"team", "club"})  # words of its team names that don't tell teams apart

    def known_as(self, ids: dict[str, list[str]]) -> list[str]:
        """Names a person on Wikidata goes by, from their ids (by property), beside their labels and aliases."""
        return []

    def plays(self, ids: dict[str, list[str]], disciplines: list[str]) -> set[str]:
        """What a person on Wikidata plays, as game_key writes it, from their ids (by property) and the
        disciplines they compete in."""
        return set()

    def game_key(self, game: str | None) -> str:
        """A game however it's written, as one key; "" for none."""
        return ""

    # ---- search: a registry athlete as a result

    # A search result's line comes from its source rather than the page's (chess, tennis: an agent may
    # call a league side an individual's team).
    source_line_first = False

    def search_detail(self, athlete: Athlete) -> list[str]:
        """What a result says under the name: teams and country ("Al Nassr · Portugal")."""
        return [*list(athlete.teams or [])[:2], athlete.country or ""]

    def result_sport(self, athlete: Athlete) -> str:
        """A registry athlete's sport as a result names it."""
        return athlete.sport.title()

    # ---- moments (moments.detect)

    def matchup(self, state: dict[str, Any]) -> str:
        """"India v Australia" from a scoreboard with two teams, or "" for one without."""
        names = [t.get("name") for t in state.get("teams") or []]
        return " v ".join(n for n in names if n) if len(names) == 2 and all(names) else ""

    def lineup_title(self, who: str, starting: bool) -> str:
        return f"{who} starts" if starting else f"{who} is on the bench"

    def starts_unlisted(self, now: Seen) -> bool:
        """Whether a game starting is told for a player with no line in it yet (one player a side)."""
        return False

    def left_out(self, now: Seen) -> bool:
        """Known not to be in the game: the line-ups are out and the player isn't in them."""
        return False

    def start(self, who: str, now: Seen) -> Found:
        return Found("start", "key", f"{who}'s game has started", joined(now.score, now.clock))

    def final(self, who: str, now: Seen) -> Found:
        head = (now.line or {}).get("headline") or ""
        return Found("final", "key", f"{who}: {self.result_text(now.score, now.clock) or 'full time'}", head)

    def after_final(self, who: str, athlete_id: str, now: Seen) -> list[Found]:
        """More to tell about a result than the result (a tennis upset)."""
        return []

    def during(self, who: str, athlete_id: str, before: Seen, now: Seen) -> list[Found]:
        """What happened to the player between two polls: goals, fifties, sets."""
        return []

    def taken_back(self, who: str, athlete_id: str, before: Seen, now: Seen, told: list[tuple[str, str]]) -> list[Found]:
        """What the source has taken back since the last poll (a goal VAR rules out), of the moments found
        for the player in this game so far (`told`: each one's kind and detail, oldest first). Each is a
        "correction" whose detail names the moment it takes back; it replaces the game's notification for
        the fans who were sent that moment, and reaches no one else (alerts.py). A moment taken back that
        the source shows again may be found again here, under a new detail."""
        return []

    def poster(self, found: list[Found], versus: str) -> Poster | None:
        """A player's game this week as a poster in Today's "This week" (week.py), from the moments found for
        them in it, oldest first (none a source took back): by default its biggest high (high), the newest
        of equals. `versus` is the other side of the game ("v Australia"), or "" when the game's title
        doesn't say. None for a game without one."""
        best = None
        for f in sorted(found, key=lambda f: f.priority):  # stable: of equals, the newest comes last
            if (made := self.high(f, versus)) is not None:
                made.of = made.of or f
                best = made
        return best

    def high(self, f: Found, versus: str) -> Poster | None:
        """One moment as a poster, if it's one of the player's highs, read off the title and body this sport
        wrote it with: the title says what the kind can't (a cricketer's "takes 5 wickets" is a "Five-for").
        None for a set, a yellow card, a line-up, being out, the news. A sport no module knows has none."""
        return None

    def finished_poster(self, line: Line, result: str, versus: str) -> Poster | None:
        """A player's finished game as a poster when no moment found in it makes one (week.py), read off the
        line it left them ({"headline": "112 (98)", "stats": […]}) and its result from their side ("Won
        2–1"), in the words high() has for the same high, with the priority its moment would have had.
        Moments are found only while a game is polled live, so a game first seen after it ended has none;
        where they're there they say more ("Late equaliser", "Upset") and win. None for a game without a
        high, and for a sport no module knows."""
        return None

    # ---- league-wide games: Today's big results and big games coming up (league.py)

    def notable(self, fixture: Fixture) -> Notable | None:
        """How big a game its sources list league-wide is (Adapter.schedule), from what the source says of
        it: its tier and facts, its stage (detail) and, once it's over, its result. None leaves it out. A
        sport whose sources list no schedule has none."""
        return None

    # ---- who's out: injuries and bans its sources list league-wide, on Today's Wire (sidelined.py)

    def out_fact(self, who: str, kind: str, what: str, until: date | None) -> Fact | None:
        """A player out of their team's games as the Wire tells it: of a kind its sources share ("injury",
        "suspension", "other": Absence.kind), as the source says what ("Back Injury"), until when they're
        expected back. None for a kind the sport doesn't tell, and for a sport whose sources list nobody out."""
        return None

    def back_fact(self, who: str, kind: str) -> Fact | None:
        """A player of this sport back from a spell of this kind (out_fact), as the Wire tells it; None for a
        kind it doesn't tell."""
        return None

    # ---- a game followed by itself (matches.py): its own moments, from both sides

    def game_moments(self, name: str, before: Played | None, now: Played, told: list[tuple[str, str]]) -> list[Found]:
        """What happened in a game since the last poll, from both sides, for fans who follow the game itself,
        each titled with the game's name ("India v Australia: Kohli out for 82"): it starting (game_start), what
        happened during it (game_during), and its result (game_final). Each says whom it's about (Found.about),
        so a fan who also follows one of them hears it once, as their player's. before=None is a first look at
        a game already under way (a follow, or polling, began mid-game): only its result is told, so nobody
        hears of a wicket an hour old. `told` is what was found for the game before (each moment's kind and
        detail, oldest first): what it may take back (a goal VAR rules out)."""
        found: list[Found] = []
        was = before.status if before else None
        if before is not None and now.status == "live" and was in PRE_MATCH:
            found.append(self.game_start(name, now))
        if now.status == "final" and was != "final":
            found.append(self.game_final(name, now))
        if before is not None:
            found += self.game_during(name, before, now, told)
        for f in found:
            f.about = f.about or EVERYONE
        return found

    def game_start(self, name: str, now: Played) -> Found:
        return Found("start", "key", f"{name}: under way", joined(now.score, now.clock), "", 40, EVERYONE)

    def game_final(self, name: str, now: Played) -> Found:
        """The result as the head of its match page says it ("Arsenal won", "India won by 57 runs"), else its
        result line, with the final score under it."""
        said = self.verdict(now.state, now.score, now.clock) or self.result_text(now.score, now.clock) or "full time"
        return Found("final", "key", f"{name}: {said}", now.score if now.score != said else now.clock, "", 80, EVERYONE)

    def side_final(self, state: dict[str, Any], score: str, clock: str, team_id: str) -> str:
        """A finished game's result from one side's, by its id in the source, for the fans who follow that team
        (teams.py): "Arsenal won 2–1 v Chelsea", "Chelsea lost 1–2 v Arsenal", "Arsenal drew 1–1 v Chelsea". By
        default from a board of two sides (sides()): the one the source marks the winner, else the more on the
        board. "" where the board doesn't say which side is the team's, or who won: the game's own result is
        told instead (game_final)."""
        pair = sides(state)
        ours = next((s for s in pair if str(s.get("id") or "") == team_id), None)
        if ours is None:
            return ""
        theirs = pair[1 - pair.index(ours)]
        name, other = ours.get("name"), theirs.get("name")
        mine, against = ("" if s.get("score") is None else str(s["score"]) for s in (ours, theirs))  # a sweep's 0 is a score
        if not (name and other):
            return ""
        if ours.get("winner") or theirs.get("winner"):
            word = "won" if ours.get("winner") else "lost"
        elif mine.isdigit() and against.isdigit():
            word = "won" if int(mine) > int(against) else "lost" if int(mine) < int(against) else "drew"
        else:
            return ""
        tally = f" {mine}–{against}" if mine and against else ""
        return f"{name} {word}{tally} v {other}"

    def side_result(self, state: dict[str, Any], score: str, clock: str, team_id: str) -> str:
        """A finished game's result from one side's as a result card says it ("Won 2–1", "Lost by 57 runs"), from
        its last board: a followed team's game the poller saw finish while its source's schedule, kept a while, has
        no result for it yet (teams.py). By default side_final's without the sides' names; "" where that's ""."""
        said = re.match(r"^.+? (won|lost|drew)(.*?) v .+$", self.side_final(state, score, clock, team_id))
        return f"{said[1].capitalize()}{said[2]}" if said else ""

    def game_during(self, name: str, before: Played, now: Played, told: list[tuple[str, str]]) -> list[Found]:
        """By default, every named player's moments as the player rules find them (during: a fifty, out, a
        wicket haul, 30 points), each about that player, in the same words a fan of theirs gets, and their detail
        led by the player's id ("253802:out", "253802:i2:100"; a correction's, the moment's it takes back): each
        player's out is the game's own, not the first one's alone (alerts.same reads it without). A game whose
        earlier poll had no lines yet while it was live (the source's first lines) tells none: what they say
        happened before. A sport whose moments are a side's, or the board's (football's goals, chess's turning,
        esports' maps), overrides it."""
        if before.status == "live" and not before.lines and now.lines:
            return []
        found = []
        for athlete_id in now.lines:
            who = now.names.get(athlete_id)
            if not who:
                continue
            for f in self.during(who, athlete_id, before.seen(athlete_id), now.seen(athlete_id)):
                kind, said, detail = f.detail.partition(":") if f.kind == "correction" else ("", "", f.detail)
                f.title, f.about, f.detail = f"{name}: {f.title}", (athlete_id,), f"{kind}{said}{athlete_id}:{detail}"
                found.append(f)
        return found

    # ---- a game's match page (match.py)

    def match_page(self, state: dict[str, Any], events: list[dict[str, Any]], lines: dict[str, Line], names: dict[str, tuple[str, str]]) -> dict[str, Any]:
        """What a game's match page shows under its board, as sections its sport's web view draws by name
        (web/src/sports/<sport>.tsx, Match), from its scoreboard (Snapshot.state), the source's key events,
        newest first (Snapshot.moments), every player's line the source gave, by their id there, and who those
        are ({id: (name, team)}). By default the key events as they come, which the page lists for any sport
        ({"events": [{"clock", "kind", "text"}]}); a sport whose board says it all (chess) has nothing more."""
        return {"events": [{k: e.get(k) for k in ("clock", "kind", "text")} for e in events]} if events else {}

    def verdict(self, state: dict[str, Any], score: str, clock: str) -> str:
        """Who won a finished game, in a few words, for the head of its match page above the final board:
        "Arsenal won", "Draw", "India won by 57 runs". From its final scoreboard (Snapshot.state) and its score
        and clock lines. "" where the board says it all, or for a sport no module knows."""
        return ""

    # ---- live games and cards (structured.py)

    def glance(self, state: dict[str, Any], score: str, clock: str, status: str | None, events: list[dict[str, Any]]) -> Glance:
        """A game at a glance as an Android notification draws it (livecards.py: a moment's scorecard, a live
        card), from its scoreboard (Snapshot.state), its score and clock lines, its status (live, final…) and the
        source's key events, newest first (none where only the stored state is at hand). By default its board's two
        sides (sides()), each by its abbreviation, else short name, else name, with its score as the board has it,
        and the clock line as where it's at: no progress, no marks."""
        return Glance([(short(s), "" if s.get("score") is None else str(s["score"])) for s in sides(state)], clock or "")

    def result_text(self, score: str, clock: str) -> str:
        """A finished game's result as a line: its score ("2–1"); cricket's is its summary."""
        return score

    def missed_start(self, snap: Snapshot) -> bool:
        """A game first seen live this far along was missed, not just started (a football clock past 10')."""
        return False

    team_moments = False  # moments are told from the player's team's side: no player lines (esports)
    # Its sources sit side by side, each for its own games (esports): an athlete is bound to the source
    # they came from, not the sport's first.
    side_by_side = False
    engine = False  # live positions are evaluated by our engine (chess)

    def credits_also(self) -> list[dict[str, Any]]:
        """Others whose work runs in the sport's own pages, for the Credits page while it's on (chess's
        engine on the fan's device, under its analysis board)."""
        return []

    def settle(self, before: dict[str, Any] | None, ev: dict[str, Any]) -> dict[str, Any]:
        """The engine's latest evaluation of a game (engine.py) with what the sport makes of it, held against
        the one settled before (chess: the band it's in); as it comes, for a sport the engine doesn't evaluate."""
        return ev

    # ---- predictions (predictions.py): what fans call before a game, read off its board (Snapshot.state)

    predicts = False  # it asks fans to call its games (asks): the worker reads its games coming up ahead, to ask in time

    def board(self, state: dict[str, Any]) -> str:
        """The game as its board tells it apart, where one game is under several fixtures (a chess board is one of
        each player's): an id they share, so its questions are one set whichever address asks, and Today shows it
        once whichever of a fan's players it's under (structured.game_key). "" where the fixture is the game."""
        return ""

    def board_players(self, state: dict[str, Any]) -> tuple[str, ...]:
        """Who's on a game under several fixtures (board), by their ids in the source, each with a fixture of their
        own: its fixtures here are theirs, so alerts, live cards and mutes take them for one game (structured.boards).
        () where the fixture is the game."""
        return ()

    def pairing(self, state: dict[str, Any]) -> str:
        """Which meeting of its players a game under several fixtures is, as each of its fixtures' boards says it: the
        same all through it, a mini-match's games too, and another for the same players meeting again (another round),
        so its fixtures are told apart from theirs (structured.boards). By default its players alone; "" where its
        board can't say, and its fixtures aren't taken for one game (each side tells its own)."""
        return "|".join(sorted(self.board_players(state)))

    def asks(self, state: dict[str, Any]) -> list[Ask]:
        """The questions fans may call about this game, from its board before it starts: none, for a sport that
        asks nothing (yet)."""
        return []

    def closed(self, ask: str, status: str | None, state: dict[str, Any]) -> bool:
        """Whether a question's picks are closed, by the board: the game, or the part of it the question's about,
        has started."""
        return status in ("live", "final")

    def answer(self, ask: str, status: str | None, state: dict[str, Any]) -> str | None:
        """A question's answer, once the board says it: an option's key; VOID when it won't have one (a map the
        series didn't need); None while it's still to come. A game called off waits for its new date: one that
        never comes is voided by predictions.sweep."""
        return None

    # ---- form (form.summarize)

    draws = False  # a record always shows draws (W–D–L), even with none

    def outcome(self, result: str, teams: list[str]) -> str | None:
        return outcome(result)

    def form(self, items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
        """Numbers over the player's last results (each {"result", …}) and the lines stored with them."""
        return []

    def form_games(self, items: list[dict[str, Any]]) -> tuple[str, str]:
        return self.games

    # ---- the Feed's content judge (curate.py)

    # A video longer than this (seconds) from a channel nobody vouches for is taken for a re-upload of a whole game,
    # and not shown; None where long videos are normal, for the judge's model to tell apart.
    restream_after: int | None = 20 * 60

    def content_hints(self) -> str:
        """A few lines the content judge reads about this sport: what a milestone and a big moment are here, and
        what's a promo, a rumour or a re-upload in its fans' world. "" for none: the judge's own rules alone."""
        return ""

    # ---- the feed (curator.py): videos about its players

    # The only channels its players' videos come from, by their ids where they post: official ones (leagues,
    # federations, clubs, broadcasters, tournaments) and a few top creators. A video source takes those of the shape
    # its own channel ids have (VideoSource.channel_pattern). Each channel's new uploads that name a followed player
    # of the sport are kept for the feed, and what a search finds is kept only from these (candidates.keep); stored
    # rows from any other channel aren't served (rank.rescore). A sport that's off has none read. Every id is a real
    # one that answers (README.md, "Adding or removing").
    video_channels: tuple[str, ...] = ()
    # Words a video's title keeps as they're written when the Feed puts a shouted title in sentence case (titles.py):
    # its competitions', bodies' and teams' short names, as fans write them ("IPL", "T20I", "UEFA", "US Open").
    title_words: tuple[str, ...] = ()
    # Publishers' feeds (RSS or Atom) it reads for stories about its players (news.py), each with the publisher's
    # name as a story credits it: a news source takes those of the shape its feed addresses have
    # (NewsSource.feed_pattern). Each feed's new stories that name a followed player of the sport are kept for the
    # feed; a sport that's off has none read. Every address is a real feed that answers with headlines, links, dates
    # and a short description (README.md, "Adding or removing"), never a search engine's feed of other sites.
    news_feeds: dict[str, str] = {}
    # Words a team's name goes without in its videos' titles, at its start or end (team_names): "FC" in "Liverpool
    # FC", "Team" in "Team Falcons". A video naming a player's team, and not them, is kept for them as their team's
    # (candidates.py, BHA-382).
    team_affixes: tuple[str, ...] = ()
    # Words that, right after a team's name, make it another side ("India Women", "Liverpool U21"): such a mention
    # isn't the player's team. A sport adds its own (cricket's "Legends").
    other_sides: tuple[str, ...] = ("women", "womens", "w", "u17", "u18", "u19", "u20", "u21", "u23", "academy", "b", "ii")

    def team_names(self, name: str) -> list[str]:
        """A team's names as its videos' titles write them, each as plain words (naming.words) joined by spaces: its
        own, and without the words its sport's titles leave off (team_affixes): "liverpool" for "Liverpool FC",
        "falcons" for "Team Falcons". A name the affixes would leave empty stays as it is."""
        own = naming.words(name)
        affixes = [naming.words(a) for a in self.team_affixes]
        bare = list(own)
        cut = True
        while cut:
            cut = False
            for a in (a for a in affixes if a and len(a) < len(bare)):
                if bare[:len(a)] == a:
                    bare, cut = bare[len(a):], True
                elif bare[-len(a):] == a:
                    bare, cut = bare[:-len(a)], True
        return list(dict.fromkeys(" ".join(w) for w in (own, bare) if w))

    def video_words(self) -> str:
        """Words that, after a player's name, find videos about them on a video source's search when there's no moment
        to name (sweeps.py: a player with nothing to watch this week, and their most watched ever): the sport as its
        fans' titles name it ("cricket", "Valorant"), so the search finds this player and not a namesake. "" for the
        name alone."""
        return ""

    def search_words(self, fired: Fired) -> str | None:
        """Words that, after the player's name, find fans' videos of this moment on a video source's search: "hundred
        Lord's", "brace Liverpool vs Everton", "wins game 4". None for a moment not worth a search (most aren't:
        each one is a hundredth of a day's quota). The feed searches once a few hours after the moment, and again
        a day later (searches.py)."""
        return None
