"""Esports from PandaScore (licensed): a pro's team's series, results by series and by map, team form.

Team titles only (Valorant, CS2, League of Legends, Dota 2, Mobile Legends and the rest in GAMES): a
series is one event between two teams, so it fits the model every other sport uses. Battle royale
(BGMI, PUBG) doesn't: a match there is a lobby of sixteen teams scored by placement (BACKLOG.md, item 8).

The free plan has no player numbers and no live in-game data, so a player's results, form and moments
are their team's, and lines are empty. A running series' map results do arrive as each map ends, so
series are polled once a minute, all of them in one request (structured.poll_lane), for the map and
series moments.

Players are known by their PandaScore id: the registry imports the pros of IMPORT_GAMES from here
(registry.import_rosters), since Wikidata knows only a few thousand esports players. Each title is a sport of
its own (sports/esports.py); one turned off isn't read, searched or imported.

Dota 2 series get what happens inside each map from OpenDota (dota.py): kills, the clock and the gold
lead while a map is on, and every roster player's hero and numbers once it's over.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, cast

from .. import bus, naming, sports
from ..sports import esports
from . import budget, dota, opendota, pandascore
from .base import DIGITS, Adapter, AdapterError, Fixture, LiveGame, PlayerRef, Roster, Rostered, Snapshot, SourceTeam, Stream

log = logging.getLogger("sports_follow.esports")

# PandaScore's team titles: slug -> the sport each is (sports/esports.py: "cs-go" is CS2). Its 1v1 titles
# (StarCraft, EA Sports FC), battle royale (PUBG) and simulated matches (eSoccer, eCricket…) are left out.
GAMES = {
    "valorant": "valorant", "cs-go": "cs2", "league-of-legends": "lol", "dota-2": "dota2",
    "mlbb": "mlbb", "r6-siege": "r6siege", "ow": "overwatch", "rl": "rocketleague",
    "cod-mw": "cod", "lol-wild-rift": "wildrift", "kog": "hok",
}
GAME_IDS = {"league-of-legends": 1, "cs-go": 3, "dota-2": 4, "ow": 14, "rl": 22, "cod-mw": 23, "r6-siege": 24, "valorant": 26, "kog": 27, "lol-wild-rift": 28, "mlbb": 34}
# The titles whose pros the registry imports, so search finds them: every team title, for fans anywhere
# (Mobile Legends and King of Glory are among the most watched in the world, though not in India).
IMPORT_GAMES = [g for g in os.environ.get("SPORTS_FOLLOW_ESPORTS_GAMES", ",".join(GAMES)).split(",") if g in GAME_IDS]

_STATUS = {"not_started": "scheduled", "running": "live", "finished": "final", "canceled": "postponed", "postponed": "postponed"}

# The tournament tiers whose series are among the big games on now (live_now), as LiveGame tiers, biggest
# first. PandaScore rates a tournament S > A > B > C > D, or "unranked", by its organiser, teams and prize
# pool: S is Worlds, The International, a Major; A a top regional league (LCK, VCT EMEA); B a smaller
# region's league or the qualifier for an S event (CBLOL, The International's). Most closed qualifiers are
# C or D, a second division (ESEA Advanced) D.
LIVE_TIERS = {"s": 3, "a": 2, "b": 1}
# And the tier whose series stand in for them in a game none of whose series on now is of those (live_now): a
# closed qualifier, a smaller league (EPL Masters), so a game with a series on isn't empty for days between
# its big events. A LiveGame tier of 0, below every rated one. D and unranked never.
STAND_IN_TIER = "c"
# And whose series are among the big results and big games coming up (schedule): world events and top leagues.
SCHEDULE_TIERS = ("s", "a")
DOTA_WAIT = 6 * 3600  # how long after a Dota 2 series ends its match page waits for OpenDota to parse its maps
SCHEDULE_PAGES = 3  # pages of 100 series: three weeks of S and A tier events is about two
# The tournaments whose teams the team registry lists (teams): those the app rates (LIVE_TIERS). A team only in
# closed qualifiers and second divisions isn't, as there are thousands; an operator adds one by hand (teams.py).
TEAM_TIERS = tuple(LIVE_TIERS)
TEAM_PAGES = 2  # pages of 100 tournaments a list (on now, starting, ended), as a team's series are read (_between)

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
    """A title as the app names it: "cs-go" is "CS2"."""
    return sports.BY_KEY[GAMES[slug]].label if slug in GAMES else None


def game_ids(slugs: list[str]) -> str:
    """PandaScore's ids for these titles, as a filter takes them."""
    return ",".join(str(GAME_IDS[g]) for g in slugs if g in GAME_IDS)


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


def _status(m: dict[str, Any]) -> str:
    """Ours from PandaScore's. A forfeit is "canceled" with the series awarded (forfeit and a winner):
    a result, not a cancelled series."""
    if m.get("status") == "canceled" and m.get("forfeit") and _winner(m):
        return "final"
    return _STATUS.get(m.get("status") or "", "scheduled")


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


def _streams(m: dict[str, Any]) -> list[Stream]:
    """Where the series can be watched (its streams_list), in PandaScore's order: each stream's own page
    ("https://www.twitch.tv/riotgames"), not PandaScore's player for it, as the app makes its own (watch.py)."""
    out = []
    for s in m.get("streams_list") or []:
        url = (s.get("raw_url") or "").strip() if isinstance(s, dict) else ""
        if url:
            out.append(Stream(url=url, language=(s.get("language") or "").strip().lower(), official=bool(s.get("official")), main=bool(s.get("main"))))
    return out


# ---------------------------------------------------------------- parsers (pure)


def parse_fixtures(rows: list[dict[str, Any]]) -> list[Fixture]:
    """Matches (/matches?filter[opponent_id]=…) -> fixtures, with the series result for finished ones."""
    out = []
    for m in rows:
        teams = _teams(m)
        if not teams or not m.get("id"):
            continue  # a 1v1 title's match, or no team decided yet
        status = _status(m)
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
            sport=GAMES.get((m.get("videogame") or {}).get("slug") or "", ""),
            streams=_streams(m),
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
    won = esports.took(ours, theirs, bool(r.get("draw")))  # the winner as marked, else (a poll late) by the maps
    return f"Won {score}" if won else f"Lost {score}" if won is False else f"Drew {score}"


def parse_match(m: dict[str, Any]) -> Snapshot:
    """A match -> snapshot: the series score, each map's winner, which map is on."""
    status = _status(m)
    teams = _teams(m)
    scores, winner = _scores(m), _winner(m)
    sides = [{
        "id": str(t["id"]), "name": t.get("name") or "", "abbr": t.get("acronym"),
        "score": scores.get(str(t["id"]), 0), "winner": (winner == str(t["id"])) if status == "final" and winner else None,
    } for t in teams]
    # Maps never played aren't maps: "not_played" after a series is won, still "not_started" in a forfeit.
    unplayed = ("not_played", "not_started") if status == "final" else ("not_played",)
    games = sorted((g for g in m.get("games") or [] if g.get("status") not in unplayed), key=lambda g: g.get("position") or 0)
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
        "draw": bool(m.get("draw")),  # level when every game's played: the series has no winner to wait for
        # Called off for good, not put back to a later date: what's still to be played of it won't be.
        "cancelled": status == "postponed" and m.get("status") == "canceled",
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


def parse_team(t: dict[str, Any]) -> SourceTeam | None:
    """A team and the players on it today (pure): a team title's, with its game as each player's league. None
    for a team with no id or name, or of a title this source's pros aren't from (GAMES)."""
    slug = (t.get("current_videogame") or {}).get("slug")
    if not t.get("id") or not (t.get("name") or "").strip() or slug not in GAMES:
        return None
    team_id, name = str(t["id"]), t["name"].strip()
    players = [
        PlayerRef(system="pandascore_esports", athlete_id=str(p["id"]), name=p["name"].strip(), team_ids=[team_id], team_names=[name], league=slug,
                  born=p.get("birthday"), game=game_name(slug))
        for p in t.get("players") or []
        if p.get("id") and naming.words(p.get("name") or "") and p.get("active") is not False
    ]
    # Its sport too, as teams() gives it: a team an operator adds by hand is of its own game, not the source's first.
    return SourceTeam(system="pandascore_esports", team_id=team_id, name=name, game=game_name(slug), players=players, sport=GAMES[slug],
                      short=(t.get("acronym") or "").strip(), competition=game_name(slug) or "", country=country(t.get("location")) or "")


def parse_teams(tournaments: list[dict[str, Any]], slugs: list[str]) -> list[SourceTeam]:
    """Tournaments (each with its teams) -> the team registry's rows of their teams, of these titles only: each with
    its game as its sport, its game's name as what it plays in, where it's from, and the best tier of the tournaments
    it's in here (LIVE_TIERS' scale). A team in several is listed once."""
    out: dict[str, SourceTeam] = {}
    for t in tournaments:
        slug = (t.get("videogame") or {}).get("slug")
        if slug not in GAMES or slug not in slugs:
            continue
        tier = LIVE_TIERS.get(t.get("tier") or "", 0)
        for team in t.get("teams") or []:
            name = (team.get("name") or "").strip()
            if not team.get("id") or not name:
                continue
            listed = out.setdefault(str(team["id"]), SourceTeam(
                system="pandascore_esports", team_id=str(team["id"]), name=name, game=game_name(slug), sport=GAMES[slug],
                short=(team.get("acronym") or "").strip(), competition=game_name(slug) or "", country=country(team.get("location")) or ""))
            listed.tier = max(listed.tier, tier)
    return list(out.values())


def full_name(p: dict[str, Any]) -> str:
    """ "Tyson Ngo" from first and last name, or ""."""
    return " ".join(x.strip() for x in (p.get("first_name"), p.get("last_name")) if x and x.strip())


ROSTER_PACE = 9.0  # seconds between pages: at most 400 requests an hour of PandaScore's 1,000
ROSTER_WAIT = 3600  # how long a run waits, in all, for live series to leave it some of the hour


def rostered(players: list[dict[str, Any]]) -> list[Rostered]:
    """PandaScore players for the registry (pure). The handle is the name ("TenZ") and the full name an
    alias, so either finds them; a pro on a team today counts as current."""
    out = []
    for p in players:
        if not p.get("id") or not naming.words(p.get("name") or ""):
            continue
        ref = parse_player(p)
        if ref.league not in GAMES:
            continue  # a 1v1 or battle-royale title
        out.append(Rostered(key=f"pandascore:{ref.athlete_id}", ref=ref, aliases=[full_name(p)], country=country(p.get("nationality")), current=bool(p.get("active")) and bool(ref.team_ids)))
    return out


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


class PandascoreEsports(Adapter):
    system = "pandascore_esports"
    name = "PandaScore"
    provider = pandascore.PROVIDER
    covers = "Valorant, CS2, League of Legends, Dota 2 and other team games"
    sports = tuple(dict.fromkeys(GAMES.values()))
    games = tuple(GAMES)  # its own names for them, beside Liquipedia's: players never move between them
    no_team_is_real = True  # a pro with no current team has none: between teams, or retired
    live_cadence = 60  # map results arrive as each map ends; one request a round for every live series
    MULTI_MAX = 100  # match ids one request takes (a page)
    WINDOW_BACK, WINDOW_AHEAD = 60, 45  # days of series a refresh reads; older ones come from history_pages
    HISTORY_DAYS = 360  # how far before that window history.py reads, once: four 90-day pages per team

    def budgets(self) -> tuple[budget.Budget, ...]:
        return (*super().budgets(), opendota.BUDGET)  # Dota 2 maps come from OpenDota

    def __init__(self) -> None:
        # Matches as the last list returned them, so a finished series' snapshot needs no request of its own.
        self._seen: dict[str, dict[str, Any]] = {}

    def spare(self) -> bool:
        """Whether history.py may read now: live series need the hour's last LIVE_RESERVE requests."""
        return pandascore.spare()

    def sport_of(self, ref: PlayerRef) -> str:
        """The pro's game, from the title the ref names (its league), or the game a binding stored before
        refs had a league says."""
        found = GAMES.get(ref.league or "") or (sports.family(ref.game) if ref.game else "")
        return self.one_of(found)  # a title it doesn't cover, or none: esports, the group

    def _on(self) -> list[str]:
        """The titles whose sports run now: the only ones read."""
        running = self.running()
        return [g for g, sport in GAMES.items() if sport in running]

    def _keep(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for m in rows:
            if m.get("id") and _status(m) == "final":
                self._seen[str(m["id"])] = m
        if len(self._seen) > 5000:
            for key in list(self._seen)[:1000]:
                self._seen.pop(key, None)
        return rows

    roster_prefix = "pandascore:"

    @classmethod
    def available(cls) -> bool:
        return bool(pandascore.token())

    def roster(self, keep: Callable[[list[Rostered]], int], wanted: set[str] | None = None) -> Roster:
        """The active pros of IMPORT_GAMES of the sports wanted (all of its own by default), a page of 100
        every ROSTER_PACE seconds and only while live series keep their share of the hour (pandascore.spare)."""
        read = [g for g in IMPORT_GAMES if wanted is None or GAMES[g] in wanted]
        if not read:
            return Roster(complete=True, leagues=[])
        params = {"filter[videogame_id]": game_ids(read), "filter[active]": "true", "sort": "id", "page[size]": pandascore.PAGE}
        page, waited, complete = 1, 0, True
        while True:
            if not pandascore.spare() and waited < ROSTER_WAIT:
                time.sleep(60)
                waited += 60
                continue
            try:
                players = pandascore.get_json("/players", ttl=0, **params, **{"page[number]": page})
            except pandascore.RateLimited:
                if waited >= ROSTER_WAIT:
                    complete = False
                    break
                time.sleep(60)
                waited += 60
                continue
            except AdapterError as exc:
                log.warning("registry esports: page %d failed: %s", page, exc)
                complete = False
                break
            players = players if isinstance(players, list) else []
            total = keep(rostered(players))
            log.info("registry esports: %d pages read, %d pros kept", page, total)
            if len(players) < pandascore.PAGE:
                break
            page += 1
            time.sleep(ROSTER_PACE)
        return Roster(complete=complete, leagues=read)

    def credits_also(self) -> list[dict[str, Any]]:
        if not opendota.ENABLED:
            return []
        return [{
            "name": "OpenDota",
            "url": "https://www.opendota.com",
            "what": "Dota 2 inside the map: kills, the clock and the gold lead while a map is on, and each player's hero, kills, deaths, assists, gold a minute and last hits after it, from Valve's match data.",
            "note": "Run by the OpenDota community, best effort.",
        }]

    def credit(self, snap: Snapshot) -> str:
        # The inside of a Dota 2 map comes from OpenDota (_dota): credit both.
        return f"{self.name} and OpenDota" if snap.state.get("dota") else self.name

    def search(self, name: str, league: str | None = None) -> list[PlayerRef]:
        games = game_ids(self._on())
        if not games:
            return []
        params = {"search[name]": name, "filter[videogame_id]": games, "page[size]": 10}
        rows = pandascore.get_json("/players", ttl=24 * 3600, **params)
        return [parse_player(p) for p in rows if p.get("id")] if isinstance(rows, list) else []

    def find_player(self, name: str) -> PlayerRef | None:
        """The pro with exactly this handle; of two, the one on a team, if only one is."""
        hits = [r for r in self.search(name) if naming.fold(r.name).strip() == naming.fold(name).strip()]
        on_teams = [r for r in hits if r.team_ids]
        pick = hits if len(hits) == 1 else on_teams
        return pick[0] if len(pick) == 1 else None

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        try:
            data = pandascore.get_json(f"/players/{athlete_id}", ttl=6 * 3600)
        except pandascore.NotFound:
            return None
        return parse_player(data) if isinstance(data, dict) and data.get("id") else None

    team_id_pattern = DIGITS

    def team(self, team_id: str) -> SourceTeam | None:
        """A team and who's on it now (a campaign's whole-team follow), read at most every 6 hours."""
        try:
            data = pandascore.get_json(f"/teams/{team_id}", ttl=6 * 3600)
        except pandascore.NotFound:
            return None
        return parse_team(data) if isinstance(data, dict) else None

    def find_teams(self, name: str) -> list[SourceTeam]:
        """Teams by name in the team titles, for an operator picking a campaign's team by its id."""
        games = game_ids(self._on())
        if not games:
            return []
        rows = pandascore.get_json("/teams", ttl=3600, **{"search[name]": name, "filter[videogame_id]": games, "page[size]": 20})
        return [t for t in (parse_team(r) for r in rows if isinstance(r, dict)) if t] if isinstance(rows, list) else []

    def _between(self, team_id: str, start: date, end: date, ttl: float) -> list[Fixture]:
        span = f"{start.isoformat()}T00:00:00Z,{end.isoformat()}T23:59:59Z"
        rows = pandascore.get_all("/matches", ttl=ttl, pages=2, **{"filter[opponent_id]": team_id, "range[scheduled_at]": span, "sort": "scheduled_at"})
        return parse_fixtures(self._keep(rows))

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        seen: dict[str, Fixture] = {}
        for team_id in ref.team_ids:
            for f in self.team_fixtures(team_id):
                seen.setdefault(f.source_id, f)
        return sorted(seen.values(), key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def team_fixtures(self, team_id: str) -> list[Fixture]:
        """One team's series over the refresh window, finished and to come, as fixtures() reads a player's (whose
        team's are these): a page or two of 100 (_between)."""
        today = datetime.now(timezone.utc).date()
        once: dict[str, Fixture] = {}
        for f in self._between(team_id, today - timedelta(days=self.WINDOW_BACK), today + timedelta(days=self.WINDOW_AHEAD), ttl=1800):
            once.setdefault(f.source_id, f)  # a series both pages held (the list moved between them), once
        return sorted(once.values(), key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def teams(self) -> list[SourceTeam]:
        """The teams in the running titles' rated events over the refresh window, for the team registry: the
        TEAM_TIERS tournaments on now, starting in the window and ended in it (as schedule() finds its own), TEAM_PAGES
        pages of 100 a list, each tournament with its teams; so the teams playing those series, each with the best
        tier of its tournaments. Three to six requests, kept a day. None of its games running: not one."""
        games = game_ids(self._on())
        if not games:
            return []
        today = datetime.now(timezone.utc).date()
        back = f"{(today - timedelta(days=self.WINDOW_BACK)).isoformat()}T00:00:00Z,{today.isoformat()}T23:59:59Z"
        ahead = f"{today.isoformat()}T00:00:00Z,{(today + timedelta(days=self.WINDOW_AHEAD)).isoformat()}T23:59:59Z"
        which = {"filter[videogame_id]": games, "filter[tier]": ",".join(TEAM_TIERS)}
        rows: list[dict[str, Any]] = []
        for path, window in (("/tournaments/running", {}), ("/tournaments/upcoming", {"range[begin_at]": ahead}), ("/tournaments/past", {"range[end_at]": back})):
            rows += pandascore.get_all(path, ttl=24 * 3600, pages=TEAM_PAGES, **which, **window)
        return parse_teams(rows, self._on())

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
        read: list[dict[str, Any]] = []

        def series(ttl: float) -> Snapshot:  # the request match() makes for its page, too
            data = pandascore.get_json(f"/matches/{match_id}", ttl=ttl)
            if not isinstance(data, dict) or not data.get("id"):
                raise AdapterError(f"PandaScore has no match {match_id}")
            read[:] = self._keep([data])
            return parse_match(data)

        snap = self.read_snapshot(series, final, 30)
        if not final:
            self._dota(read[0], snap)
        return snap

    @staticmethod
    def _known_maps(series: str, n: int, game: dict[str, Any] | None) -> dict[str, int]:
        """The series' maps seen live so far (map number -> OpenDota match), kept in Redis for the worker and
        the API alike, with the live game added as map n (dota.remember)."""
        key = f"dota:series:{series}"
        try:
            r = bus.sync_redis()
            known = json.loads(r.get(key) or "{}")
            if game:
                updated = dota.remember(known, n, int(game["match_id"]))
                if updated != known:
                    r.set(key, json.dumps(updated), ex=3 * 86400)
                known = updated
            return known
        except Exception:  # Redis down: no memory, the fallback still works
            return {}

    @staticmethod
    def _lead_line(game: dict[str, Any]) -> list[list[int]]:
        """The live map's gold line so far, with this reading (dota.add_reading), kept in Redis for the worker
        and the API alike: each one that reads the live list adds to it."""
        key = f"dota:lead:{game.get('match_id')}"
        try:
            r = bus.sync_redis()
            line = json.loads(cast(str | None, r.get(key)) or "[]")
            updated = dota.add_reading(line, game.get("game_time"), game.get("radiant_lead"))
            if updated is not line:
                r.set(key, json.dumps(updated), ex=86400)
            return updated
        except Exception:  # Redis down: no line, the rest still shows
            return []

    def _dota(self, m: dict[str, Any], snap: Snapshot, detail: bool = False) -> bool:
        """A Dota 2 series on now (or just over) gains what OpenDota knows inside its maps: the map being
        played, and each finished map's numbers for both rosters' players. Anything OpenDota can't say
        leaves the series as PandaScore has it. Returns whether that's all there will be: False while a
        finished map's numbers are missing (OpenDota down, or the map not parsed yet).

        With detail (the match page's own read, not a poll, whose every state is stored and sent to each
        card), each finished map's part of the page as well (dota.map_detail), and the live map's (its gold
        line so far, buildings and ten heroes: dota.live_detail), as the state's "page_detail": the same
        OpenDota matches and live list, so no more requests but the item names, kept a week. Every read of a
        live map adds to its gold line, a poll's too."""
        if not opendota.ENABLED or (m.get("videogame") or {}).get("slug") != "dota-2" or snap.status not in ("live", "final"):
            return True
        teams = snap.state.get("teams") or []
        if len(teams) != 2:
            return True
        try:
            roster = [p for t in teams for p in (pandascore.get_json(f"/teams/{t['id']}", ttl=24 * 3600) or {}).get("players") or []]
            finished = [g for g in snap.state.get("maps") or [] if g.get("winner")]
            over = opendota.pro_matches()
            game = dota.find_live(opendota.live(), teams) if snap.status == "live" else None
            if game and int(game.get("match_id") or 0) in {int(x.get("match_id") or 0) for x in over}:
                game = None  # finished: the live list lags a little
            players = dota.accounts(roster, [t["name"] for t in teams], opendota.pro_players(), (game or {}).get("players") or [])
            heroes = opendota.heroes()
            known = self._known_maps(str(m.get("id")), len(finished) + 1, game)
            numbered = sorted((int(n), mid) for n, mid in known.items() if int(n) <= len(finished))
            if len(numbered) < len(finished):
                # Maps not seen live (the worker started, or the page was first opened, mid-series): OpenDota's last
                # hundred pro matches.
                begin = _time(m.get("begin_at") or m.get("scheduled_at"))
                numbered = dota.fill_maps(numbered, dota.find_maps(over, teams, int(begin.timestamp()) if begin else 0), len(finished))
            names = {str(p["id"]): p.get("name") or "" for p in roster if p.get("id") is not None}
            items = opendota.items() if detail else {}
            maps, parts, parsed = [], [], 0
            for n, match_id in numbered if players else []:
                details = opendota.match(match_id)
                if details:
                    maps.append({"n": n, "match_id": match_id, "players": dota.map_stats(details, players, heroes)})
                    parsed += opendota.parsed(details)
                    part = dota.map_detail(details, n, teams, players, names, heroes, items) if detail else None
                    if part:
                        parts.append(part)
            live = dota.live_map(game, teams, players, heroes, len(finished) + 1) if game else None
            line = self._lead_line(game) if game else []
            now = dota.live_detail(game, teams, players, names, heroes, len(finished) + 1, line) if game and detail else None
        except AdapterError as exc:
            log.info("dota %s: no OpenDota data: %s", m.get("id"), exc)
            return False
        snap.state["dota"] = {"maps": maps, "live": live}
        if parts or now:
            snap.state["page_detail"] = {"teams": [{"id": t["id"], "name": t["name"], "short": dota.short(t["name"])} for t in teams], "maps": parts, "live": now}
        snap.lines.update(dota.lines(maps, live, final=snap.status == "final"))
        if live:
            snap.clock_label = f"Map {live['n']} · {live['clock']}"
        return parsed >= len(finished)

    def live_now(self) -> list[LiveGame]:
        """The big series on now in the plan's games (matches/running, one request): those of a tournament in
        LIVE_TIERS, the biggest tier first and of equals in PandaScore's order; in a game with none of those on,
        its STAND_IN_TIER series. Its teams, not its players: a match names no players, and the registry knows
        the teams' pros."""
        games = game_ids(self._on())
        if not games:
            return []
        # Matches filter by game as "videogame" (ids or slugs); "videogame_id" is the teams' and players' name for it.
        # It can't filter by tier, so the big series are picked from a page of PandaScore's most.
        rows = pandascore.get_json("/matches/running", ttl=60, **{"filter[videogame]": games, "page[size]": pandascore.PAGE})
        rows = rows if isinstance(rows, list) else []
        by_id = {str(m.get("id")): m for m in rows}
        out, stand_ins = [], []
        for f in parse_fixtures(rows):
            m = by_id[f.source_id]
            rated = (m.get("tournament") or {}).get("tier") or ""  # "unranked", or none yet
            snap = parse_match(m)
            if snap.status == "live" and (rated in LIVE_TIERS or rated == STAND_IN_TIER):
                game = LiveGame(source_id=f.source_id, title=f.title, competition=f.competition, start_utc=f.start_utc, snapshot=snap,
                                teams=f.team_names, league=(m.get("videogame") or {}).get("slug"), tier=LIVE_TIERS.get(rated, 0), sport=f.sport)
                (out if game.tier else stand_ins).append(game)
        big = {g.sport for g in out}
        out += [g for g in stand_ins if g.sport not in big]
        return sorted(out, key=lambda g: -g.tier)

    def match(self, source_id: str) -> LiveGame | None:
        """One series by its id, whoever follows it, for its match page: map by map, and inside a Dota 2 map what
        OpenDota knows; its teams, not its players (match.py offers the pros the registry knows); where it's streamed
        (its fixture's streams). One request; a
        Dota 2 series on now or just over costs what a live poll's does besides (_dota): its two teams' rosters
        (kept a day) and OpenDota's live games, pro matches and each finished map, most of them kept a while."""
        if not self._on():
            return None  # none of its games runs: not a request
        try:
            m = pandascore.get_json(f"/matches/{source_id}", ttl=30)
        except pandascore.NotFound:
            return None
        found = parse_fixtures(self._keep([m])) if isinstance(m, dict) and m.get("id") else []
        if not found or (m.get("videogame") or {}).get("slug") not in self._on():
            return None  # a game turned off (or one it doesn't cover) has no page, as its series aren't polled
        f, snap = found[0], parse_match(m)
        whole = self._dota(m, snap, detail=True)
        # A finished Dota 2 series whose maps OpenDota hasn't all got yet is read again, for a while: past
        # DOTA_WAIT after it ended, a map OpenDota hasn't got isn't coming.
        ended = _time(m.get("end_at"))
        partial = snap.status == "final" and not whole and (ended is None or datetime.now(timezone.utc) - ended < timedelta(seconds=DOTA_WAIT))
        return LiveGame(source_id=f.source_id, title=f.title, competition=f.competition, start_utc=f.start_utc, snapshot=snap, teams=f.team_names,
                        league=(m.get("videogame") or {}).get("slug"), tier=LIVE_TIERS.get((m.get("tournament") or {}).get("tier") or "", 0), sport=f.sport,
                        partial=partial, streams=f.streams)

    def schedule(self, start: date, end: date) -> list[Fixture]:
        """The series of the plan's games' big events (SCHEDULE_TIERS) between two days, each with its
        event's tier. PandaScore lists series by tournament but can't filter them by tier, and only about
        one in eight is S or A, so the S and A tournaments come first (on now, starting in the window, ended
        in it: a request each), then their series in the window, a page of 100 a request."""
        span = f"{start.isoformat()}T00:00:00Z,{end.isoformat()}T23:59:59Z"
        games = game_ids(self._on())
        if not games:
            return []
        # Tournaments filter by game as "videogame_id", and by tier.
        which = {"filter[videogame_id]": games, "filter[tier]": ",".join(SCHEDULE_TIERS), "page[size]": pandascore.PAGE}
        tiers: dict[str, int] = {}
        for path, window in (("/tournaments/running", {}), ("/tournaments/upcoming", {"range[begin_at]": span}), ("/tournaments/past", {"range[end_at]": span})):
            found = pandascore.get_json(path, ttl=1800, **which, **window)
            for t in found if isinstance(found, list) else []:
                if t.get("id") and t.get("tier") in SCHEDULE_TIERS:
                    tiers[str(t["id"])] = LIVE_TIERS[t["tier"]]
        if not tiers:
            return []
        rows = pandascore.get_all("/matches", ttl=1800, pages=SCHEDULE_PAGES, **{"filter[tournament_id]": ",".join(sorted(tiers)), "range[scheduled_at]": span, "sort": "scheduled_at"})
        tier_of = {str(m.get("id")): tiers.get(str(m.get("tournament_id") or (m.get("tournament") or {}).get("id")), 0) for m in rows}
        out = []
        for f in parse_fixtures(self._keep(rows)):
            f.tier = tier_of.get(f.source_id, 0)
            out.append(f)
        return out

    def locator(self, source_id: str) -> dict[str, Any] | None:
        """A series polled by its id alone, as a followed player's fixture of it is (matches.py)."""
        return {"match": source_id} if self.fixture_pattern.fullmatch(source_id) else None

    def snapshots(self, locators: dict[Any, dict[str, Any]]) -> dict[Any, Snapshot]:
        """Many series at once, MULTI_MAX to a request: the live lane's one fetch a round. Keyed as
        given; a match PandaScore doesn't return is left out."""
        wanted = {key: str(loc["match"]) for key, loc in locators.items()}
        ids = sorted(set(wanted.values()))
        found: dict[str, Snapshot] = {}
        for i in range(0, len(ids), self.MULTI_MAX):
            rows = pandascore.get_json("/matches", ttl=0, **{"filter[id]": ",".join(ids[i:i + self.MULTI_MAX]), "page[size]": self.MULTI_MAX})
            for m in self._keep(rows if isinstance(rows, list) else []):
                found[str(m.get("id"))] = snap = parse_match(m)
                self._dota(m, snap)
        return {key: found[mid] for key, mid in wanted.items() if mid in found}

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        return result_label(fixture, team_ids)

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        """No player numbers on the free plan: the team's series and maps over the refresh window (the
        list fixtures() just read, so no new request)."""
        finals = [f for f in self.fixtures(ref) if f.status == "final"] if ref.team_ids else []
        return team_form(finals, ref.team_ids, (ref.team_names or [None])[0])
