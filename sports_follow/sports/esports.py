"""Esports, by game. A pro's sport on their page is the game they play, as fans name it ("Valorant", "CS2",
"BGMI"), not "Esports"; esports is what those games share: one search filter, one set of moment and form
rules, one colour in the app. The registry keeps "esports" as the athlete's sport.

Two shapes of game: team series, map by map (Valorant, CS2), and battle royale lobbies (BGMI, Free Fire,
PUBG), which rank every team in one result: {"teams": 16, "standings": [{"id", "name", "place", "points", …}]}.
Sources report teams, not players, so moments are told from the player's team's side.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable

from .base import Found, Line, Notable, Poster, Seen, Sport, joined, ordinal, stage

if TYPE_CHECKING:
    from ..adapters.base import Fixture
    from ..models import Athlete


# A league game's size (notable): its event's tier, as its source rates it (Fixture.tier: 3 a world event, 2
# a top league), and how late in the event it is; smaller events' games aren't big ones.
SERIES_RANK = {3: 60, 2: 40}
LOBBY_RANK = {3: 55, 2: 35}
STAGE_BONUS = {3: 30, 2: 15, 1: 5}  # a grand final of a world event is 90
TIERS = {3: "Tier S", 2: "Tier A"}


def points(value: Any) -> str:
    value = float(value or 0)
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def lobby_result(fixture: Fixture, team_ids: list[str], key: Callable[[str], str] = str) -> str:
    """ "3rd of 16 · 87 pts" from the player's team's side; key is how the source compares team ids."""
    r = fixture.result or {}
    mine = {key(t) for t in team_ids}
    ours = next((s for s in r.get("standings") or [] if key(s["id"]) in mine), None)
    if ours is None:
        return ""
    return f"{ordinal(ours['place'])} of {r.get('teams')} · {points(ours['points'])} pts"


# ---------------------------------------------------------------- moments


def _lobby(seen: Seen) -> bool:
    return seen.state.get("kind") == "battle_royale"


def _sides(now: Seen) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """A series' two teams, the player's first, or None when their team isn't one of them."""
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


def _standing(seen: Seen, teams: list[str] | None = None) -> dict[str, Any] | None:
    """The player's team's row in a lobby's standings (their team now, for an earlier poll: the poller's
    baseline doesn't carry it)."""
    teams = seen.teams if teams is None else teams
    return next((t for t in seen.state.get("standings") or [] if t.get("id") in teams), None)


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
        where = f"Match {g['n']} · {g.get('kills', 0)} finishes · {ordinal(ours.get('place'))} overall, {points(ours.get('points'))} pts"
        if g["place"] == 1:
            found.append(Found("dinner", "key", f"{now.state.get('win_word') or 'Win'}: {ours['name']}", f"{who} · {where}", f"g{g['n']}"))
        else:
            found.append(Found("game", "minor", f"{who}: {ours['name']} {ordinal(g['place'])} in match {g['n']}", where, f"g{g['n']}"))
    was = earlier.get("place")
    if ours.get("place") == 1 and was not in (None, 1):  # on the points, which may come before the placing
        played = len(ours.get("games") or [])
        found.append(Found("lead", "key", f"{who}: {ours['name']} go top", f"{points(ours.get('points'))} pts after {played} match{'es' if played != 1 else ''}", f"lead{played}"))
    return found


def _lobby_final(who: str, now: Seen) -> Found:
    """Where the player's team finished the lobby: "Jonathan: GodLike Esports finish 3rd · 87 pts"."""
    ours = _standing(now)
    teams = len(now.state.get("standings") or [])
    stage = now.state.get("stage")
    if ours is None:
        return Found("final", "key", f"{who}: {now.score or 'lobby over'}", stage or "")
    place = ours.get("place")
    verb = "win the lobby" if place == 1 else f"finish {ordinal(place)}"
    body = " · ".join(x for x in (f"{teams} teams" if teams else None, f"{ours.get('wins')} won" if ours.get("wins") else None, f"{ours.get('kills', 0)} finishes", stage) if x)
    return Found("final", "key", f"{who}: {ours['name']} {verb} · {points(ours.get('points'))} pts", body, priority=90 if place == 1 else 0)


def _series_won(won: int, lost: int) -> str:
    """A series won as a poster's word: "Sweep 2–0" when the other side took no map (of more than one), else "Win 2–1"."""
    return f"{'Sweep' if lost == 0 and won > 1 else 'Win'} {won}–{lost}"


# ---------------------------------------------------------------- form


def _placed(result: str | None) -> tuple[int, int, float] | None:
    """ "3rd of 16 · 87 pts" (a battle royale lobby) -> (3, 16, 87.0)."""
    match = re.match(r"(\d+)(?:st|nd|rd|th) of (\d+) · ([\d.]+) pts", result or "")
    return (int(match.group(1)), int(match.group(2)), float(match.group(3))) if match else None


# ---------------------------------------------------------------- the sport


class Esports(Sport):
    key = "esports"
    label = "Esports"
    names = ("e-sports", "electronic sports")
    # Its sources add the games they cover (adapters' game_names).
    other_games = ("counter-strike 2", "lol", "dota", "mlbb", "honor of kings", "apex", "call of duty: mobile", "codm")
    kinds = ("esports", "battle_royale")
    roster = True
    team_moments = True
    side_by_side = True
    colour = {"sport": "#ff4fa3", "on": "#1a0010", "on_soft": "rgba(26, 0, 16, 0.8)", "ink": "#ff4fa3"}
    # Every game wears the esports colour; eFootball is a game too.
    pattern = (r"e-?sports?\b|e-?football|valorant|counter-strike|\bcs2\b|\bcs:go\b|league of legends|\blol\b|dota|mobile legends|\bmlbb\b"
               r"|overwatch|rainbow six|rocket league|call of duty|\bcodm\b|wild rift|king of glory|honor of kings|bgmi|pubg|free fire|apex legends")
    alerts = {
        "key": ("Starts, wins and results", "A series or lobby starting, winning a lobby match, going top, the result and transfers"),
        "more": ("every map and match", "each map or lobby match, a Dota player’s numbers each map, and milestones"),
    }
    stats_title = "Overview"
    keeps_names = True

    def label_of(self, name: str) -> str:
        """A game's own name as written ("CS2", "PUBG Mobile", "League of Legends")."""
        return name if name.strip().lower() != self.key else name.title()

    def result_sport(self, athlete: Athlete) -> str:
        """The game, from the league the athlete's source keeps: "valorant" -> "Valorant"; an Indian player
        on the PUBG Mobile wiki plays BGMI."""
        return game_of_league(athlete.league, athlete.country) or athlete.sport.title()

    def search_detail(self, athlete: Athlete) -> list[str]:
        return [*list(athlete.teams or [])[:1], athlete.country or ""]  # the game is the result's sport

    def starts_unlisted(self, now: Seen) -> bool:
        # A series starts for the whole team (a Dota player OpenDota can't name has no line), and so
        # does a lobby nobody listed players for.
        return not _lobby(now) or not now.lineups

    def left_out(self, now: Seen) -> bool:
        return _lobby(now) and now.lineups and now.line is None

    def start(self, who: str, now: Seen) -> Found:
        if _lobby(now):
            played = now.state.get("total")
            return Found("start", "key", f"{who} is playing", " · ".join(x for x in (now.state.get("stage"), f"{played} matches" if played else None, now.state.get("game")) if x))
        return Found("start", "key", f"{who}'s series has started", " · ".join(x for x in (self.matchup(now.state), now.state.get("format"), now.state.get("stage")) if x))

    def final(self, who: str, now: Seen) -> Found:
        return _lobby_final(who, now) if _lobby(now) else _series(who, now)

    def during(self, who: str, athlete_id: str, before: Seen, now: Seen) -> list[Found]:
        return _lobby_games(who, before, now) if _lobby(now) else _maps(who, before, now) + _dota(who, athlete_id, before, now)

    def high(self, f: Found, versus: str) -> Poster | None:
        """The player's team winning: a series ("Sweep 2–0" when the other side took no map, "Win 2–1"), a
        lobby, or one of its games (the game's own word: "Booyah", "Chicken dinner")."""
        if m := re.search(r" win (\d+)–(\d+)$", f.title):
            return Poster(_series_won(int(m[1]), int(m[2])), f.body.split(" · ", 1)[0])  # "v GenOne"
        if m := re.search(r": (.+) win the lobby · ([\d.]+ pts)$", f.title):
            return Poster("Lobby won", joined(m[1], m[2]))
        if f.kind == "dinner":
            word, _, team = f.title.partition(": ")
            game = re.search(r"Match \d+", f.body)
            return Poster(word, joined(team, game[0].lower() if game else None))
        return None

    def finished_poster(self, line: Line, result: str, versus: str) -> Poster | None:
        """The player's team winning, off a finished series' or lobby's result from their side, as high()
        words its moment: "Won 2–1" is a "Win 2–1" v the other side, "Won 2–0" a "Sweep 2–0"; "1st of 16 ·
        87 pts" is "Lobby won" (the result doesn't name the team: "87 pts · 16 teams"). Not a forfeit."""
        if m := re.fullmatch(r"Won (\d+)–(\d+)", result):
            return Poster(_series_won(int(m[1]), int(m[2])), versus, priority=80)  # a result's (moments.PRIORITY)
        if (placed := _placed(result)) and placed[0] == 1:
            return Poster("Lobby won", f"{points(placed[2])} pts · {placed[1]} teams", priority=90)  # as _lobby_final's
        return None

    def notable(self, fixture: Fixture) -> Notable | None:
        """A series or lobby of a big event (Fixture.tier S or A), as big as its tier and, for a series, how
        late in the event it is (its stage, as its detail says: "Grand final · Bo5"), 8 more once it's over
        if it went the distance. A forfeit isn't one. Its word is the series score, the winner's first, or a
        lobby's winner. Its group is its game, as its source names it (facts["game"]: "Valorant"), so each
        game takes its turn among the sport's and one game's season never fills Today."""
        r = fixture.result or {}
        group = str(fixture.facts.get("game") or "")
        if "standings" in r or (fixture.status != "final" and len(fixture.team_ids) > 2):
            rank = LOBBY_RANK.get(fixture.tier)
            if rank is None:
                return None
            table = r.get("standings") or []
            if fixture.status != "final" or not table:
                return Notable(rank, why=TIERS[fixture.tier], group=group)
            top = table[0]
            return Notable(rank, word=str(top.get("name") or ""), line=f"Won the lobby · {points(top.get('points'))} pts · {r.get('teams')} teams", why=TIERS[fixture.tier], group=group)
        if fixture.tier not in SERIES_RANK or r.get("forfeit"):
            return None
        level, said = stage(fixture.detail)
        rank = SERIES_RANK[fixture.tier] + STAGE_BONUS.get(level, 0)
        why = said or TIERS[fixture.tier]
        sides = r.get("teams") or []
        if fixture.status != "final" or len(sides) != 2:
            return Notable(rank, why=why, group=group)
        scores = sorted((int(t.get("score") or 0) for t in sides), reverse=True)
        best_of = re.search(r"\bBo(\d+)\b", fixture.detail or "")
        distance = scores[1] > 0 and (scores[0] - scores[1] == 1) and (not best_of or scores[0] + scores[1] == int(best_of[1]))
        winner = next((t for t in sides if t.get("winner")), None)
        loser = next((t for t in sides if t is not winner), None) if winner else None
        # The winner's score first, as the line under it says it: "3–2", "EDward Gaming beat AG Super Play".
        first, second = (winner, loser) if winner and loser else sides
        line = f"{winner['name']} beat {loser['name']}" if winner and loser else ""
        return Notable(min(100, rank + (8 if distance else 0)), word=f"{first.get('score')}–{second.get('score')}", line=line,
                       why=why if said or not distance else "Decider", group=group)

    def form(self, items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
        """Battle royale: placings and points from the lobby results ("3rd of 16 · 87 pts"). Team series:
        maps won and lost, from the results ("Won 2–1"); a forfeit has no maps."""
        placed = [p for p in (_placed(r.get("result")) for r in items) if p]
        if placed:
            n = len(placed)
            return [
                {"label": "Avg place", "value": f"{sum(p for p, _, _ in placed) / n:.1f}"},
                {"label": "Top 3", "value": str(sum(1 for p, _, _ in placed if p <= 3))},
                {"label": "Avg points", "value": f"{sum(pts for _, _, pts in placed) / n:.0f}"},
            ]
        won = lost = 0
        for r in items:
            match = re.match(r"(?:Won|Lost|Drew) (\d+)–(\d+)$", r.get("result") or "")
            if match:
                won, lost = won + int(match.group(1)), lost + int(match.group(2))
        return [{"label": "Maps", "value": f"{won}–{lost}"}] if won or lost else []

    def form_games(self, items: list[dict[str, Any]]) -> tuple[str, str]:
        return ("lobby", "lobbies") if any(_placed(r.get("result")) for r in items) else ("series", "series")


def game_of_league(league: str | None, country: str | None = None) -> str | None:
    """The game a registry esports athlete plays, from the league their source keeps (a PandaScore slug or
    a Liquipedia wiki), asking each esports source (adapters' game_of_league)."""
    from ..adapters import KNOWN  # here: sources import this module for what esports sources share

    for adapter in KNOWN.values():
        game_of = getattr(adapter, "game_of_league", None)
        if adapter.sport == SPORT.key and game_of and (game := game_of(league, country)):
            return game
    return None


SPORT = Esports()
