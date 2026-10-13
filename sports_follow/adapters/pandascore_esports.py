"""Esports from PandaScore (licensed): a pro's team's series, results by series and by map, team form.

Team titles only (Valorant, CS2, League of Legends, Dota 2, Mobile Legends and the rest in GAMES): a
series is one event between two teams, so it fits the model every other sport uses. Battle royale
(BGMI, PUBG) doesn't: a match there is a lobby of sixteen teams scored by placement (BACKLOG.md, item 8).

The free plan has no player numbers and no live in-game data, so a player's results, form and moments
are their team's, and lines are empty. A running series' map results do arrive as each map ends, so
series are polled once a minute, all of them in one request (structured.poll_lane), for the map and
series moments.

Players are known by their PandaScore id: the registry imports the pros of IMPORT_GAMES from here
(registry.import_esports), since Wikidata knows only a few thousand esports players.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import espn, pandascore
from .base import AdapterError, Fixture, PlayerRef, Snapshot

# PandaScore's team titles: slug -> the name fans use. Its 1v1 titles (StarCraft, EA Sports FC), battle
# royale (PUBG) and simulated matches (eSoccer, eCricket…) are left out.
GAMES = {
    "valorant": "Valorant", "cs-go": "CS2", "league-of-legends": "League of Legends", "dota-2": "Dota 2",
    "mlbb": "Mobile Legends", "r6-siege": "Rainbow Six Siege", "ow": "Overwatch", "rl": "Rocket League",
    "cod-mw": "Call of Duty", "lol-wild-rift": "Wild Rift", "kog": "King of Glory",
}
GAME_IDS = {"league-of-legends": 1, "cs-go": 3, "dota-2": 4, "ow": 14, "rl": 22, "cod-mw": 23, "r6-siege": 24, "valorant": 26, "kog": 27, "lol-wild-rift": 28, "mlbb": 34}
# The titles whose pros the registry imports, so search finds them: every team title, for fans anywhere
# (Mobile Legends and King of Glory are among the most watched in the world, though not in India).
IMPORT_GAMES = [g for g in os.environ.get("SPORTS_FOLLOW_ESPORTS_GAMES", ",".join(GAMES)).split(",") if g in GAME_IDS]

_STATUS = {"not_started": "scheduled", "running": "live", "finished": "final", "canceled": "postponed", "postponed": "postponed"}

# Nationalities come as ISO 3166 codes; the countries most pros play for, by name. Others show as the code.
COUNTRIES = {
    "IN": "India", "US": "United States", "CA": "Canada", "BR": "Brazil", "AR": "Argentina", "CL": "Chile", "MX": "Mexico",
    "GB": "United Kingdom", "FR": "France", "DE": "Germany", "ES": "Spain", "IT": "Italy", "PT": "Portugal", "NL": "Netherlands",
    "BE": "Belgium", "DK": "Denmark", "SE": "Sweden", "NO": "Norway", "FI": "Finland", "PL": "Poland", "CZ": "Czechia",
    "SK": "Slovakia", "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria", "RS": "Serbia", "HR": "Croatia", "BA": "Bosnia and Herzegovina",
    "SI": "Slovenia", "XK": "Kosovo", "AT": "Austria", "CH": "Switzerland", "UA": "Ukraine", "RU": "Russia", "BY": "Belarus",
    "KZ": "Kazakhstan", "UZ": "Uzbekistan", "MN": "Mongolia", "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania", "TR": "Türkiye",
    "IL": "Israel", "SA": "Saudi Arabia", "AE": "United Arab Emirates", "JO": "Jordan", "EG": "Egypt", "MA": "Morocco",
    "ZA": "South Africa", "CN": "China", "KR": "South Korea", "JP": "Japan", "TW": "Taiwan", "HK": "Hong Kong", "VN": "Vietnam",
    "TH": "Thailand", "PH": "Philippines", "ID": "Indonesia", "MY": "Malaysia", "SG": "Singapore", "AU": "Australia",
    "NZ": "New Zealand", "PK": "Pakistan", "BD": "Bangladesh", "LK": "Sri Lanka", "NP": "Nepal",
}


def game_name(slug: str | None) -> str | None:
    return GAMES.get(slug or "")


def country(code: str | None) -> str | None:
    return COUNTRIES.get((code or "").upper(), code or None)


def _time(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None


def _teams(m: dict[str, Any]) -> list[dict[str, Any]]:
    """The two teams, as PandaScore lists them; one or none while a bracket slot is still to be decided."""
    return [o["opponent"] for o in m.get("opponents") or [] if o.get("type") == "Team" and (o.get("opponent") or {}).get("id")]


def _scores(m: dict[str, Any]) -> dict[str, int]:
    """Maps (or games) won in the series, by team id."""
    return {str(r["team_id"]): int(r.get("score") or 0) for r in m.get("results") or [] if r.get("team_id") is not None}


def _winner(m: dict[str, Any]) -> str | None:
    return str(m["winner_id"]) if m.get("winner_id") is not None and m.get("winner_type") in (None, "Team") else None


def _format(m: dict[str, Any]) -> str | None:
    """ "Bo3", "First to 4", "2 games"."""
    n = int(m.get("number_of_games") or 0)
    kind = m.get("match_type")
    if not n:
        return None
    if kind in ("best_of", "ow_best_of"):
        return f"Bo{n}"
    if kind == "first_to":
        return f"First to {n}"
    if kind == "all_games_played":
        return f"{n} game{'s' if n != 1 else ''}"
    return None


def _stage(m: dict[str, Any]) -> str | None:
    """Where in the event the series is: the match name's prefix ("Upper bracket final: SEN vs 100T"),
    else the tournament's name ("Playoffs", "Group A")."""
    head, sep, _ = (m.get("name") or "").partition(":")
    if sep and head.strip():
        return head.strip()
    return (m.get("tournament") or {}).get("name") or None


def _competition(m: dict[str, Any]) -> str:
    """The league and the season or stage of it: "VCT Americas Stage 2 2026"."""
    league = (m.get("league") or {}).get("name") or ""
    serie = (m.get("serie") or {}).get("full_name") or ""
    if serie and serie.lower() not in league.lower():
        return f"{league} {serie}".strip()
    return league or serie


def _start(m: dict[str, Any], status: str) -> datetime | None:
    """When it starts: the schedule's time until it does (a rescheduled series moves), then when it began."""
    first, then = ("scheduled_at", "begin_at") if status in ("scheduled", "postponed") else ("begin_at", "scheduled_at")
    return _time(m.get(first) or m.get(then))


# ---------------------------------------------------------------- parsers (pure)


def parse_fixtures(rows: list[dict[str, Any]]) -> list[Fixture]:
    """Matches (/matches?filter[opponent_id]=…) -> fixtures, with the series result for finished ones."""
    out = []
    for m in rows:
        teams = _teams(m)
        if not teams or not m.get("id"):
            continue  # a 1v1 title's match, or no team decided yet
        status = _STATUS.get(m.get("status") or "", "scheduled")
        names = [t.get("name") or "" for t in teams]
        result = None
        if status == "final":
            scores, winner = _scores(m), _winner(m)
            result = {
                "teams": [{"id": str(t["id"]), "name": t.get("name"), "score": scores.get(str(t["id"]), 0), "winner": (winner == str(t["id"])) if winner else None} for t in teams],
                "forfeit": bool(m.get("forfeit")),
                "draw": bool(m.get("draw")),
            }
        out.append(Fixture(
            source_id=str(m["id"]),
            title=" vs ".join([*names, "TBD"][:2]),
            competition=_competition(m),
            start_utc=_start(m, status),
            venue=None,
            status=status,
            team_ids=[str(t["id"]) for t in teams],
            team_names=names,
            locator={"match": str(m["id"])},
            detail=" · ".join(x for x in (_stage(m), _format(m)) if x) or None,
            result=result,
        ))
    return out


def result_label(fixture: Fixture, team_ids: list[str]) -> str:
    """ "Won 2–1" / "Lost 0–2" / "Won by forfeit" from the player's team's side, else the plain score."""
    r = fixture.result
    if not r or len(r.get("teams") or []) != 2:
        return ""
    a, b = r["teams"]
    ours, theirs = (a, b) if a["id"] in team_ids else (b, a) if b["id"] in team_ids else (None, None)
    if ours is None:
        return f"{a['name']} {a['score']}–{b['score']} {b['name']}"
    if r.get("forfeit"):
        return "Won by forfeit" if ours.get("winner") else "Lost by forfeit" if theirs.get("winner") else "Forfeit"
    score = f"{ours['score']}–{theirs['score']}"
    if ours.get("winner"):
        return f"Won {score}"
    if theirs.get("winner"):
        return f"Lost {score}"
    return f"Drew {score}" if r.get("draw") or ours["score"] == theirs["score"] else score


def parse_match(m: dict[str, Any]) -> Snapshot:
    """A match -> snapshot: the series score, each map's winner, which map is on."""
    status = _STATUS.get(m.get("status") or "", "scheduled")
    teams = _teams(m)
    scores, winner = _scores(m), _winner(m)
    sides = [{
        "id": str(t["id"]), "name": t.get("name") or "", "abbr": t.get("acronym"),
        "score": scores.get(str(t["id"]), 0), "winner": (winner == str(t["id"])) if status == "final" and winner else None,
    } for t in teams]
    games = sorted((g for g in m.get("games") or [] if g.get("status") != "not_played"), key=lambda g: g.get("position") or 0)
    maps = [{"n": g.get("position"), "status": g.get("status"), "winner": str((g.get("winner") or {}).get("id")) if (g.get("winner") or {}).get("id") is not None else None} for g in games]
    running = next((g for g in games if g.get("status") == "running"), None)
    fmt = _format(m)
    if status == "live":
        clock = f"Map {running.get('position')}" if running else "Between maps" if any(g.get("status") == "finished" for g in games) else "Starting"
    elif status == "final":
        clock = "Forfeit" if m.get("forfeit") else "Final"
    elif status == "postponed":
        clock = "Cancelled" if m.get("status") == "canceled" else "Postponed"
    else:
        clock = fmt or ""
    names = [s["name"] for s in sides] + ["TBD"] * (2 - len(sides))
    if status in ("live", "final") and len(sides) == 2:
        score = f"{names[0]} {sides[0]['score']} – {sides[1]['score']} {names[1]}"
    else:
        score = f"{names[0]} vs {names[1]}"
    state = {
        "kind": "esports",
        "game": game_name((m.get("videogame") or {}).get("slug")),
        "teams": sides,
        "maps": maps,
        "format": fmt,
        "stage": _stage(m),
        "forfeit": bool(m.get("forfeit")),
    }
    return Snapshot(status=status, score_label=score, clock_label=clock, state=state, source_url=None)


def parse_player(p: dict[str, Any]) -> PlayerRef:
    """A player -> ref: their current team, and their game as the league."""
    team = p.get("current_team") or {}
    return PlayerRef(
        system="pandascore_esports",
        athlete_id=str(p["id"]),
        name=p.get("name") or "",
        team_ids=[str(team["id"])] if team.get("id") else [],
        team_names=[team.get("name") or ""] if team.get("id") else [],
        league=(p.get("current_videogame") or {}).get("slug"),
        born=p.get("birthday"),
        game=game_name((p.get("current_videogame") or {}).get("slug")),
    )


def full_name(p: dict[str, Any]) -> str:
    """ "Tyson Ngo" from first and last name, or ""."""
    return " ".join(x.strip() for x in (p.get("first_name"), p.get("last_name")) if x and x.strip())


def team_form(fixtures: list[Fixture], team_ids: list[str], team: str | None) -> tuple[list[dict[str, str]], str]:
    """Series and maps won and lost over these finished series, from the team's side."""
    series, maps = [0, 0], [0, 0]
    for f in fixtures:
        r = f.result
        if not r or len(r.get("teams") or []) != 2:
            continue
        a, b = r["teams"]
        ours, theirs = (a, b) if a["id"] in team_ids else (b, a) if b["id"] in team_ids else (None, None)
        if ours is None:
            continue
        if ours.get("winner") or theirs.get("winner"):
            series[0 if ours.get("winner") else 1] += 1
        if not r.get("forfeit"):
            maps[0] += int(ours["score"])
            maps[1] += int(theirs["score"])
    played = series[0] + series[1]
    if not played:
        return [], ""
    stats = [{"label": "Series", "value": f"{series[0]}–{series[1]}"}, {"label": "Series won", "value": f"{100 * series[0] / played:.0f}%"}]
    if maps[0] or maps[1]:
        stats.append({"label": "Maps", "value": f"{maps[0]}–{maps[1]}"})
    return stats, f"{team or 'Their team'}, last {played} series"


# ---------------------------------------------------------------- the adapter


class PandascoreEsports:
    system = "pandascore_esports"
    # Its games by name too: a pro's sport is their game ("CS2"), and for_sport() finds the source by it.
    sports = ("esports", "e-sports", "electronic sports", *(g.lower() for g in GAMES.values()), "counter-strike", "cs:go")
    games = tuple(GAMES)  # covers these games of the sport, beside Liquipedia's: players never move between them
    live_cadence = 60  # map results arrive as each map ends; one request a round for every live series
    MULTI_MAX = 100  # match ids one request takes (a page)
    WINDOW_BACK, WINDOW_AHEAD = 60, 45  # days of series a refresh reads; older ones come from history_pages
    HISTORY_DAYS = 360  # how far before that window history.py reads, once: four 90-day pages per team

    def __init__(self) -> None:
        # Matches as the last list returned them, so a finished series' snapshot needs no request of its own.
        self._seen: dict[str, dict[str, Any]] = {}

    def spare(self) -> bool:
        """Whether history.py may read now: live series need the hour's last LIVE_RESERVE requests."""
        return pandascore.spare()

    def game_of(self, ref: PlayerRef) -> str | None:
        """The pro's game, from the ref, or from its league for a binding stored before refs had one."""
        return ref.game or game_name(ref.league)

    def _keep(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for m in rows:
            if m.get("id") and m.get("status") == "finished":
                self._seen[str(m["id"])] = m
        if len(self._seen) > 5000:
            for key in list(self._seen)[:1000]:
                self._seen.pop(key, None)
        return rows

    def search(self, name: str) -> list[PlayerRef]:
        params = {"search[name]": name, "filter[videogame_id]": ",".join(str(i) for i in GAME_IDS.values()), "page[size]": 10}
        rows = pandascore.get_json("/players", ttl=24 * 3600, **params)
        return [parse_player(p) for p in rows if p.get("id")] if isinstance(rows, list) else []

    def find_player(self, name: str) -> PlayerRef | None:
        """The pro with exactly this handle; of two, the one on a team, if only one is."""
        hits = [r for r in self.search(name) if espn.fold(r.name).strip() == espn.fold(name).strip()]
        on_teams = [r for r in hits if r.team_ids]
        pick = hits if len(hits) == 1 else on_teams
        return pick[0] if len(pick) == 1 else None

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        try:
            data = pandascore.get_json(f"/players/{athlete_id}", ttl=6 * 3600)
        except pandascore.NotFound:
            return None
        return parse_player(data) if isinstance(data, dict) and data.get("id") else None

    def _between(self, team_id: str, start: date, end: date, ttl: float) -> list[Fixture]:
        span = f"{start.isoformat()}T00:00:00Z,{end.isoformat()}T23:59:59Z"
        rows = pandascore.get_all("/matches", ttl=ttl, pages=2, **{"filter[opponent_id]": team_id, "range[scheduled_at]": span, "sort": "scheduled_at"})
        return parse_fixtures(self._keep(rows))

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        today = datetime.now(timezone.utc).date()
        seen: dict[str, Fixture] = {}
        for team_id in ref.team_ids:
            for f in self._between(team_id, today - timedelta(days=self.WINDOW_BACK), today + timedelta(days=self.WINDOW_AHEAD), ttl=1800):
                seen.setdefault(f.source_id, f)
        return sorted(seen.values(), key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def history_pages(self, ref: PlayerRef, anchor: date) -> list[dict[str, Any]]:
        """Before the refresh window, 90 days at a time for each team, back HISTORY_DAYS."""
        pages = []
        end = anchor - timedelta(days=self.WINDOW_BACK + 1)
        stop = end - timedelta(days=self.HISTORY_DAYS)
        while end > stop:
            start = max(stop, end - timedelta(days=89))
            pages += [{"team": team_id, "from": start.isoformat(), "to": end.isoformat()} for team_id in ref.team_ids]
            end = start - timedelta(days=1)
        return pages

    def history(self, ref: PlayerRef, page: dict[str, Any]) -> list[Fixture]:
        found = self._between(page["team"], date.fromisoformat(page["from"]), date.fromisoformat(page["to"]), ttl=24 * 3600)
        return [f for f in found if f.status == "final"]

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        match_id = str(locator["match"])
        if final and match_id in self._seen:
            return parse_match(self._seen[match_id])
        # A short cache lets every followed player in one series share each poll's fetch.
        data = pandascore.get_json(f"/matches/{match_id}", ttl=24 * 3600 if final else 30)
        if not isinstance(data, dict) or not data.get("id"):
            raise AdapterError(f"PandaScore has no match {match_id}")
        self._keep([data])
        return parse_match(data)

    def snapshots(self, locators: dict[Any, dict[str, Any]]) -> dict[Any, Snapshot]:
        """Many series at once, MULTI_MAX to a request: the live lane's one fetch a round. Keyed as
        given; a match PandaScore doesn't return is left out."""
        wanted = {key: str(loc["match"]) for key, loc in locators.items()}
        ids = sorted(set(wanted.values()))
        found: dict[str, Snapshot] = {}
        for i in range(0, len(ids), self.MULTI_MAX):
            rows = pandascore.get_json("/matches", ttl=0, **{"filter[id]": ",".join(ids[i:i + self.MULTI_MAX]), "page[size]": self.MULTI_MAX})
            for m in self._keep(rows if isinstance(rows, list) else []):
                found[str(m.get("id"))] = parse_match(m)
        return {key: found[mid] for key, mid in wanted.items() if mid in found}

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        return result_label(fixture, team_ids)

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        """No player numbers on the free plan: the team's series and maps over the refresh window (the
        list fixtures() just read, so no new request)."""
        finals = [f for f in self.fixtures(ref) if f.status == "final"] if ref.team_ids else []
        return team_form(finals, ref.team_ids, (ref.team_names or [None])[0])
