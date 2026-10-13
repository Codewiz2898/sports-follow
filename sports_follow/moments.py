"""Moments: what's worth telling a player's fans, found by comparing one live poll with the last.

detect() is pure: it takes the event and the player's line as they were and as they are, and says
what happened in between (a goal, a fifty, a set, the result). Each moment carries a stable key, so
however many polls see the same fifty, fans hear about it once. Its level decides who hears:

    key    goals, assists, red cards, fifties and hundreds, 3+ wickets, out, 30/40/50 points,
           triple-doubles, chess games starting, a chess player winning, in trouble or turning a
           game around, the line-up (starting or on the bench), esports series and lobbies
           starting, a chicken dinner (Booyah), going top of a lobby, every result, upsets,
           injuries, transfers
    minor  coming on, yellow cards, 20 points, double-doubles, each set, each esports map (with a Dota
           player's kills, deaths and assists once OpenDota has them), each
           battle royale game's placement, a chess player better, worse or level again, milestones
           in the news

Fans choose per player (follow.alert_rules["level"]): everything, key moments (the default),
results only, or off.

Each moment also has a priority, 0 to 100: how much it's worth interrupting someone for. A hundred
outranks a fifty, a late equaliser outranks a fourth goal in a rout, a duck outranks being out for 20.
alerts.py uses it to pick which moment leads a notification and which may still buzz once a fan's
phone has buzzed enough.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import games
from .adapters.espn_cricket import innings as cricket_innings

LEVELS = ("everything", "key", "results", "off")
DEFAULT_LEVEL = "key"
NEWS_KINDS = {"injury": "key", "transfer": "key", "retirement": "key", "milestone": "minor"}
NEWS_PRIORITY = {"injury": 75, "transfer": 70, "retirement": 90, "milestone": 40}
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}
# A moment's priority unless its rule sets one (a hundred over a fifty, a late equaliser over a goal).
PRIORITY = {
    "final": 80, "start": 40, "lineup": 50, "goal": 85, "assist": 65, "red": 80, "yellow": 20, "on": 30,
    "fifty": 70, "hundred": 90, "out": 60, "wickets": 70, "points": 30, "double": 30, "triple": 85,
    "set": 30, "map": 30, "kda": 30, "game": 30, "dinner": 90, "lead": 75, "upset": 90, "swing": 30, "news": 60,
}
LATE_MINUTE = 80  # a goal from here on that levels the game or puts a side ahead is "late"


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
    priority: int = 0  # 0 to 100; 0 takes the kind's PRIORITY


def wanted(level: str, moment_level: str, kind: str) -> bool:
    """Whether a fan who chose `level` for a player hears about this moment."""
    if level == "everything":
        return True
    if level == "results":
        return kind == "final"
    if level == "off":
        return False
    return moment_level == "key"


def news_kind(value: str | None) -> str | None:
    """The agent's kind if it is one of NEWS_KINDS ("Injury" is), else None. Matched whole: by substring,
    "contract extension" was a transfer and "record" a milestone."""
    text = (value or "").strip().lower()
    return text if text in NEWS_KINDS else None


# ---------------------------------------------------------------- reading a line


def stat(line: dict[str, Any] | None, label: str) -> str | None:
    for s in (line or {}).get("stats") or []:
        if s.get("label") == label:
            return str(s.get("value"))
    return None


def number(value: str | None) -> int:
    match = re.match(r"\s*(\d+)", value or "")
    return int(match.group(1)) if match else 0


def counts(line: dict[str, Any] | None) -> dict[str, int]:
    """A basketball line's counting stats: points, rebounds and assists from the headline
    ("15 PTS · 3 REB · 10 AST"), steals and blocks from the stats."""
    head = (line or {}).get("headline") or ""
    got = {key: int(n) for n, key in re.findall(r"(\d+)\s*(PTS|REB|AST)", head)}
    got["STL"], got["BLK"] = number(stat(line, "Steals")), number(stat(line, "Blocks"))
    return got


def crossed(before: int, now: int, marks: tuple[int, ...]) -> list[int]:
    return [m for m in marks if before < m <= now]


# ---------------------------------------------------------------- per sport


def detect(sport: str, who: str, athlete_id: str, before: Seen | None, now: Seen) -> list[Found]:
    """What happened to this player between two polls.

    before=None is a first look at a game already under way (the worker just started, or the fan
    followed mid-innings): only a result is reported from it, so nobody hears "reaches 50" for runs
    scored an hour ago. A line-up is still news on a first look, as long as the game hasn't started.
    """
    sport = games.family(sport)
    found: list[Found] = []
    was = before.status if before else None
    head = (now.line or {}).get("headline") or ""
    in_game = now.line is not None and head != "On the bench" and not head.startswith("Did not play")

    found += _lineup(sport, who, now)
    # A series starts for the whole team (a Dota player OpenDota can't name has no line), and so does a
    # lobby nobody listed players for.
    team_start = sport == "esports" and (not _lobby(now) or not now.lineups)
    if before is not None and now.status == "live" and was in ("scheduled", "armed", "postponed") and (in_game or sport in ("tennis", "chess") or team_start):
        found.append(_start(sport, who, now))
    if now.status == "final" and was != "final" and not _left_out(sport, now):
        found.append(_final(sport, who, now))
        found += _upset(sport, who, athlete_id, now)
    if before is not None:
        if sport in ("football", "soccer") and now.line is not None:
            found += _football(who, athlete_id, before.line, now)
        elif sport in ("cricket", "basketball", "tennis") and now.line is not None:
            found += {"cricket": _cricket, "basketball": _basketball, "tennis": _tennis}[sport](who, before.line, now)
        elif sport == "chess":
            found += _chess(who, before, now)
        elif sport == "esports":
            found += _lobby_games(who, before, now) if _lobby(now) else _maps(who, before, now) + _dota(who, athlete_id, before, now)
    for f in found:
        f.priority = f.priority or PRIORITY.get(f.kind, 50)
    return found


def _matchup(state: dict[str, Any]) -> str:
    """"Liverpool v Chelsea" from a football or cricket state, or "" for other sports."""
    if state.get("kind") == "football":
        names = [(state.get(side) or {}).get("name") for side in ("home", "away")]
    else:
        names = [t.get("name") for t in state.get("teams") or []]
    return " v ".join(n for n in names if n) if len(names) == 2 and all(names) else ""


def _lineup(sport: str, who: str, now: Seen) -> list[Found]:
    """The team sheet is out, before the start: the player starts, or is on the bench. Its key is the
    role, so a late change ("now on the bench") is news too. Not being on the sheet isn't told: a
    national team game shows up for every player of that country, called up or not."""
    if now.status not in ("scheduled", "armed") or now.role not in ("starting", "bench"):
        return []
    starting = now.role == "starting"
    if sport == "cricket":
        title = f"{who} is in the XI" if starting else f"{who} is named a substitute"
    else:
        title = f"{who} starts" if starting else f"{who} is on the bench"
    body = " · ".join(x for x in (_matchup(now.state), "line-ups are out") if x)
    return [Found("lineup", "key", title, body[:1].upper() + body[1:], now.role, 50 if starting else 45)]


def _left_out(sport: str, now: Seen) -> bool:
    """Known not to be in the game: the line-ups are out and the player isn't in them (a national
    team game they weren't called up for). The card says the same ("Not in the matchday squad").
    In cricket no line only means they haven't batted or bowled, so it doesn't count."""
    return (sport in ("football", "soccer", "basketball") or (sport == "esports" and _lobby(now))) and now.lineups and now.line is None


# ---------------------------------------------------------------- chess swings

# An engine evaluation (engine.py) falls in a band, from White's side: 2 winning, 1 better, 0 level,
# -1 worse, -2 losing. +3.0 and +1.4 are 75% and 63% winning chances in Lichess's model; a forced
# mate is winning. Leaving a band takes MARGIN more, so a score sitting on a line doesn't flap.
WINNING, BETTER, MARGIN = 300, 140, 30


def _level(cp: int) -> int:
    return 2 if cp >= WINNING else 1 if cp >= BETTER else -2 if cp <= -WINNING else -1 if cp <= -BETTER else 0


def band(ev: dict[str, Any], current: int = 0) -> int:
    if ev.get("mate") is not None:
        return 2 if ev["mate"] > 0 else -2
    cp = ev.get("cp") or 0
    new = _level(cp)
    if new > current:
        return max(current, _level(cp - MARGIN))
    if new < current:
        return min(current, _level(cp + MARGIN))
    return new


def settle(before: dict[str, Any] | None, ev: dict[str, Any]) -> dict[str, Any]:
    """An evaluation with its settled band. The band moves only when two evaluations in a row agree,
    so a broadcast glitch (a board sending a wrong move for a few seconds) isn't a swing.

    "told" is the winning band (2 or -2) the game last moved into, kept until it's level again or the
    other side is better: an evaluation hovering on the winning line (+2.6, +3.4, +2.7…) moves the band
    back and forth, and the player is winning once, not at every climb."""
    if before and before.get("at") == ev.get("at"):
        return before
    settled = before.get("band", 0) if before else 0
    raw = band(ev, settled)
    moved = before is None or raw == before.get("raw")
    new = raw if moved else settled
    told = before.get("told", 0) if before else 0
    if before is not None and abs(new) == 2 and new != settled:
        told = new
    elif new * told <= 0:
        told = 0
    return {**ev, "move": _fullmove(ev.get("fen")), "raw": raw, "band": new, "told": told}


def _fullmove(fen: str | None) -> int | None:
    fields = (fen or "").split()
    return int(fields[5]) if len(fields) > 5 and fields[5].isdigit() else None


def _chess(who: str, before: Seen, now: Seen) -> list[Found]:
    """A followed player's game turning, as the engine sees it (from their side)."""
    was, got = before.state.get("eval"), now.state.get("eval")
    if now.status != "live" or not was or not got or before.state.get("game_id") != now.state.get("game_id"):
        return []
    colour = stat(now.line, "Colour")
    side = 1 if colour == "White" else -1 if colour == "Black" else 0
    old, new = was["band"] * side, got["band"] * side
    if not side or old == new:
        return []
    if abs(new) == 2 and (was.get("told") or 0) * side == new:
        return []  # told already, and the game hasn't been level or turned since: back over the line
    if new == 2:
        title, level = f"{who} is winning", "key"
    elif new == -2:
        title, level = f"{who} is in trouble", "key"
    elif new == 1 and old <= 0:
        title, level = f"{who} is better", "minor"
    elif new == -1 and old >= 0:
        title, level = f"{who} is worse", "minor"
    elif new == 0 and abs(old) == 2:
        title, level = (f"{who} is back level" if old < 0 else f"{who}'s advantage is gone"), "minor"
    else:
        return []  # drifting toward level on the same side
    turnaround = old * new < 0
    if turnaround:
        title, level = f"Turnaround: {title}", "key"
    opponent = stat(now.line, "Opponent")
    body = " · ".join(x for x in (f"Engine {_score(got, side)}", f"move {got['move']}" if got.get("move") else None, f"vs {opponent}" if opponent else None) if x)
    return [Found("swing", level, title, body, f"b{new}m{got.get('move') or 0}", 85 if turnaround else 70 if level == "key" else 30)]


def _score(ev: dict[str, Any], side: int) -> str:
    """An evaluation from the player's side: "+3.6", "mate in 4", "facing mate in 4"."""
    if ev.get("mate") is not None:
        mate = ev["mate"] * side
        return f"mate in {mate}" if mate > 0 else f"facing mate in {-mate}"
    return f"{(ev.get('cp') or 0) * side / 100:+.1f}"


# ---------------------------------------------------------------- start and result


def _start(sport: str, who: str, now: Seen) -> Found:
    if sport == "chess":
        rnd = now.state.get("round") or "Their game"
        opp = stat(now.line, "Opponent")
        return Found("start", "key", f"{who} is playing", f"{rnd}{f' · vs {opp}' if opp else ''}")
    if sport == "tennis":
        return Found("start", "key", f"{who}'s match has started", " · ".join(x for x in (now.state.get("round"), now.state.get("tournament")) if x))
    if sport == "esports" and _lobby(now):
        games = now.state.get("total")
        return Found("start", "key", f"{who} is playing", " · ".join(x for x in (now.state.get("stage"), f"{games} matches" if games else None, now.state.get("game")) if x))
    if sport == "esports":
        return Found("start", "key", f"{who}'s series has started", " · ".join(x for x in (_matchup(now.state), now.state.get("format"), now.state.get("stage")) if x))
    return Found("start", "key", f"{who}'s game has started", " · ".join(x for x in (now.score, now.clock) if x))


def _final(sport: str, who: str, now: Seen) -> Found:
    if sport == "esports":
        return _lobby_final(who, now) if _lobby(now) else _series(who, now)
    head = (now.line or {}).get("headline") or ""
    if sport in ("tennis", "chess"):
        return Found("final", "key", f"{who}: {head or 'result'}", now.score)
    result = now.clock if sport == "cricket" else now.score
    return Found("final", "key", f"{who}: {result or 'full time'}", head)


def _sides(now: Seen) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """An esports series' two teams, the player's first, or None when their team isn't one of them."""
    teams = now.state.get("teams") or []
    ours = next((t for t in teams if t.get("id") in now.teams), None)
    theirs = next((t for t in teams if t is not ours), None)
    return (ours, theirs) if ours and theirs else None


def _series(who: str, now: Seen) -> Found:
    """The series result from the player's team's side: "TenZ: Sentinels win 2–1"."""
    sides = _sides(now)
    stage = now.state.get("stage")
    if sides is None:
        return Found("final", "key", f"{who}: {now.score or 'series over'}", stage or "")
    ours, theirs = sides
    if now.state.get("forfeit"):
        verb = "win by forfeit" if ours.get("winner") else "lose by forfeit" if theirs.get("winner") else "forfeit"
        title = f"{who}: {ours['name']} {verb}"
    else:
        verb = "win" if ours.get("winner") else "lose" if theirs.get("winner") else "draw"
        title = f"{who}: {ours['name']} {verb} {ours['score']}–{theirs['score']}"
    return Found("final", "key", title, " · ".join(x for x in (f"v {theirs['name']}", stage) if x))


def _maps(who: str, before: Seen, now: Seen) -> list[Found]:
    """Each map the player's team won or lost since the last poll."""
    sides = _sides(now)
    if sides is None or now.status == "final":
        return []  # the result says it
    ours, theirs = sides
    done = {m.get("n") for m in before.state.get("maps") or [] if m.get("winner")}
    found = []
    for m in now.state.get("maps") or []:
        if not m.get("winner") or m.get("n") in done:
            continue
        won = m["winner"] == ours["id"]
        title = f"{who}: {ours['name']} {'win' if won else 'lose'} map {m.get('n')}"
        found.append(Found("map", "minor", title, f"{ours['name']} {ours['score']}–{theirs['score']} {theirs['name']}", f"map{m.get('n')}"))
    return found


def _dota(who: str, athlete_id: str, before: Seen, now: Seen) -> list[Found]:
    """A Dota 2 player's numbers for each map whose figures arrived since the last poll (OpenDota has
    them a few minutes after the map ends): "Yatoro: 12/2/9 on Faceless Void"."""
    had = {m.get("n") for m in ((before.state.get("dota") or {}).get("maps") or []) if athlete_id in (m.get("players") or {})}
    found = []
    for m in (now.state.get("dota") or {}).get("maps") or []:
        p = (m.get("players") or {}).get(athlete_id)
        if not p or m.get("n") in had:
            continue
        title = f"{who}: {p['kills']}/{p['deaths']}/{p['assists']} on {p['hero']}"
        body = f"Map {m['n']} {'won' if p.get('won') else 'lost'} · {p['gpm']} GPM · {p['last_hits']} last hits"
        found.append(Found("kda", "minor", title, body, f"kda{m['n']}", 60 if p["kills"] >= 10 else 0))
    return found


def _lobby(seen: Seen) -> bool:
    return seen.state.get("kind") == "battle_royale"


def _standing(seen: Seen, teams: list[str] | None = None) -> dict[str, Any] | None:
    """The player's team's row in a battle royale lobby's standings (their team now, for an earlier poll:
    the poller's baseline doesn't carry it)."""
    teams = seen.teams if teams is None else teams
    return next((t for t in seen.state.get("standings") or [] if t.get("id") in teams), None)


def _place(n: int | None) -> str:
    if not n:
        return "?"
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _pts(value: Any) -> str:
    points = float(value or 0)
    return str(int(points)) if points == int(points) else f"{points:.1f}"


def _lobby_games(who: str, before: Seen, now: Seen) -> list[Found]:
    """Each game of a lobby the player's team finished since the last poll: a chicken dinner (Booyah)
    when they won it, their placement otherwise; and going top of the lobby."""
    ours = _standing(now)
    if ours is None or now.status == "final":
        return []  # the result says it
    found = []
    earlier = _standing(before, now.teams) or {}
    seen = {g.get("n") for g in earlier.get("games") or [] if g.get("place")}  # points can come a poll before the placing
    for g in ours.get("games") or []:
        if g.get("n") in seen or not g.get("place"):
            continue
        where = f"Match {g['n']} · {g.get('kills', 0)} finishes · {_place(ours.get('place'))} overall, {_pts(ours.get('points'))} pts"
        if g["place"] == 1:
            found.append(Found("dinner", "key", f"{now.state.get('win_word') or 'Win'}: {ours['name']}", f"{who} · {where}", f"g{g['n']}"))
        else:
            found.append(Found("game", "minor", f"{who}: {ours['name']} {_place(g['place'])} in match {g['n']}", where, f"g{g['n']}"))
    was = earlier.get("place")
    if ours.get("place") == 1 and was not in (None, 1):  # on the points, which may come before the placing
        played = len(ours.get("games") or [])
        found.append(Found("lead", "key", f"{who}: {ours['name']} go top", f"{_pts(ours.get('points'))} pts after {played} match{'es' if played != 1 else ''}", f"lead{played}"))
    return found


def _lobby_final(who: str, now: Seen) -> Found:
    """Where the player's team finished the lobby: "Jonathan: GodLike Esports finish 3rd · 87 pts"."""
    ours = _standing(now)
    teams = len(now.state.get("standings") or [])
    stage = now.state.get("stage")
    if ours is None:
        return Found("final", "key", f"{who}: {now.score or 'lobby over'}", stage or "")
    place = ours.get("place")
    verb = "win the lobby" if place == 1 else f"finish {_place(place)}"
    body = " · ".join(x for x in (f"{teams} teams" if teams else None, f"{ours.get('wins')} won" if ours.get("wins") else None, f"{ours.get('kills', 0)} finishes", stage) if x)
    return Found("final", "key", f"{who}: {ours['name']} {verb} · {_pts(ours.get('points'))} pts", body, priority=90 if place == 1 else 0)


def _upset(sport: str, who: str, athlete_id: str, now: Seen) -> list[Found]:
    """A tennis win over a better-seeded player (or any seed, for an unseeded one), in a match played
    to the end: a walkover or a retirement isn't an upset."""
    if sport != "tennis" or now.state.get("ending"):
        return []
    players = now.state.get("players") or []
    me = next((p for p in players if str(p.get("id")) == athlete_id), None)
    other = next((p for p in players if p is not me), None)
    if not (me and me.get("winner") and other and other.get("seed")) or (me.get("seed") and me["seed"] < other["seed"]):
        return []
    return [Found("upset", "key", f"Upset: {who} beats [{other['seed']}] {other.get('name')}", now.score)]


def _football(who: str, athlete_id: str, before: dict[str, Any] | None, now: Seen) -> list[Found]:
    found = []
    where = " · ".join(x for x in (now.score, now.clock) if x)
    for label, kind, level, title in (("Goals", "goal", "key", "Goal: {who}"), ("Assists", "assist", "key", "Assist: {who}"), ("Red", "red", "key", "Red card: {who}"), ("Yellow", "yellow", "minor", "Yellow card: {who}")):
        was, got = number(stat(before, label)), number(stat(now.line, label))
        for n in range(was + 1, got + 1):
            if kind == "goal":
                found.append(_goal(who, athlete_id, n, now, where))
            elif kind == "assist":
                found.append(Found(kind, level, title.format(who=who) + (f" ({n})" if n > 1 else ""), where, _play_key(kind, n, _play(now, athlete_id, n, at=1))))
            else:
                found.append(Found(kind, level, title.format(who=who), where, f"{kind}{n}"))
    if (before or {}).get("headline") == "On the bench" and (now.line or {}).get("headline") not in (None, "On the bench"):
        found.append(Found("on", "minor", f"{who} comes on", where))
    return found


def _play(now: Seen, athlete_id: str, n: int, at: int = 0) -> dict[str, Any] | None:
    """The nth goal the player scored (at=0) or set up (at=1) in the source's events, or None when the
    events don't have it. Their own goals are neither."""
    plays = [e for e in reversed(now.events) if e.get("kind") == "GOAL" and not e.get("own_goal") and (e.get("athlete_ids") or [])[at:at + 1] == [athlete_id]]
    return plays[n - 1] if len(plays) >= n else None


def _play_key(kind: str, n: int, play: dict[str, Any] | None) -> str:
    """A goal's or assist's key detail: the source's play (its id, else its minute), not the count. A
    goal VAR rules out takes the count down, and the next real one would have the same count again.
    The count ("goal2") only when the events don't have the play yet."""
    ref = play and (play.get("id") or play.get("clock"))
    return f"{kind}@{ref}" if ref else f"{kind}{n}"


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


def _cricket(who: str, before: dict[str, Any] | None, now: Seen) -> list[Found]:
    """Fifties, hundreds, out and wicket hauls in the player's latest innings: a line's stats are its
    latest batting and bowling innings, and its headline lists every innings ("103 (152) & 48* (60)").
    In a Test the second innings starts again from nothing, and its moments say which innings they
    are in the detail ("i2:100"), so they aren't taken for the first's ("100")."""
    found = []
    (bats_was, bowls_was), (bats_now, bowls_now) = (cricket_innings((line or {}).get("headline")) for line in (before, now.line))
    runs_was, runs_now = stat(before, "Runs"), stat(now.line, "Runs")
    if _another(bats_was, bats_now):
        runs_was = None  # the last poll's runs were the innings before
    if runs_now is not None:
        balls = stat(now.line, "Balls")
        innings = f"{runs_now} ({balls})" if balls else runs_now
        pre, nth = _nth(len(bats_now))
        for mark in crossed(number(runs_was), number(runs_now), (50, 100, 150, 200)):
            found.append(Found("hundred" if mark >= 100 else "fifty", "key", f"{who} reaches {mark}", _body(innings, nth, now.score), f"{pre}{mark}", 95 if mark >= 150 else 0))
        out_now = not runs_now.endswith("*") and stat(now.line, "Out") is not None
        out_was = runs_was is not None and not runs_was.endswith("*") and stat(before, "Out") is not None
        if out_now and not out_was:
            runs = number(runs_now)
            found.append(Found("out", "key", f"{who} out for {runs}", _body(innings, nth, stat(now.line, "Out")), f"{pre}out", 75 if runs >= 50 else 65 if runs == 0 else 60))
    wickets_was, wickets_now = number(stat(before, "Wickets")), number(stat(now.line, "Wickets"))
    if _another(bowls_was, bowls_now):
        wickets_was = 0
    pre, nth = _nth(len(bowls_now))
    for mark in crossed(wickets_was, wickets_now, (3, 4, 5, 6, 7, 8, 9, 10)):
        figures = f"{wickets_now}/{stat(now.line, 'Runs conceded') or '?'} ({stat(now.line, 'Overs') or '?'} ov)"
        found.append(Found("wickets", "key", f"{who} takes {mark} wickets", _body(figures, nth, now.score), f"{pre}w{mark}", 90 if mark >= 5 else 75 if mark == 4 else 70))
    return found


def _another(was: list[Any], now: list[Any]) -> bool:
    """Whether the player has started another innings since the last poll: the line has more innings,
    and the ones it had are unchanged. A last line that read otherwise (one holding only the latest
    innings) doesn't count, so milestones already passed aren't told late."""
    return len(now) > len(was) and now[:len(was)] == was


def _nth(innings: int) -> tuple[str, str]:
    """The player's innings in a cricket moment: the detail's prefix ("i2:"; none for their first, as
    before) and its name in the body ("2nd innings")."""
    if innings < 2:
        return "", ""
    return f"i{innings}:", f"{ORDINAL.get(innings, f'{innings}th')} innings"


def _body(*parts: str | None) -> str:
    return " · ".join(p for p in parts if p)


def _basketball(who: str, before: dict[str, Any] | None, now: Seen) -> list[Found]:
    found = []
    was, got = counts(before), counts(now.line)
    where = " · ".join(x for x in ((now.line or {}).get("headline"), now.score, now.clock) if x)
    for mark in crossed(was.get("PTS", 0), got.get("PTS", 0), (20, 30, 40, 50, 60)):
        found.append(Found("points", "key" if mark >= 30 else "minor", f"{who}: {mark} points", where, str(mark), {20: 30, 30: 65, 40: 85}.get(mark, 95)))
    tens_was = sum(1 for k in ("PTS", "REB", "AST", "STL", "BLK") if was.get(k, 0) >= 10)
    tens_now = sum(1 for k in ("PTS", "REB", "AST", "STL", "BLK") if got.get(k, 0) >= 10)
    if tens_was < 2 <= tens_now:
        found.append(Found("double", "minor", f"{who}: double-double", where))
    if tens_was < 3 <= tens_now:
        found.append(Found("triple", "key", f"{who}: triple-double", where))
    return found


def _tennis(who: str, before: dict[str, Any] | None, now: Seen) -> list[Found]:
    def sets(line: dict[str, Any] | None) -> tuple[int, int]:
        won, _, lost = (stat(line, "Sets") or "0–0").partition("–")
        return number(won), number(lost)

    (won_was, lost_was), (won_now, lost_now) = sets(before), sets(now.line)
    if now.status == "final":
        return []  # the result says it
    found, n = [], won_was + lost_was
    # Polls come every ten seconds, so this is almost always one set; if two finished in between,
    # which came first isn't known and wins are listed first.
    for won in [True] * max(0, won_now - won_was) + [False] * max(0, lost_now - lost_was):
        n += 1
        title = f"{who} {'wins' if won else 'loses'} the {ORDINAL.get(n, f'{n}th')} set"
        found.append(Found("set", "minor", title, f"Sets {won_now}–{lost_now} · {now.score}".strip(" ·"), f"set{n}"))
    return found
