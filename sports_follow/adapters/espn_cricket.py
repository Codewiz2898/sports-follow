"""Cricket from ESPN (the same ids as ESPNcricinfo): calendar, live scorecards, recent form.

ESPN has no cricket team-schedule endpoint, so fixtures come from the day-by-day scoreboard feed:
one request per day, cached and shared by every cricket player, filtered by team. Career stats
aren't served either (the stats endpoint returns {}), so stats are computed from the player's
recent completed scorecards, which is exact for the matches it covers.
"""

from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import espn
from .base import AdapterError, Fixture, Line, PlayerRef, Snapshot

SPORT_UID = "s:200~"
DAYS_AHEAD = 14
DAYS_BACK = 30  # enough completed matches for recent form
HISTORY_DAYS = 180  # how far back history.py reads, once, ten days at a time

_STATUS = {"pre": "scheduled", "in": "live", "post": "final"}
# Status type ids of matches that weren't played, though their state is "post": the feed's types have
# an id and a description ("Postponed", "Abandoned"), no name.
_POSTPONED, _ABANDONED = "113", "6"


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


def _off(kind: dict[str, Any], played: bool) -> bool:
    """A match that won't be played or wasn't: postponed, cancelled, or abandoned without a ball bowled.
    One abandoned after play is a no result, with its scorecard."""
    words = f"{kind.get('name') or ''} {kind.get('description') or ''}".upper()
    if str(kind.get("id")) == _POSTPONED or "POSTPONE" in words or "CANCEL" in words:
        return True
    return (str(kind.get("id")) == _ABANDONED or "ABANDON" in words) and not played


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
            if _off((e.get("fullStatus") or {}).get("type") or {}, any(c.get("score") for c in competitors)):
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
    if _off(kind, bool(innings)):
        status = "postponed"

    def fmt(i: dict[str, Any]) -> str:
        wk = "" if i["wickets"] >= 10 else f"/{i['wickets']}"
        return f"{i['abbr'] or i['team']} {i['runs']}{wk} ({i['overs']} ov)"

    score_label = " · ".join(fmt(i) for i in innings) or " v ".join(t["name"] or "" for t in teams)
    clock_bits = [status_obj.get("session"), kind.get("description") if kind.get("description") not in ("Live", "Scheduled") else None]
    clock_label = " · ".join(b for b in clock_bits if b) or kind.get("shortDetail") or ""
    if status == "final":
        clock_label = status_obj.get("summary") or kind.get("description") or "Result"
    elif status == "postponed":
        # "Postponed" or "Abandoned"; a postponed match's summary is still its old start time.
        clock_label = kind.get("description") or "Postponed"

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
        lines=parse_lines(data),
        moments=[],
        source_url=f"https://www.espncricinfo.com/ci/engine/match/{header.get('id')}.html",
    )


def _numbers(stats: dict[str, Any]) -> dict[str, str]:
    """One innings of a roster's linescores as {name: value}: runs, ballsFaced, outs, balls, wickets…"""
    return {s.get("name"): str(s.get("displayValue") or "") for c in stats.get("categories") or [] for s in c.get("stats") or []}


def parse_lines(data: dict[str, Any]) -> dict[str, Line]:
    """Each player's line, from the rosters, which hold every innings of both sides (the summary's
    matchcards hold only the latest innings, and lag the score). The headline has every innings
    ("103 (152) & 48* (60) · 3/45 (20 ov)"); the stats are the latest batting and bowling innings,
    which is what the fifty, hundred, out and wicket alerts read (as Sportmonks' lines)."""
    lines: dict[str, Line] = {}
    for side in data.get("rosters") or []:
        for p in side.get("roster") or []:
            pid = str((p.get("athlete") or {}).get("id") or "")
            bats, bowls = [], []
            for ls in sorted(p.get("linescores") or [], key=lambda ls: _int(ls.get("period"))):
                stats = ls.get("statistics") or {}
                n = _numbers(stats)
                out = _int(n.get("outs")) > 0
                # A batter yet to face a ball isn't an innings yet, as on Sportmonks' cards.
                if _int(n.get("batted")) and (_int(n.get("ballsFaced")) or _int(n.get("runs")) or out):
                    # The scorecard's words, in HTML: "c &dagger;Curtis b Kerr", " b Agar".
                    how = ((stats.get("batting") or {}).get("outDetails") or {}).get("shortText") or ""
                    bats.append((n, out, html.unescape(how).strip() or "out"))
                if _int(n.get("balls")):
                    bowls.append(n)
            if not pid or not (bats or bowls):
                continue
            heads, stats_out = [], []
            if bats:
                heads.append(" & ".join(f"{_int(n.get('runs'))}{'' if out else '*'} ({_int(n.get('ballsFaced'))})" for n, out, _ in bats))
                n, out, how = bats[-1]
                runs, balls = _int(n.get("runs")), _int(n.get("ballsFaced"))
                stats_out += [
                    {"label": "Runs", "value": f"{runs}{'' if out else '*'}"},
                    {"label": "Balls", "value": str(balls)},
                    {"label": "4s", "value": str(_int(n.get("fours")))},
                    {"label": "6s", "value": str(_int(n.get("sixes")))},
                    {"label": "Strike rate", "value": f"{runs * 100 / balls:.1f}" if balls else "–"},
                ]
                if out:
                    stats_out.append({"label": "Out", "value": how})
            if bowls:
                heads.append(" & ".join(f"{_int(n.get('wickets'))}/{_int(n.get('conceded'))} ({n.get('overs') or '0'} ov)" for n in bowls))
                n = bowls[-1]
                stats_out += [
                    {"label": "Wickets", "value": str(_int(n.get("wickets")))},
                    {"label": "Overs", "value": n.get("overs") or "0"},
                    {"label": "Runs conceded", "value": str(_int(n.get("conceded")))},
                    {"label": "Economy", "value": f"{_int(n.get('conceded')) * 6 / _int(n.get('balls')):.2f}"},
                ]
            lines[pid] = Line(headline=" · ".join(heads), stats=stats_out)
    return lines


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
    lines, formats = [], []
    for fixture, snap in recent:
        line = snap.lines.get(athlete_id)
        if line is None:
            continue
        lines.append({"headline": line.headline, "stats": line.stats})
        if (f := match_format(fixture)) and f not in formats:
            formats.append(f)
    if not lines:
        return [], ""
    kinds = fmt or " & ".join(formats)
    note = f"Last {len(lines)} match{'es' if len(lines) != 1 else ''}{f' ({kinds})' if kinds else ''}, from ESPNcricinfo scorecards"
    return batting_bowling(lines), note


_BAT = re.compile(r"(\d+)(\*?) \((\d+)\)")
_BOWL = re.compile(r"(\d+)/(\d+) \(([\d.]+) ov\)")


def innings(headline: str | None) -> tuple[list[tuple[int, bool, int]], list[tuple[int, int, str]]]:
    """Every innings in a cricket line's headline ("103 (152) & 48* (60) · 3/45 (20 ov)"): batting as
    (runs, out, balls), bowling as (wickets, runs conceded, overs)."""
    bats, bowls = [], []
    for part in re.split(r" & | · ", headline or ""):
        if m := _BAT.fullmatch(part):
            bats.append((int(m[1]), not m[2], int(m[3])))
        elif m := _BOWL.fullmatch(part):
            bowls.append((int(m[1]), int(m[2]), m[3]))
    return bats, bowls


def batting_bowling(lines: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Runs, average, strike rate, highest, 100s/50s, wickets and economy over some matches' lines
    ({"headline", "stats"}), every innings counted: a line's stats are only its latest innings, so a
    Test's two come from the headline."""
    runs = balls = outs = batted = hundreds = fifties = 0
    best = None
    wickets = conceded = 0
    balls_bowled = 0
    for line in lines:
        bats, bowls = innings(line.get("headline"))
        values = {s["label"]: s["value"] for s in line.get("stats") or []}
        if not bats and "Runs" in values:
            bats = [(_int(values["Runs"].rstrip("*")), not values["Runs"].endswith("*"), _int(values.get("Balls")))]
        if not bowls and "Wickets" in values:
            bowls = [(_int(values["Wickets"]), _int(values.get("Runs conceded")), str(values.get("Overs", "0")))]
        for r, out, b in bats:
            batted += 1
            runs += r
            balls += b
            outs += out
            hundreds += r >= 100
            fifties += 50 <= r < 100
            best = max(best or 0, r)
        for w, c, overs in bowls:
            wickets += w
            conceded += c
            whole, _, part = overs.partition(".")
            balls_bowled += _int(whole) * 6 + _int(part or 0)
    stats = []
    if batted:
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
    return stats


# ---------------------------------------------------------------- the adapter


class EspnCricket:
    system = "espn_cricket"
    sports = ("cricket",)
    live_cadence = 8

    def find_player(self, name: str) -> PlayerRef | None:
        for hit in espn.search_athletes(name, SPORT_UID):
            if espn.names_match(name, hit["name"]):
                return self.player(hit["athlete_id"])
        return None

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        athlete = espn.get_json(f"{espn.WEB}/common/v3/sports/cricket/athletes/{athlete_id}", ttl=6 * 3600).get("athlete") or {}
        if not athlete.get("id"):
            return None
        team = athlete.get("team") or {}
        team_ids = [str(team["id"])] if team.get("id") else []
        return PlayerRef(self.system, str(athlete["id"]), athlete.get("displayName") or "", team_ids, [team.get("displayName") or ""], espn.profile_link(athlete), born=espn.birth_date(athlete))

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
        return self._matches(ref, [now + timedelta(days=offset) for offset in range(-DAYS_BACK, DAYS_AHEAD + 1)])

    def history_pages(self, ref: PlayerRef, anchor: date) -> list[dict[str, Any]]:
        offsets = list(range(DAYS_BACK + 1, HISTORY_DAYS + 1))
        return [{"days": [(anchor - timedelta(days=d)).strftime("%Y%m%d") for d in offsets[i:i + 10]], "cost": len(offsets[i:i + 10])} for i in range(0, len(offsets), 10)]

    def history(self, ref: PlayerRef, page: dict[str, Any]) -> list[Fixture]:
        days = [datetime.strptime(d, "%Y%m%d").replace(tzinfo=timezone.utc) for d in page["days"]]
        return [f for f in self._matches(ref, days) if f.status == "final"]

    def _matches(self, ref: PlayerRef, days: list[datetime]) -> list[Fixture]:
        """The player's matches on these days: their teams' matches, minus squads that leave them out."""

        def day(when: datetime) -> list[Fixture]:
            try:
                return self._day(when)
            except AdapterError:
                return []

        with ThreadPoolExecutor(max_workers=8) as pool:
            candidates: dict[str, Fixture] = {}
            for fixtures in pool.map(day, days):
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
