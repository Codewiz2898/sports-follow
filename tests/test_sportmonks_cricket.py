"""Sportmonks cricket parsers, against small made-up responses in Sportmonks' shapes (their terms
don't allow republishing real ones, so none are committed)."""

from __future__ import annotations

import copy
import time
from dataclasses import asdict
from datetime import date
from types import SimpleNamespace

import pytest

from sports_follow import moments, structured
from sports_follow.adapters import Snapshot, sportmonks, sportmonks_cricket as sc
from sports_follow.adapters import budget

IND, AFG = 10, 46
OPENER, KEEPER, TAILENDER, SPINNER, SEAMER = 3338, 2900, 9001, 9002, 9003

INDIA = {"id": IND, "name": "India", "code": "IND"}
AFGHANISTAN = {"id": AFG, "name": "Afghanistan", "code": "AFG"}


def _player(pid: int, last: str) -> dict:
    return {"id": pid, "lastname": last, "fullname": f"A {last}"}


def _bat(pid: int, runs: int, balls: int, result: str | None, board: str = "S1", sort: int = 1, **who) -> dict:
    wicket = result not in (None, "Not Out")
    return {"player_id": pid, "team_id": IND, "scoreboard": board, "sort": sort, "score": runs, "ball": balls, "four_x": 2, "six_x": 1,
            "result": {"name": result or "Not Out", "is_wicket": wicket}, **who}


def _match(status: str, runs: list[dict], **extra) -> dict:
    return {
        "id": 71215, "status": status, "type": "T20I", "round": "3rd T20I", "season_id": 1715, "starting_at": "2026-09-17T14:00:00.000000Z",
        "league": {"name": "Twenty20 International", "type": "phase"}, "stage": {"name": "Afghanistan vs India in India"},
        "venue": {"name": "Arun Jaitley Stadium", "city": "Delhi"},
        "localteam": INDIA, "visitorteam": AFGHANISTAN, "localteam_id": IND, "visitorteam_id": AFG, "runs": runs, **extra,
    }


FIRST = {"team_id": IND, "inning": 1, "score": 221, "wickets": 7, "overs": 20}
CHASE = {"team_id": AFG, "inning": 2, "score": 94, "wickets": 10, "overs": 14.1}


def test_fixture_reads_series_round_venue_and_result():
    f = sc.parse_fixture(_match("Finished", [FIRST, CHASE], note="India won by 127 runs", winner_team_id=IND))
    assert (f.title, f.competition, f.detail, f.venue) == ("India v Afghanistan", "Afghanistan vs India in India", "3rd T20I", "Arun Jaitley Stadium, Delhi")
    assert f.start_utc.isoformat() == "2026-09-17T14:00:00+00:00" and f.locator == {"fixture": "71215"}
    assert f.result["teams"] == [{"id": "10", "name": "India", "score": "221/7", "winner": True}, {"id": "46", "name": "Afghanistan", "score": "94", "winner": False}]
    assert sc.SportmonksCricket().result_label(f, ["10"]) == "India won by 127 runs"
    league = sc.parse_fixture({**_match("NS", []), "league": {"name": "Big Bash League", "type": "league"}, "stage": {"name": "Regular"}, "round": "5th Match"})
    assert (league.competition, league.status, league.result) == ("Big Bash League", "scheduled", None)


def test_statuses_map_to_our_four():
    assert [sc._status(s) for s in ("NS", "Delayed", "1st Innings", "Innings Break", "Stump Day 2", "Tea Break", "Finished", "Aban.", "Postp.")] == [
        "scheduled", "scheduled", "live", "live", "live", "live", "final", "postponed", "postponed"]


def test_a_players_match_is_their_xi_then_their_squad():
    xi = _match("Finished", [], lineup=[{"id": OPENER}, {"id": KEEPER}])
    assert sc.in_match(xi, str(OPENER), None) is True
    assert sc.in_match(xi, str(TAILENDER), {str(TAILENDER)}) is False  # the XI outranks the squad
    upcoming = _match("NS", [])
    assert sc.in_match(upcoming, str(OPENER), {str(OPENER)}) is True
    assert sc.in_match(upcoming, "278", {str(OPENER)}) is False  # not in this format's squad
    assert sc.in_match(upcoming, str(OPENER), set()) is None  # squad not announced


def test_dismissals_read_like_a_scorecard():
    bowler, fielder = _player(1, "Khan"), _player(2, "Gurbaz")
    assert sc.dismissal({"result": {"name": "Catch Out"}, "bowler": bowler, "catchstump": fielder}) == "c Gurbaz b Khan"
    assert sc.dismissal({"result": {"name": "Catch Out"}, "bowler": bowler, "catchstump": bowler}) == "c & b Khan"
    assert sc.dismissal({"result": {"name": "Run Out"}, "catchstump": fielder}) == "run out (Gurbaz)"
    assert sc.dismissal({"result": {"name": "Run Out (Sub)"}, "runoutby": fielder}) == "run out (Gurbaz (sub))"
    assert sc.dismissal({"result": {"name": "Stump Out"}, "bowler": bowler, "catchstump": fielder}) == "st Gurbaz b Khan"
    assert sc.dismissal({"result": {"name": "LBW OUT"}, "bowler": bowler}) == "lbw b Khan"
    assert sc.dismissal({"result": {"name": "Clean Bowled"}, "bowler": bowler}) == "b Khan"


def test_a_finished_match_has_innings_lines_and_the_result():
    batting = [
        _bat(OPENER, 108, 34, "Catch Out", sort=2, bowler=_player(1, "Khan"), catchstump=_player(2, "Gurbaz")),
        _bat(KEEPER, 50, 35, "Not Out", sort=1),
        _bat(TAILENDER, 0, 0, "Not Out", sort=9),  # walked out for the last ball: didn't bat
    ]
    bowling = [{"player_id": SPINNER, "scoreboard": "S2", "sort": 1, "overs": 3.2, "runs": 21, "wickets": 3, "rate": 6.3}]
    snap = sc.parse_match(_match("Finished", [CHASE, FIRST], note="India won by 127 runs", winner_team_id=IND, batting=batting, bowling=bowling, manofmatch={"fullname": "Abhishek Sharma"}))
    assert (snap.status, snap.score_label, snap.clock_label) == ("final", "IND 221/7 (20 ov) · AFG 94 (14.1 ov)", "India won by 127 runs")
    assert [(i["abbr"], i["runs"], i["target"], i["batting"]) for i in snap.state["innings"]] == [("IND", 221, None, False), ("AFG", 94, 222, False)]
    assert (snap.state["format"], snap.state["player_of_match"], snap.state["summary"]) == ("T20I", "Abhishek Sharma", "India won by 127 runs")

    opener = snap.lines[str(OPENER)]
    assert opener.headline == "108 (34)"
    assert opener.stats[0] == {"label": "Runs", "value": "108"} and {"label": "Out", "value": "c Gurbaz b Khan"} in opener.stats
    assert snap.lines[str(KEEPER)].headline == "50* (35)"
    assert str(TAILENDER) not in snap.lines
    assert snap.lines[str(SPINNER)].headline == "3/21 (3.2 ov)"
    assert snap.lines[str(SPINNER)].stats == [{"label": "Wickets", "value": "3"}, {"label": "Overs", "value": "3.2"}, {"label": "Runs conceded", "value": "21"}, {"label": "Economy", "value": "6.30"}]


def test_a_test_match_line_shows_both_innings_and_scores_the_latest():
    batting = [_bat(OPENER, 12, 30, "LBW OUT", board="S1", bowler=_player(1, "Khan")), _bat(OPENER, 45, 60, "Not Out", board="S3")]
    snap = sc.parse_match({**_match("3rd Innings", [FIRST, CHASE]), "type": "Test/5day", "batting": batting})
    assert snap.lines[str(OPENER)].headline == "12 (30) & 45* (60)"
    assert snap.lines[str(OPENER)].stats[0] == {"label": "Runs", "value": "45*"}
    assert snap.state["format"] == "Test" and snap.state["innings"][1]["target"] is None


def test_a_live_chase_says_what_is_needed():
    snap = sc.parse_match(_match("2nd Innings", [FIRST, {"team_id": AFG, "inning": 2, "score": 180, "wickets": 4, "overs": 15.2}]))
    assert snap.clock_label == "2nd Innings · Afghanistan need 42 from 28 balls"
    assert snap.state["innings"][1]["batting"] and not snap.state["innings"][0]["batting"]
    assert sc.parse_match(_match("Innings Break", [FIRST])).state["innings"][0]["batting"] is False


def test_nothing_before_the_toss():
    snap = sc.parse_match(_match("NS", [], batting=[_bat(OPENER, 0, 0, None)]))
    assert (snap.status, snap.score_label, snap.lines) == ("scheduled", "India v Afghanistan", {})


def test_live_lines_raise_the_usual_cricket_alerts():
    def seen(runs: int, balls: int, result: str | None, wickets: int) -> moments.Seen:
        batting = [_bat(OPENER, runs, balls, result, bowler=_player(1, "Khan"), catchstump=_player(2, "Gurbaz"))]
        bowling = [{"player_id": OPENER, "scoreboard": "S2", "overs": 4, "runs": 30, "wickets": wickets, "rate": 7.5}]
        snap = sc.parse_match(_match("2nd Innings", [FIRST, CHASE], batting=batting, bowling=bowling))
        return moments.Seen(snap.status, snap.state, asdict(snap.lines[str(OPENER)]), snap.score_label)

    found = moments.detect("cricket", "Abhishek", str(OPENER), seen(48, 30, None, 0), seen(52, 32, "Catch Out", 3))
    assert [(f.kind, f.title) for f in found] == [("fifty", "Abhishek reaches 50"), ("out", "Abhishek out for 52"), ("wickets", "Abhishek takes 3 wickets")]


def test_a_hundred_in_each_innings_of_a_test_is_told_twice():
    # 103 in the first innings, 104 in the third: each moment has its own detail, so its own key.
    def seen(*batting: dict) -> moments.Seen:
        snap = sc.parse_match({**_match("3rd Innings", [FIRST, CHASE]), "type": "Test/5day", "batting": list(batting)})
        return moments.Seen(snap.status, snap.state, asdict(snap.lines[str(OPENER)]), snap.score_label)

    caught = {"bowler": _player(1, "Khan"), "catchstump": _player(2, "Gurbaz")}
    first = _bat(OPENER, 103, 152, "Catch Out", **caught)
    polls = [
        seen(_bat(OPENER, 48, 70, None)), seen(_bat(OPENER, 103, 150, None)), seen(first),
        seen(first, _bat(OPENER, 48, 60, None, board="S3")), seen(first, _bat(OPENER, 104, 140, None, board="S3")),
        seen(first, _bat(OPENER, 104, 141, "Catch Out", board="S3", **caught)),
    ]
    found = [f for before, now in zip(polls, polls[1:]) for f in moments.detect("cricket", "Abhishek", str(OPENER), before, now)]
    assert [f.detail for f in found] == ["50", "100", "out", "i2:50", "i2:100", "i2:out"]
    assert found[-1].body == "104 (141) · 2nd innings · c Gurbaz b Khan"


def test_current_teams_are_each_competitions_latest_recent_season():
    seasons = {1: {"name": "2024", "league_id": 3}, 2: {"name": "2026", "league_id": 3}, 3: {"name": "2022/2023", "league_id": 5}, 4: {"name": "2025/2026", "league_id": 5}}
    player = {"teams": [
        {"id": 50, "name": "Melbourne Renegades", "national_team": False, "in_squad": {"season_id": 3, "league_id": 5}},
        {"id": 51, "name": "Melbourne Stars", "national_team": False, "in_squad": {"season_id": 4, "league_id": 5}},
        {"id": 10, "name": "India", "national_team": True, "in_squad": {"season_id": 2, "league_id": 3}},
        {"id": 99, "name": "Old Side", "national_team": False, "in_squad": {"season_id": 1, "league_id": 7}},
        {"id": 77, "name": "Unknown Season", "national_team": False, "in_squad": {"season_id": 555, "league_id": 8}},
    ]}
    assert sc.current_teams(player, seasons, date(2026, 9, 30)) == [("10", "India"), ("51", "Melbourne Stars")]
    retired = {"teams": [{"id": 10, "name": "India", "national_team": True, "in_squad": {"season_id": 1, "league_id": 3}}]}
    assert sc.current_teams(retired, seasons, date(2026, 9, 30)) == []  # last T20I season 2024


def test_placeholder_birth_dates_are_unknown():
    assert sc.born("2000-09-04") == "2000-09-04"
    assert sc.born("2001-01-01") is None and sc.born(None) is None


def test_form_note_names_the_formats():
    snap = sc.parse_match(_match("Finished", [FIRST, CHASE], batting=[_bat(OPENER, 60, 30, "Clean Bowled", bowler=_player(1, "Khan"))]))
    stats, note = sc.recent_form(str(OPENER), [(None, snap), (None, snap)])
    assert note == "Last 2 matches (T20I), from Sportmonks scorecards"
    assert stats[0] == {"label": "Runs", "value": "120"}


def test_cricket_keeps_the_match_endpoint_for_live_matches():
    budget.observe(sportmonks.BUDGET, "Cricket fixtures", 0, 600)
    assert sc.SportmonksCricket().spare()  # lists of fixtures are counted apart from matches
    budget.observe(sportmonks.BUDGET, sportmonks.CRICKET_MATCH, sportmonks.LIVE_RESERVE, 600)
    assert not sc.SportmonksCricket().spare()
    budget.observe(sportmonks.BUDGET, sportmonks.CRICKET_MATCH, sportmonks.LIVE_RESERVE + 1, 600)
    assert sc.SportmonksCricket().spare()


def test_cricket_requests_count_against_their_endpoint():
    assert sportmonks._bucket(f"{sportmonks.CRICKET}/fixtures/71012") == sportmonks.CRICKET_MATCH
    assert sportmonks._bucket(f"{sportmonks.CRICKET}/fixtures") == "Cricket fixtures"
    assert sportmonks._bucket(f"{sportmonks.CRICKET}/teams/10/squad/1689") == "Cricket teams/{id}/squad/{id}"
    assert sportmonks._bucket(f"{sportmonks.CRICKET}/livescores") == "Cricket livescores"  # the live lane's own 2,000


def test_the_xi_is_the_team_sheet_before_the_start():
    snap = sc.parse_match(_match("NS", [], lineup=[{"id": OPENER, "lineup": {"substitution": False}}, {"id": KEEPER, "lineup": {"substitution": True}}]))
    assert snap.lines == {} and snap.squad == {str(OPENER): "starting", str(KEEPER): "bench"}
    assert sc.parse_match(_match("NS", [])).squad == {}


# ---------------------------------------------------------------- the live lane


def _row(status: str, runs: list[dict] | None = None, lineup: list[dict] | None = None, **extra) -> dict:
    """A match as a match read and livescores both send it: every relation MATCH_INCLUDE asks for, empty
    until there's something in it."""
    return _match(status, runs or [], **{"lineup": lineup or [], "batting": [], "bowling": [], "manofmatch": None, **extra})


def _picked(pid: int, team: int, sub: bool = False) -> dict:
    return {"id": pid, "lastname": "X", "fullname": "A X", "lineup": {"team_id": team, "captain": False, "wicketkeeper": False, "substitution": sub}}


XI = [_picked(OPENER, IND), _picked(KEEPER, IND), _picked(TAILENDER, IND, sub=True), _picked(SPINNER, AFG)]
CAUGHT = {"bowler": _player(1, "Khan"), "catchstump": _player(2, "Gurbaz")}
FIFTY = _bat(OPENER, 52, 32, "Catch Out", **CAUGHT)
SPELL = {"player_id": OPENER, "team_id": IND, "scoreboard": "S2", "sort": 1, "overs": 3, "runs": 20, "wickets": 3, "rate": 6.67}
# The match at each poll worth telling: half an hour before, the toss, the opener's fifty, out, the
# break, three wickets in the chase, the result.
STAGES = [
    _row("NS"),
    _row("NS", lineup=XI),
    _row("1st Innings", [{**FIRST, "score": 60, "wickets": 0, "overs": 5.4}], XI, batting=[_bat(OPENER, 48, 30, None)]),
    _row("1st Innings", [{**FIRST, "score": 70, "wickets": 1, "overs": 6.1}], XI, batting=[FIFTY]),
    _row("Innings Break", [FIRST], XI, batting=[FIFTY]),
    _row("2nd Innings", [FIRST, {"team_id": AFG, "inning": 2, "score": 80, "wickets": 3, "overs": 10}], XI, batting=[FIFTY], bowling=[SPELL]),
    _row("Finished", [FIRST, CHASE], XI, batting=[FIFTY], bowling=[{**SPELL, "overs": 4, "runs": 30}], note="India won by 127 runs", winner_team_id=IND, manofmatch={"fullname": "A Opener"}),
]
TOSS, START, BREAK, FINISHED = 1, 2, 4, len(STAGES) - 1
ELSEWHERE = {**_row("2nd Innings", [FIRST, CHASE]), "id": 71299}  # another match on, nobody's following
LOC = {"fixture": "71215"}
STAGE_SECONDS = 120  # how long the match stays at each stage, on a timed run


def _in_play(row: dict) -> bool:
    return sc._status(row["status"]) == "live"


# Which matches livescores carries besides those in play isn't documented, so every reading is run.
CARRIED = {
    "all-day": lambda row: True,
    "in-play-only": _in_play,
    "from-the-start-through-the-result": lambda row: row["status"] != "NS",
    "never": lambda row: False,
}


class Sportmonks:
    """Sportmonks cricket behind the real client (sportmonks.get_json, with its cache and the source
    budget), on a clock the test moves. The match is at STAGES[stage], or, timed, at the stage the clock
    has reached. Livescores carries it, beside a match nobody follows, while carried(row) says so (thin:
    without its batting). The match endpoint answers with fixture_row when that's set (lagging, say), and
    404 for any other match. Every request is kept, as (seconds since the start, endpoint)."""

    START = 1_790_000_000.0

    def __init__(self, monkeypatch, carried=CARRIED["all-day"], timed=False, thin=False):
        self.now, self.stage, self.fixture_row = self.START, 0, None
        self.carried, self.timed, self.thin, self.sent = carried, timed, thin, []
        monkeypatch.setattr(time, "time", lambda: self.now)
        monkeypatch.setattr(time, "monotonic", lambda: self.now)
        monkeypatch.setattr(sportmonks, "token", lambda: "example")
        monkeypatch.setattr(sportmonks, "_cache", {})
        monkeypatch.setattr(sportmonks, "_client", self)

    def at(self) -> int:
        return min(int((self.now - self.START) // STAGE_SECONDS), FINISHED) if self.timed else self.stage

    def get(self, url, params=None, headers=None):
        assert params == {"include": sc.MATCH_INCLUDE} and headers == {"authorization": "example"}
        path = url.removeprefix(sc.API)
        self.sent.append((int(self.now - self.START), path))
        row = STAGES[self.at()]
        if path == "/livescores":
            ours = [{k: v for k, v in row.items() if k != "batting"} if self.thin else row] if self.carried(row) else []
            body, status = {"data": [ELSEWHERE, *ours]}, 200
        elif path == "/fixtures/71215":
            body, status = {"data": STAGES[self.at()] if self.fixture_row is None else self.fixture_row}, 200
        else:
            body, status = {"message": "No result(s) found"}, 404
        return SimpleNamespace(status_code=status, headers={}, json=lambda: copy.deepcopy(body))

    def requests(self, since: int = 0) -> list[str]:
        return [path for at, path in self.sent if at >= since]


def _follow(monkeypatch, sm: Sportmonks, lane: bool) -> list[Snapshot]:
    """Poll the match from half an hour before to the result, as the worker does: poll_event's loop for
    it alone, or poll_lane's for every match on. Returns each snapshot handed to apply_poll."""
    adapter = sc.SportmonksCricket()
    applied: list[Snapshot] = []

    def apply_poll(event_id, live_binding, source, watchers, snap):
        changed = not applied or snap != applied[-1]
        applied.append(snap)
        return snap.status, 10 if snap.status == "live" else 60, changed

    def watching(event_id):
        over = applied and applied[-1].status == "final"
        return None if over else ({"adapter": adapter.system, "locator": LOC, "cadence": adapter.live_cadence}, adapter, [(1, str(OPENER))])

    def sleep(seconds):
        sm.now += seconds
        return True

    monkeypatch.setitem(structured.ADAPTERS, adapter.system, adapter)
    monkeypatch.setattr(structured, "_watching", watching)
    monkeypatch.setattr(structured, "lane_events", lambda system: [7] if watching(7) else [])
    monkeypatch.setattr(structured, "apply_poll", apply_poll)
    monkeypatch.setattr(structured.shutdown, "sleep", sleep)
    monkeypatch.setattr(structured.bus, "try_lock", lambda *args: "token")
    monkeypatch.setattr(structured.bus, "extend", lambda *args: True)
    monkeypatch.setattr(structured.bus, "release", lambda *args: None)
    with budget.in_lane("live"):
        for _ in range(10):  # sessions: each hands over after POLL_SESSION, and the next minute's tick starts another
            if applied and applied[-1].status == "final":
                break
            if lane:
                structured.poll_lane(adapter.system)
            else:
                structured.poll_event(7)
            sm.now += 60 - (sm.now - sm.START) % 60
    assert applied and applied[-1].status == "final"
    return applied


def _distinct(snaps: list[Snapshot]) -> list[Snapshot]:
    return [s for i, s in enumerate(snaps) if i == 0 or s != snaps[i - 1]]


def _told(snaps: list[Snapshot]) -> list[tuple[str, str]]:
    """The moments a follower of the opener hears, poll by poll (as apply_poll asks moments.detect)."""
    found, before = [], None
    for snap in snaps:
        line = snap.lines.get(str(OPENER))
        seen = moments.Seen(snap.status, snap.state, asdict(line) if line else None, snap.score_label, snap.clock_label, lineups=bool(snap.lines), role=snap.squad.get(str(OPENER)))
        found += [(f.kind, f.title) for f in moments.detect("cricket", "Abhishek", str(OPENER), before, seen)]
        before = seen
    return found


@pytest.mark.parametrize("carried", list(CARRIED.values()), ids=list(CARRIED))
def test_the_live_lane_tells_a_match_as_its_own_poll_did(monkeypatch, carried):
    # One match, from half an hour before to the result, polled by poll_event (a match read a poll, as
    # cricket was until BHA-49) and by poll_lane (livescores for every match on), through the real client
    # and its cache. Each sees every stage, so the moments, the scorecards and the result are the same.
    alone = _follow(monkeypatch, Sportmonks(monkeypatch, timed=True), lane=False)
    sm = Sportmonks(monkeypatch, carried, timed=True)
    together = _follow(monkeypatch, sm, lane=True)
    assert _distinct(together) == _distinct(alone) and len(_distinct(alone)) == len(STAGES)
    assert _told(_distinct(together)) == _told(_distinct(alone))
    assert [kind for kind, _ in _told(_distinct(together))] == ["lineup", "start", "fifty", "out", "wickets", "final"]

    # The refresh after the result reads the scorecard (cached a day): the result, whatever the lane read.
    sm.now += 1800
    final = sc.SportmonksCricket().snapshot(LOC, final=True)
    assert final == together[-1]
    assert (structured.snap_result(final), structured.contribution(final, str(OPENER))) == ("India won by 127 runs", "52 (32) · 3/30 (4 ov)")

    # A round is one livescores request, and a match it leaves out is read on its own at most once a
    # minute unless it's in play and changing.
    rounds: dict[int, list[str]] = {}
    for at, path in sm.sent[:-1]:
        rounds.setdefault(at, []).append(path)
    assert all(paths[0] == "/livescores" and len(paths) <= 2 for paths in rounds.values())
    carried_in_play = [paths for at, paths in rounds.items() if _in_play(STAGES[min(at // STAGE_SECONDS, FINISHED)]) and carried(STAGES[min(at // STAGE_SECONDS, FINISHED)])]
    assert all(paths == ["/livescores"] for paths in carried_in_play)
    before_toss = [at for at, path in sm.sent if path == "/fixtures/71215" and at < START * STAGE_SECONDS]
    assert all(b - a >= sc.SportmonksCricket.ALONE_EVERY for a, b in zip(before_toss, before_toss[1:]))


DELAYED = _row("Delayed", [FIRST], XI, batting=[FIFTY])  # rain in the break


@pytest.mark.parametrize(("answer", "status"), [(None, "live"), (STAGES[TOSS], "live"), (DELAYED, "scheduled")], ids=["read-again", "match-endpoint-lagging", "delayed"])
def test_a_match_livescores_drops_in_play_is_read_at_once(monkeypatch, answer, status):
    # Read on its own before the toss, carried once it starts, quiet through a break for over a minute,
    # then left out (a gap in livescores): it's read again at once, uncached. An answer lagging livescores
    # ("NS") doesn't take it back to not started, which would take it out of the lane (a 50-second cache
    # of the read before the toss did that, BHA-49 review); a delay after the start is told.
    sm = Sportmonks(monkeypatch, carried=_in_play)
    adapter = sc.SportmonksCricket()
    assert adapter.snapshots({7: LOC})[7].status == "scheduled"
    sm.stage = BREAK
    for _ in range(10):
        sm.now += 10
        assert adapter.snapshots({7: LOC})[7].clock_label == "Innings Break"
    sm.now, sm.carried, sm.fixture_row = sm.now + 10, CARRIED["never"], answer
    snap = adapter.snapshots({7: LOC})[7]
    assert snap.status == status
    assert snap.lines.get(str(OPENER)) == (sc.parse_match(STAGES[BREAK]).lines[str(OPENER)] if status == "live" else None)
    assert [(at, path) for at, path in sm.sent if path != "/livescores"] == [(0, "/fixtures/71215"), (110, "/fixtures/71215")]


def test_a_match_whose_read_fails_is_tried_again_thirty_seconds_on(monkeypatch):
    # A match Sportmonks has no more (renumbered, removed), or whose read keeps failing: tried again every
    # ALONE_RETRY seconds, not every round, while the matches livescores carries go on.
    sm = Sportmonks(monkeypatch, carried=_in_play)
    sm.stage = START
    adapter = sc.SportmonksCricket()
    for _ in range(8):
        assert set(adapter.snapshots({1: LOC, 3: {"fixture": "999"}})) == {1}
        sm.now += 10
    assert [at for at, path in sm.sent if path == "/fixtures/999"] == [0, 30, 60]


def test_a_match_livescores_finished_is_read_final_afterwards(monkeypatch):
    # Read on its own before the toss, then carried by livescores through to the result: the refresh that
    # follows asks for the scorecard (final, cached a day) and gets the result, not the copy from before
    # the toss, which would have stored the result without the player's line (BHA-49 review).
    sm = Sportmonks(monkeypatch, carried=CARRIED["from-the-start-through-the-result"])
    adapter = sc.SportmonksCricket()
    adapter.snapshots({7: LOC})
    sm.now, sm.stage = sm.now + 10, FINISHED
    assert adapter.snapshots({7: LOC})[7].status == "final"
    sm.now += 1800
    final = adapter.snapshot(LOC, final=True)
    assert final.status == "final" and final.lines[str(OPENER)].headline == "52 (32) · 3/30 (4 ov)"


def test_a_scorecard_cached_in_play_isnt_taken_for_the_result(monkeypatch):
    sm = Sportmonks(monkeypatch)
    sm.stage = START
    assert sc.SportmonksCricket().snapshot(LOC).status == "live"  # a poll in play, cached
    sm.now, sm.stage = sm.now + 1800, FINISHED
    assert sc.SportmonksCricket().snapshot(LOC, final=True).status == "final"
    assert sc.SportmonksCricket().snapshot(LOC, final=True).status == "final" and sm.requests() == ["/fixtures/71215"] * 2  # then kept


def test_live_matches_come_in_one_livescores_request(monkeypatch):
    sm = Sportmonks(monkeypatch, carried=_in_play)
    sm.stage = START
    snaps = sc.SportmonksCricket().snapshots({1: LOC, 2: LOC, 3: {"fixture": "999"}})
    # Two events, one match, carried: one request for both; a match Sportmonks has no more is left out.
    assert set(snaps) == {1, 2} and snaps[1] == snaps[2] and snaps[1] is not snaps[2]
    assert sm.requests() == ["/livescores", "/fixtures/999"] and sportmonks._cache == {}  # never cached


def test_a_quiet_match_read_on_its_own_is_read_once_a_minute(monkeypatch):
    # In play but not in livescores: read every round while it's changing; unchanged for a minute (a long
    # break, stumps), once a minute, as poll_event slows a quiet match; every round again once it moves.
    sm = Sportmonks(monkeypatch, carried=CARRIED["never"])
    sm.stage = BREAK
    adapter = sc.SportmonksCricket()
    for second in range(0, 300, 10):
        sm.now = sm.START + second
        sm.stage = BREAK if second < 200 else BREAK + 1
        adapter.snapshots({7: LOC})
    reads = [at for at, path in sm.sent if path == "/fixtures/71215"]
    assert reads == [0, 10, 20, 30, 40, 50, 60, 110, 160, 210, 220, 230, 240, 250, 260, 270]


def test_a_livescores_row_without_the_scorecard_is_read_on_its_own(monkeypatch, caplog):
    sm = Sportmonks(monkeypatch, thin=True)
    sm.stage = START + 1
    adapter = sc.SportmonksCricket()
    for _ in range(3):
        snap = adapter.snapshots({7: LOC})[7]
        sm.now += 10
    assert snap.lines[str(OPENER)].headline == "52 (32)"
    assert sm.requests() == ["/livescores", "/fixtures/71215"] * 3
    # Told once, so a livescores that never sends the scorecard (every match read on its own) is noticed.
    assert [r.getMessage() for r in caplog.records if "without" in r.getMessage()] == [
        "Sportmonks livescores sent cricket match 71215 without batting; matches it sends so are read on their own, a request each (told once)"]


def test_live_now_lists_every_match_on_with_its_playing_elevens(monkeypatch):
    def player(pid: int, name: str, team: int, sub: bool = False) -> dict:
        return {"id": pid, "fullname": name, "lineup": {"team_id": team, "substitution": sub}}

    live = _match("2nd Innings", [FIRST, {**CHASE, "score": 60, "wickets": 3, "overs": 8}],
                  lineup=[player(OPENER, "Abhishek Sharma", IND), player(SPINNER, "Rashid Khan", AFG), player(SEAMER, "A Sub", IND, sub=True)])
    asked = []
    monkeypatch.setattr(sportmonks, "get_json", lambda path, ttl, **params: asked.append(sportmonks._bucket(path)) or {"data": [live, _match("Finished", [FIRST, CHASE], id=71216)]})
    [game] = sc.SportmonksCricket().live_now()
    assert asked == ["Cricket livescores"]
    assert (game.title, game.teams, game.snapshot.status) == ("India v Afghanistan", ["India", "Afghanistan"], "live")
    assert [(p.athlete_id, p.name, p.team_names) for p in game.players] == [(str(OPENER), "Abhishek Sharma", ["India"]), (str(SPINNER), "Rashid Khan", ["Afghanistan"])]
