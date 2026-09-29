"""Football from ESPN: club (and national team) schedules, live match summaries, season stats."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import espn
from .base import AdapterError, Fixture, Line, PlayerRef, Snapshot

SPORT_UID = "s:600~"

_STATUS = {"pre": "scheduled", "in": "live", "post": "final"}

# Match stats in the order a fan reads them; ESPN names -> labels.
_LINE_STATS = [
    ("totalGoals", "Goals"),
    ("goalAssists", "Assists"),
    ("totalShots", "Shots"),
    ("shotsOnTarget", "On target"),
    ("foulsCommitted", "Fouls"),
    ("offsides", "Offsides"),
    ("yellowCards", "Yellow"),
    ("redCards", "Red"),
]

# Season-stats columns (athlete stats endpoint) -> labels.
_SEASON_STATS = [("G", "Goals"), ("A", "Assists"), ("STRT", "Starts"), ("SHOT", "Shots"), ("SOG", "On target"), ("YC", "Yellow cards")]


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _status(status: dict[str, Any]) -> str:
    kind = status.get("type") or {}
    if "POSTPONED" in (kind.get("name") or "") or "CANCELED" in (kind.get("name") or ""):
        return "postponed"
    return _STATUS.get(kind.get("state"), "scheduled")


def _home_away(competitors: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[-1])
    return home, away


def _score(c: dict[str, Any]) -> str:
    score = c.get("score")
    if isinstance(score, dict):
        return str(score.get("displayValue") or score.get("value") or "")
    return str(score or "")


# ---------------------------------------------------------------- parsers (pure)


def parse_schedule(data: dict[str, Any]) -> list[Fixture]:
    """A team schedule (…/teams/{id}/schedule) -> fixtures."""
    out: list[Fixture] = []
    for event in data.get("events", []):
        comp = (event.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        if len(competitors) < 2:
            continue
        home, away = _home_away(competitors)
        league = event.get("league") or comp.get("league") or {}
        status = _status(comp.get("status") or event.get("status") or {})
        result = None
        if status == "final":
            result = {
                "home": {"id": home["team"]["id"], "name": home["team"]["displayName"], "score": _score(home), "winner": home.get("winner")},
                "away": {"id": away["team"]["id"], "name": away["team"]["displayName"], "score": _score(away), "winner": away.get("winner")},
            }
        out.append(
            Fixture(
                source_id=str(event["id"]),
                title=f"{home['team']['displayName']} vs {away['team']['displayName']}",
                competition=league.get("name") or league.get("abbreviation") or "",
                start_utc=_time(event.get("date")),
                venue=((comp.get("venue") or {}).get("fullName")),
                status=status,
                team_ids=[str(home["team"]["id"]), str(away["team"]["id"])],
                team_names=[home["team"]["displayName"], away["team"]["displayName"]],
                locator={"league": league.get("slug") or "all", "event": str(event["id"])},
                detail=(comp.get("notes") or [{}])[0].get("headline") if comp.get("notes") else None,
                result=result,
            )
        )
    return out


def parse_summary(data: dict[str, Any], league: str) -> Snapshot:
    """A match summary (…/soccer/{league}/summary?event=) -> snapshot."""
    header = data["header"]
    comp = header["competitions"][0]
    home, away = _home_away(comp["competitors"])
    status = _status(comp.get("status") or {})
    kind = (comp.get("status") or {}).get("type") or {}
    clock = (comp.get("status") or {}).get("displayClock")
    clock_label = kind.get("shortDetail") or clock or ""
    if status == "live" and clock and kind.get("shortDetail") in (None, "", "In Progress"):
        clock_label = clock

    events = data.get("keyEvents") or []
    goals = [e for e in events if e.get("scoringPlay")]

    def scorers(team_id: str) -> list[str]:
        out = []
        for g in goals:
            if str((g.get("team") or {}).get("id")) != team_id:
                continue
            who = ((g.get("participants") or [{}])[0].get("athlete") or {}).get("displayName") or "Goal"
            out.append(f"{who} {(g.get('clock') or {}).get('displayValue', '')}".strip())
        return out

    state = {
        "kind": "football",
        "home": {"id": str(home["team"]["id"]), "name": home["team"]["displayName"], "abbr": home["team"].get("abbreviation"), "score": _score(home), "scorers": scorers(str(home["team"]["id"]))},
        "away": {"id": str(away["team"]["id"]), "name": away["team"]["displayName"], "abbr": away["team"].get("abbreviation"), "score": _score(away), "scorers": scorers(str(away["team"]["id"]))},
        "detail": kind.get("description"),
    }

    lines: dict[str, Line] = {}
    for roster in data.get("rosters") or []:
        for entry in roster.get("roster") or []:
            athlete = entry.get("athlete") or {}
            stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats") or []}
            if not stats:
                continue
            shown = [{"label": label, "value": str(stats[name])} for name, label in _LINE_STATS if stats.get(name) not in (None, "")]
            goals_n = int(stats.get("totalGoals") or 0)
            assists_n = int(stats.get("goalAssists") or 0)
            if entry.get("starter"):
                role = "Started"
            elif entry.get("subbedIn"):
                role = "Came on"
            else:
                # An unused substitute's zeros aren't stats.
                lines[str(athlete.get("id"))] = Line(headline="On the bench", stats=[])
                continue
            bits = [f"{goals_n} goal{'s' if goals_n != 1 else ''}" if goals_n else None, f"{assists_n} assist{'s' if assists_n != 1 else ''}" if assists_n else None]
            headline = ", ".join(b for b in bits if b) or role
            lines[str(athlete.get("id"))] = Line(headline=headline, stats=shown)

    moments = []
    for e in reversed(events):
        kind_e = (e.get("type") or {}).get("type") or ""
        if kind_e not in ("goal", "yellow-card", "red-card", "own-goal", "penalty---scored"):
            continue
        moments.append({
            "clock": (e.get("clock") or {}).get("displayValue", ""),
            "kind": "GOAL" if e.get("scoringPlay") else ("RED" if "red" in kind_e else "YELLOW"),
            "text": e.get("text") or e.get("shortText") or "",
            "athlete_ids": [str(((p.get("athlete") or {}).get("id"))) for p in e.get("participants") or []],
        })

    return Snapshot(
        status=status,
        score_label=f"{home['team']['displayName']} {_score(home)} – {_score(away)} {away['team']['displayName']}",
        clock_label=clock_label,
        state=state,
        lines=lines,
        moments=moments[:12],
        source_url=f"https://www.espn.com/soccer/match/_/gameId/{header.get('id')}",
    )


def parse_season_stats(data: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    """Athlete stats (…/athletes/{id}/stats) -> this season's numbers, summed across competitions."""
    categories = data.get("categories") or []
    if not categories:
        return [], ""
    cat = categories[0]
    names = cat.get("names") or cat.get("labels") or []
    rows = cat.get("statistics") or []
    if not rows:
        return [], ""
    latest = max((r.get("season") or {}).get("year") or 0 for r in rows)
    current = [r for r in rows if ((r.get("season") or {}).get("year") or 0) == latest]
    totals: dict[str, float] = {}
    for row in current:
        for name, value in zip(names, row.get("stats") or []):
            try:
                totals[name] = totals.get(name, 0) + float(str(value).replace(",", ""))
            except ValueError:
                pass
    stats = [{"label": label, "value": f"{int(totals[name])}"} for name, label in _SEASON_STATS if name in totals]
    leagues = sorted({(r.get("season") or {}).get("type", {}).get("name") or r.get("leagueSlug") or "" for r in current} - {""})
    note = f"{latest}–{str(latest + 1)[-2:]} season, all competitions ESPN tracks" if len(leagues) != 1 else f"{leagues[0]}"
    return stats, note


def result_label(fixture: Fixture, team_ids: list[str]) -> str:
    """ "Won 2–1" / "Lost 0–1" / "Drew 1–1" from the player's team's side, else the plain score."""
    r = fixture.result
    if not r:
        return ""
    home, away = r["home"], r["away"]
    ours, theirs = (home, away) if str(home["id"]) in team_ids else (away, home) if str(away["id"]) in team_ids else (None, None)
    if ours is None:
        return f"{home['name']} {home['score']}–{away['score']} {away['name']}"
    score = f"{ours['score']}–{theirs['score']}"
    if ours.get("winner"):
        return f"Won {score}"
    if theirs.get("winner"):
        return f"Lost {score}"
    return f"Drew {score}"


# ---------------------------------------------------------------- the adapter


class EspnSoccer:
    system = "espn_soccer"
    sports = ("football", "soccer", "association football")
    live_cadence = 10

    def find_player(self, name: str) -> PlayerRef | None:
        for hit in espn.search_athletes(name, SPORT_UID):
            if not espn.names_match(name, hit["name"]):
                continue
            athlete = espn.get_json(f"{espn.WEB}/common/v3/sports/soccer/athletes/{hit['athlete_id']}", ttl=6 * 3600).get("athlete") or {}
            team_ids, team_names = [], []
            club = athlete.get("team") or {}
            if club.get("id"):
                team_ids.append(str(club["id"]))
                team_names.append(club.get("displayName") or "")
            country = athlete.get("citizenshipCountry") or {}
            national = self._national_team(country.get("abbreviation"), athlete.get("citizenship"))
            if national and national[0] not in team_ids:
                team_ids.append(national[0])
                team_names.append(national[1])
            return PlayerRef(self.system, hit["athlete_id"], athlete.get("displayName") or hit["name"], team_ids, team_names, hit.get("url"))
        return None

    def _national_team(self, abbreviation: str | None, country: str | None) -> tuple[str, str] | None:
        """Find a men's national team by country name via search; None when it isn't unambiguous."""
        if not country:
            return None
        data = espn.get_json(f"{espn.WEB}/search/v2?query={espn.quote(country)}&limit=10", ttl=24 * 3600)
        for group in data.get("results", []):
            if group.get("type") != "team":
                continue
            for item in group.get("contents", []):
                uid = item.get("uid") or ""
                if uid.startswith(SPORT_UID) and espn.fold(item.get("displayName", "")) == espn.fold(country):
                    return uid.split("~t:")[1], item["displayName"]
        return None

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        seen: dict[str, Fixture] = {}
        for team_id in ref.team_ids:
            for suffix, ttl in (("?fixture=true", 1800), ("", 1800)):
                try:
                    data = espn.get_json(f"{espn.SITE}/soccer/all/teams/{team_id}/schedule{suffix}", ttl=ttl)
                except AdapterError:
                    continue
                for f in parse_schedule(data):
                    seen.setdefault(f.source_id, f)
        return sorted(seen.values(), key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        league, event = locator.get("league") or "all", locator["event"]
        # A 5 s cache lets every player in one match share a single fetch per poll.
        data = espn.get_json(f"{espn.SITE}/soccer/{league}/summary?event={event}", ttl=24 * 3600 if final else 5)
        return parse_summary(data, league)

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        return result_label(fixture, team_ids)

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        data = espn.get_json(f"{espn.WEB}/common/v3/sports/soccer/athletes/{ref.athlete_id}/stats", ttl=3 * 3600)
        return parse_season_stats(data)
