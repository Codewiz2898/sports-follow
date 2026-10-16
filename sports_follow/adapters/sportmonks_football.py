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

from .. import naming
from ..sports import football
from . import sportmonks
from .base import Absence, Adapter, AdapterError, Fixture, Line, LiveGame, PlayerRef, Snapshot

LIST_INCLUDE = "participants;scores;state;league;round;venue"
# Every match of the plan's leagues on some days (schedule): enough for the score and the stage.
SCHEDULE_INCLUDE = "participants;scores;state;league;round"
SCHEDULE_PAGE = 50  # rows a page of it, Sportmonks' most: a week of the five leagues is about one
MATCH_INCLUDE = "participants;scores;state;periods;league;round;venue;lineups.details.type;events.type;metadata"
# Every match in play, league-wide: enough for its score, clock, scorers and the players in it.
LIVE_INCLUDE = "participants;scores;state;periods;league;lineups;events.type"
PLAYER_INCLUDE = "teams.team;nationality"
# The plan's leagues with their seasons on now (sidelined): whose teams are read.
LEAGUES_INCLUDE = "currentSeason"
# A season's teams, each with every player of theirs out now: the spell, its type ("Back Injury"), the player.
SIDELINED_INCLUDE = "sidelined.player;sidelined.type"

# Match states (…/football/states): everything else is "scheduled".
_LIVE = {"INPLAY_1ST_HALF", "HT", "BREAK", "INPLAY_2ND_HALF", "INPLAY_ET", "EXTRA_TIME_BREAK", "INPLAY_ET_SECOND_HALF", "PEN_BREAK", "INPLAY_PENALTIES", "AWAITING_UPDATES"}
_FINAL = {"FT", "AET", "FT_PEN", "WO", "AWARDED"}
# Play stopped (weather, floodlights, the crowd) and may restart: the match stays live, and polled, on
# its day; STOPPED_FOR after kick-off it's off until the schedule says otherwise.
_STOPPED = {"INTERRUPTED", "SUSPENDED"}
STOPPED_FOR = timedelta(hours=6)
_OFF = {"POSTPONED", "CANCELLED", "ABANDONED", "DELETED"}
_CLOCK = {"HT": "Half-time", "BREAK": "Break", "EXTRA_TIME_BREAK": "Extra-time break", "PEN_BREAK": "Before penalties", "INPLAY_PENALTIES": "Penalties",
          "INTERRUPTED": "Interrupted", "SUSPENDED": "Suspended",
          "FT": "Full time", "AET": "After extra time", "FT_PEN": "After penalties", "AWAITING_UPDATES": "Awaiting result"}

# A player's match stats in the order a fan reads them: Sportmonks type -> label (labels as ESPN's adapter).
_LINE_STATS = [("GOALS", "Goals"), ("ASSISTS", "Assists"), ("SHOTS_TOTAL", "Shots"), ("SHOTS_ON_TARGET", "On target"),
               ("FOULS", "Fouls"), ("OFFSIDES", "Offsides"), ("YELLOWCARDS", "Yellow"), ("REDCARDS", "Red"), ("MINUTES_PLAYED", "Minutes")]
_SEASON_STATS = [("APPEARANCES", "Apps"), ("GOALS", "Goals"), ("ASSISTS", "Assists"), ("MINUTES_PLAYED", "Minutes"), ("YELLOWCARDS", "Yellow cards"), ("REDCARDS", "Red cards")]
_STARTER, _BENCH = 11, 12  # lineup type ids
_LINEUP_CONFIRMED = 572  # metadata type: {"confirmed": true} once the clubs have published their team sheets
_GOALS = {"GOAL", "OWNGOAL", "PENALTY"}


def _status(state: dict[str, Any] | None, start: datetime | None = None, now: datetime | None = None) -> str:
    code = (state or {}).get("developer_name") or ""
    if code in _LIVE:
        return "live"
    if code in _FINAL:
        return "final"
    if code in _STOPPED:
        return "live" if start is None or (now or datetime.now(timezone.utc)) - start < STOPPED_FOR else "postponed"
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


def _place(side: dict[str, Any]) -> int | None:
    """A side's place in its league table, as Sportmonks sends it with the side (meta.position), if it does."""
    place = (side.get("meta") or {}).get("position")
    return place if isinstance(place, int) and place > 0 else None


def parse_fixtures(rows: list[dict[str, Any]]) -> list[Fixture]:
    """Fixtures (…/fixtures/between/…, a team's or every one) -> fixtures, with the result for finished
    ones, and both sides' places in their table where Sportmonks sends them (the sport ranks league games
    by them: sports.football)."""
    out = []
    for x in rows:
        home, away = _sides(x)
        if not home.get("id") or not away.get("id"):
            continue
        places = [_place(home), _place(away)]
        status = _status(x.get("state"), _start(x))
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
            facts={"table": places} if any(places) else {},
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
    published team sheet isn't a line yet. Nor are Sportmonks' predicted line-ups, if the clubs' sheets
    are still unconfirmed at kick-off: a guessed starter would be told as one."""
    if status == "scheduled" or _predicted(x):
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
    status = _status(x.get("state"), _start(x))
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
        if not e.get("player_name") or kind not in (*_GOALS, "YELLOWCARD", "REDCARD", "YELLOWREDCARD") or (e.get("rescinded") and kind not in _GOALS):
            continue  # a coach's card has no player; a card taken back is none
        # A goal VAR rules out stays, marked, so the goal's alert is corrected (sports.football).
        tag = ("DISALLOWED" if e.get("rescinded") else "GOAL") if kind in _GOALS else "YELLOW" if kind == "YELLOWCARD" else "RED"
        text = f"{e['player_name']} ({names.get(e.get('participant_id'), '')})".replace(" ()", "")
        if kind == "OWNGOAL":
            text += ", own goal"
        elif kind == "PENALTY":
            text += ", penalty"
        elif kind == "YELLOWREDCARD":
            text += ", second yellow"
        if tag == "GOAL" and e.get("related_player_name") and kind != "OWNGOAL":
            text += f", assist {e['related_player_name']}"
        moments.append({
            "clock": minute(e), "kind": tag, "text": text, "athlete_ids": [str(i) for i in (e.get("player_id"), e.get("related_player_id")) if i],
            # For a goal's notification: whose goal it was, who set it up (sports.football._goal).
            "team_id": str(e.get("participant_id")), "assist": e.get("related_player_name") if tag == "GOAL" and kind != "OWNGOAL" else None, "own_goal": kind == "OWNGOAL",
        })

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
        # The newest dozen, and every goal, ruled out or not: a goal's alert reads its minute and assist here.
        moments=[m for i, m in enumerate(moments) if i < 12 or m["kind"] in ("GOAL", "DISALLOWED")],
        source_url=None,  # Sportmonks has no public match pages
        squad=parse_squad(x),
    )


_UPFRONT = {27: 0, 26: 1, 25: 2, 24: 3}  # position ids, attackers first: attacker, midfielder, defender, goalkeeper


def _taking_turns(sides: list[list[Any]]) -> list[Any]:
    """The sides' players in turn, the home side's first: a game's best known come from both."""
    return [side[i] for i in range(max((len(s) for s in sides), default=0)) for side in sides if i < len(side)]


def parse_live(rows: list[dict[str, Any]], system: str) -> list[LiveGame]:
    """Livescores in play -> the games on now, each with its starting players, named as the line-ups name them."""
    out = []
    for x in rows:
        fixtures = parse_fixtures([x])
        snap = parse_match(x)
        if not fixtures or snap.status != "live":
            continue
        f = fixtures[0]
        teams = dict(zip(f.team_ids, f.team_names))
        starters = [r for r in x.get("lineups") or [] if r.get("type_id") == _STARTER and r.get("player_id") and r.get("player_name")]
        sides = [sorted((r for r in starters if str(r.get("team_id")) == team), key=lambda r: _UPFRONT.get(r.get("position_id"), 2)) for team in f.team_ids]
        players = [
            PlayerRef(system=system, athlete_id=str(r["player_id"]), name=r["player_name"], team_ids=[str(r.get("team_id"))], team_names=[teams.get(str(r.get("team_id")), "")])
            for r in _taking_turns(sides)
        ]
        out.append(LiveGame(source_id=f.source_id, title=f.title, competition=f.competition, start_utc=f.start_utc, snapshot=snap, players=players, teams=f.team_names))
    return out


def parse_squad(x: dict[str, Any]) -> dict[str, str]:
    """The team sheet, published about an hour before kick-off: who starts and who's on the bench. Until
    then Sportmonks fills the line-ups with its own prediction (eleven starters a side, days ahead), marked
    unconfirmed: that's nobody's team sheet, so it says nothing (seen 15 Oct 2026)."""
    if _predicted(x):
        return {}
    roles = {_STARTER: "starting", _BENCH: "bench"}
    return {str(r["player_id"]): roles[r.get("type_id")] for r in x.get("lineups") or [] if r.get("player_id") and r.get("type_id") in roles}


def _predicted(x: dict[str, Any]) -> bool:
    """Whether the line-ups are Sportmonks' prediction, not the clubs' published team sheets."""
    return any(m.get("type_id") == _LINEUP_CONFIRMED and (m.get("values") or {}).get("confirmed") is False for m in x.get("metadata") or [])


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
        # Names come padded, with a no-break space at times ("Lautaro Martínez\u00a0"): strip() takes both.
        name=str(p.get("display_name") or p.get("common_name") or p.get("name") or "").strip(),
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


def _day(text: Any) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10]) if text else None
    except ValueError:
        return None


# Spells Sportmonks files as "suspended" that aren't bans: a player not registered for the competition, or away
# with their country. Their types' developer names, which are their names in capitals ("No Eligibility").
_NOT_BANS = {"NO_ELIGIBILITY", "CALLED_UP_TO_NATIONAL_TEAM"}


def _out_for(spell: dict[str, Any]) -> str:
    """A spell's kind, as football's sources share them: Sportmonks' "injury"; a ban, any "suspended" spell
    but those that aren't one (_NOT_BANS: a red card, five yellows, a sports court's); or "other"."""
    category = spell.get("category")
    if category == "injury":
        return "injury"
    kind = spell.get("type") or {}
    named = str(kind.get("developer_name") or re.sub(r"\W+", "_", str(kind.get("name") or "")).strip("_").upper())
    return "suspension" if category == "suspended" and named not in _NOT_BANS else "other"


def parse_sidelined(teams: list[dict[str, Any]], system: str = "sportmonks_football") -> dict[str, list[Absence]]:
    """A season's teams with sidelined (with player and type) -> each team, by id, with its players out; a
    team whose answer has no sidelined list is left out, not taken for one with nobody out. Sportmonks lists
    a spell until it's over (completed), and its end date is when the player is expected back."""
    out: dict[str, list[Absence]] = {}
    for team in teams:
        if not team.get("id") or "sidelined" not in team:
            continue  # an answer without the list says nothing of who's out: as if the team weren't read
        listed = out.setdefault(str(team["id"]), [])
        for spell in team.get("sidelined") or []:
            player = spell.get("player") or {}
            if not spell.get("id") or not player.get("id") or spell.get("completed"):
                continue
            name = str(player.get("display_name") or player.get("common_name") or player.get("name") or "").strip()
            ref = PlayerRef(system=system, athlete_id=str(player["id"]), name=name, team_ids=[str(team["id"])], team_names=[team.get("name") or ""],
                            born=player.get("date_of_birth"))
            listed.append(Absence(source_id=str(spell["id"]), ref=ref, kind=_out_for(spell), what=str((spell.get("type") or {}).get("name") or "").strip(),
                                  since=_day(spell.get("start_date")), until=_day(spell.get("end_date"))))
    return out


# ---------------------------------------------------------------- the adapter


class SportmonksFootball(Adapter):
    system = "sportmonks_football"
    sport = "football"
    name = "Sportmonks"
    provider = sportmonks.PROVIDER
    covers = "Football"
    live_cadence = 15  # one fixtures/multi request for every live match: 240 an hour, of 2,000
    MULTI_MAX = 50  # fixture ids fixtures/multi takes in one request
    WINDOW_BACK, WINDOW_AHEAD = 120, 60  # days of fixtures a refresh reads; older ones come from history_pages
    HISTORY_DAYS = 360  # how far before that window history.py reads, once: four 90-day pages per team

    def spare(self) -> bool:
        """Whether history.py may read now: every call here is a Fixture request, and live matches
        need the hour's last LIVE_RESERVE of them."""
        return sportmonks.spare(sportmonks.FIXTURES)

    @classmethod
    def available(cls) -> bool:
        return bool(sportmonks.token())

    def search(self, name: str, league: str | None = None) -> list[PlayerRef]:
        data = sportmonks.get_json(f"/football/players/search/{quote(name)}", ttl=24 * 3600, include=PLAYER_INCLUDE)
        return [parse_player(p) for p in (data.get("data") or [])[:8] if p.get("id")]

    def find_player(self, name: str) -> PlayerRef | None:
        hits = [r for r in self.search(name) if naming.names_match(name, r.name)]
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

    def schedule(self, start: date, end: date) -> list[Fixture]:
        """Every match of the plan's leagues between two days, whoever plays it (fixtures/between with no
        team): the top five leagues' 50 or so a week, SCHEDULE_PAGE a request, up to sportmonks.MAX_PAGES.
        Counted against the Fixture entity, as every list of fixtures is."""
        rows = sportmonks.get_all(f"/football/fixtures/between/{start.isoformat()}/{end.isoformat()}", ttl=1800, include=SCHEDULE_INCLUDE, per_page=SCHEDULE_PAGE)
        return parse_fixtures(rows)

    def live_now(self) -> list[LiveGame]:
        """Every match in the plan's leagues in play now, with its starting elevens: one request (livescores/inplay,
        which Sportmonks counts as Fixture requests)."""
        rows = sportmonks.get_json("/football/livescores/inplay", ttl=30, include=LIVE_INCLUDE).get("data") or []
        return parse_live([rows] if isinstance(rows, dict) else rows, self.system)

    def sidelined(self) -> dict[str, list[Absence]]:
        """Every team of the plan's leagues, with who of theirs is out now: the leagues with their current
        seasons (one League request, kept a day), then each season's teams with who's out (one Team request
        a league: the plan's five leagues' 96 teams in five). Uncached: each read is compared with the last."""
        leagues = sportmonks.get_all("/football/leagues", ttl=24 * 3600, include=LEAGUES_INCLUDE, per_page=50)
        seasons = sorted({str(season["id"]) for league in leagues if (season := league.get("currentseason") or league.get("currentSeason")) and season.get("id")})
        out: dict[str, list[Absence]] = {}
        for season in seasons:
            rows = sportmonks.get_json(f"/football/teams/seasons/{season}", ttl=0, include=SIDELINED_INCLUDE).get("data") or []
            for team_id, listed in parse_sidelined([rows] if isinstance(rows, dict) else rows, self.system).items():
                out.setdefault(team_id, []).extend(listed)
        return out

    def snapshots(self, locators: dict[Any, dict[str, Any]]) -> dict[Any, Snapshot]:
        """Many matches at once, MULTI_MAX to a request (fixtures/multi): the live lane's one fetch a
        round, however many matches are on. Keyed as given; a fixture Sportmonks doesn't return is left out."""
        wanted = {key: str(loc["fixture"]) for key, loc in locators.items()}
        ids = sorted(set(wanted.values()))
        found: dict[str, Snapshot] = {}
        for i in range(0, len(ids), self.MULTI_MAX):
            # Not cached: each round wants the matches as they are now, and a matchday's worth is large.
            data = sportmonks.get_json(f"/football/fixtures/multi/{','.join(ids[i:i + self.MULTI_MAX])}", ttl=0, include=MATCH_INCLUDE)
            rows = data.get("data") or []
            for x in [rows] if isinstance(rows, dict) else rows:
                found[str(x.get("id"))] = parse_match(x)
        return {key: found[fid] for key, fid in wanted.items() if fid in found}

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        return football.result_label(fixture, team_ids)

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        data = sportmonks.get_json(f"/football/players/{ref.athlete_id}", ttl=3 * 3600, include="statistics.details.type;statistics.season.league")
        return parse_season_stats(data.get("data") or {}, ref.team_ids)
