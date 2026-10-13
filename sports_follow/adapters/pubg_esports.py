"""PUBG: Battlegrounds esports from PUBG's official API: a pro's lobbies, standings, and their own numbers.

A tournament lists its matches (games) as each one ends; there is no schedule, and no team or
tournament names. So:

- A lobby is one session of a tournament's matches (games less than LOBBY_GAP apart) played by the same
  teams: a tournament day, or one group of it. It's one event, with the same battle royale state as
  Liquipedia's lobbies (standings, each game's winner), so the app's board and alerts work unchanged.
- A team is the tag its players carry ("TWIS" for TWIS_Lu, TWIS_xmpl…). A player is their account
  ("account.…"), named without the tag.
- Standings use the standard PUBG esports points: placement (POINTS) plus one a finish. Some events
  change them; the Credits page says which are used.
- Every player has their own line: finishes, damage, assists and knocks over the lobby, beside their
  team's standing.

A lobby appears once its first match has ended, and is read again every couple of minutes while it's on.
The worker keeps a shared index of recent matches up to date (warm), so finding a player's lobbies
reads Redis, not PUBG.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from . import espn, pubg
from .base import Fixture, Line, PlayerRef, Snapshot
from .liquipedia_esports import ordinal, result_label as _result_label

GAME = "PUBG"
MAPS = {
    "Baltic_Main": "Erangel", "Erangel_Main": "Erangel", "Desert_Main": "Miramar", "Savage_Main": "Sanhok", "DihorOtok_Main": "Vikendi",
    "Tiger_Main": "Taego", "Kiki_Main": "Deston", "Neon_Main": "Rondo", "Chimera_Main": "Paramo", "Summerland_Main": "Karakin", "Heaven_Main": "Haven",
}
POINTS = {1: 10, 2: 6, 3: 5, 4: 4, 5: 3, 6: 2, 7: 1, 8: 1}  # placement; each finish adds one
# Tournament ids are "<region>-<code>" ("eu-pecff2", "as-pgsc4t"): the circuit's name when the code says it.
LEAGUES = {
    "pgc": "PUBG Global Championship", "pgs": "PUBG Global Series", "pnc": "PUBG Nations Cup", "pcs": "PUBG Continental Series",
    "pec": "PUBG Europe Championship", "pas": "PUBG Americas Series", "pws": "PUBG Weekly Series",
}
REGIONS = {
    "eu": "Europe", "as": "Asia", "kr": "Korea", "sea": "Southeast Asia", "am": "Americas", "na": "North America", "cn": "China",
    "tw": "Taiwan", "sa": "South America", "jp": "Japan", "ap": "Asia Pacific", "oc": "Oceania", "th": "Thailand", "hk": "Hong Kong",
}
RECENT = timedelta(days=14)  # PUBG keeps matches this long
# The tournament shard also holds matches that aren't competition: exhibitions and tests a few minutes
# long, modes where players reach 40 finishes. Seen on 13 Oct 2026, real lobby matches ran 25 to 30
# minutes with 16 teams and at most 12 finishes for anyone; these bounds keep those and drop the rest.
MIN_MINUTES, MIN_TEAMS, MAX_FINISHES = 22, 8, 25
LOBBY_GAP = timedelta(hours=4)  # a longer break between matches starts a new lobby
OVER_AFTER = timedelta(minutes=90)  # no new match this long after the last one ended: the lobby is over


def _time(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None


def team_tag(names: list[str]) -> str:
    """A roster's tag: the prefix its players share ("TWIS" from TWIS_Lu, TWIS_xmpl)."""
    prefixes = Counter(n.split("_", 1)[0] for n in names if "_" in n)
    return prefixes.most_common(1)[0][0] if prefixes else (names[0] if names else "?")


def display_name(name: str, tag: str) -> str:
    """ "TWIS_Lu" -> "Lu"."""
    return name[len(tag) + 1:] if name.startswith(f"{tag}_") and len(name) > len(tag) + 1 else name


def event_name(tournament: str) -> str:
    """ "as-pgsc4t" -> "PUBG Global Series"; a code nobody decodes -> the region: "PUBG Southeast Asia"."""
    region, _, code = tournament.replace("_", "-").partition("-")
    league = next((name for key, name in LEAGUES.items() if key in code.lower()), None)
    return league or f"PUBG {REGIONS.get(region.lower(), region.upper())}"


# ---------------------------------------------------------------- parsers (pure)


def summarize_match(doc: dict[str, Any]) -> dict[str, Any]:
    """A match document (JSON:API) -> what's kept of it: when, where, and each roster's placement and
    players' numbers."""
    data = doc.get("data") or {}
    attrs = data.get("attributes") or {}
    included = doc.get("included") or []
    people = {x["id"]: (x.get("attributes") or {}).get("stats") or {} for x in included if x.get("type") == "participant"}
    rosters = []
    for r in (x for x in included if x.get("type") == "roster"):
        players = [people[p["id"]] for p in ((r.get("relationships") or {}).get("participants") or {}).get("data") or [] if p.get("id") in people]
        tag = team_tag([p.get("name") or "" for p in players])
        rosters.append({
            "rank": int(((r.get("attributes") or {}).get("stats") or {}).get("rank") or 0),
            "team": tag,
            "players": [{
                "id": p.get("playerId"), "name": p.get("name"), "kills": int(p.get("kills") or 0), "damage": round(float(p.get("damageDealt") or 0)),
                "assists": int(p.get("assists") or 0), "knocks": int(p.get("DBNOs") or 0),
            } for p in players],
        })
    return {
        "id": data.get("id"), "at": attrs.get("createdAt"), "duration": int(attrs.get("duration") or 0),
        "map": MAPS.get(attrs.get("mapName") or "", (attrs.get("mapName") or "").replace("_Main", "")),
        "rosters": sorted(rosters, key=lambda r: r["rank"] or 99),
    }


def competitive(m: dict[str, Any]) -> bool:
    """A real lobby match: long enough, enough teams, and nobody's finishes beyond what competition sees."""
    top = max((p["kills"] for r in m.get("rosters") or [] for p in r["players"]), default=0)
    return (m.get("duration") or 0) >= MIN_MINUTES * 60 and len(m.get("rosters") or []) >= MIN_TEAMS and top <= MAX_FINISHES


def group_lobbies(tournament: str, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A tournament's matches -> its lobbies: sessions (matches less than LOBBY_GAP apart), each split
    into groups of matches the same teams played. Numbered per day, in the order they began. Matches
    that aren't competition (competitive) are left out."""
    played = sorted((m for m in matches if m and m.get("at") and m.get("rosters") and competitive(m)), key=lambda m: m["at"])
    sessions: list[list[dict[str, Any]]] = []
    for m in played:
        if sessions and _time(m["at"]) - _time(sessions[-1][-1]["at"]) < LOBBY_GAP:
            sessions[-1].append(m)
        else:
            sessions.append([m])
    lobbies: list[dict[str, Any]] = []
    per_day: Counter[str] = Counter()
    for day_number, session in enumerate(sessions, start=1):
        groups: list[tuple[set[str], list[dict[str, Any]]]] = []
        for m in session:
            teams = {r["team"] for r in m["rosters"]}
            home = next((g for g in groups if len(teams & g[0]) * 2 >= min(len(teams), len(g[0]))), None)
            if home:
                home[0].update(teams)
                home[1].append(m)
            else:
                groups.append((set(teams), [m]))
        day = session[0]["at"][:10]
        for i, (_, group) in enumerate(groups):
            per_day[day] += 1
            lobbies.append({"tournament": tournament, "day": day, "n": per_day[day], "day_number": day_number, "group": chr(65 + i) if len(groups) > 1 else None, "matches": group})
    return lobbies


def lobby_key(lobby: dict[str, Any]) -> str:
    return f"{lobby['tournament']}:{lobby['day']}:{lobby['n']}"


def _status(lobby: dict[str, Any], now: datetime) -> str:
    last = lobby["matches"][-1]
    ended = _time(last["at"]) + timedelta(seconds=last.get("duration") or 0)
    return "final" if now - ended > OVER_AFTER else "live"


def standings(lobby: dict[str, Any]) -> list[dict[str, Any]]:
    """Every team in the lobby: points (placement plus finishes), finishes, games won and each game's
    result, best first (ties on points go to games won, then finishes)."""
    rows: dict[str, dict[str, Any]] = {}
    for n, m in enumerate(lobby["matches"], start=1):
        for r in m["rosters"]:
            kills = sum(p["kills"] for p in r["players"])
            points = POINTS.get(r["rank"], 0) + kills
            row = rows.setdefault(r["team"], {"id": r["team"], "name": r["team"], "points": 0.0, "kills": 0, "wins": 0, "games": []})
            row["points"] += points
            row["kills"] += kills
            row["wins"] += r["rank"] == 1
            row["games"].append({"n": n, "place": r["rank"], "kills": kills, "points": float(points)})
    table = sorted(rows.values(), key=lambda r: (-r["points"], -r["wins"], -r["kills"], r["name"]))
    for place, row in enumerate(table, start=1):
        row["place"] = place
    return table


def _stage(lobby: dict[str, Any]) -> str:
    return " · ".join(x for x in (f"Day {lobby['day_number']}", f"Group {lobby['group']}" if lobby.get("group") else None) if x)


def parse_fixture(lobby: dict[str, Any], now: datetime | None = None) -> Fixture:
    now = now or datetime.now(timezone.utc)
    status = _status(lobby, now)
    table = standings(lobby)
    name = event_name(lobby["tournament"])
    return Fixture(
        source_id=lobby_key(lobby),
        title=f"{name} · {_stage(lobby)}",
        competition=name,
        start_utc=_time(lobby["matches"][0]["at"]),
        venue=None,
        status=status,
        team_ids=[r["id"] for r in table],
        team_names=[r["name"] for r in table],
        locator={"tournament": lobby["tournament"], "day": lobby["day"], "n": lobby["n"]},
        detail=None,
        result={"teams": len(table), "standings": [{k: r[k] for k in ("id", "name", "place", "points", "kills", "wins")} for r in table]} if status == "final" else None,
    )


def player_lines(lobby: dict[str, Any], table: list[dict[str, Any]]) -> dict[str, Line]:
    """Each player's own numbers over the lobby, beside their team's standing, keyed by account."""
    totals: dict[str, dict[str, Any]] = {}
    for m in lobby["matches"]:
        for r in m["rosters"]:
            for p in r["players"]:
                t = totals.setdefault(p["id"], {"team": r["team"], "kills": 0, "damage": 0, "assists": 0, "knocks": 0, "games": 0})
                for k in ("kills", "damage", "assists", "knocks"):
                    t[k] += p[k]
                t["games"] += 1
    by_team = {r["id"]: r for r in table}
    lines = {}
    for account, t in totals.items():
        team = by_team.get(t["team"]) or {}
        lines[account] = Line(
            headline=f"{t['kills']} finish{'es' if t['kills'] != 1 else ''} · {t['damage']:,} dmg",
            stats=[
                {"label": "Team place", "value": ordinal(team.get("place"))}, {"label": "Team points", "value": str(int(team.get("points") or 0))},
                {"label": "Assists", "value": str(t["assists"])}, {"label": "Knocks", "value": str(t["knocks"])},
                {"label": "Finishes", "value": str(t["kills"])}, {"label": "Damage", "value": f"{t['damage']:,}"}, {"label": "Games", "value": str(t["games"])},
            ],
        )
    return lines


def parse_lobby(lobby: dict[str, Any], now: datetime | None = None) -> Snapshot:
    now = now or datetime.now(timezone.utc)
    status = _status(lobby, now)
    table = standings(lobby)
    games = [{"n": n, "map": m.get("map"), "status": "finished", "winner": next((r["team"] for r in m["rosters"] if r["rank"] == 1), None)} for n, m in enumerate(lobby["matches"], start=1)]
    leader = table[0] if table else None
    played = len(games)
    if status == "final" and leader:
        score, clock = f"{leader['name']} win · {int(leader['points'])} pts", "Final standings"
    elif leader:
        score, clock = f"{leader['name']} lead · {int(leader['points'])} pts", f"After match {played}"
    else:
        score, clock = "", ""
    state = {"kind": "battle_royale", "game": GAME, "win_word": "Chicken dinner", "stage": _stage(lobby), "standings": table, "games": games, "total": played}
    return Snapshot(status=status, score_label=score, clock_label=clock, state=state, lines=player_lines(lobby, table))


def players_of(lobbies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Everyone in these lobbies, by account, as last seen: name without the tag, in-game name, team tag."""
    seen: dict[str, dict[str, Any]] = {}
    for lobby in sorted(lobbies, key=lambda lb: lb["matches"][-1]["at"]):
        for m in lobby["matches"]:
            for r in m["rosters"]:
                for p in r["players"]:
                    if p.get("id"):
                        seen[p["id"]] = {"name": display_name(p.get("name") or "", r["team"]), "in_game": p.get("name"), "team": r["team"], "at": m["at"]}
    return seen


def ref_of(account: str, info: dict[str, Any]) -> PlayerRef:
    return PlayerRef(system="pubg_esports", athlete_id=account, name=info["name"], team_ids=[info["team"]], team_names=[info["team"]], league="pubg", game=GAME)


def player_form(lobbies: list[dict[str, Any]], account: str) -> tuple[list[dict[str, str]], str]:
    """A player's numbers over these lobbies (finished ones): their own, and their team's placings."""
    mine = []
    for lobby in lobbies:
        table = standings(lobby)
        line = player_lines(lobby, table).get(account)
        team = next((r for m in lobby["matches"] for r in m["rosters"] if any(p["id"] == account for p in r["players"])), None)
        row = next((t for t in table if team and t["id"] == team["team"]), None)
        if line and row:
            stats = {s["label"]: s["value"] for s in line.stats}
            mine.append((row, int(stats["Finishes"]), int(stats["Damage"].replace(",", "")), int(stats["Games"])))
    if not mine:
        return [], ""
    games = sum(g for *_, g in mine) or 1
    stats = [
        {"label": "Lobbies", "value": str(len(mine))},
        {"label": "Avg place", "value": f"{sum(r['place'] for r, *_ in mine) / len(mine):.1f}"},
        {"label": "Finishes", "value": str(sum(k for _, k, _, _ in mine))},
        {"label": "Damage / game", "value": f"{sum(d for _, _, d, _ in mine) / games:.0f}"},
        {"label": "Games won", "value": str(sum(r["wins"] for r, *_ in mine))},
    ]
    return stats, f"Last {len(mine)} {'lobby' if len(mine) == 1 else 'lobbies'} (PUBG keeps 14 days)"


# ---------------------------------------------------------------- the adapter


class PubgEsports:
    system = "pubg_esports"
    sports = ("pubg", "pubg: battlegrounds", "playerunknown's battlegrounds", "esports", "e-sports")
    games = ("pubg",)  # covers PUBG beside the other esports sources: players never move between them
    live_cadence = 120  # a match takes half an hour; a new one is in the list soon after it ends
    INDEX_AGE = 3600  # how old a tournament's match list may be when finding a player's lobbies
    LIVE_AGE = 90  # …and when polling a live lobby

    def __init__(self) -> None:
        self._index: tuple[float, list[dict[str, Any]]] = (0.0, [])

    def spare(self) -> bool:
        return True  # no history pages: PUBG keeps 14 days, which the index already covers

    def game_of(self, ref: PlayerRef) -> str:
        return GAME

    def _recent(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        # A tournament made up to a fortnight before its last match may still have matches in the window.
        # Ids written the usual way ("kr-26pws2f") first: the same matches can be listed again under a
        # variant ("kr_26pws2f"), and the first listing is kept (_unique).
        recent = [t for t in pubg.tournaments() if t.get("createdAt") and now - _time(t["createdAt"]) <= 2 * RECENT]
        return sorted(recent, key=lambda t: ("_" in t["id"], t["id"]))

    @staticmethod
    def _unique(lobbies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Lobbies, leaving out any whose matches all came in an earlier one (a tournament listed twice)."""
        seen: set[str] = set()
        out = []
        for lb in lobbies:
            ids = {m["id"] for m in lb["matches"]}
            if ids - seen:
                out.append(lb)
            seen |= ids
        return out

    def _tournament_lobbies(self, tournament: str, max_age: float) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        listed = [m for m in pubg.tournament_matches(tournament, max_age) if m.get("createdAt") and now - _time(m["createdAt"]) <= RECENT]
        return group_lobbies(tournament, [pubg.match(m["id"], summarize_match) for m in listed])

    def warm(self) -> int:
        """Bring the shared index up to date (the worker, every 10 minutes): tournaments made in the last
        days read afresh, older ones hourly, every new match downloaded once. Returns the lobbies indexed."""
        lobbies: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for t in self._recent():
            fresh = now - _time(t["createdAt"]) <= timedelta(days=3)
            try:
                lobbies += self._tournament_lobbies(t["id"], 300 if fresh else self.INDEX_AGE)
            except pubg.RateLimited:
                break  # the rest next time
            except pubg.NotFound:
                continue
        lobbies = self._unique(lobbies)
        self._index = (now.timestamp(), lobbies)
        return len(lobbies)

    def lobbies(self) -> list[dict[str, Any]]:
        """Every recent lobby, from the shared index (read again at most once a minute in this process)."""
        at, cached = self._index
        if datetime.now(timezone.utc).timestamp() - at < 60:
            return cached
        lobbies: list[dict[str, Any]] = []
        for t in self._recent():
            try:
                lobbies += self._tournament_lobbies(t["id"], self.INDEX_AGE)
            except (pubg.RateLimited, pubg.NotFound):
                continue
        lobbies = self._unique(lobbies)
        self._index = (datetime.now(timezone.utc).timestamp(), lobbies)
        return lobbies

    def _mine(self, account: str) -> list[dict[str, Any]]:
        return [lb for lb in self.lobbies() if any(p["id"] == account for m in lb["matches"] for r in m["rosters"] for p in r["players"])]

    def players(self) -> dict[str, dict[str, Any]]:
        return players_of(self.lobbies())

    def search(self, name: str) -> list[PlayerRef]:
        wanted = espn.fold(name).strip()
        return [ref_of(acc, info) for acc, info in self.players().items() if wanted in (espn.fold(info["name"]), espn.fold(info.get("in_game") or ""))]

    def find_player(self, name: str) -> PlayerRef | None:
        hits = self.search(name)
        return hits[0] if len(hits) == 1 else None

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        info = players_of(self._mine(athlete_id)).get(athlete_id)
        return ref_of(athlete_id, info) if info else None

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        return sorted((parse_fixture(lb) for lb in self._mine(ref.athlete_id)), key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def history_pages(self, ref: PlayerRef, anchor: Any) -> list[dict[str, Any]]:
        return []

    def history(self, ref: PlayerRef, page: dict[str, Any]) -> list[Fixture]:
        return []

    def _lobby(self, locator: dict[str, Any], max_age: float) -> dict[str, Any] | None:
        return next((lb for lb in self._tournament_lobbies(locator["tournament"], max_age) if lb["day"] == locator["day"] and lb["n"] == locator["n"]), None)

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        lobby = self._lobby(locator, self.INDEX_AGE if final else self.LIVE_AGE)
        if lobby is None:
            raise pubg.NotFound(f"PUBG has no lobby {locator}")
        return parse_lobby(lobby)

    def snapshots(self, locators: dict[Any, dict[str, Any]]) -> dict[Any, Snapshot]:
        """Every live lobby: one request a tournament a round, however many lobbies and players."""
        by_tournament: dict[str, list[dict[str, Any]]] = {}
        for t in {loc["tournament"] for loc in locators.values()}:
            by_tournament[t] = self._tournament_lobbies(t, self.LIVE_AGE)
        out = {}
        for key, loc in locators.items():
            lobby = next((lb for lb in by_tournament[loc["tournament"]] if lb["day"] == loc["day"] and lb["n"] == loc["n"]), None)
            if lobby:
                out[key] = parse_lobby(lobby)
        return out

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        return _result_label(fixture, team_ids)

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        now = datetime.now(timezone.utc)
        return player_form([lb for lb in self._mine(ref.athlete_id) if _status(lb, now) == "final"], ref.athlete_id)
