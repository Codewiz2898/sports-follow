"""Dota 2 inside the map, from OpenDota, laid over a PandaScore series (pure functions; the requests are
in pandascore_esports.PandascoreEsports._dota).

PandaScore says which map of a series is on and who won each; OpenDota's live list has the map being
played (kills, clock, gold lead, each player's hero), and its finished matches every player's hero,
kills, deaths, assists, gold a minute and last hits. The two are joined by team name, and a player by
their Steam account: the OpenDota pro with the PandaScore player's name on the same team. Each map's
OpenDota match is remembered while it's live (remember), since its list of finished pro matches leaves
out smaller leagues and keeps only the last hundred.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .. import naming
from .base import Line

FILLER = {"team", "esports", "esport", "gaming", "club", "the", "gg"}


def team_words(name: str | None) -> str:
    """ "Team Spirit" -> "spirit", "Tundra Esports" -> "tundra"; for comparing names, never shown."""
    words = [w for w in naming.team_key(name or "").split() if w not in FILLER]
    return " ".join(words) or naming.team_key(name or "")


def same_team(a: str | None, b: str | None) -> bool:
    ka, kb = team_words(a), team_words(b)
    return bool(ka and kb) and (ka == kb or ka.replace(" ", "") == kb.replace(" ", ""))


def accounts(roster: list[dict[str, Any]], team_names: list[str], pros: list[dict[str, Any]], live_players: list[dict[str, Any]] = ()) -> dict[int, str]:
    """Steam account -> PandaScore player id, for the roster's players OpenDota can name: the pro with the
    same name on the same team (or the only pro with that name), else a live game's player of that name."""
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in [*pros, *live_players]:
        if p.get("name") and p.get("account_id"):
            by_name[naming.fold(p["name"])].append(p)
    found: dict[int, str] = {}
    for player in roster:
        candidates = by_name.get(naming.fold(player.get("name") or ""), [])
        on_team = [c for c in candidates if any(same_team(c.get("team_name"), t) for t in team_names)]
        pick = {c["account_id"] for c in (on_team or candidates)}
        if len(pick) == 1:
            found[int(pick.pop())] = str(player["id"])
    return found


def _sides(radiant: str | None, dire: str | None, teams: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The series' two teams as (Radiant, Dire), or None when the names aren't these two teams."""
    if len(teams) != 2:
        return None
    a, b = teams
    if same_team(radiant, a["name"]) and same_team(dire, b["name"]):
        return a, b
    if same_team(radiant, b["name"]) and same_team(dire, a["name"]):
        return b, a
    return None


def find_live(games: list[dict[str, Any]], teams: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The live game between the series' two teams, if OpenDota lists it."""
    return next((g for g in games if g.get("league_id") and _sides(g.get("team_name_radiant"), g.get("team_name_dire"), teams)), None)


def clock(seconds: Any) -> str:
    """ "37:12"; "draft" before the horn (the game clock is 0 or below while heroes are picked)."""
    s = int(seconds or 0)
    return f"{s // 60}:{s % 60:02d}" if s > 0 else "draft"


def live_map(game: dict[str, Any], teams: list[dict[str, Any]], players: dict[int, str], hero_names: dict[int, str], n: int) -> dict[str, Any]:
    """The map being played: each side's kills, the clock, the gold lead, and the followed roster's heroes."""
    radiant, dire = _sides(game.get("team_name_radiant"), game.get("team_name_dire"), teams)
    lead = int(game.get("radiant_lead") or 0)
    return {
        "n": n,
        "clock": clock(game.get("game_time")),
        "radiant": {"id": radiant["id"], "name": radiant["name"], "kills": int(game.get("radiant_score") or 0)},
        "dire": {"id": dire["id"], "name": dire["name"], "kills": int(game.get("dire_score") or 0)},
        "gold_lead": {"id": (radiant if lead >= 0 else dire)["id"], "amount": abs(lead)},
        "heroes": {players[int(p["account_id"])]: hero_names.get(int(p.get("hero_id") or 0)) for p in game.get("players") or [] if int(p.get("account_id") or 0) in players and p.get("hero_id")},
    }


SERIES_SLACK = 3600  # OpenDota's start times run ahead of PandaScore's (17 minutes, seen 13 Oct 2026)


def find_maps(finished: list[dict[str, Any]], teams: list[dict[str, Any]], begin_at: int) -> list[int]:
    """The OpenDota matches of this series' finished maps, oldest first: between these two teams, since
    about when the series began."""
    rows = [m for m in finished if (m.get("start_time") or 0) >= begin_at - SERIES_SLACK and _sides(m.get("radiant_name"), m.get("dire_name"), teams)]
    return [int(m["match_id"]) for m in sorted(rows, key=lambda m: m["start_time"])]


def remember(known: dict[str, int], n: int, match_id: int) -> dict[str, int]:
    """The series' maps by number -> OpenDota match, with the game now live as map n, unless map n or the
    game is already known: the first sighting wins, so a map PandaScore hasn't caught up on yet (it still
    says map 2 while map 3 is on) can't take map 2's place."""
    if str(n) in known or match_id in known.values():
        return known
    return {**known, str(n): match_id}


def number_maps(ids: list[int], finished: int) -> list[tuple[int, int]]:
    """(map number, OpenDota match) for the series' finished maps. More matches than PandaScore has
    finished maps means PandaScore hasn't caught up: the first ones are its maps. Fewer means the earliest
    have left OpenDota's last 100 pro matches: the ones found are the latest maps."""
    if len(ids) >= finished:
        return [(i + 1, mid) for i, mid in enumerate(ids[:finished])]
    return [(finished - len(ids) + i + 1, mid) for i, mid in enumerate(ids)]


def map_stats(details: dict[str, Any], players: dict[int, str], hero_names: dict[int, str]) -> dict[str, dict[str, Any]]:
    """A finished map's numbers for the roster's players, by PandaScore player id."""
    out = {}
    for p in details.get("players") or []:
        pid = players.get(int(p.get("account_id") or 0))
        if pid is None:
            continue
        radiant = bool(p.get("isRadiant", (p.get("player_slot") or 0) < 128))
        out[pid] = {
            "hero": hero_names.get(int(p.get("hero_id") or 0), "?"), "kills": int(p.get("kills") or 0), "deaths": int(p.get("deaths") or 0),
            "assists": int(p.get("assists") or 0), "gpm": int(p.get("gold_per_min") or 0), "last_hits": int(p.get("last_hits") or 0),
            "won": bool(details.get("radiant_win")) == radiant,
        }
    return out


def lines(maps: list[dict[str, Any]], live: dict[str, Any] | None, final: bool) -> dict[str, Line]:
    """Each roster player's line: the hero they're on now, or their last map; the series' totals beside."""
    ids = {pid for m in maps for pid in m.get("players") or {}} | set((live or {}).get("heroes") or {})
    out = {}
    for pid in ids:
        played = [(m["n"], m["players"][pid]) for m in maps if pid in (m.get("players") or {})]
        k, d, a = (sum(p[x] for _, p in played) for x in ("kills", "deaths", "assists"))
        if final and played:
            headline = f"{k}/{d}/{a} over {len(played)} map{'s' if len(played) != 1 else ''}"
        elif live and pid in live.get("heroes", {}):
            headline = f"On {live['heroes'][pid]} · map {live['n']}"
        elif played:
            n, last = played[-1]
            headline = f"{last['kills']}/{last['deaths']}/{last['assists']} on {last['hero']} · map {n}"
        else:
            continue
        stats = []
        if played:
            stats = [
                {"label": "K/D/A", "value": f"{k}/{d}/{a}"},
                {"label": "GPM", "value": str(round(sum(p["gpm"] for _, p in played) / len(played)))},
                {"label": "Last hits", "value": str(sum(p["last_hits"] for _, p in played))},
                {"label": "Heroes", "value": ", ".join(dict.fromkeys(p["hero"] for _, p in played))},
            ]
        out[pid] = Line(headline=headline, stats=stats)
    return out
