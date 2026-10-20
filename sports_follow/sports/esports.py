"""Esports: each game a sport of its own ("Valorant", "CS2", "BGMI"), as fans follow them, and what the games
share, kept here once: series and lobby moments, form, alert wording, how big a game is, who's a pro on
Wikidata. Esports holds those rules; each game is one entry in GAMES (Game: its key, its name and the other
names it goes by, and its colour), so adding a game is one line there and the source that covers it. SPORT
is the esports group: what a setting naming "esports" stands for (every game), and the sport of a pro whose
game can't be told; it isn't one of the sports the app lists.

Two shapes of game: team series, map by map (Valorant, CS2), and battle royale lobbies (BGMI, Free Fire,
PUBG), which rank every team in one result: {"teams": 16, "standings": [{"id", "name", "place", "points", …}]}.
Sources report teams, not players, so moments are told from the player's team's side.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable

from .base import Fired, Found, Line, Notable, Played, Poster, Seen, Sport, joined, ordinal, stage, words

if TYPE_CHECKING:
    from ..adapters.base import Fixture
    from ..models import Athlete


# A league game's size (notable): its event's tier, as its source rates it (Fixture.tier: 3 a world event, 2
# a top league), and how late in the event it is; smaller events' games aren't big ones.
SERIES_RANK = {3: 60, 2: 40}
LOBBY_RANK = {3: 55, 2: 35}
STAGE_BONUS = {3: 30, 2: 15, 1: 5}  # a grand final of a world event is 90
TIERS = {3: "Tier S", 2: "Tier A"}
DISAMBIGUATED = re.compile(r"_?\(.*\)$")  # a Liquipedia page's "_(Korean_player)"
# A game as one key, however a source, a Liquipedia wiki or Wikidata writes it: its name run together
# ("Dota 2", "dota2"), or as here where the names differ.
GAME_KEYS = {
    "counterstrike": "cs2", "counterstrike2": "cs2", "counterstrikeglobaloffensive": "cs2", "csgo": "cs2",
    "rainbowsix": "rainbowsixsiege", "tomclancysrainbowsixsiege": "rainbowsixsiege", "r6siege": "rainbowsixsiege",
    "honorofkings": "kingofglory", "mobilelegendsbangbang": "mobilelegends", "leagueoflegendswildrift": "wildrift",
    "overwatch2": "overwatch", "playerunknownsbattlegrounds": "pubg", "pubgbattlegrounds": "pubg",
    "bgmi": "pubgmobile", "battlegroundsmobileindia": "pubgmobile", "freefiremax": "freefire",
}


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


def _game_maps(name: str, before: Played, now: Played) -> list[Found]:
    """Each map of a series taken since the last poll, from the side that took it, with the series score:
    for a fan following the series itself these are its key moments, as goals are a match's."""
    teams = {t.get("id"): t for t in now.state.get("teams") or []}
    done = {m.get("n") for m in before.state.get("maps") or [] if m.get("winner")}
    if len(teams) != 2 or now.status == "final":
        return []  # the result says it
    a, b = teams.values()
    found = []
    for m in now.state.get("maps") or []:
        winner = teams.get(m.get("winner"))
        if winner and m.get("n") not in done:
            found.append(Found("map", "key", f"{name}: {winner.get('name')} take map {m.get('n')}", f"{a.get('name')} {a.get('score')}–{b.get('score')} {b.get('name')}", f"map{m.get('n')}", 60))
    return found


def _game_lobby(name: str, before: Played, now: Played) -> list[Found]:
    """Each match of a lobby won since the last poll, by whom ("Booyah: GodLike Esports"), with who's top."""
    teams = {t.get("id"): t for t in now.state.get("standings") or []}
    done = {g.get("n") for g in before.state.get("games") or [] if g.get("winner")}
    top = next((t for t in teams.values() if t.get("place") == 1), None)
    if now.status == "final":
        return []
    found = []
    for g in now.state.get("games") or []:
        winner = teams.get(g.get("winner"))
        if winner and g.get("n") not in done:
            body = joined(f"Match {g['n']}", g.get("map"), f"{top['name']} top, {points(top.get('points'))} pts" if top and top.get("name") else None)
            found.append(Found("dinner", "key", f"{name}: {now.state.get('win_word') or 'Win'}, {winner.get('name')}", body, f"g{g['n']}", 70))
    return found


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
    """What every esports game shares, and the esports group itself (SPORT)."""

    key = "esports"
    label = "Esports"
    # Games no source covers, as an AI-built page may call them: esports, but none of its games' sports.
    names = ("e-sports", "electronic sports", "call of duty: mobile", "codm")
    kinds = ("esports", "battle_royale")
    roster = True
    team_moments = True
    side_by_side = True
    follow_teams = True  # a team title's teams, where its source lists them; not a battle royale's (Game: teams=False)
    colour = {"sport": "#ff4fa3", "on": "#1a0010", "on_soft": "rgba(26, 0, 16, 0.85)", "ink": "#ff4fa3"}
    # A game no source covers is esports; eFootball is a game too.
    pattern = r"e-?sports?\b|e-?football|call of duty:? ?mobile|\bcodm\b"
    alerts = {
        "key": ("Starts, wins and results", "A series or lobby starting, winning a lobby match, going top, the result and transfers"),
        "more": ("every map and match", "each map or lobby match, a Dota player’s numbers each map, and milestones"),
    }
    game_alerts = {
        "key": ("Maps and the result", "Each map of a series or match of a lobby won, the start and the result"),
        "more": ("", ""),
    }
    kind_words = {"map": "Map"}
    stats_title = "Overview"
    keeps_names = True

    def label_of(self, name: str) -> str:
        """A game's own name as written ("PUBG Mobile", "Counter-Strike"); its label for its key or label in any case."""
        return self.label if name.strip().lower() in (self.key, self.label.lower()) else name

    def result_sport(self, athlete: Athlete) -> str:
        """The game: the registry keeps each pro under theirs ("cs2" is "CS2")."""
        return self.label

    def search_detail(self, athlete: Athlete) -> list[str]:
        return [*list(athlete.teams or [])[:1], athlete.country or ""]  # the game is the result's sport

    # ---- the registry: a pro's Wikidata person, for their photo (roster_wikidata.py)

    # Pros on Wikidata: anyone with a Liquipedia id (P10918, "valorant/TenZ"), an HLTV one (P8878) or an
    # Esports Earnings one (P10803), or a professional gamer (Q4379701) by occupation.
    people_ids = ("P10918", "P8878", "P10803")
    people_occupations = ("Q4379701",)
    team_filler = frozenset({"team", "club", "esports", "esport", "e", "sports", "gaming", "gg"})  # "Gen.G Esports" is Gen.G

    def known_as(self, ids: dict[str, list[str]]) -> list[str]:
        """The handles in a pro's ids: their Liquipedia page ("valorant/TenZ", "dota2/Collapse_(Russian_player)")
        and their HLTV name ("11893/zywoo")."""
        pages = [DISAMBIGUATED.sub("", v.split("/", 1)[-1]).replace("_", " ") for v in ids.get("P10918", [])]
        return pages + [v.rsplit("/", 1)[-1] for v in ids.get("P8878", [])]

    def plays(self, ids: dict[str, list[str]], disciplines: list[str]) -> set[str]:
        """The games a pro on Wikidata plays: the wiki their Liquipedia page is on ("valorant/TenZ"),
        Counter-Strike for an HLTV id, and the disciplines they compete in ("League of Legends")."""
        games = [v.split("/", 1)[0] for v in ids.get("P10918", [])] + (["cs2"] if ids.get("P8878") else []) + list(disciplines)
        return {key for key in map(self.game_key, games) if key}

    def game_key(self, game: str | None) -> str:
        """"Counter-Strike 2", "CS2" and the counterstrike wiki are "cs2"; "Dota 2" is "dota2"."""
        key = re.sub(r"[^a-z0-9]", "", (game or "").lower())
        return GAME_KEYS.get(key, key)

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

    def game_during(self, name: str, before: Played, now: Played, told: list[tuple[str, str]]) -> list[Found]:
        """A series' maps, each from the side that took it, or a lobby's matches, each by who won it: about the
        whole game, as a fan of a player in it hears each from their team's side."""
        return _game_lobby(name, before, now) if now.state.get("kind") == "battle_royale" else _game_maps(name, before, now)

    def match_page(self, state: dict[str, Any], events: list[dict[str, Any]], lines: dict[str, Line], names: dict[str, tuple[str, str]]) -> dict[str, Any]:
        """Every game's, whichever title: a series map by map (who took each, the one being played, and in a
        Dota 2 map, its best player's numbers where OpenDota has them), or a lobby's whole standings and who
        won each of its matches."""
        if state.get("kind") == "battle_royale":
            teams = {t.get("id"): t.get("name") for t in state.get("standings") or []}
            return {
                "standings": [{k: t.get(k) for k in ("place", "name", "points", "kills", "wins")} for t in state.get("standings") or []],
                "games": [{"n": g.get("n"), "map": g.get("map"), "winner": teams.get(g.get("winner"))} for g in state.get("games") or []],
            }
        teams = {t.get("id"): t.get("name") for t in state.get("teams") or []}
        dota = {m.get("n"): m.get("players") or {} for m in (state.get("dota") or {}).get("maps") or []}
        maps = []
        for m in state.get("maps") or []:
            numbers = dota.get(m.get("n")) or {}
            best = max(numbers.items(), key=lambda p: (p[1].get("kills") or 0, -(p[1].get("deaths") or 0)), default=None)
            who = best and names.get(best[0], ("", ""))[0]
            maps.append({"n": m.get("n"), "status": m.get("status"), "winner": teams.get(m.get("winner")),
                         "best": f"{who} {best[1]['kills']}/{best[1]['deaths']}/{best[1]['assists']} on {best[1]['hero']}" if best and who else None})
        return {"maps": maps} if maps else {}

    def verdict(self, state: dict[str, Any], score: str, clock: str) -> str:
        """Who took the series, and by how many maps ("Sentinels won 2–1", "NRG won by forfeit"), or the
        lobby ("GodLike Esports won the lobby")."""
        if state.get("kind") == "battle_royale":
            top = [t for t in state.get("standings") or [] if t.get("place") == 1]
            return f"{top[0]['name']} won the lobby" if len(top) == 1 and top[0].get("name") else ""  # level on points: nobody's alone
        teams = state.get("teams") or []
        winner = next((t for t in teams if t.get("winner")), None)
        loser = next((t for t in teams if t is not winner), None)
        if winner is None or loser is None or not winner.get("name"):
            return ""
        return f"{winner['name']} won by forfeit" if state.get("forfeit") else f"{winner['name']} won {winner.get('score')}–{loser.get('score')}"

    def side_final(self, state: dict[str, Any], score: str, clock: str, team_id: str) -> str:
        """A series from one side's, as every sport's ("T1 won 3–1 v Gen.G"), or by forfeit ("NRG lost by forfeit
        v Sentinels"). A lobby has no two sides: its result is the game's own."""
        if state.get("forfeit"):
            teams = [t for t in state.get("teams") or [] if isinstance(t, dict)]
            ours = next((t for t in teams if str(t.get("id") or "") == team_id), None)
            theirs = next((t for t in teams if t is not ours), None)
            if ours is None or theirs is None or not (ours.get("name") and theirs.get("name")) or not (ours.get("winner") or theirs.get("winner")):
                return ""
            return f"{ours['name']} {'won' if ours.get('winner') else 'lost'} by forfeit v {theirs['name']}"
        return super().side_final(state, score, clock, team_id)

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
        lobby's winner."""
        r = fixture.result or {}
        if "standings" in r or (fixture.status != "final" and len(fixture.team_ids) > 2):
            rank = LOBBY_RANK.get(fixture.tier)
            if rank is None:
                return None
            table = r.get("standings") or []
            if fixture.status != "final" or not table:
                return Notable(rank, why=TIERS[fixture.tier])
            top = table[0]
            return Notable(rank, word=str(top.get("name") or ""), line=f"Won the lobby · {points(top.get('points'))} pts · {r.get('teams')} teams", why=TIERS[fixture.tier])
        if fixture.tier not in SERIES_RANK or r.get("forfeit"):
            return None
        level, said = stage(fixture.detail)
        rank = SERIES_RANK[fixture.tier] + STAGE_BONUS.get(level, 0)
        why = said or TIERS[fixture.tier]
        sides = r.get("teams") or []
        if fixture.status != "final" or len(sides) != 2:
            return Notable(rank, why=why)
        scores = sorted((int(t.get("score") or 0) for t in sides), reverse=True)
        best_of = re.search(r"\bBo(\d+)\b", fixture.detail or "")
        distance = scores[1] > 0 and (scores[0] - scores[1] == 1) and (not best_of or scores[0] + scores[1] == int(best_of[1]))
        winner = next((t for t in sides if t.get("winner")), None)
        loser = next((t for t in sides if t is not winner), None) if winner else None
        # The winner's score first, as the line under it says it: "3–2", "EDward Gaming beat AG Super Play".
        first, second = (winner, loser) if winner and loser else sides
        line = f"{winner['name']} beat {loser['name']}" if winner and loser else ""
        return Notable(min(100, rank + (8 if distance else 0)), word=f"{first.get('score')}–{second.get('score')}", line=line,
                       why=why if said or not distance else "Decider")

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

    # ---- the Feed's content judge (curate.py)

    def content_hints(self) -> str:
        return """\
A milestone: a big tournament win (a Major, Worlds, Masters, a world championship), an MVP, a record. A match \
moment: a clutch, an ace or pentakill, a team wipe, a chicken dinner. Roster moves a team announces are news; \
"leaks" and "sources say" before that are rumours. Skin betting, case-opening and gambling sites are betting; a \
sponsor's peripheral or energy drink held up in a clip is a promo. Full VOD re-uploads of a broadcast are \
restreams; a player's own stream highlights aren't."""

    # ---- the feed (curator.py): channels trusted for videos about its pros, every game's (each feed answered,
    # 20 Oct 2026)

    video_channels = (
        "UCfeeUuW7edMxF3M_cyxGT8Q",  # Thooorin: CS2 and League interviews, history
        "UCXbsUubmNPK8XJFbkmlMcMg",  # HLTV: CS2 interviews, news
        "UC9k--dE_UE0Faxzgb_DDkYQ",  # BLAST Premier: CS2 interviews, features
        "UCvqRdlKsE5Q8mf8YXbdIJLw",  # LoL Esports: League highlights, interviews
        "UCnMM5HJs5y6qCKqnefg8Oig",  # Upthrust Esports: BGMI scrims, players
    )

    def search_words(self, fired: Fired) -> str | None:
        """A series won from the semi-finals on, or a lobby won ("win the lobby"), at the event: league play's wins
        are too many for a day's searches."""
        if fired.kind != "final" or " win" not in fired.title:
            return None
        late, word = stage(fired.body, fired.game)
        if "win the lobby" in fired.title or late >= 2:
            return words("wins", word.lower() if late else "", fired.competition)
        return None


class Game(Esports):
    """One esports game, a sport of its own: its key (the registry's, the settings' and the search filter's),
    its name and the other names a source or a page gives it, the free-text names that are it (pattern, for
    the apps), and its colour. Everything else is esports'."""

    group = Esports.key
    kinds = ()  # its scoreboards are esports' (SPORT's kinds)

    def __init__(self, key: str, label: str, colour: dict[str, str], names: tuple[str, ...] = (), pattern: str = "", teams: bool = True) -> None:
        self.key, self.label, self.colour, self.names = key, label, colour, names
        # Fans can follow its teams (teams.py): a team title's. A battle royale's teams are a lobby's, a dozen or more
        # a game, whose results are standings, not one side's against another's.
        self.follow_teams = teams
        self.pattern = pattern or re.escape(label.lower()).replace("\\ ", " ")

    def __repr__(self) -> str:
        return f"Game({self.key!r})"


def _light(block: str, ink: str) -> dict[str, str]:
    """A light block with dark text on it, as basketball's."""
    r, g, b = (int(ink[i : i + 2], 16) for i in (1, 3, 5))
    return {"sport": block, "on": ink, "on_soft": f"rgba({r}, {g}, {b}, 0.85)", "ink": block}


def _dark(block: str, ink: str) -> dict[str, str]:
    """A dark block with white text on it, and its colour lighter as text on ink, as cricket's."""
    return {"sport": block, "on": "#ffffff", "on_soft": "#ffffff", "ink": ink}


# Every game a source covers, in the order the app lists them, after the other sports. Each colour reads
# with its text at 4.5:1 or better and stands apart from every other sport's (tests/test_sports.py).
GAMES: tuple[Game, ...] = (
    Game("valorant", "Valorant", _light("#ff6b6b", "#1f0005")),
    Game("cs2", "CS2", _dark("#334155", "#a8b8cc"), ("Counter-Strike 2", "Counter-Strike", "CS:GO", "CSGO"), r"\bcs2\b|counter-?strike|\bcs:? ?go\b"),
    Game("lol", "League of Legends", _light("#19c3e6", "#00161c"), ("LoL",), r"league of legends|\blol\b"),
    Game("dota2", "Dota 2", _dark("#a3213a", "#ff8fa3"), ("Dota", "Dota2"), r"\bdota ?2?\b"),
    Game("bgmi", "BGMI", _dark("#166534", "#4ade80"), ("Battlegrounds Mobile India",), r"\bbgmi\b|battlegrounds mobile india", teams=False),
    Game("freefire", "Free Fire", _light("#e879f9", "#1f0024"), ("Free Fire MAX", "Garena Free Fire"), r"free ?fire", teams=False),
    Game("pubgmobile", "PUBG Mobile", _light("#60a5fa", "#001a33"), ("PUBGM",), r"pubg ?mobile|\bpubgm\b", teams=False),
    Game("pubg", "PUBG", _light("#ffc61a", "#1a1400"), ("PUBG: Battlegrounds", "PlayerUnknown's Battlegrounds"), r"\bpubg\b|playerunknown|battlegrounds", teams=False),
    Game("mlbb", "Mobile Legends", _dark("#0f766e", "#2dd4bf"), ("Mobile Legends: Bang Bang", "MLBB"), r"mobile legends|\bmlbb\b"),
    Game("hok", "Honor of Kings", _dark("#8b5a2b", "#e0a96d"), ("King of Glory", "Honour of Kings"), r"hono(?:u)?r of kings|king of glory"),
    Game("r6siege", "Rainbow Six Siege", _dark("#6b2d5c", "#e79ad3"), ("Rainbow Six", "R6 Siege"), r"rainbow six|\br6\b"),
    Game("overwatch", "Overwatch", _light("#ffb37a", "#1f0d00"), ("Overwatch 2",), r"overwatch"),
    Game("apex", "Apex Legends", _dark("#7c2d12", "#fb9a6c"), ("Apex",), r"apex legends|\bapex\b", teams=False),
    Game("rocketleague", "Rocket League", _light("#5eead4", "#00201b")),
    Game("cod", "Call of Duty", _dark("#6b5d4f", "#d2c4b4"), ("CoD",), r"call of duty(?!:? ?mobile)|\bcod\b"),
    Game("wildrift", "Wild Rift", _light("#c4b5fd", "#1a0f3d"), ("League of Legends: Wild Rift",), r"(?:league of legends:? )?wild ?rift"),
)
SPORT = Esports()
