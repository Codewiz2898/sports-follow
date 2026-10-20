"""Football: goals with what they did, assists and cards, a goal VAR rules out, a result from the player's
side, form in apps and goals."""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date
from typing import TYPE_CHECKING, Any

from .base import Fact, Fired, Found, Glance, Line, Notable, Played, Poster, Seen, Sport, joined, num, number, ordinal, spaced, stage, stat, values, words

if TYPE_CHECKING:
    from ..adapters.base import Fixture, Snapshot

LATE_MINUTE = 80  # a goal from here on that levels the game or puts a side ahead is "late"
UNDER_WAY = 10  # minutes on the clock: a game first seen live this late was missed, not just started
# One goal or assist on a poster in Today's "This week", by its title's first words ("Equaliser: Saka"). A
# "Hat-trick" alone is the one of three that still stands after VAR took two back: a goal.
HIGHS = {"Late equaliser": "Late equaliser", "Equaliser": "Equaliser", "Late goal": "Late goal", "Goal": "Goal", "Hat-trick": "Goal", "Assist": "Assist"}
MANY_GOALS = {2: "Brace", 3: "Hat-trick"}
# A league game's size (notable): by its stage, else by where its sides are in their table.
STAGE_RANK = {3: 95, 2: 85, 1: 70}
TOP, NEAR_TOP = 4, 7  # two of the top four meeting is a top-of-the-table game; two of the top seven, nearly
UPSET_PLACES = 8  # a winner this many places below the side it beat
ROUT, GOALS = 3, 6  # a win by this many goals, or this many in the game
# Who's out, on the Wire (out_fact): an injury and a ban are news; being ineligible (not registered for the
# competition) isn't. How each is over, said of the player.
BACK = {"injury": "is fit again", "suspension": "is back from suspension"}
# A notification's game at a glance (glance): the clock's short words as the long ones (ESPN's as Sportmonks'), and
# where a clock without a minute stands, of how many, in lower case: a break, extra time's or the shootout's.
CLOCK_WORDS = {"FT": "Full time", "HT": "Half-time", "AET": "After extra time", "FT-Pens": "After penalties"}
BREAKS = {"half-time": (45, 90), "halftime": (45, 90), "break": (90, 120), "extra-time break": (105, 120), "before penalties": (120, 120), "penalties": (120, 120)}
EXTRA = re.compile(r"\bextra|\bpen|\baet\b", re.I)  # a clock or board past 90 minutes: extra time, penalties
# The few of the sources' words for what's wrong that don't read as one on the Wire, lower case.
SAID = {"unknown injury": "injury", "fitness": "lacking fitness", "ill": "illness", "indirect card suspension": "yellow card suspension",
        "suspension through sports court": "banned by a sports court"}


def pens(a: int | None, b: int | None) -> str:
    """ " (4–2 pens)" after a shootout, else ""."""
    return f" ({a}–{b} pens)" if a is not None and b is not None else ""


def _tally(side: dict[str, Any]) -> tuple[int, int] | None:
    """A side's goals, then its shootout penalties, to compare; None if the score isn't a number."""
    score = str(side.get("score") or "")
    return (int(score), side.get("pens") or 0) if score.isdigit() else None


def result_label(fixture: Fixture, team_ids: list[str]) -> str:
    """ "Won 2–1" / "Lost 0–1" / "Drew 1–1" from the player's team's side, else the plain score; a
    shootout adds its score ("Lost 0–0 (2–4 pens)")."""
    r = fixture.result
    if not r:
        return ""
    home, away = r["home"], r["away"]
    ours, theirs = (home, away) if str(home["id"]) in team_ids else (away, home) if str(away["id"]) in team_ids else (None, None)
    if ours is None:
        return f"{home['name']} {home['score']}–{away['score']} {away['name']}{pens(home.get('pens'), away.get('pens'))}"
    score = f"{ours['score']}–{theirs['score']}{pens(ours.get('pens'), theirs.get('pens'))}"
    if ours.get("winner") or theirs.get("winner"):
        return f"{'Won' if ours.get('winner') else 'Lost'} {score}"
    # No winner flag: the score decides.
    mine, other = _tally(ours), _tally(theirs)
    if mine and other and mine != other:
        return f"{'Won' if mine > other else 'Lost'} {score}"
    return f"Drew {score}"


def _plays(now: Seen, athlete_id: str, at: int = 0) -> list[dict[str, Any]]:
    """The goals the player scored (at=0) or set up (at=1) in the source's events, oldest first. Their
    own goals are neither."""
    return [e for e in reversed(now.events) if e.get("kind") == "GOAL" and not e.get("own_goal") and (e.get("athlete_ids") or [])[at:at + 1] == [athlete_id]]


def _play(now: Seen, athlete_id: str, n: int, at: int = 0) -> dict[str, Any] | None:
    """The nth goal the player scored (at=0) or set up (at=1) in the source's events, or None when the
    events don't have it."""
    plays = _plays(now, athlete_id, at)
    return plays[n - 1] if len(plays) >= n else None


def _play_key(kind: str, n: int, play: dict[str, Any] | None) -> str:
    """A goal's or assist's key detail: the source's play (its id, else its minute), not the count. A
    goal VAR rules out takes the count down, and the next real one would have the same count again.
    The count ("goal2") only when the events don't have the play yet."""
    ref = play and (play.get("id") or play.get("clock"))
    return f"{kind}@{ref}" if ref else f"{kind}{n}"


def _base(detail: str) -> str:
    """A goal's (or assist's) key detail without the times it was told again after a correction
    ("goal@101#1" is goal@101, told once more)."""
    return detail.split("#")[0]


def _minute(clock: str | None) -> float:
    """A clock's minute, added time in ("45+2'" is 47); infinity for none (a play keyed by its id)."""
    match = re.fullmatch(r"(\d+)(?:\+(\d+))?'", clock or "")
    return int(match.group(1)) + int(match.group(2) or 0) if match else math.inf


def _standing(kind: str, at: int, athlete_id: str, told: list[str], now: Seen) -> set[str]:
    """Which of the goals (or assists) told stand in the source's events. One told by its play stands
    while the play is anyone's goal. A play of the player's that no told one names is one told before
    its play was in the events (by its count), or one whose play the source has moved (a revised
    minute): as many stand as there are such plays, the ones told by count first, then the nearest in
    minutes to one of them, then the oldest."""
    listed = {_play_key(kind, 0, e) for e in now.events if e.get("kind") == "GOAL"}
    standing = {d for d in told if _base(d) in listed}
    named = {_base(d) for d in standing}
    free = [_minute(e.get("clock")) for e in _plays(now, athlete_id, at) if _play_key(kind, 0, e) not in named]

    def nearest(detail: str) -> float:
        minute = _minute(_base(detail).partition("@")[2])
        return min((abs(minute - m) for m in free), default=math.inf) if minute != math.inf else math.inf

    rest = sorted((d for d in told if d not in standing), key=lambda d: ("@" in d, nearest(d)))
    return standing | set(rest[:len(free)])


def _goal(who: str, athlete_id: str, n: int, now: Seen, where: str) -> Found:
    """The player's nth goal, told with what it did when the source's events say: an equaliser, a goal
    that puts their side ahead (both "late" from LATE_MINUTE), a hat-trick; with its minute and assist."""
    title, priority = f"Goal: {who}" + (f" ({n})" if n > 1 else ""), 85
    goal = _play(now, athlete_id, n)
    if goal is None:
        return Found("goal", "key", title, where, _play_key("goal", n, None), priority)
    minute = goal.get("clock") or now.clock
    body = " · ".join(x for x in (now.score, minute, f"assist {goal['assist']}" if goal.get("assist") else None) if x)
    if n == 3:
        title, priority = f"Hat-trick: {who}", 95
    # What the goal did can be read off the score only if nothing has been scored since.
    newest = next((e for e in now.events if e.get("kind") == "GOAL"), None)
    sides = {(now.state.get(s) or {}).get("id"): now.state.get(s) or {} for s in ("home", "away")}
    mine = sides.get(goal.get("team_id"))
    if newest is goal and mine and mine.get("name") and len(sides) == 2 and n != 3:
        theirs = next(v for k, v in sides.items() if k != goal.get("team_id"))
        lead = number(str(mine.get("score"))) - number(str(theirs.get("score")))
        late = number(minute) >= LATE_MINUTE
        if lead == 0:
            title = f"Late equaliser: {who}" if late else f"Equaliser: {who}"
        elif lead == 1:
            title = f"{'Late goal' if late else 'Goal'}: {who} puts {mine['name']} ahead"
        if lead in (0, 1):
            priority = 95 if late else 90
    return Found("goal", "key", title, body, _play_key("goal", n, goal), priority)


# A game's goals and cards, as the source's events name them (Snapshot.moments), as a game's own moments:
# kind, level and priority. A goal's priority rises with what it did (_goal_head).
PLAYS = {"GOAL": ("goal", "key", 85), "RED": ("red", "key", 80), "YELLOW": ("yellow", "minor", 20)}


def _keyed(events: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """The source's goals and cards, oldest first, each with its detail in a game's moments: its kind and the
    source's play (its id, else its minute; a second alike in one minute counted on: "goal@23'#2"). A goal VAR
    ruled out (DISALLOWED) keeps its goal's, so its correction names the goal told."""
    out, seen = [], Counter()
    for e in reversed(events):
        kind = "goal" if e.get("kind") in ("GOAL", "DISALLOWED") else PLAYS.get(e.get("kind") or "", ("",))[0]
        if not kind:
            continue
        ref = _play_key(kind, 0, e)
        seen[ref] += 1
        out.append((ref if seen[ref] == 1 else f"{ref}#{seen[ref]}", e))
    return out


def _who(e: dict[str, Any]) -> str:
    """The player an event names, without their side ("Saka (Arsenal), assist Ødegaard" is Saka)."""
    return re.split(r" \(|, ", e.get("text") or "", maxsplit=1)[0] or "?"


def _goal_head(e: dict[str, Any], now: Played, newest: bool) -> tuple[str, int]:
    """A game's goal as its moment's title says it, and how big: an equaliser or a side going ahead (late
    from LATE_MINUTE) when it's the newest goal, so the score says what it did; an own goal as one."""
    who = _who(e)
    if e.get("own_goal"):
        return f"own goal by {who}", 85
    sides = {(now.state.get(s) or {}).get("id"): now.state.get(s) or {} for s in ("home", "away")}
    mine = sides.get(e.get("team_id"))
    if not (newest and mine and mine.get("name") and len(sides) == 2):
        return f"{who} scores", 85
    theirs = next(v for k, v in sides.items() if k != e.get("team_id"))
    lead = number(str(mine.get("score"))) - number(str(theirs.get("score")))
    late = number(e.get("clock")) >= LATE_MINUTE
    if lead == 0:
        return (f"late equaliser from {who}" if late else f"{who} equalises"), 95 if late else 90
    if lead == 1:
        return (f"late goal: {who} puts {mine['name']} ahead" if late else f"{who} puts {mine['name']} ahead"), 95 if late else 90
    return f"{who} scores", 85


class Football(Sport):
    key = "football"
    label = "Football"
    names = ("soccer", "association football")
    kinds = ("football",)
    team = True
    follow_teams = True
    # Women's players are on Soccerdonna (Transfermarkt's women's site) and FBref, rarely Transfermarkt.
    wikidata = {"espn_soccer": "P3681", "transfermarkt": "P2446", "soccerdonna": "P4381", "fbref": "P5750"}
    athletes = 260_000
    no_line = "Not in the matchday squad"
    team_sheet_before = 75 * 60  # the Premier League's since 2024-25; most leagues publish about an hour ahead
    draws = True
    colour = {"sport": "#12c46c", "on": "#0b1a10", "on_soft": "rgba(11, 26, 16, 0.8)", "ink": "#12c46c"}
    pattern = r"soccer|football"
    exclude = r"american|australian|gaelic|canadian|rugby|rules"  # other codes of football
    alerts = {
        "key": ("Line-ups, goals and results", "Starting or on the bench, goals, assists, red cards, kick-off, the result, injuries and transfers"),
        "more": ("subs and yellow cards", "coming on, yellow cards and milestones"),
    }

    game_alerts = {
        "key": ("Goals, red cards and the result", "Every goal, whoever scores it, red cards, kick-off and the result"),
        "more": ("yellow cards", "yellow cards"),
    }
    kind_words = {"goal": "Goal", "red": "Red card"}

    def matchup(self, state: dict[str, Any]) -> str:
        names = [(state.get(side) or {}).get("name") for side in ("home", "away")]
        return " v ".join(n for n in names if n) if len(names) == 2 and all(names) else ""

    def match_page(self, state: dict[str, Any], events: list[dict[str, Any]], lines: dict[str, Line], names: dict[str, tuple[str, str]]) -> dict[str, Any]:
        """A timeline: each goal (one VAR ruled out, marked) and card at its minute, newest first, on the side
        it counts for (0 home, 1 away; an own goal the other side's)."""
        ids = [str((state.get(side) or {}).get("id")) for side in ("home", "away")]
        timeline = []
        for e in events:
            side = ids.index(str(e.get("team_id"))) if str(e.get("team_id")) in ids else None
            if side is not None and e.get("own_goal"):
                side = 1 - side
            timeline.append({"clock": e.get("clock"), "kind": e.get("kind"), "text": e.get("text"), "side": side})
        return {"timeline": timeline} if timeline else {}

    def glance(self, state: dict[str, Any], score: str, clock: str, status: str | None, events: list[dict[str, Any]]) -> Glance:
        """Home and away with their goals, the clock ("67'", "Half-time", "Full time"), the minute of 90 (of 120 in
        extra time, a minute past 90 that isn't added time; a break where it stands), and each goal at its minute
        for the side it counts for, as the match page's timeline has them (an own goal the other side's; a goal VAR
        ruled out, DISALLOWED, isn't one)."""
        seen = super().glance(state, score, clock, status, events)
        played = re.fullmatch(r"(\d+)(?:\+(\d+))?'", clock or "")
        extra = bool(EXTRA.search(f"{clock} {state.get('detail') or ''}"))
        if played:
            of = 120 if extra or int(played[1]) > 90 else 90
            seen.progress = (min(int(played[1]) + int(played[2] or 0), of), of)
        elif status == "final":
            seen.progress = (120, 120) if extra else (90, 90)
        else:
            seen.progress = BREAKS.get((clock or "").lower())
        of = seen.progress[1] if seen.progress else 120 if extra else 90
        ids = [str((state.get(side) or {}).get("id")) for side in ("home", "away")]
        for e in events:
            minute = _minute(e.get("clock"))
            if e.get("kind") == "GOAL" and str(e.get("team_id")) in ids and minute != math.inf:
                side = ids.index(str(e.get("team_id")))
                seen.marks.append((min(minute, of), 1 - side if e.get("own_goal") else side))
        seen.marks.sort()
        seen.status = CLOCK_WORDS.get(clock, clock) or ("Full time" if status == "final" else "")
        return seen

    def verdict(self, state: dict[str, Any], score: str, clock: str) -> str:
        """The side with more goals won; level is a draw, but for a game settled on penalties, which the
        board's goals don't say: then nothing, and the clock line says how it ended."""
        home, away = state.get("home") or {}, state.get("away") or {}
        goals = [str(side.get("score") or "") for side in (home, away)]
        if not all(g.isdigit() for g in goals):
            return ""
        if goals[0] != goals[1]:
            return f"{(home if int(goals[0]) > int(goals[1]) else away).get('name') or 'They'} won"
        return "" if re.search(r"pen", f"{state.get('detail') or ''} {clock}", re.I) else "Draw"

    def side_final(self, state: dict[str, Any], score: str, clock: str, team_id: str) -> str:
        """As every sport's, but a game level on goals and settled on penalties, which the board's goals don't
        say, isn't a draw: the game's own result is told instead."""
        home, away = state.get("home") or {}, state.get("away") or {}
        level = str(home.get("score") or "") == str(away.get("score") or "")
        if level and not (home.get("winner") or away.get("winner")) and re.search(r"pen", f"{state.get('detail') or ''} {clock}", re.I):
            return ""
        return super().side_final(state, score, clock, team_id)

    def game_during(self, name: str, before: Played, now: Played, told: list[tuple[str, str]]) -> list[Found]:
        """Every goal and card the source's events add, whoever's: a goal with what it did ("Saka puts Arsenal
        ahead", an equaliser, an own goal) and its minute, a red card (key), a yellow (minor); each about the
        player it names. And a goal told that the source has since ruled out (DISALLOWED), corrected. A play
        already told isn't again, should the source drop and add it back."""
        had = {ref for ref, _ in _keyed(before.events)} | {d for k, d in told if k != "correction"}
        plays = _keyed(now.events)
        newest = next((e for _, e in reversed(plays) if e.get("kind") == "GOAL"), None)
        found = []
        for ref, e in plays:
            scorer = (e.get("athlete_ids") or [""])[0]
            # An own goal isn't its scorer's goal (their line doesn't count it): about them apart, so it's never
            # taken for one of theirs, nor counted among them.
            about = (f"og:{scorer}",) if e.get("own_goal") else (scorer,)
            where = joined(now.score, e.get("clock"), "penalty" if "penalty" in (e.get("text") or "") else None, f"assist {e['assist']}" if e.get("assist") else None)
            if e.get("kind") in PLAYS and ref not in had:
                kind, level, priority = PLAYS[e["kind"]]
                if kind == "goal":
                    head, priority = _goal_head(e, now, e is newest)
                else:
                    head = f"{'red' if kind == 'red' else 'yellow'} card for {e.get('text') or _who(e)}"
                found.append(Found(kind, level, f"{name}: {head[:1].upper()}{head[1:]}", where, ref, priority, about))
            elif e.get("kind") == "DISALLOWED" and ("goal", ref) in told and ("correction", f"goal:{ref}") not in told:
                found.append(Found("correction", "key", f"{name}: Goal ruled out, {_who(e)}", joined(now.score, now.clock), f"goal:{ref}", 85, about))
        return found

    def left_out(self, now: Seen) -> bool:
        # A national team game shows up for every player of that country, called up or not.
        return now.lineups and now.line is None

    def missed_start(self, snap: Snapshot) -> bool:
        return number(snap.clock_label) >= UNDER_WAY

    def during(self, who: str, athlete_id: str, before: Seen, now: Seen) -> list[Found]:
        if now.line is None:
            return []
        was_line = before.line
        found = []
        where = joined(now.score, now.clock)
        for label, kind, level, title in (("Goals", "goal", "key", "Goal: {who}"), ("Assists", "assist", "key", "Assist: {who}"), ("Red", "red", "key", "Red card: {who}"), ("Yellow", "yellow", "minor", "Yellow card: {who}")):
            was, got = number(stat(was_line, label)), number(stat(now.line, label))
            for n in range(was + 1, got + 1):
                if kind == "goal":
                    found.append(_goal(who, athlete_id, n, now, where))
                elif kind == "assist":
                    found.append(Found(kind, level, title.format(who=who) + (f" ({n})" if n > 1 else ""), where, _play_key(kind, n, _play(now, athlete_id, n, at=1))))
                else:
                    found.append(Found(kind, level, title.format(who=who), where, f"{kind}{n}"))
        if (was_line or {}).get("headline") == "On the bench" and (now.line or {}).get("headline") not in (None, "On the bench"):
            found.append(Found("on", "minor", f"{who} comes on", where))
        return found

    def taken_back(self, who: str, athlete_id: str, before: Seen, now: Seen, told: list[tuple[str, str]]) -> list[Found]:
        """A goal fans were told about that VAR has ruled out, and the assist on it: one that doesn't stand
        in the source's events (_standing: no one's goal now, nor a play of theirs moved or re-credited),
        with the player's count below the number told. The count is the evidence: below in this poll and
        the last (or in the final one), or in this poll alone when the source marks the goal ruled out
        (DISALLOWED). So one poll from an older copy of the game, a play the source moved (a revised
        minute), or a goal rescinded and added again, takes nothing back. A goal taken back that the
        source shows as theirs again, the count back up in both polls (the correction came from older
        copies), is told again."""
        if before.line is None or now.line is None:
            return []
        corrections = [d for k, d in told if k == "correction"]
        where = joined(now.score, now.clock)
        found = []
        for kind, label, at, title in (("goal", "Goals", 0, "Goal ruled out: {who}"), ("assist", "Assists", 1, "Goal ruled out: no assist for {who}")):
            mine = [d for k, d in told if k == kind and f"{kind}:{d}" not in corrections]
            got = number(stat(now.line, label))
            counts = [got] if now.status == "final" else [number(stat(before.line, label)), got]
            standing = _standing(kind, at, athlete_id, mine, now)
            missing = [d for d in reversed(mine) if d not in standing]  # newest first
            # Marked ruled out: the count below what was told in this poll is enough. Otherwise in both.
            marked = {_play_key(kind, 0, e) for e in now.events if e.get("kind") == "DISALLOWED" and (e.get("athlete_ids") or [])[at:at + 1] == [athlete_id]}
            gone = [d for d in missing if _base(d) in marked][:max(len(mine) - got, 0)]
            gone += [d for d in missing if d not in gone][:max(len(mine) - max(counts) - len(gone), 0)]
            found += [Found("correction", "key", title.format(who=who), where, f"{kind}:{d}") for d in gone]
            # Taken back, and theirs again with the count back up: told again, under the times it was taken back.
            times = Counter(_base(d.split(":", 1)[1]) for d in corrections if d.startswith(f"{kind}:"))
            theirs = {_play_key(kind, 0, e): n for n, e in enumerate(_plays(now, athlete_id, at), 1)}
            back = [b for b in times if "@" in b and b in theirs and (kind, f"{b}#{times[b]}") not in told]
            for b in back[:max(min(counts) - len(mine), 0)]:
                again = _goal(who, athlete_id, theirs[b], now, where) if kind == "goal" else Found(kind, "key", f"Assist: {who}", where)
                again.detail = f"{b}#{times[b]}"
                found.append(again)
        return found

    def poster(self, found: list[Found], versus: str) -> Poster | None:
        """Two goals or more are a "Brace", a "Hat-trick", "4 goals", counted from the goals that stand (a
        goal's title says what it did, which can drop its count: a second goal that levels the game is an
        "Equaliser"); two assists and no goal, "2 assists". Else the biggest single high."""
        for kind, many in (("goal", MANY_GOALS), ("assist", {})):
            mine = [f for f in found if f.kind == kind]
            if len(mine) > 1:
                return Poster(many.get(len(mine), f"{len(mine)} {kind}s"), versus, max(mine, key=lambda f: f.priority))
            if mine:
                break  # one goal outshines any number of assists
        return super().poster(found, versus)

    def high(self, f: Found, versus: str) -> Poster | None:
        """One goal by what it did ("Late equaliser", "Equaliser") or an assist, at its minute: "67' v Chelsea"."""
        word = HIGHS.get(f.title.partition(": ")[0])
        if word is None:
            return None  # a card, a goal ruled out
        minute = re.search(r"\b\d+(?:\+\d+)?'", f.body)
        return Poster(word, spaced(minute[0] if minute else None, versus))

    def finished_poster(self, line: Line, result: str, versus: str) -> Poster | None:
        """The goals a finished game's line counts ("2 goals, 1 assist"), else its assists, as poster() words
        them: "Brace", "Hat-trick", "4 goals", one "Goal"; "2 assists", one "Assist". No minute: the
        line doesn't say when, nor what a goal did."""
        for kind, one, many in (("goal", HIGHS["Goal"], MANY_GOALS), ("assist", HIGHS["Assist"], {})):
            if said := re.search(rf"\b(\d+) {kind}s?\b", line.get("headline") or ""):
                n = int(said[1])
                priority = (95 if n >= 3 else 85) if kind == "goal" else 65  # a hat-trick's moment's, a goal's, an assist's
                return Poster(many.get(n, f"{n} {kind}s") if n > 1 else one, versus, priority=priority)
        return None

    def notable(self, fixture: Fixture) -> Notable | None:
        """Every game of the leagues its sources cover is one, as big as its stage (a final 95, a semi-final
        85, another knockout round 70) or its sides' places in their table (two of the top four 80, two of
        the top seven 65, the leaders' 55, any other 40). Once it's over, 15 more for an upset (the winner
        UPSET_PLACES or more below), 10 for a shootout, a rout or a goal-fest. Its word is the score."""
        level, why = stage(fixture.detail)
        places = [p if isinstance(p, int) and p > 0 else None for p in (fixture.facts.get("table") or [])[:2]]
        rank = STAGE_RANK.get(level, 40)
        if not level and len(places) == 2 and None not in places:
            high, low = sorted(p for p in places if p)
            if low <= TOP or (low <= NEAR_TOP and high <= NEAR_TOP):
                rank, why = (80 if low <= TOP else 65), f"{ordinal(places[0])} v {ordinal(places[1])}"
            elif high == 1:
                rank, why = 55, "Leaders"
        r = fixture.result
        if fixture.status != "final" or not r:
            return Notable(rank, why=why)
        home, away = r.get("home") or {}, r.get("away") or {}
        sides = [_tally(home), _tally(away)]
        bonus, said = 0, ""
        if sides[0] and sides[1]:
            goals = (sides[0][0], sides[1][0])
            won = 0 if home.get("winner") or sides[0] > sides[1] else 1 if away.get("winner") or sides[1] > sides[0] else None
            winner, loser = (places[won], places[1 - won]) if won is not None and len(places) == 2 else (None, None)
            if winner and loser and winner - loser >= UPSET_PLACES:
                bonus, said = 15, "Upset"
            elif home.get("pens") is not None:
                bonus, said = 10, "Penalties"
            elif abs(goals[0] - goals[1]) >= ROUT or sum(goals) >= GOALS:
                bonus, said = 10, f"{sum(goals)} goals"
        return Notable(min(100, rank + bonus), word=f"{home.get('score')}–{away.get('score')}", why=why or said)

    def out_fact(self, who: str, kind: str, what: str, until: date | None) -> Fact | None:
        """ "Out", then who, what ("back injury", "red card suspension"; SAID rewords a few) and until when, if
        the source says: "William Saliba · back injury · until 11 Dec"."""
        if kind not in BACK:
            return None
        what = what.strip().lower()
        what = SAID.get(what, what) or kind
        return Fact("Out", joined(who, what, f"until {until.day} {until:%b}" if until else None))

    def back_fact(self, who: str, kind: str) -> Fact | None:
        """ "Back": "William Saliba is fit again", "Phil Foden is back from suspension"."""
        return Fact("Back", f"{who} {BACK[kind]}") if kind in BACK else None

    def form(self, items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
        played = [ln for ln in lines if ln.get("headline") not in (None, "On the bench")]
        total = lambda label: int(sum(num(values(ln).get(label)) for ln in played))  # noqa: E731
        stats = [{"label": "Apps", "value": str(len(played))}]
        if played:
            stats += [{"label": "Goals", "value": str(total("Goals"))}, {"label": "Assists", "value": str(total("Assists"))}]
            shots, on_target = total("Shots"), total("On target")
            if shots:
                stats.append({"label": "Shots (on target)", "value": f"{shots} ({on_target})"})
        return stats

    # ---- the Feed's content judge (curate.py)

    def content_hints(self) -> str:
        return """\
A milestone: a club or career goal landmark (100th, 500th), a hat-trick, a debut, 100 caps, a trophy (league, cup, \
Champions League, World Cup), an individual award (Ballon d'Or, Golden Boot). A match moment: a goal, an assist, a \
save, a red card, a missed penalty. Transfers and injuries reported by clubs or outlets are news; "here we go", \
"done deal" or "sources" from aggregator accounts before a club confirms are rumours. Full-match or full-half \
uploads are restreams; extended highlights from a league or broadcaster are official."""

    # ---- the feed (curator.py): channels trusted for videos about its players (each feed answered, 20 Oct 2026)

    video_channels = (
        "UC6c1z7bA__85CIWZ_jpCK-Q",  # ESPN FC: analysis, debate
        "UCjXIw1GlwaY1IzpW_jN9iCQ",  # The Overlap: interviews
        "UCXzrxvm0Jqq1OpSZQhT6lEQ",  # Statman Dave: tactical analysis
        "UCEPKlFeUjRY_1mpZP8GnkVg",  # COPA90: fan culture, features
        "UCjVd6vTuoAYLFYvX5TSK1aA",  # GOAL: features, interviews
    )

    def search_words(self, fired: Fired) -> str | None:
        """A goal, a brace or a hat-trick (the player's goals in the game so far), in the game: "brace Liverpool vs
        Everton". A later goal in the same game takes over its search before that runs (searches.py)."""
        if fired.kind != "goal":
            return None
        return words({1: "goal", 2: "brace"}.get(fired.count, "hat-trick"), fired.game)


SPORT = Football()
