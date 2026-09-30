"""Football from Sportmonks (licensed): team fixtures, live matches with every player's line, season stats.

Replaces the ESPN football adapter for a public build. The shapes it returns match ESPN's, so the
moments, the results history and the app's scoreboard work unchanged: the same line labels ("Goals",
"Assists", "Yellow", "Red"), the same "On the bench" headline, the same football state for the app.

Which competitions answer depends on the plan: leagues outside it return no fixtures (a national team
on a plan without internationals just has none).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from . import espn, sportmonks
from .base import AdapterError, Fixture, Line, PlayerRef, Snapshot
from .espn_soccer import result_label as _result_label

LIST_INCLUDE = "participants;scores;state;league;round;venue"
MATCH_INCLUDE = "participants;scores;state;periods;league;round;venue;lineups.details.type;events.type"
PLAYER_INCLUDE = "teams.team;nationality"

# Match states (…/football/states): everything else is "scheduled".
_LIVE = {"INPLAY_1ST_HALF", "HT", "BREAK", "INPLAY_2ND_HALF", "INPLAY_ET", "EXTRA_TIME_BREAK", "INPLAY_ET_SECOND_HALF", "PEN_BREAK", "INPLAY_PENALTIES", "AWAITING_UPDATES"}
_FINAL = {"FT", "AET", "FT_PEN", "WO", "AWARDED"}
_OFF = {"POSTPONED", "SUSPENDED", "CANCELLED", "ABANDONED", "INTERRUPTED", "DELETED"}
_CLOCK = {"HT": "Half-time", "BREAK": "Break", "EXTRA_TIME_BREAK": "Extra-time break", "PEN_BREAK": "Before penalties", "INPLAY_PENALTIES": "Penalties",
          "FT": "Full time", "AET": "After extra time", "FT_PEN": "After penalties", "AWAITING_UPDATES": "Awaiting result"}

# A player's match stats in the order a fan reads them: Sportmonks type -> label (labels as ESPN's adapter).
_LINE_STATS = [("GOALS", "Goals"), ("ASSISTS", "Assists"), ("SHOTS_TOTAL", "Shots"), ("SHOTS_ON_TARGET", "On target"),
               ("FOULS", "Fouls"), ("OFFSIDES", "Offsides"), ("YELLOWCARDS", "Yellow"), ("REDCARDS", "Red"), ("MINUTES_PLAYED", "Minutes")]
_SEASON_STATS = [("APPEARANCES", "Apps"), ("GOALS", "Goals"), ("ASSISTS", "Assists"), ("MINUTES_PLAYED", "Minutes"), ("YELLOWCARDS", "Yellow cards"), ("REDCARDS", "Red cards")]
_STARTER, _BENCH = 11, 12  # lineup type ids
_GOALS = {"GOAL", "OWNGOAL", "PENALTY"}


def _status(state: dict[str, Any] | None) -> str:
    code = (state or {}).get("developer_name") or ""
    if code in _LIVE:
        return "live"
    if code in _FINAL:
        return "final"
    if code in _OFF:
        return "postponed"
    return "scheduled"


def _start(x: dict[str, Any]) -> datetime | None:
    ts = x.get("starting_at_timestamp")
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


def _sides(x: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    parts = x.get("participants") or []
    home = next((p for p in parts if (p.get("meta") or {}).get("location") == "home"), parts[0] if parts else {})
    away = next((p for p in parts if (p.get("meta") or {}).get("location") == "away"), parts[-1] if parts else {})
    return home, away


def _goals(x: dict[str, Any]) -> dict[str, str]:
    current = [s for s in x.get("scores") or [] if s.get("description") == "CURRENT"]
    return {s["score"]["participant"]: str(s["score"]["goals"]) for s in current}


def _competition(x: dict[str, Any]) -> str:
    """The league's name, without the number Sportmonks gives its friendlies tables ("Club Friendlies 1")."""
    return re.sub(r"^(Club Friendlies)\s+\d+$", r"\1", (x.get("league") or {}).get("name") or "")


def _detail(x: dict[str, Any]) -> str | None:
    name = str((x.get("round") or {}).get("name") or "")
    return f"Matchday {name}" if name.isdigit() else (name or None)


# ---------------------------------------------------------------- parsers (pure)


def parse_fixtures(rows: list[dict[str, Any]]) -> list[Fixture]:
    """Fixtures (…/fixtures/between/…/{team}) -> fixtures, with the result for finished ones."""
    out = []
    for x in rows:
        home, away = _sides(x)
        if not home.get("id") or not away.get("id"):
            continue
        status = _status(x.get("state"))
        goals = _goals(x)
        result = None
        if status == "final":
            result = {side: {"id": str(p["id"]), "name": p.get("name"), "score": goals.get(side, ""), "winner": (p.get("meta") or {}).get("winner")} for side, p in (("home", home), ("away", away))}
        out.append(Fixture(
            source_id=str(x["id"]),
            title=f"{home.get('name')} vs {away.get('name')}",
            competition=_competition(x),
            start_utc=_start(x),
            venue=(x.get("venue") or {}).get("name"),
            status=status,
            team_ids=[str(home["id"]), str(away["id"])],
            team_names=[home.get("name") or "", away.get("name") or ""],
            locator={"fixture": str(x["id"])},
            detail=_detail(x),
            result=result,
        ))
    return out


def clock(x: dict[str, Any]) -> str:
    """ "67'", "45+2'", "Half-time", "Full time"."""
    code = (x.get("state") or {}).get("developer_name") or ""
    if code in _CLOCK:
        return _CLOCK[code]
    ticking = next((p for p in x.get("periods") or [] if p.get("ticking")), None)
    if ticking and ticking.get("minutes") is not None:
        limit = int(ticking.get("counts_from") or 0) + int(ticking.get("period_length") or 45)
        minute = int(ticking["minutes"])
        return f"{limit}+{minute - limit}'" if minute > limit else f"{minute}'"
    return ""


def _values(row: dict[str, Any]) -> dict[str, Any]:
    """A line-up's or a season's details by type: match details carry {"data": {"value": 3}},
    season details {"value": {"total": 3}}."""
    out = {}
    for d in row.get("details") or []:
        value = (d.get("data") or {}).get("value") if "data" in d else d.get("value")
        out[(d.get("type") or {}).get("developer_name")] = value.get("total") if isinstance(value, dict) else value
    return out


def parse_lines(x: dict[str, Any], status: str) -> dict[str, Line]:
    """Each squad player's line, keyed by Sportmonks player id. Before kick-off there are none: a
    published team sheet isn't a line yet."""
    if status == "scheduled":
        return {}
    lines: dict[str, Line] = {}
    for row in x.get("lineups") or []:
        pid = row.get("player_id")
        if not pid:
            continue
        values = _values(row)
        minutes = int(values.get("MINUTES_PLAYED") or 0)
        red = int(values.get("REDCARDS") or 0) + int(values.get("YELLOWREDCARDS") or 0)
        stats = [{"label": label, "value": str(red if key == "REDCARDS" else values[key])} for key, label in _LINE_STATS if (red if key == "REDCARDS" else values.get(key)) not in (None, "", 0)]
        if row.get("type_id") == _STARTER:
            role = "Started"
        elif minutes > 0:
            role = "Came on"
        else:
            lines[str(pid)] = Line(headline="On the bench", stats=[])
            continue
        goals, assists = int(values.get("GOALS") or 0), int(values.get("ASSISTS") or 0)
        bits = [f"{goals} goal{'s' if goals != 1 else ''}" if goals else None, f"{assists} assist{'s' if assists != 1 else ''}" if assists else None]
        lines[str(pid)] = Line(headline=", ".join(b for b in bits if b) or role, stats=stats)
    return lines


def parse_match(x: dict[str, Any]) -> Snapshot:
    """A fixture with participants, scores, state, periods, line-ups and events -> snapshot."""
    home, away = _sides(x)
    status = _status(x.get("state"))
    goals = _goals(x)
    events = sorted(x.get("events") or [], key=lambda e: (e.get("minute") or 0, e.get("extra_minute") or 0, e.get("sort_order") or 0))

    def minute(e: dict[str, Any]) -> str:
        return f"{e.get('minute')}+{e['extra_minute']}'" if e.get("extra_minute") else f"{e.get('minute')}'"

    def scorers(team_id: Any) -> list[str]:
        out = []
        for e in events:
            kind = (e.get("type") or {}).get("developer_name")
            if kind not in _GOALS or e.get("rescinded"):
                continue
            # An own goal counts for the other side.
            scored_for = e.get("participant_id") if kind != "OWNGOAL" else next((p["id"] for p in (home, away) if p.get("id") != e.get("participant_id")), None)
            if scored_for == team_id:
                out.append(f"{e.get('player_name') or 'Goal'} {minute(e)}{' (OG)' if kind == 'OWNGOAL' else ' (pen)' if kind == 'PENALTY' else ''}")
        return out

    names = {p.get("id"): p.get("name") for p in (home, away)}
    moments = []
    for e in reversed(events):
        kind = (e.get("type") or {}).get("developer_name")
        if e.get("rescinded") or not e.get("player_name") or kind not in (*_GOALS, "YELLOWCARD", "REDCARD", "YELLOWREDCARD"):
            continue  # a coach's card has no player
        tag = "GOAL" if kind in _GOALS else "YELLOW" if kind == "YELLOWCARD" else "RED"
        text = f"{e['player_name']} ({names.get(e.get('participant_id'), '')})".replace(" ()", "")
        if kind == "OWNGOAL":
            text += ", own goal"
        elif kind == "PENALTY":
            text += ", penalty"
        elif kind == "YELLOWREDCARD":
            text += ", second yellow"
        if tag == "GOAL" and e.get("related_player_name") and kind != "OWNGOAL":
            text += f", assist {e['related_player_name']}"
        moments.append({"clock": minute(e), "kind": tag, "text": text, "athlete_ids": [str(i) for i in (e.get("player_id"), e.get("related_player_id")) if i]})

    state = {
        "kind": "football",
        "home": {"id": str(home.get("id")), "name": home.get("name"), "abbr": home.get("short_code"), "score": goals.get("home", ""), "scorers": scorers(home.get("id"))},
        "away": {"id": str(away.get("id")), "name": away.get("name"), "abbr": away.get("short_code"), "score": goals.get("away", ""), "scorers": scorers(away.get("id"))},
        "detail": (x.get("state") or {}).get("name"),
    }
    return Snapshot(
        status=status,
        score_label=f"{home.get('name')} {goals.get('home', '')} – {goals.get('away', '')} {away.get('name')}",
        clock_label=clock(x),
        state=state,
        lines=parse_lines(x, status),
        moments=moments[:12],
        source_url=None,  # Sportmonks has no public match pages
    )


def parse_player(p: dict[str, Any], today: date | None = None) -> PlayerRef:
    """A player (with teams.team) -> ref: current teams, club first, then country."""
    today = today or datetime.now(timezone.utc).date()
    current = []
    for t in p.get("teams") or []:
        end = t.get("end")
        if end and end < today.isoformat():
            continue
        team = t.get("team") or {}
        current.append((team.get("type") != "domestic", str(t.get("team_id")), team.get("name") or ""))
    current.sort()
    return PlayerRef(
        system="sportmonks_football",
        athlete_id=str(p["id"]),
        name=p.get("display_name") or p.get("common_name") or p.get("name") or "",
        team_ids=[tid for _, tid, _ in current],
        team_names=[name for _, _, name in current],
        born=p.get("date_of_birth"),
    )


def parse_season_stats(p: dict[str, Any], team_ids: list[str]) -> tuple[list[dict[str, str]], str]:
    """A player's statistics (with season and details) -> this season's numbers for their teams,
    summed across competitions."""
    rows = [r for r in p.get("statistics") or [] if (r.get("season") or {}).get("is_current") and (not team_ids or str(r.get("team_id")) in team_ids)]
    totals: dict[str, float] = {}
    leagues: list[str] = []
    for row in rows:
        values = _values(row)
        if not values:
            continue
        league = ((row.get("season") or {}).get("league") or {}).get("name")
        if league and league not in leagues:
            leagues.append(league)
        for key, _ in _SEASON_STATS:
            value = values.get(key)
            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + value
    stats = [{"label": label, "value": str(int(totals[key]))} for key, label in _SEASON_STATS if key in totals]
    if not stats:
        return [], ""
    season = next(((r.get("season") or {}).get("name") for r in rows if r.get("season")), "") or ""
    note = f"{leagues[0]} {season}".strip() if len(leagues) == 1 else f"{season} season, all competitions".strip()
    return stats, note


# ---------------------------------------------------------------- the adapter


class SportmonksFootball:
    system = "sportmonks_football"
    sports = ("football", "soccer", "association football")
    live_cadence = 15  # 240 requests an hour per live match, well inside a 2,500 hourly limit
    WINDOW_BACK, WINDOW_AHEAD = 120, 60  # days of fixtures a refresh reads; older ones come from history_pages
    HISTORY_DAYS = 360  # how far before that window history.py reads, once: four 90-day pages per team

    def spare(self) -> bool:
        """Whether history.py may read now: every call here is a Fixture request, and live matches
        need the hour's last LIVE_RESERVE of them."""
        return sportmonks.spare("Fixture")

    def search(self, name: str) -> list[PlayerRef]:
        data = sportmonks.get_json(f"/football/players/search/{quote(name)}", ttl=24 * 3600, include=PLAYER_INCLUDE)
        return [parse_player(p) for p in (data.get("data") or [])[:8] if p.get("id")]

    def find_player(self, name: str) -> PlayerRef | None:
        hits = [r for r in self.search(name) if espn.names_match(name, r.name)]
        return hits[0] if len(hits) == 1 else None  # two namesakes: the agent decides who is meant

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        try:
            data = sportmonks.get_json(f"/football/players/{athlete_id}", ttl=6 * 3600, include=PLAYER_INCLUDE)
        except AdapterError:
            return None
        return parse_player(data["data"]) if (data.get("data") or {}).get("id") else None

    def _between(self, team_id: str, start: date, end: date, ttl: float) -> list[Fixture]:
        rows = sportmonks.get_all(f"/football/fixtures/between/{start.isoformat()}/{end.isoformat()}/{team_id}", ttl=ttl, include=LIST_INCLUDE)
        return parse_fixtures(rows)

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        today = datetime.now(timezone.utc).date()
        seen: dict[str, Fixture] = {}
        for team_id in ref.team_ids:
            try:
                found = self._between(team_id, today - timedelta(days=self.WINDOW_BACK), today + timedelta(days=self.WINDOW_AHEAD), ttl=1800)
            except sportmonks.RateLimited:
                raise
            except AdapterError:
                continue  # a team outside the plan (a national team without internationals)
            for f in found:
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
        # A short cache lets every followed player in one match share each poll's fetch.
        data = sportmonks.get_json(f"/football/fixtures/{locator['fixture']}", ttl=24 * 3600 if final else 5, include=MATCH_INCLUDE)
        if not data.get("data"):
            raise AdapterError(f"Sportmonks has no fixture {locator['fixture']}")
        return parse_match(data["data"])

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        return _result_label(fixture, team_ids)

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        data = sportmonks.get_json(f"/football/players/{ref.athlete_id}", ttl=3 * 3600, include="statistics.details.type;statistics.season.league")
        return parse_season_stats(data.get("data") or {}, ref.team_ids)
