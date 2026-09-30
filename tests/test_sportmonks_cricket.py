"""Sportmonks cricket parsers, against small made-up responses in Sportmonks' shapes (their terms
don't allow republishing real ones, so none are committed)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

from sports_follow import moments
from sports_follow.adapters import sportmonks, sportmonks_cricket as sc

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


def test_cricket_reserve_is_per_minute(monkeypatch):
    monkeypatch.setattr(sportmonks, "remaining", {"Cricket": (sportmonks.CRICKET_RESERVE, 1e12)})
    assert not sc.SportmonksCricket().spare()
    sportmonks.remaining["Cricket"] = (sportmonks.CRICKET_RESERVE + 1, 1e12)
    assert sc.SportmonksCricket().spare()
