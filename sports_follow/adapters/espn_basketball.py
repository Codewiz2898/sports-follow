"""Basketball from ESPN: NBA, WNBA and US college. Team schedules, live box scores, season averages."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import espn
from .base import AdapterError, Fixture, Line, PlayerRef, Snapshot

SPORT_UID = "s:40~"
# ESPN's league ids (from search uids) -> the slug its URLs use.
LEAGUES = {"46": "nba", "59": "wnba", "41": "mens-college-basketball", "54": "womens-college-basketball"}

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


def result_label(fixture: Fixture, team_ids: list[str]) -> str:
    r = fixture.result
    if not r:
        return ""
    home, away = r["home"], r["away"]
    ours, theirs = (home, away) if home["id"] in team_ids else (away, home) if away["id"] in team_ids else (None, None)
    if ours is None:
        return f"{away['name']} {away['score']}–{home['score']} {home['name']}"
    return f"{'Won' if ours.get('winner') else 'Lost'} {ours['score']}–{theirs['score']}"


# ---------------------------------------------------------------- the adapter


class EspnBasketball:
    system = "espn_basketball"
    sports = ("basketball",)
    live_cadence = 10

    def find_player(self, name: str) -> PlayerRef | None:
        for hit in espn.search_athletes(name, SPORT_UID):
            league = LEAGUES.get(hit.get("league_id") or "")
            if league is None or not espn.names_match(name, hit["name"]):
                continue
            athlete = espn.get_json(f"{espn.WEB}/common/v3/sports/basketball/{league}/athletes/{hit['athlete_id']}", ttl=6 * 3600).get("athlete") or {}
            team = athlete.get("team") or {}
            team_ids = [str(team["id"])] if team.get("id") else []
            team_names = [team.get("displayName") or ""] if team_ids else []
            return PlayerRef(self.system, hit["athlete_id"], athlete.get("displayName") or hit["name"], team_ids, team_names, hit.get("url"), league=league)
        return None

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        league = ref.league or "nba"
        seen: dict[str, Fixture] = {}
        for team_id in ref.team_ids:
            # The plain schedule is the current phase only (preseason in October); ask for each phase.
            for suffix in ("", "?seasontype=2", "?seasontype=3"):
                try:
                    data = espn.get_json(f"{espn.SITE}/basketball/{league}/teams/{team_id}/schedule{suffix}", ttl=1800)
                except AdapterError:
                    continue
                for f in parse_schedule(data, league):
                    seen.setdefault(f.source_id, f)
        return sorted(seen.values(), key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        data = espn.get_json(f"{espn.SITE}/basketball/{locator['league']}/summary?event={locator['event']}", ttl=24 * 3600 if final else 5)
        return parse_summary(data)

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        return result_label(fixture, team_ids)

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        data = espn.get_json(f"{espn.WEB}/common/v3/sports/basketball/{ref.league or 'nba'}/athletes/{ref.athlete_id}/stats", ttl=6 * 3600)
        return parse_season_stats(data)
