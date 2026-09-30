"""Cricket from Sportmonks (licensed): team fixtures, live scorecards with every player's line, form.

Replaces the ESPN cricket adapter for a public build, returning the same shapes: the same line labels
("Runs", "Balls", "Out", "Wickets", "Runs conceded", "Overs") that moments.py turns into fifty,
hundred, out and wicket alerts, and the same cricket state the app draws.

A player's teams are the sides they played for in each competition's latest season, if that season
is this year's or last year's (Sportmonks' "current teams" misses franchise sides). A team fields
different squads (India's T20I, ODI and Test sides), so a match counts only if the player is in its
XI, or in the team's squad for that season before the XI is out.

Which competitions answer depends on the plan: the free plan has T20 internationals, the Big Bash
and the CSA T20 Challenge; World adds ODIs, Tests, the IPL and the rest.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import espn, sportmonks
from .base import AdapterError, Fixture, Line, PlayerRef, Snapshot
from .espn_cricket import batting_bowling

API = sportmonks.CRICKET
LIST_INCLUDE = "localteam,visitorteam,league,stage,venue,runs,lineup"
MATCH_INCLUDE = "localteam,visitorteam,league,stage,venue,runs,lineup,manofmatch,bowling,batting.result,batting.bowler,batting.catchstump,batting.runoutby"

_FINAL = {"Finished"}
_OFF = {"Cancl.", "Postp.", "Aban."}
_BREAKS = {"Innings Break", "Lunch", "Tea Break", "Dinner", "Int."}
_OVERS = {"T20I": 20, "T20": 20, "ODI": 50, "List A": 50, "T10": 10}
_FORMATS = {"Test/5day": "Test", "4day": "First-class"}


def _status(value: str | None) -> str:
    """Sportmonks match status ("NS", "2nd Innings", "Stump Day 2", "Finished", "Aban.") -> ours."""
    value = value or ""
    if value in _FINAL:
        return "final"
    if value in _OFF:
        return "postponed"  # as ESPN's adapter: an abandoned match has no result to show
    if value.endswith("Innings") or value.startswith("Stump") or value in _BREAKS:
        return "live"
    return "scheduled"  # "NS", "Delayed"


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _num(value: Any) -> str:
    """Overs as cricket writes them: 20 -> "20", 14.1 -> "14.1"."""
    return f"{float(value):g}" if value not in (None, "") else ""


def _format(x: dict[str, Any]) -> str | None:
    kind = x.get("type") or ""
    return _FORMATS.get(kind, kind) or None


def _competition(x: dict[str, Any]) -> str:
    """The series for internationals ("West Indies tour of India"), the league for everything else."""
    league = x.get("league") or {}
    stage = (x.get("stage") or {}).get("name")
    return (stage if league.get("type") == "phase" and stage else league.get("name")) or stage or ""


def _team(x: dict[str, Any], side: str) -> dict[str, Any]:
    return x.get(side) or {"id": x.get(f"{side}_id")}


def _innings_score(runs: list[dict[str, Any]], team_id: Any) -> str:
    """A side's innings as a score: "221/7", "94", "150 & 230/4"."""
    mine = sorted((r for r in runs if r.get("team_id") == team_id), key=lambda r: r.get("inning") or 0)
    return " & ".join(f"{r.get('score') or 0}{'' if (r.get('wickets') or 0) >= 10 else '/' + str(r.get('wickets') or 0)}" for r in mine)


def born(value: str | None) -> str | None:
    """A birth date, unless it's the 1 January Sportmonks fills in when it doesn't know one."""
    return value if value and not value.endswith("-01-01") else None


# ---------------------------------------------------------------- parsers (pure)


def parse_fixture(x: dict[str, Any]) -> Fixture:
    home, away = _team(x, "localteam"), _team(x, "visitorteam")
    status = _status(x.get("status"))
    runs = x.get("runs") or []
    result = None
    if status == "final":
        result = {
            "summary": x.get("note") or "",
            "teams": [{"id": str(t.get("id")), "name": t.get("name"), "score": _innings_score(runs, t.get("id")), "winner": x.get("winner_team_id") == t.get("id") if x.get("winner_team_id") else None} for t in (home, away)],
        }
    venue = x.get("venue") or {}
    return Fixture(
        source_id=str(x["id"]),
        title=f"{home.get('name')} v {away.get('name')}",
        competition=_competition(x),
        start_utc=_time(x.get("starting_at")),
        venue=", ".join(v for v in (venue.get("name"), venue.get("city")) if v) or None,
        status=status,
        team_ids=[str(home.get("id")), str(away.get("id"))],
        team_names=[home.get("name") or "", away.get("name") or ""],
        locator={"fixture": str(x["id"])},
        detail=x.get("round") or None,
        result=result,
    )


def in_match(x: dict[str, Any], athlete_id: str, squad: set[str] | None) -> bool | None:
    """Is the player in this match? The XI once it's out, the season's squad before, None before either."""
    xi = {str(p.get("id")) for p in x.get("lineup") or []}
    if xi:
        return athlete_id in xi
    if squad:
        return athlete_id in squad
    return None


def _surname(p: dict[str, Any] | None) -> str:
    return ((p or {}).get("lastname") or (p or {}).get("fullname") or "").strip()


def dismissal(b: dict[str, Any]) -> str:
    """How a batter got out, as a scorecard writes it: "c Gurbaz b Khan", "run out (Khan)", "lbw b Bumrah"."""
    kind = ((b.get("result") or {}).get("name") or "").lower()
    bowler = _surname(b.get("bowler"))
    fielder = _surname(b.get("catchstump")) or _surname(b.get("runoutby"))
    sub = " (sub)" if "(sub)" in kind else ""
    if "run out" in kind:
        return f"run out ({fielder}{sub})" if fielder else "run out"
    if "catch" in kind:
        return f"c & b {bowler}" if fielder == bowler else f"c {fielder or 'sub'}{sub if fielder else ''} b {bowler}"
    if "stump" in kind:
        return f"st {fielder}{sub} b {bowler}"
    if "lbw" in kind:
        return f"lbw b {bowler}"
    if "hit wicket" in kind:
        return f"hit wicket b {bowler}"
    if "bowled" in kind:
        return f"b {bowler}"
    return kind


def _chase(x: dict[str, Any], innings: list[dict[str, Any]]) -> str | None:
    """ "India need 45 from 30 balls" in the second innings of a limited-overs match."""
    overs = int(x.get("rpc_overs") or 0) or _OVERS.get(x.get("type") or "")
    if not overs or len(innings) != 2 or not innings[1]["batting"]:
        return None
    target = int(x.get("rpc_target") or 0) or innings[0]["runs"] + 1
    whole, _, part = innings[1]["overs"].partition(".")
    balls_left = overs * 6 - (int(whole or 0) * 6 + int(part or 0))
    need = target - innings[1]["runs"]
    if need <= 0 or balls_left <= 0:
        return None
    return f"{innings[1]['team']} need {need} from {balls_left} ball{'s' if balls_left != 1 else ''}"


def parse_lines(x: dict[str, Any]) -> dict[str, Line]:
    """Each player's line: every innings in the headline ("12 (30) & 45* (60) · 3/45 (20 ov)"); the
    stats are the latest innings, which is what the fifty, hundred, out and wicket alerts read."""
    order = {f"S{i}": i for i in range(1, 5)}
    bats: dict[str, list[dict[str, Any]]] = {}
    for b in sorted(x.get("batting") or [], key=lambda b: (order.get(b.get("scoreboard"), 9), b.get("sort") or 0)):
        wicket = bool((b.get("result") or {}).get("is_wicket"))
        if b.get("player_id") and (b.get("ball") or b.get("score") or wicket):  # a 0 (0) not out didn't bat
            bats.setdefault(str(b["player_id"]), []).append(b)
    bowls: dict[str, list[dict[str, Any]]] = {}
    for w in sorted(x.get("bowling") or [], key=lambda w: (order.get(w.get("scoreboard"), 9), w.get("sort") or 0)):
        if w.get("player_id") and w.get("overs"):
            bowls.setdefault(str(w["player_id"]), []).append(w)

    lines: dict[str, Line] = {}
    for pid in dict.fromkeys([*bats, *bowls]):
        heads, stats = [], []
        if pid in bats:
            parts = []
            for b in bats[pid]:
                not_out = not (b.get("result") or {}).get("is_wicket")
                parts.append(f"{b.get('score') or 0}{'*' if not_out else ''} ({b.get('ball') or 0})")
            heads.append(" & ".join(parts))
            b = bats[pid][-1]
            runs, balls, not_out = int(b.get("score") or 0), int(b.get("ball") or 0), not (b.get("result") or {}).get("is_wicket")
            stats += [
                {"label": "Runs", "value": f"{runs}{'*' if not_out else ''}"},
                {"label": "Balls", "value": str(balls)},
                {"label": "4s", "value": str(b.get("four_x") or 0)},
                {"label": "6s", "value": str(b.get("six_x") or 0)},
                {"label": "Strike rate", "value": f"{runs * 100 / balls:.1f}" if balls else "–"},
            ]
            if not not_out:
                stats.append({"label": "Out", "value": dismissal(b)})
        if pid in bowls:
            heads.append(" & ".join(f"{w.get('wickets') or 0}/{w.get('runs') or 0} ({_num(w.get('overs'))} ov)" for w in bowls[pid]))
            w = bowls[pid][-1]
            stats += [
                {"label": "Wickets", "value": str(w.get("wickets") or 0)},
                {"label": "Overs", "value": _num(w.get("overs"))},
                {"label": "Runs conceded", "value": str(w.get("runs") or 0)},
                {"label": "Economy", "value": f"{float(w['rate']):.2f}" if w.get("rate") not in (None, "") else "–"},
            ]
        lines[pid] = Line(headline=" · ".join(heads), stats=stats)
    return lines


def parse_match(x: dict[str, Any]) -> Snapshot:
    """A fixture with teams, runs, line-ups, batting and bowling -> snapshot with the full scorecard."""
    home, away = _team(x, "localteam"), _team(x, "visitorteam")
    status = _status(x.get("status"))
    names = {t.get("id"): t for t in (home, away)}
    runs = sorted(x.get("runs") or [], key=lambda r: r.get("inning") or 0)
    in_play = status == "live" and x.get("status") not in _BREAKS and not str(x.get("status")).startswith("Stump")
    innings = [{
        "team": (names.get(r.get("team_id")) or {}).get("name"),
        "abbr": (names.get(r.get("team_id")) or {}).get("code"),
        "period": r.get("inning"),
        "runs": int(r.get("score") or 0),
        "wickets": int(r.get("wickets") or 0),
        "overs": _num(r.get("overs")),
        "batting": in_play and i == len(runs) - 1,
        "target": None,
    } for i, r in enumerate(runs)]
    if len(innings) == 2 and (x.get("type") or "") in _OVERS:
        innings[1]["target"] = int(x.get("rpc_target") or 0) or innings[0]["runs"] + 1

    def fmt(i: dict[str, Any]) -> str:
        wk = "" if i["wickets"] >= 10 else f"/{i['wickets']}"
        return f"{i['abbr'] or i['team']} {i['runs']}{wk} ({i['overs']} ov)"

    if status == "final":
        clock_label = x.get("note") or "Result"
    elif status == "live":
        clock_label = " · ".join(b for b in (x.get("status"), _chase(x, innings)) if b)
    else:
        clock_label = x.get("status") if status == "postponed" else ""
    teams = [{"id": str(t.get("id")), "name": t.get("name"), "abbr": t.get("code"), "score": _innings_score(runs, t.get("id")), "winner": (x.get("winner_team_id") == t.get("id")) if x.get("winner_team_id") else None} for t in (home, away)]
    return Snapshot(
        status=status,
        score_label=" · ".join(fmt(i) for i in innings) or f"{home.get('name')} v {away.get('name')}",
        clock_label=clock_label,
        state={
            "kind": "cricket",
            "teams": teams,
            "innings": innings,
            "summary": x.get("note") or "",
            "format": _format(x),
            "player_of_match": (x.get("manofmatch") or {}).get("fullname"),
        },
        lines=parse_lines(x) if status in ("live", "final") else {},
        moments=[],
        source_url=None,  # Sportmonks has no public match pages
    )


def _season_year(name: str) -> int:
    """ "2026" -> 2026, "2025/2026" -> 2026."""
    years = re.findall(r"\d{4}", name or "")
    return int(years[-1]) if years else 0


def current_teams(p: dict[str, Any], seasons: dict[int, dict[str, Any]], today: date) -> list[tuple[str, str]]:
    """The sides a player (with teams) turned out for in each competition's latest season, if that
    season is this year's or last year's: [(team id, name)], national side first."""
    latest: dict[Any, tuple[int, bool, str, str]] = {}
    for t in p.get("teams") or []:
        squad = t.get("in_squad") or {}
        season = seasons.get(squad.get("season_id"))
        if season is None:
            continue  # a season outside the plan
        year = _season_year(season.get("name") or "")
        league = squad.get("league_id") or season.get("league_id")
        if year > latest.get(league, (0,))[0]:
            latest[league] = (year, bool(t.get("national_team")), str(t.get("id")), t.get("name") or "")
    recent = sorted((v for v in latest.values() if v[0] >= today.year - 1), key=lambda v: (not v[1], -v[0]))
    return list(dict.fromkeys((tid, name) for _, _, tid, name in recent))


def recent_form(athlete_id: str, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
    """Batting and bowling over the player's recent completed matches, from their scorecards."""
    lines, formats = [], []
    for _, snap in recent:
        line = snap.lines.get(athlete_id)
        if line is None:
            continue
        lines.append(line.stats)
        if (f := snap.state.get("format")) and f not in formats:
            formats.append(f)
    if not lines:
        return [], ""
    kinds = " & ".join(formats)
    return batting_bowling(lines), f"Last {len(lines)} match{'es' if len(lines) != 1 else ''}{f' ({kinds})' if kinds else ''}, from Sportmonks scorecards"


# ---------------------------------------------------------------- the adapter


class SportmonksCricket:
    system = "sportmonks_cricket"
    sports = ("cricket",)
    live_cadence = 10  # 6 requests a minute per live match, of 180
    WINDOW_BACK, WINDOW_AHEAD = 120, 90  # days of fixtures a refresh reads (two requests per team)
    HISTORY_DAYS = 360  # how far before that window history.py reads, once: four 90-day pages per team

    def spare(self) -> bool:
        """Whether history.py may read now, leaving the minute's last CRICKET_RESERVE requests to live matches."""
        return sportmonks.spare("Cricket")

    def _seasons(self) -> dict[int, dict[str, Any]]:
        return {s["id"]: s for s in sportmonks.get_json(f"{API}/seasons", ttl=24 * 3600).get("data") or []}

    def _ref(self, p: dict[str, Any]) -> PlayerRef:
        teams = current_teams(p, self._seasons(), datetime.now(timezone.utc).date())
        return PlayerRef(self.system, str(p["id"]), p.get("fullname") or "", [t for t, _ in teams], [n for _, n in teams], born=born(p.get("dateofbirth")))

    def search(self, name: str) -> list[PlayerRef]:
        """Players with this surname whose first name matches too, each read with their teams."""
        words = espn.fold(name).split()
        if not words:
            return []
        rows = sportmonks.get_json(f"{API}/players", ttl=24 * 3600, **{"filter[lastname]": words[-1]}).get("data") or []
        hits = [r for r in rows if (w := espn.fold(r.get("fullname") or "").split()) and w[0] == words[0] and w[-1] == words[-1]]
        return [ref for ref in (self.player(str(r["id"])) for r in hits[:4]) if ref]

    def find_player(self, name: str) -> PlayerRef | None:
        hits = self.search(name)
        return hits[0] if len(hits) == 1 else None  # two namesakes: the agent decides who is meant

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        try:
            data = sportmonks.get_json(f"{API}/players/{athlete_id}", ttl=6 * 3600, include="teams")
        except AdapterError:
            return None
        return self._ref(data["data"]) if (data.get("data") or {}).get("id") else None

    def _squad(self, team_id: str, season_id: Any) -> set[str]:
        try:
            data = sportmonks.get_json(f"{API}/teams/{team_id}/squad/{season_id}", ttl=12 * 3600).get("data") or {}
        except AdapterError:
            return set()
        return {str(p.get("id")) for p in data.get("squad") or []}

    def _matches(self, ref: PlayerRef, start: date, end: date, ttl: float) -> list[Fixture]:
        """The player's matches between two dates: their teams' matches, minus XIs and squads that leave them out."""
        rows: dict[Any, dict[str, Any]] = {}
        for team_id in ref.team_ids:
            for side in ("localteam_id", "visitorteam_id"):
                params = {f"filter[{side}]": team_id, "filter[starts_between]": f"{start.isoformat()},{end.isoformat()}", "include": LIST_INCLUDE}
                for x in sportmonks.get_all(f"{API}/fixtures", ttl=ttl, **params):
                    rows.setdefault(x["id"], x)
        out = []
        for x in rows.values():
            f = parse_fixture(x)
            if f.status != "postponed":
                ours = next((t for t in f.team_ids if t in ref.team_ids), None)
                squad = self._squad(ours, x.get("season_id")) if ours and not x.get("lineup") and x.get("season_id") else None
                member = in_match(x, ref.athlete_id, squad)
                if member is False:
                    continue
                if member is None and f.status == "scheduled":
                    f.detail = f"{f.detail} · squad not announced" if f.detail else "Squad not announced"
            out.append(f)
        return sorted(out, key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        today = datetime.now(timezone.utc).date()
        return self._matches(ref, today - timedelta(days=self.WINDOW_BACK), today + timedelta(days=self.WINDOW_AHEAD), ttl=1800)

    def history_pages(self, ref: PlayerRef, anchor: date) -> list[dict[str, Any]]:
        """Before the refresh window, 90 days at a time for each team, back HISTORY_DAYS."""
        pages = []
        end = anchor - timedelta(days=self.WINDOW_BACK + 1)
        stop = end - timedelta(days=self.HISTORY_DAYS)
        while end > stop:
            start = max(stop, end - timedelta(days=89))
            pages += [{"team": team_id, "from": start.isoformat(), "to": end.isoformat(), "cost": 2} for team_id in ref.team_ids]
            end = start - timedelta(days=1)
        return pages

    def history(self, ref: PlayerRef, page: dict[str, Any]) -> list[Fixture]:
        one = PlayerRef(**{**ref.__dict__, "team_ids": [page["team"]]})
        return [f for f in self._matches(one, date.fromisoformat(page["from"]), date.fromisoformat(page["to"]), ttl=24 * 3600) if f.status == "final"]

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        # A short cache lets every followed player in one match share each poll's fetch.
        data = sportmonks.get_json(f"{API}/fixtures/{locator['fixture']}", ttl=24 * 3600 if final else 5, include=MATCH_INCLUDE)
        if not data.get("data"):
            raise AdapterError(f"Sportmonks has no cricket fixture {locator['fixture']}")
        return parse_match(data["data"])

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        r = fixture.result or {}
        return r.get("summary") or " · ".join(f"{t['name']} {t['score']}" for t in r.get("teams", []))

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        return recent_form(ref.athlete_id, recent)
