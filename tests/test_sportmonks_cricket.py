"""Sportmonks cricket parsers, against small made-up responses in Sportmonks' shapes (their terms
don't allow republishing real ones, so none are committed)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

from sports_follow import moments, sports
from sports_follow.adapters import sportmonks, sportmonks_cricket as sc
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


def test_the_xi_is_the_team_sheet_before_the_start():
    snap = sc.parse_match(_match("NS", [], lineup=[{"id": OPENER, "lineup": {"substitution": False}}, {"id": KEEPER, "lineup": {"substitution": True}}]))
    assert snap.lines == {} and snap.squad == {str(OPENER): "starting", str(KEEPER): "bench"}
    assert sc.parse_match(_match("NS", [])).squad == {}


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


def test_a_fixture_says_whether_nations_played_it_and_a_super_over_decided_it():
    nations = {**INDIA, "national_team": True}
    assert sc.parse_fixture(_match("Finished", [FIRST, CHASE], localteam=nations, visitorteam={**AFGHANISTAN, "national_team": True}, super_over=True)).facts == {"national": True, "super_over": True}
    assert sc.parse_fixture(_match("Finished", [FIRST, CHASE], localteam=nations)).facts == {}


def test_the_schedule_is_every_match_between_two_days_in_one_request(monkeypatch):
    asked = []

    def get_json(path, ttl, **params):
        asked.append((sportmonks._bucket(path), params))
        return {"data": [_match("Finished", [FIRST, CHASE], note="India won by 127 runs", winner_team_id=IND), _match("NS", [], id=71216)], "meta": {"current_page": 1, "last_page": 1}}

    monkeypatch.setattr(sportmonks, "get_json", get_json)
    games = sc.SportmonksCricket().schedule(date(2026, 10, 9), date(2026, 10, 30))
    assert [(g.source_id, g.status) for g in games] == [("71215", "final"), ("71216", "scheduled")]
    [(bucket, params)] = asked
    assert bucket == "Cricket fixtures" and params["filter[starts_between]"] == "2026-10-09,2026-10-30"
    assert not any("team" in k for k in params if k.startswith("filter"))  # whoever plays


def test_a_match_page_reads_one_match_with_its_scorecard_and_both_elevens(monkeypatch):
    def player(pid: int, name: str, team: int, sub: bool = False) -> dict:
        return {"id": pid, "fullname": name, "lineup": {"team_id": team, "substitution": sub}}

    lineup = [player(SPINNER, "Rashid Khan", AFG), player(OPENER, "Abhishek Sharma", IND), player(SEAMER, "A Sub", IND, sub=True)]
    live = _match("2nd Innings", [FIRST, {**CHASE, "score": 60, "wickets": 3, "overs": 8}], lineup=lineup, batting=[_bat(OPENER, 108, 34, "Catch Out")])
    asked = []
    monkeypatch.setattr(sportmonks, "get_json", lambda path, ttl, **params: asked.append((sportmonks._bucket(path), params["include"])) or {"data": live})
    game = sc.SportmonksCricket().match("71215")
    # The match a followed player's live poll reads too: one request.
    assert asked == [(sportmonks.CRICKET_MATCH, sc.MATCH_INCLUDE)]
    assert game is not None and (game.source_id, game.teams) == ("71215", ["India", "Afghanistan"])
    assert [(p.name, p.team_names) for p in game.players] == [("Abhishek Sharma", ["India"]), ("Rashid Khan", ["Afghanistan"])]  # home side first, no substitute
    assert game.snapshot.lines[str(OPENER)].headline == "108 (34)"


def test_a_match_page_reads_a_finished_match_with_its_whole_scorecard_and_who_won(monkeypatch):
    def player(pid: int, name: str, team: int) -> dict:
        return {"id": pid, "fullname": name, "lineup": {"team_id": team, "substitution": False}}

    # A made-up finished T20I: India batted first, Afghanistan were all out chasing.
    bowling = [{"player_id": SPINNER, "team_id": AFG, "scoreboard": "S1", "sort": 1, "overs": 4, "runs": 30, "wickets": 2, "rate": 7.5}]
    x = _match("Finished", [FIRST, CHASE], note="India won by 127 runs", winner_team_id=IND, batting=[_bat(OPENER, 108, 34, "Catch Out")], bowling=bowling,
               lineup=[player(OPENER, "Abhishek Sharma", IND), player(SPINNER, "Rashid Khan", AFG)])
    monkeypatch.setattr(sportmonks, "get_json", lambda path, ttl, **params: {"data": x})
    game = sc.SportmonksCricket().match("71215")
    assert game is not None and game.snapshot.status == "final"
    cricket = sports.get("cricket")
    assert cricket.verdict(game.snapshot.state, game.snapshot.score_label, game.snapshot.clock_label) == "India won by 127 runs"
    names = {p.athlete_id: (p.name, p.team_names[0]) for p in game.players}
    lines = {aid: {"headline": line.headline, "stats": line.stats} for aid, line in game.snapshot.lines.items()}
    india = cricket.match_page(game.snapshot.state, [], lines, names)["scorecard"][0]
    assert (india["team"], [b["name"] for b in india["batting"]], [w["name"] for w in india["bowling"]]) == ("India", ["Abhishek Sharma"], ["Rashid Khan"])


def test_a_match_pages_scorecard_names_an_impact_substitute_who_bowls_without_offering_them():
    def player(pid: int, name: str, team: int, sub: bool = False) -> dict:
        return {"id": pid, "fullname": name, "lineup": {"team_id": team, "substitution": sub}}

    bowling = [{"player_id": SEAMER, "scoreboard": "S1", "sort": 1, "overs": 4, "runs": 30, "wickets": 2, "rate": 7.5}]
    x = _match("2nd Innings", [FIRST, {**CHASE, "score": 60, "wickets": 3, "overs": 8}], batting=[_bat(OPENER, 108, 34, "Catch Out")], bowling=bowling,
               lineup=[player(OPENER, "Abhishek Sharma", IND), player(SPINNER, "Rashid Khan", AFG), player(SEAMER, "Impact Seamer", AFG, sub=True)])
    game = sc.parse_game(x, "sportmonks_cricket")
    assert [p.name for p in game.players] == ["Abhishek Sharma", "Rashid Khan"] and [(p.name, p.team_names) for p in game.bench] == [("Impact Seamer", ["Afghanistan"])]
    names = {p.athlete_id: (p.name, p.team_names[0]) for p in [*game.players, *game.bench]}
    lines = {aid: {"headline": line.headline, "stats": line.stats} for aid, line in game.snapshot.lines.items()}
    india = sports.get("cricket").match_page(game.snapshot.state, [], lines, names)["scorecard"][0]
    assert india["team"] == "India" and [w["name"] for w in india["bowling"]] == ["Impact Seamer"]
