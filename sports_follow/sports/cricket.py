"""Cricket: a line's innings, fifties, hundreds and wicket hauls in each, the XI before the start, results
named by the side that won, and form over a run of matches."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..naming import team_key
from .base import ORDINAL, Found, Line, Notable, Poster, Seen, Sport, crossed, joined, number, spaced, stage, stat, undecided, values

if TYPE_CHECKING:
    from ..adapters.base import Fixture


def _int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


# A league game's size (notable): by its stage, else by who plays it; a finish this close is one to see.
STAGE_RANK = {3: 95, 2: 85, 1: 70}
NATIONS, LEAGUE = 55, 40  # an international, any other game the sources list
CLOSE_RUNS, CLOSE_WICKETS, CLOSE_BALLS = 5, 2, 3
_MARGIN = re.compile(r"\bwon by (\d+) (runs?|wickets?)(?:.*?\bwith (\d+) balls? remaining)?", re.I)

_BAT = re.compile(r"(\d+)(\*?) \((\d+)\)")
_BOWL = re.compile(r"(\d+)/(\d+) \(([\d.]+) ov\)")


def innings(headline: str | None) -> tuple[list[tuple[int, bool, int]], list[tuple[int, int, str]]]:
    """Every innings in a cricket line's headline ("103 (152) & 48* (60) · 3/45 (20 ov)"): batting as
    (runs, out, balls), bowling as (wickets, runs conceded, overs)."""
    bats, bowls = [], []
    for part in re.split(r" & | · ", headline or ""):
        if m := _BAT.fullmatch(part):
            bats.append((int(m[1]), not m[2], int(m[3])))
        elif m := _BOWL.fullmatch(part):
            bowls.append((int(m[1]), int(m[2]), m[3]))
    return bats, bowls


def batting_bowling(lines: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Runs, average, strike rate, highest, 100s/50s, wickets and economy over some matches' lines
    ({"headline", "stats"}), every innings counted: a line's stats are only its latest innings, so a
    Test's two come from the headline."""
    runs = balls = outs = batted = hundreds = fifties = 0
    best = None
    wickets = conceded = 0
    balls_bowled = 0
    for line in lines:
        bats, bowls = innings(line.get("headline"))
        values = {s["label"]: s["value"] for s in line.get("stats") or []}
        if not bats and "Runs" in values:
            bats = [(_int(values["Runs"].rstrip("*")), not values["Runs"].endswith("*"), _int(values.get("Balls")))]
        if not bowls and "Wickets" in values:
            bowls = [(_int(values["Wickets"]), _int(values.get("Runs conceded")), str(values.get("Overs", "0")))]
        for r, out, b in bats:
            batted += 1
            runs += r
            balls += b
            outs += out
            hundreds += r >= 100
            fifties += 50 <= r < 100
            best = max(best or 0, r)
        for w, c, overs in bowls:
            wickets += w
            conceded += c
            whole, _, part = overs.partition(".")
            balls_bowled += _int(whole) * 6 + _int(part or 0)
    stats = []
    if batted:
        stats += [
            {"label": "Runs", "value": str(runs)},
            {"label": "Average", "value": f"{runs / outs:.2f}" if outs else "–"},
            {"label": "Strike rate", "value": f"{runs * 100 / balls:.1f}" if balls else "–"},
            {"label": "Highest", "value": str(best)},
            {"label": "100s / 50s", "value": f"{hundreds} / {fifties}"},
        ]
    if balls_bowled:
        stats += [
            {"label": "Wickets", "value": str(wickets)},
            {"label": "Economy", "value": f"{conceded * 6 / balls_bowled:.2f}"},
        ]
    return stats


# ---------------------------------------------------------------- the sport

# A poster's word for a batting milestone (any other mark is its number: "150") and a wicket haul's count.
_RUNS = {50: "Fifty", 100: "Hundred", 200: "Double hundred"}
_HAUL = {3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten"}
MARKS = (50, 100, 150, 200)  # the batting milestones told
# Their moments' priorities: a fifty's and a hundred's are their kinds' (moments.PRIORITY), 150 on above both.
_RUNS_PRIORITY = {50: 70, 100: 90}


def _haul(wickets: int) -> int:
    """A wicket haul's priority: a five-for is near a hundred."""
    return 90 if wickets >= 5 else 75 if wickets == 4 else 70


def _another(was: list[Any], now: list[Any]) -> bool:
    """Whether the player has started another innings since the last poll: the line has more innings,
    and the ones it had are unchanged. A last line that read otherwise (one holding only the latest
    innings) doesn't count, so milestones already passed aren't told late."""
    return len(now) > len(was) and now[:len(was)] == was


def _nth(n: int) -> tuple[str, str]:
    """The player's innings in a cricket moment: the detail's prefix ("i2:"; none for their first, as
    before) and its name in the body ("2nd innings")."""
    if n < 2:
        return "", ""
    return f"i{n}:", f"{ORDINAL.get(n, f'{n}th')} innings"


def winner_outcome(summary: str, teams: list[str]) -> str | None:
    """The winner a result names ("India won by 8 wkts", "Match tied (India won the Super Over)"), as
    one of the player's teams by whole name: "India A" and "Mumbai Indians" aren't India. The app's
    outcome() in web/src/format.ts reads results the same way."""
    winner = re.search(r"(?:^|\()([^()]+?) won\b", summary, re.I)
    if not winner:
        return "D" if any(w in summary.lower() for w in ("drawn", "tied", "no result")) else None
    named = team_key(winner.group(1))
    if named and any(team_key(t) == named for t in teams):
        return "W"
    return "L" if teams else None


class Cricket(Sport):
    key = "cricket"
    label = "Cricket"
    kinds = ("cricket",)
    team = True
    wikidata = {"espn_cricket": "P2697"}
    athletes = 19_745
    no_line = "Did not bat or bowl"
    nation_first = True  # India before Royal Challengers Bengaluru
    games = ("match", "matches")
    draws = True
    colour = {"sport": "#1f5bef", "on": "#ffffff", "on_soft": "#ffffff", "ink": "#7aa2ff"}
    pattern = r"cricket"
    alerts = {
        "key": ("Line-ups, fifties, wickets and results", "In the XI, fifties and hundreds, getting out, three wickets or more, the start, the result, injuries and transfers"),
        "more": ("milestones", "milestones in the news"),
    }
    stats_title = "Overview"

    def lineup_title(self, who: str, starting: bool) -> str:
        return f"{who} is in the XI" if starting else f"{who} is named a substitute"

    def match_page(self, state: dict[str, Any], events: list[dict[str, Any]], lines: dict[str, Line], names: dict[str, tuple[str, str]]) -> dict[str, Any]:
        """A scorecard: each side, in the order they batted, with who batted for it (runs, balls, fours, sixes,
        strike rate, how they were out) and who bowled at it, in the order the source lists them. From each
        player's line, whose stats are their latest innings (a Test's earlier ones are only in its headline)."""
        sides = [t.get("name") or "" for t in state.get("teams") or []]
        if len(sides) != 2 or not all(sides):
            return {}
        batting: dict[str, list[dict[str, str]]] = {s: [] for s in sides}
        bowling: dict[str, list[dict[str, str]]] = {s: [] for s in sides}
        for athlete_id, line in lines.items():
            name, team = names.get(athlete_id, ("", ""))
            if not name or team not in batting:
                continue
            v = values(line)
            if "Runs" in v:
                batting[team].append({"name": name, "runs": v["Runs"], "balls": v.get("Balls", ""), "fours": v.get("4s", ""), "sixes": v.get("6s", ""),
                                      "strike_rate": v.get("Strike rate", ""), "out": v.get("Out", "")})
            if "Wickets" in v:
                bowling[sides[1 - sides.index(team)]].append({"name": name, "overs": v.get("Overs", ""), "runs": v.get("Runs conceded", ""), "wickets": v["Wickets"],
                                                              "economy": v.get("Economy", "")})
        scores = {t.get("name"): t.get("score") or "" for t in state.get("teams") or []}
        batted = [s for s in dict.fromkeys([*(i.get("team") for i in state.get("innings") or []), *sides]) if s in batting]
        card = [{"team": s, "score": scores.get(s, ""), "batting": batting[s], "bowling": bowling[s]} for s in batted if batting[s] or bowling[s]]
        return {"scorecard": card} if card else {}

    def result_text(self, score: str, clock: str) -> str:
        return clock  # "India won by 57 runs"

    def during(self, who: str, athlete_id: str, before: Seen, now: Seen) -> list[Found]:
        """Fifties, hundreds, out and wicket hauls in the player's latest innings: a line's stats are its
        latest batting and bowling innings, and its headline lists every innings ("103 (152) & 48* (60)").
        In a Test the second innings starts again from nothing, and its moments say which innings they
        are in the detail ("i2:100"), so they aren't taken for the first's ("100")."""
        if now.line is None:
            return []
        was_line = before.line
        found = []
        (bats_was, bowls_was), (bats_now, bowls_now) = (innings((line or {}).get("headline")) for line in (was_line, now.line))
        runs_was, runs_now = stat(was_line, "Runs"), stat(now.line, "Runs")
        if _another(bats_was, bats_now):
            runs_was = None  # the last poll's runs were the innings before
        if runs_now is not None:
            balls = stat(now.line, "Balls")
            batting = f"{runs_now} ({balls})" if balls else runs_now
            pre, nth = _nth(len(bats_now))
            for mark in crossed(number(runs_was), number(runs_now), MARKS):
                found.append(Found("hundred" if mark >= 100 else "fifty", "key", f"{who} reaches {mark}", joined(batting, nth, now.score), f"{pre}{mark}", 95 if mark >= 150 else 0))
            out_now = not runs_now.endswith("*") and stat(now.line, "Out") is not None
            out_was = runs_was is not None and not runs_was.endswith("*") and stat(was_line, "Out") is not None
            if out_now and not out_was:
                runs = number(runs_now)
                found.append(Found("out", "key", f"{who} out for {runs}", joined(batting, nth, stat(now.line, "Out")), f"{pre}out", 75 if runs >= 50 else 65 if runs == 0 else 60))
        wickets_was, wickets_now = number(stat(was_line, "Wickets")), number(stat(now.line, "Wickets"))
        if _another(bowls_was, bowls_now):
            wickets_was = 0
        pre, nth = _nth(len(bowls_now))
        for mark in crossed(wickets_was, wickets_now, (3, 4, 5, 6, 7, 8, 9, 10)):
            figures = f"{wickets_now}/{stat(now.line, 'Runs conceded') or '?'} ({stat(now.line, 'Overs') or '?'} ov)"
            found.append(Found("wickets", "key", f"{who} takes {mark} wickets", joined(figures, nth, now.score), f"{pre}w{mark}", _haul(mark)))
        return found

    def high(self, f: Found, versus: str) -> Poster | None:
        """A fifty or more ("Hundred"), or three wickets or more ("Five-for"), with the innings as the body
        has it: "112 (98) v Australia", "5/23 (4 ov) v Australia"."""
        figures = f.body.split(" · ", 1)[0]
        if m := re.search(r" reaches (\d+)$", f.title):
            word = _RUNS.get(int(m[1]), m[1])
        elif m := re.search(r" takes (\d+) wickets$", f.title):
            word = f"{_HAUL.get(int(m[1]), m[1])}-for"
        else:
            return None
        return Poster(word, spaced(figures, versus))

    def finished_poster(self, line: Line, result: str, versus: str) -> Poster | None:
        """The match's best innings, from the headline's ("103 (152) & 48* (60) · 5/23 (4 ov)"): fifty or more
        ("Hundred", "112 (98) v Australia"), or three wickets or more ("Five-for", "5/23 (4 ov) v
        Australia"), as high() words their moments; of a batting and a bowling high, the bigger."""
        made: list[tuple[tuple[int, ...], Poster]] = []  # each by how big: priority, batting first, then more runs or wickets, fewer conceded
        for part in re.split(r" & | · ", line.get("headline") or ""):
            if (m := _BAT.fullmatch(part)) and (marks := [k for k in MARKS if k <= int(m[1])]):
                poster = Poster(_RUNS.get(marks[-1], str(marks[-1])), spaced(part, versus), priority=_RUNS_PRIORITY.get(marks[-1], 95))
                made.append(((poster.priority, 1, int(m[1]), 0), poster))
            elif (m := _BOWL.fullmatch(part)) and int(m[1]) >= 3:
                poster = Poster(f"{_HAUL.get(int(m[1]), m[1])}-for", spaced(part, versus), priority=_haul(int(m[1])))
                made.append(((poster.priority, 0, int(m[1]), -int(m[2])), poster))
        return max(made, key=lambda x: x[0], default=(None, None))[1]

    def outcome(self, result: str, teams: list[str]) -> str | None:
        # "India won by 5 wickets": a win for an India player, a loss for their opponents'.
        return winner_outcome(result, teams)

    def notable(self, fixture: Fixture) -> Notable | None:
        """Every match its sources list is one, as big as its stage (a final 95, a semi-final 85, a playoff
        70, by the round's name: "Final", "Qualifier 1") or who plays it (an international 55, any other 40). Once it's over, 15 more for a close finish: a tie or a super over, CLOSE_RUNS runs or fewer,
        CLOSE_WICKETS wickets or fewer, or CLOSE_BALLS balls to spare. Its word is the margin ("By 6
        wickets"), under it who beat whom. A match to come whose sides are both still to be decided ("TBC v
        TBC", a final before its semi-finals) isn't one until they are: there's nobody to watch yet."""
        level, why = stage(fixture.detail)  # not the competition's: a "… Finals Series" is every round of it
        rank = STAGE_RANK.get(level) or (NATIONS if fixture.facts.get("national") else LEAGUE)
        r = fixture.result
        if fixture.status != "final" or not r:
            return None if undecided(fixture.team_names) else Notable(rank, why=why)
        summary = str(r.get("summary") or "")
        teams = r.get("teams") or []
        winner = next((t for t in teams if t.get("winner")), None)
        loser = next((t for t in teams if t is not winner), None) if winner else None
        line = f"{winner['name']} beat {loser['name']}" if winner and loser and winner.get("name") and loser.get("name") else ""
        margin = _MARGIN.search(summary)
        if fixture.facts.get("super_over") or re.search(r"super over", summary, re.I):
            word, close = "Super over", "Super over"
        elif re.search(r"\btied?\b", summary, re.I):
            word, close = "Tied", "Tied"
        elif margin:
            n, unit, balls = int(margin[1]), margin[2].lower(), margin[3]
            word = f"By {n} {unit}"
            close = "Last over" if balls is not None and int(balls) <= CLOSE_BALLS else "Thriller" if n <= (CLOSE_RUNS if unit.startswith("run") else CLOSE_WICKETS) else ""
        elif re.search(r"no result|abandoned", summary, re.I):
            return None  # nothing to see
        else:
            word, close = (winner or {}).get("name") or summary, ""
        return Notable(min(100, rank + (15 if close else 0)), word=word, line=line, why=why or close)

    def form(self, items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
        return batting_bowling(lines)


SPORT = Cricket()
