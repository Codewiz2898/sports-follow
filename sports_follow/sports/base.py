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
    from ..adapters.base import Snapshot
    from ..models import Athlete

ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}
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
    detail: str = ""  # tells two moments of one kind apart in the key ("50", "i2:100", "goal2", "set3")
    priority: int = 0  # 0 to 100; 0 takes the kind's PRIORITY (moments.py)


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

    # ---- how the app shows it (GET /api/sports, /api/sports.css)

    # Its colour, as CSS custom properties on [data-sport]: the block colour, text on it, softer text on
    # it, and the colour itself as text on the dark ink. A sport without one wears ink.
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
