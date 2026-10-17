"""Basketball from ESPN: NBA, WNBA and US college. Team schedules, live box scores, season averages."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from .. import naming
from . import espn
from .base import Adapter, AdapterError, Fixture, Line, PlayerRef, Snapshot, SourceTeam

SPORT_UID = "s:40~"
# ESPN's league ids (from search uids) -> the slug its URLs use.
LEAGUES = {"46": "nba", "59": "wnba", "41": "mens-college-basketball", "54": "womens-college-basketball"}
# The leagues whose teams fans can follow (teams): the pros. College basketball's are hundreds.
TEAM_LEAGUES = ("nba", "wnba")
# ESPN numbers each league's teams apart (the Cavaliers and the Fever are both 5), so a team's id here carries its
# league: "nba:5", "wnba:5". A team's games name both sides so (team_fixtures); a player's games, every result and
# every board keep ESPN's own numbers.
TEAM_ID = re.compile(rf"(?:{'|'.join(TEAM_LEAGUES)}):[0-9]{{1,12}}")
# A team's schedule comes a phase at a time, the plain one being the phase on now (preseason in October).
_PHASES = ("", "?seasontype=2", "?seasontype=3")

_STATUS = {"pre": "scheduled", "in": "live", "post": "final"}

# Box-score columns shown under the headline (the headline already says points, rebounds, assists).
_LINE_STATS = [("MIN", "Minutes"), ("FG", "FG"), ("3PT", "3PT"), ("FT", "FT"), ("STL", "Steals"), ("BLK", "Blocks"), ("TO", "Turnovers"), ("+/-", "+/-")]
# Season-average columns -> labels.
_SEASON_STATS = [("PTS", "Points"), ("REB", "Rebounds"), ("AST", "Assists"), ("FG%", "FG%"), ("3P%", "3P%"), ("MIN", "Minutes"), ("GP", "Games")]


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _status(status: dict[str, Any]) -> str:
    kind = status.get("type") or {}
    name = kind.get("name") or ""
    if "POSTPONED" in name or "CANCELED" in name:
        return "postponed"
    return _STATUS.get(kind.get("state"), "scheduled")


def _score(c: dict[str, Any]) -> str:
    score = c.get("score")
    if isinstance(score, dict):
        return str(score.get("displayValue") or "")
    return str(score or "")


def _sides(competitors: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[-1])
    return home, away


# ---------------------------------------------------------------- parsers (pure)


def parse_schedule(data: dict[str, Any], league: str) -> list[Fixture]:
    """A team schedule (…/basketball/{league}/teams/{id}/schedule) -> fixtures. Titles read "Away at Home"."""
    out = []
    for event in data.get("events", []):
        comp = (event.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        if len(competitors) < 2:
            continue
        home, away = _sides(competitors)
        status = _status(comp.get("status") or {})
        result = None
        if status == "final":
            result = {side: {"id": str(c["team"]["id"]), "name": c["team"]["displayName"], "score": _score(c), "winner": c.get("winner")} for side, c in (("home", home), ("away", away))}
        season_type = (event.get("seasonType") or {}).get("name")
        note = next((n.get("headline") for n in comp.get("notes") or [] if n.get("headline")), None)
        out.append(
            Fixture(
                source_id=str(event["id"]),
                title=f"{away['team']['displayName']} at {home['team']['displayName']}",
                competition=f"{league.upper() if len(league) <= 4 else 'College basketball'}{f' {season_type}' if season_type and season_type != 'Regular Season' else ''}",
                start_utc=_time(event.get("date")),
                venue=(comp.get("venue") or {}).get("fullName"),
                status=status,
                team_ids=[str(away["team"]["id"]), str(home["team"]["id"])],
                team_names=[away["team"]["displayName"], home["team"]["displayName"]],
                locator={"league": league, "event": str(event["id"])},
                detail=note,
                result=result,
            )
        )
    return out


def parse_summary(data: dict[str, Any]) -> Snapshot:
    """A game summary (…/basketball/{league}/summary?event=) -> snapshot with every player's box-score line."""
    header = data["header"]
    comp = header["competitions"][0]
    home, away = _sides(comp["competitors"])
    status_obj = comp.get("status") or {}
    status = _status(status_obj)
    kind = status_obj.get("type") or {}

    def side(c: dict[str, Any]) -> dict[str, Any]:
        team = c.get("team") or {}
        return {
            "id": str(team.get("id")),
            "name": team.get("displayName"),
            "abbr": team.get("abbreviation"),
            "score": _score(c),
            "periods": [str(ls.get("displayValue", "")) for ls in c.get("linescores") or []],
            "winner": c.get("winner"),
        }

    state = {
        "kind": "basketball",
        "home": side(home),
        "away": side(away),
        "period": status_obj.get("period"),
        "clock": status_obj.get("displayClock"),
        "detail": kind.get("shortDetail"),
        "note": header.get("gameNote"),
    }

    lines: dict[str, Line] = {}
    for team in (data.get("boxscore") or {}).get("players") or []:
        for table in team.get("statistics") or []:
            labels = table.get("labels") or table.get("names") or []
            for entry in table.get("athletes") or []:
                athlete_id = str((entry.get("athlete") or {}).get("id"))
                values = dict(zip(labels, entry.get("stats") or []))
                if entry.get("didNotPlay") or not values:
                    reason = (entry.get("reason") or "").strip().capitalize()
                    lines[athlete_id] = Line(headline=f"Did not play{f' ({reason.lower()})' if reason else ''}", stats=[])
                    continue
                headline = f"{values.get('PTS', '0')} PTS · {values.get('REB', '0')} REB · {values.get('AST', '0')} AST"
                stats = [{"label": label, "value": str(values[key])} for key, label in _LINE_STATS if values.get(key) not in (None, "")]
                lines[athlete_id] = Line(headline=headline, stats=stats)

    detail = kind.get("shortDetail") or ""
    return Snapshot(
        status=status,
        score_label=f"{away['team']['displayName']} {_score(away)} – {_score(home)} {home['team']['displayName']}",
        clock_label=detail if status != "scheduled" else (kind.get("detail") or detail),
        state=state,
        lines=lines,
        names={str(a["id"]): a["displayName"] for team in (data.get("boxscore") or {}).get("players") or [] for table in team.get("statistics") or []
               for entry in table.get("athletes") or [] if (a := entry.get("athlete") or {}).get("id") and a.get("displayName")},
        source_url=next((l.get("href") for l in header.get("links") or [] if "summary" in (l.get("rel") or []) or "boxscore" in (l.get("rel") or [])), None)
        or f"https://www.espn.com/{'wnba' if 'wnba' in str(header.get('uid')) else 'nba'}/game/_/gameId/{header.get('id')}",
    )


def parse_season_stats(data: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    """Athlete stats (…/athletes/{id}/stats) -> the latest season's per-game averages."""
    category = next((c for c in data.get("categories") or [] if c.get("name") == "averages"), None)
    if not category or not category.get("statistics"):
        return [], ""
    labels = category.get("labels") or []
    row = category["statistics"][-1]
    values = dict(zip(labels, row.get("stats") or []))
    stats = [{"label": label, "value": str(values[key])} for key, label in _SEASON_STATS if values.get(key) not in (None, "")]
    season = (row.get("season") or {}).get("displayName") or ""
    kind = (category.get("displayName") or "Averages").replace(" Averages", "").lower()
    return stats, f"{season} {kind} averages per game".strip()


def parse_teams(data: dict[str, Any], league: str, system: str = "espn_basketball") -> list[SourceTeam]:
    """A league's teams (…/basketball/{league}/teams) -> the team registry's rows of them, each by an id that carries
    its league (TEAM_ID). An all-star side, or one that no longer plays, isn't a team to follow."""
    out = []
    for sport in data.get("sports") or []:
        for listed in sport.get("leagues") or []:
            for entry in listed.get("teams") or []:
                t = entry.get("team") or {}
                name = (t.get("displayName") or "").strip()
                if t.get("id") and name and not t.get("isAllStar") and t.get("isActive") is not False:
                    out.append(SourceTeam(system=system, team_id=f"{league}:{t['id']}", name=name, short=t.get("abbreviation") or "", competition=league.upper()))
    return out


def _born(athlete: dict[str, Any]) -> str | None:
    """A roster's birth date (ISO, at midnight on the US west coast: "1984-12-30T08:00Z"), or an athlete record's."""
    day = str(athlete.get("dateOfBirth") or "")[:10]
    return day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else espn.birth_date(athlete)


def parse_roster(data: dict[str, Any], league: str, system: str = "espn_basketball") -> SourceTeam | None:
    """A team's roster (…/basketball/{league}/teams/{id}/roster) -> the team (its id as TEAM_ID has it) with its
    players, each followable by their ESPN id in that league, as a search result is. None without the team."""
    t = data.get("team") or {}
    name = (t.get("displayName") or "").strip()
    if not t.get("id") or not name:
        return None
    number = str(t["id"])
    players: dict[str, PlayerRef] = {}
    for listed in data.get("athletes") or []:
        for a in listed.get("items") or [listed]:  # flat for basketball; grouped by position where a sport's are
            who = (a.get("displayName") or a.get("fullName") or "").strip()
            if a.get("id") and who:
                players.setdefault(str(a["id"]), PlayerRef(system, str(a["id"]), who, [number], [name], espn.profile_link(a), league=league, born=_born(a)))
    return SourceTeam(system=system, team_id=f"{league}:{number}", name=name, players=list(players.values()), short=t.get("abbreviation") or "", competition=league.upper())


def result_label(fixture: Fixture, team_ids: list[str]) -> str:
    """ "Won 101–99" / "Lost 85–102" from the player's team's side, else the plain score. A followed team's id
    carries its league (TEAM_ID, "wnba:5"): ESPN's number in it is the side's."""
    r = fixture.result
    if not r:
        return ""
    home, away = r["home"], r["away"]
    numbers = [t.rpartition(":")[2] for t in team_ids]
    ours, theirs = (home, away) if home["id"] in numbers else (away, home) if away["id"] in numbers else (None, None)
    if ours is None:
        return f"{away['name']} {away['score']}–{home['score']} {home['name']}"
    return f"{'Won' if ours.get('winner') else 'Lost'} {ours['score']}–{theirs['score']}"


# ---------------------------------------------------------------- the adapter


class EspnBasketball(espn.EspnAdapter, Adapter):
    system = "espn_basketball"
    sports = ("basketball",)
    live_cadence = 10
    name = "ESPN"
    covers = "Basketball"
    espn_uid = SPORT_UID
    espn_leagues = LEAGUES
    research_leagues = ("basketball/nba",)

    def find_player(self, name: str) -> PlayerRef | None:
        for hit in espn.search_athletes(name, SPORT_UID):
            league = LEAGUES.get(hit.get("league_id") or "")
            if league is not None and naming.names_match(name, hit["name"]):
                return self.player(hit["athlete_id"], league)
        return None

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        """By id; without a league, the pro leagues are tried in turn (ids are unique across them)."""
        for slug in [league] if league else ["nba", "wnba"]:
            try:
                athlete = espn.get_json(f"{espn.WEB}/common/v3/sports/basketball/{slug}/athletes/{athlete_id}", ttl=6 * 3600).get("athlete") or {}
            except AdapterError:
                continue
            if not athlete.get("id"):
                continue
            team = athlete.get("team") or {}
            team_ids = [str(team["id"])] if team.get("id") else []
            team_names = [team.get("displayName") or ""] if team_ids else []
            return PlayerRef(self.system, str(athlete["id"]), athlete.get("displayName") or "", team_ids, team_names, espn.profile_link(athlete), league=slug, born=espn.birth_date(athlete))
        return None

    def _schedule(self, league: str, team_id: str) -> list[Fixture]:
        """A team's games this season (by ESPN's number for it), every phase (_PHASES): a request a phase, kept half
        an hour. A phase that can't be read is left out; none read at all is a failure."""
        seen: dict[str, Fixture] = {}
        failed: AdapterError | None = None
        read = 0
        for suffix in _PHASES:
            try:
                data = espn.get_json(f"{espn.SITE}/basketball/{league}/teams/{team_id}/schedule{suffix}", ttl=1800)
            except AdapterError as exc:
                failed = exc
                continue
            read += 1
            for f in parse_schedule(data, league):
                seen.setdefault(f.source_id, f)
        if failed and not read:
            raise failed
        return list(seen.values())

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        league = ref.league or "nba"
        seen: dict[str, Fixture] = {}
        for team_id in ref.team_ids:
            try:
                found = self._schedule(league, team_id)
            except AdapterError:
                continue
            for f in found:
                seen.setdefault(f.source_id, f)
        return sorted(seen.values(), key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    team_id_pattern = TEAM_ID

    def teams(self) -> list[SourceTeam]:
        """Every NBA and WNBA team (TEAM_LEAGUES), for the team registry: a request a league, kept a day."""
        return [t for league in TEAM_LEAGUES for t in parse_teams(espn.get_json(f"{espn.SITE}/basketball/{league}/teams", ttl=24 * 3600), league, self.system)]

    def team_fixtures(self, team_id: str) -> list[Fixture]:
        """One team's games this season (its id as TEAM_ID has it: "nba:13"), every phase, as fixtures() reads a
        player's: three requests, shared with them. Each game names its sides as teams are named here ("nba:2"
        for the Celtics), so it says which is the team; its result and its board keep ESPN's numbers, which
        result_label reads either way."""
        if not TEAM_ID.fullmatch(team_id):
            raise AdapterError(f"{team_id!r} isn't an ESPN basketball team id (league:number)")
        league, _, number = team_id.partition(":")
        found = self._schedule(league, number)
        for f in found:
            f.team_ids = [f"{league}:{t}" for t in f.team_ids]
        return sorted(found, key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def board_id(self, team_id: str) -> str:
        """A team's id (TEAM_ID) as its games' boards have it: ESPN's number."""
        return team_id.rpartition(":")[2]

    def team(self, team_id: str) -> SourceTeam | None:
        """A team (TEAM_ID) and its players now, for its page: its roster, one request kept 6 hours."""
        if not TEAM_ID.fullmatch(team_id):
            return None
        league, _, number = team_id.partition(":")
        return parse_roster(espn.get_json(f"{espn.SITE}/basketball/{league}/teams/{number}/roster", ttl=6 * 3600), league, self.system)

    def history_pages(self, ref: PlayerRef, anchor: date) -> list[dict[str, Any]]:
        """Last season's regular season and playoffs for each of the player's teams."""
        league = ref.league or "nba"
        pages = []
        for team_id in ref.team_ids:
            try:
                year = (espn.get_json(f"{espn.SITE}/basketball/{league}/teams/{team_id}/schedule", ttl=1800).get("season") or {}).get("year")
            except AdapterError:
                continue
            if year:
                pages += [{"team": team_id, "season": int(year) - 1, "type": kind} for kind in (2, 3)]
        return pages

    def history(self, ref: PlayerRef, page: dict[str, Any]) -> list[Fixture]:
        league = ref.league or "nba"
        data = espn.get_json(f"{espn.SITE}/basketball/{league}/teams/{page['team']}/schedule?season={page['season']}&seasontype={page['type']}", ttl=24 * 3600)
        return [f for f in parse_schedule(data, league) if f.status == "final"]

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        data = espn.get_json(f"{espn.SITE}/basketball/{locator['league']}/summary?event={locator['event']}", ttl=24 * 3600 if final else 5)
        return parse_summary(data)

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        return result_label(fixture, team_ids)

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        data = espn.get_json(f"{espn.WEB}/common/v3/sports/basketball/{ref.league or 'nba'}/athletes/{ref.athlete_id}/stats", ttl=6 * 3600)
        return parse_season_stats(data)
