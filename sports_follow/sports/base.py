"""What a sport is to Sports Follow: its names, how its scoreboards and lines read, what's worth telling its
fans, how its form is summed up, and who counts as one of its athletes on Wikidata.

Sport holds what every sport shares; each sport's module subclasses it and changes what's its own, so
shared code asks the sport (sports.get(name)) instead of branching on it. A sport no module knows (golf,
from an AI-built page) gets Sport itself: start and result moments, a W–L record, nothing else.

Seen and Found are the words moments are found in (moments.detect): an event and a player's line at one
poll, and what happened since the last.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

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
    # The part of its sport the game is in, which takes its own turn among the sport's games (league.py), so
    # one part never fills the sport's turns: an esports title ("Valorant"); "" for a sport of one part.
    group: str = ""


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


def stat(line: dict[str, Any] | None, label: str) -> str | None:
    for s in (line or {}).get("stats") or []:
        if s.get("label") == label:
            return str(s.get("value"))
    return None


def number(value: str | None) -> int:
    match = re.match(r"\s*(\d+)", value or "")
    return int(match.group(1)) if match else 0


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
    # Games of the sport no source covers by that name, as an AI-built page may call them ("LoL"): known
    # to be the sport, but no source is looked up by them. Its sources add the games they cover.
    other_games: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()  # its scoreboards' kinds (Snapshot.state["kind"])
    team = False  # its athletes turn out for teams: clubs, countries
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
    # it, and the colour itself as text on the dark ink. A sport without one wears ink. A player's photo
    # is recoloured from "deep" (its darks) to the block colour (its lights); without "deep", from the text
    # on it, which must then be the darker. A link preview's picture is the block colour with the text on
    # it (share.py).
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

    # ---- live games and cards (structured.py)

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

    # ---- form (form.summarize)

    draws = False  # a record always shows draws (W–D–L), even with none

    def outcome(self, result: str, teams: list[str]) -> str | None:
        return outcome(result)

    def form(self, items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
        """Numbers over the player's last results (each {"result", …}) and the lines stored with them."""
        return []

    def form_games(self, items: list[dict[str, Any]]) -> tuple[str, str]:
        return self.games
