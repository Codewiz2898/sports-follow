"""Football: goals with what they did, assists and cards, a goal VAR rules out, a result from the player's
side, form in apps and goals."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import Found, Line, Seen, Sport, joined, num, number, stat, values

if TYPE_CHECKING:
    from ..adapters.base import Fixture, Snapshot

LATE_MINUTE = 80  # a goal from here on that levels the game or puts a side ahead is "late"
UNDER_WAY = 10  # minutes on the clock: a game first seen live this late was missed, not just started


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


def _standing(kind: str, at: int, athlete_id: str, told: list[str], now: Seen) -> tuple[set[str], int]:
    """Which of the goals (or assists) told stand in the source's events, and how many of the player's
    there no told one names (scored before anyone followed). One told by its play stands while the play
    is anyone's goal; one told by its count (the play wasn't in the events yet) while a play of theirs
    isn't another told one's."""
    standing = {d for d in told if d in {_play_key(kind, 0, e) for e in now.events if e.get("kind") == "GOAL"}}
    free = [e for e in _plays(now, athlete_id, at) if _play_key(kind, 0, e) not in standing]
    counted = [d for d in told if "@" not in d]
    return standing | set(counted[:len(free)]), max(len(free) - len(counted), 0)


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


class Football(Sport):
    key = "football"
    label = "Football"
    names = ("soccer", "association football")
    kinds = ("football",)
    team = True
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

    def matchup(self, state: dict[str, Any]) -> str:
        names = [(state.get(side) or {}).get("name") for side in ("home", "away")]
        return " v ".join(n for n in names if n) if len(names) == 2 and all(names) else ""

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
        """A goal fans were told about that VAR has ruled out, and the assist on it. The source marks it so
        (DISALLOWED in its events), unless the same side's goal at that minute stands for someone else
        (re-credited, not ruled out); or the player's count has been short of what they were told for two
        polls running and its play is no one's goal now, so one poll from an older copy of the game takes
        nothing back. Never one already corrected."""
        corrected = {d for k, d in told if k == "correction"}
        found = []
        for kind, label, at, title in (("goal", "Goals", 0, "Goal ruled out: {who}"), ("assist", "Assists", 1, "Goal ruled out: no assist for {who}")):
            mine = [d for k, d in told if k == kind and f"{kind}:{d}" not in corrected]
            stands = {(_play_key(kind, 0, e), e.get("team_id")) for e in now.events if e.get("kind") == "GOAL"}
            marked = [e for e in now.events if e.get("kind") == "DISALLOWED" and (e.get("athlete_ids") or [])[at:at + 1] == [athlete_id]]
            gone = [d for d in mine if any(_play_key(kind, 0, e) == d and (d, e.get("team_id")) not in stands for e in marked)]
            if before.line is not None and now.line is not None:
                # Short of what was told (and scored untold) in this poll and the last: as many as that, less
                # those marked, of the ones that don't stand, newest first.
                standing, untold = _standing(kind, at, athlete_id, mine, now)
                short = len(mine) + untold - max(number(stat(before.line, label)), number(stat(now.line, label))) - len(gone)
                gone += [d for d in reversed(mine) if d not in standing and d not in gone][:max(short, 0)]
            found += [Found("correction", "key", title.format(who=who), joined(now.score, now.clock), f"{kind}:{d}") for d in gone]
        return found

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


SPORT = Football()
