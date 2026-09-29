"""Cricket from ESPN (the same ids as ESPNcricinfo): calendar, live scorecards, recent form.

ESPN has no cricket team-schedule endpoint, so fixtures come from the day-by-day scoreboard feed:
one request per day, cached and shared by every cricket player, filtered by team. Career stats
aren't served either (the stats endpoint returns {}), so stats are computed from the player's
recent completed scorecards, which is exact for the matches it covers.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from . import espn
from .base import AdapterError, Fixture, Line, PlayerRef, Snapshot

SPORT_UID = "s:200~"
DAYS_AHEAD = 14
DAYS_BACK = 30  # enough completed matches for recent form

_STATUS = {"pre": "scheduled", "in": "live", "post": "final"}


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- parsers (pure)


def parse_day(data: dict[str, Any]) -> list[Fixture]:
    """One day of the scoreboard header feed -> every cricket fixture that day."""
    out = []
    for league in (data.get("sports") or [{}])[0].get("leagues", []):
        for e in league.get("events", []):
            competitors = e.get("competitors") or []
            if len(competitors) < 2:
                continue
            status = _STATUS.get(e.get("status"), "scheduled")
            kind = ((e.get("fullStatus") or {}).get("type") or {}).get("name") or ""
            if "ABANDON" in kind or "POSTPONE" in kind or "CANCEL" in kind:
                status = "postponed"
            detail = (e.get("description") or "").split(",")[0] or None  # "2nd ODI (D/N)"
            cls = (e.get("class") or {}).get("generalClassCard")
            result = None
            if status == "final":
                full = e.get("fullStatus") or {}
                # The top-level summary is just "Result"; the real one ("India won by 8 wkts (50b rem)") is in fullStatus.
                result = {"summary": full.get("summary") or full.get("longSummary") or e.get("summary") or "", "teams": [{"id": str(c.get("id")), "name": c.get("displayName"), "score": c.get("score") or "", "winner": c.get("winner")} for c in competitors]}
            out.append(
                Fixture(
                    source_id=str(e["id"]),
                    title=" v ".join(c.get("displayName") or "" for c in competitors),
                    competition=league.get("name") or "",
                    start_utc=_time(e.get("date")),
                    venue=e.get("location"),
                    status=status,
                    team_ids=[str(c.get("id")) for c in competitors],
                    team_names=[c.get("displayName") or "" for c in competitors],
                    locator={"league": str(league.get("id")), "event": str(e["id"])},
                    detail=f"{detail}" if detail else cls,
                    result=result,
                )
            )
    return out


def parse_summary(data: dict[str, Any]) -> Snapshot:
    """A match summary (…/cricket/{league}/summary?event=) -> snapshot with the full scorecard."""
    header = data["header"]
    comp = header["competitions"][0]
    status_obj = comp.get("status") or {}
    kind = status_obj.get("type") or {}
    status = _STATUS.get(kind.get("state"), "scheduled")

    innings = []
    teams = []
    for c in comp.get("competitors") or []:
        team = c.get("team") or {}
        teams.append({"id": str(team.get("id")), "name": team.get("displayName"), "abbr": team.get("abbreviation"), "score": c.get("score") or "", "winner": c.get("winner")})
        for ls in c.get("linescores") or []:
            # ESPN repeats the match's overs on a side that hasn't batted yet: skip innings with nothing in them.
            if not ls.get("isBatting") and not _int(ls.get("runs")) and not _int(ls.get("wickets")):
                continue
            innings.append({
                "team": team.get("displayName"),
                "abbr": team.get("abbreviation"),
                "period": ls.get("period"),
                "runs": _int(ls.get("runs")),
                "wickets": _int(ls.get("wickets")),
                "overs": ls.get("overs"),
                "batting": bool(ls.get("isBatting")) and status == "live",
                "target": _int(ls.get("target")) or None,
            })
    innings.sort(key=lambda i: (i["period"] or 0))

    def fmt(i: dict[str, Any]) -> str:
        wk = "" if i["wickets"] >= 10 else f"/{i['wickets']}"
        return f"{i['abbr'] or i['team']} {i['runs']}{wk} ({i['overs']} ov)"

    score_label = " · ".join(fmt(i) for i in innings) or " v ".join(t["name"] or "" for t in teams)
    clock_bits = [status_obj.get("session"), kind.get("description") if kind.get("description") not in ("Live", "Scheduled") else None]
    clock_label = " · ".join(b for b in clock_bits if b) or kind.get("shortDetail") or ""
    if status == "final":
        clock_label = status_obj.get("summary") or kind.get("description") or "Result"

    lines: dict[str, dict[str, Any]] = {}
    for card in data.get("matchcards") or []:
        headline = card.get("headline")
        for p in card.get("playerDetails") or []:
            pid = str(p.get("playerID") or "")
            if not pid:
                continue
            entry = lines.setdefault(pid, {"bat": None, "bowl": None, "name": p.get("playerName")})
            if headline == "Batting":
                entry["bat"] = p
            elif headline == "Bowling":
                entry["bowl"] = p

    out_lines: dict[str, Line] = {}
    for pid, entry in lines.items():
        stats, heads = [], []
        bat = entry["bat"]
        if bat and (bat.get("ballsFaced") not in (None, "", "0") or bat.get("runs") not in (None, "", "0")):
            runs, balls = _int(bat.get("runs")), _int(bat.get("ballsFaced"))
            not_out = (bat.get("dismissal") or "").lower() in ("", "not out", "batting", "notout")
            heads.append(f"{runs}{'*' if not_out else ''} ({balls})")
            stats += [
                {"label": "Runs", "value": f"{runs}{'*' if not_out else ''}"},
                {"label": "Balls", "value": str(balls)},
                {"label": "4s", "value": str(_int(bat.get("fours")))},
                {"label": "6s", "value": str(_int(bat.get("sixes")))},
                {"label": "Strike rate", "value": f"{runs * 100 / balls:.1f}" if balls else "–"},
            ]
            if not not_out and bat.get("dismissal"):
                stats.append({"label": "Out", "value": str(bat.get("dismissal"))})
        bowl = entry["bowl"]
        if bowl and bowl.get("overs") not in (None, "", "0"):
            heads.append(f"{_int(bowl.get('wickets'))}/{_int(bowl.get('conceded'))} ({bowl.get('overs')} ov)")
            stats += [
                {"label": "Wickets", "value": str(_int(bowl.get("wickets")))},
                {"label": "Overs", "value": str(bowl.get("overs"))},
                {"label": "Runs conceded", "value": str(_int(bowl.get("conceded")))},
                {"label": "Economy", "value": str(bowl.get("economyRate") or "–")},
            ]
        if heads:
            out_lines[pid] = Line(headline=" · ".join(heads), stats=stats)

    return Snapshot(
        status=status,
        score_label=score_label,
        clock_label=clock_label,
        state={
            "kind": "cricket",
            "teams": teams,
            "innings": innings,
            "summary": status_obj.get("summary") or "",
            "format": (comp.get("class") or {}).get("generalClassCard"),
            "player_of_match": next((f["athlete"].get("displayName") for f in status_obj.get("featuredAthletes") or [] if f.get("name") == "playerOfTheMatch" and f.get("athlete")), None),
        },
        lines=out_lines,
        moments=[],
        source_url=f"https://www.espncricinfo.com/ci/engine/match/{header.get('id')}.html",
    )


def involves_team(f: Fixture, ref: PlayerRef) -> bool:
    """Is one of the player's sides in this fixture: the source's team id, or another side by exact name."""
    if set(ref.team_ids) & set(f.team_ids):
        return True
    wanted = {espn.team_key(t) for t in [*ref.team_names, *ref.other_teams] if t}
    return any(espn.team_key(n) in wanted for n in f.team_names)


def squad_membership(data: dict[str, Any], athlete_id: str) -> bool | None:
    """Is the player in this match? Rosters once it's played, squads once announced, None before either."""
    def ids(groups: list[dict[str, Any]], key: str) -> set[str]:
        return {str((p.get("athlete") or p).get("id")) for g in groups for p in g.get(key) or [] if (p.get("athlete") or p).get("id")}

    playing = ids(data.get("rosters") or [], "roster")
    if playing:
        return athlete_id in playing
    squad = ids(data.get("squads") or [], "athletes")
    if squad:
        return athlete_id in squad
    return None


def match_format(fixture: Fixture) -> str | None:
    detail = fixture.detail or ""
    return next((f for f in ("T20I", "ODI", "Test", "T20") if f in detail), None)


def recent_form(athlete_id: str, recent: list[tuple[Fixture, Snapshot]], fmt: str | None = None) -> tuple[list[dict[str, str]], str]:
    """Batting and bowling numbers over the player's recent completed matches, from their scorecards."""
    runs = balls = outs = innings = hundreds = fifties = 0
    best = None
    wickets = conceded = 0
    balls_bowled = 0
    matches = 0
    formats: list[str] = []
    for fixture, snap in recent:
        line = snap.lines.get(athlete_id)
        if line is None:
            continue
        matches += 1
        if (f := match_format(fixture)) and f not in formats:
            formats.append(f)
        values = {s["label"]: s["value"] for s in line.stats}
        if "Runs" in values:
            r = _int(values["Runs"].rstrip("*"))
            innings += 1
            runs += r
            balls += _int(values.get("Balls"))
            if not values["Runs"].endswith("*"):
                outs += 1
            hundreds += r >= 100
            fifties += 50 <= r < 100
            best = max(best or 0, r)
        if "Wickets" in values:
            wickets += _int(values["Wickets"])
            conceded += _int(values.get("Runs conceded"))
            whole, _, part = str(values.get("Overs", "0")).partition(".")
            balls_bowled += _int(whole) * 6 + _int(part or 0)
    if not matches:
        return [], ""
    stats = []
    if innings:
        stats += [
            {"label": "Runs", "value": str(runs)},
            {"label": "Average", "value": f"{runs / outs:.2f}" if outs else "–"},
            {"label": "Strike rate", "value": f"{runs * 100 / balls:.1f}" if balls else "–"},
            {"label": "Highest", "value": str(best)},
            {"label": "100s / 50s", "value": f"{hundreds} / {fifties}"},
        ]
    if balls_bowled:
        stats += [
            {"label": "Wickets", "value": str(wickets)},
            {"label": "Economy", "value": f"{conceded * 6 / balls_bowled:.2f}"},
        ]
    kinds = fmt or " & ".join(formats)
    note = f"Last {matches} match{'es' if matches != 1 else ''}{f' ({kinds})' if kinds else ''}, from ESPNcricinfo scorecards"
    return stats, note


# ---------------------------------------------------------------- the adapter


class EspnCricket:
    system = "espn_cricket"
    sports = ("cricket",)
    live_cadence = 8

    def find_player(self, name: str) -> PlayerRef | None:
        for hit in espn.search_athletes(name, SPORT_UID):
            if not espn.names_match(name, hit["name"]):
                continue
            athlete = espn.get_json(f"{espn.WEB}/common/v3/sports/cricket/athletes/{hit['athlete_id']}", ttl=6 * 3600).get("athlete") or {}
            team = athlete.get("team") or {}
            team_ids = [str(team["id"])] if team.get("id") else []
            return PlayerRef(self.system, hit["athlete_id"], athlete.get("displayName") or hit["name"], team_ids, [team.get("displayName") or ""], hit.get("url"))
        return None

    def _day(self, day: datetime) -> list[Fixture]:
        today = datetime.now(timezone.utc).date()
        ttl = 1800 if day.date() >= today - timedelta(days=1) else 24 * 3600
        data = espn.get_json(f"{espn.WEB}/personalized/v2/scoreboard/header?sport=cricket&dates={day:%Y%m%d}", ttl=ttl)
        return parse_day(data)

    def _summary(self, locator: dict[str, Any], final: bool) -> dict[str, Any]:
        ttl = 24 * 3600 if final else 1800
        return espn.get_json(f"{espn.SITE}/cricket/{locator['league']}/summary?event={locator['event']}", ttl=ttl)

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        """The player's team's matches, minus those whose squad or XI leaves the player out.

        Candidates are matches of any side the player plays for: the athlete record's team plus the
        sides the agent named (ESPN files Sam Harper under Australia while he plays for Australia A).
        A team also fields different squads (India's T20I, ODI and Asian Games sides), so every
        candidate is kept only if its squad or XI includes the player, or no squad is out yet.
        """
        now = datetime.now(timezone.utc)

        def day(offset: int) -> list[Fixture]:
            try:
                return self._day(now + timedelta(days=offset))
            except AdapterError:
                return []

        with ThreadPoolExecutor(max_workers=8) as pool:
            candidates: dict[str, Fixture] = {}
            for fixtures in pool.map(day, range(-DAYS_BACK, DAYS_AHEAD + 1)):
                for f in fixtures:
                    if involves_team(f, ref):
                        candidates.setdefault(f.source_id, f)

            def keep(f: Fixture) -> Fixture | None:
                if f.status == "postponed":
                    return f
                try:
                    member = squad_membership(self._summary(f.locator, f.status == "final"), ref.athlete_id)
                except AdapterError:
                    member = None
                if member is False:
                    return None
                if member is None and f.status == "scheduled":
                    f.detail = f"{f.detail} · squad not announced" if f.detail else "Squad not announced"
                return f

            kept = [f for f in pool.map(keep, candidates.values()) if f is not None]
        return sorted(kept, key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        data = espn.get_json(f"{espn.SITE}/cricket/{locator['league']}/summary?event={locator['event']}", ttl=24 * 3600 if final else 5)
        return parse_summary(data)

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        r = fixture.result or {}
        return r.get("summary") or " · ".join(f"{t['name']} {t['score']}" for t in r.get("teams", []))

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        return recent_form(ref.athlete_id, recent)
