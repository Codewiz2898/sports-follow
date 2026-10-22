"""A game at a glance, as an Android notification draws it (Sport.glance): each sport reads its own board, from
made-up boards shaped as its sources write them (web/src/sports/facts/*.ts describe each)."""

from __future__ import annotations

from sports_follow import sports
from sports_follow.sports.base import Glance


def football(home: str, away: str, **more) -> dict:
    return {"kind": "football", "home": {"id": "1", "name": "Arsenal", "abbr": "ARS", "score": home}, "away": {"id": "2", "name": "Chelsea", "abbr": "CHE", "score": away}, **more}


def goal(clock: str, team: str, own_goal: bool = False, kind: str = "GOAL") -> dict:
    return {"id": f"g{clock}", "clock": clock, "kind": kind, "team_id": team, "athlete_ids": ["9"], "own_goal": own_goal, "text": "Someone (Arsenal)"}


def test_a_sport_no_module_knows_shows_its_two_sides_and_its_clock():
    board = {"kind": "hurling", "home": {"name": "Kilkenny", "score": 21}, "away": {"name": "Cork", "short": "COR", "score": 0}}
    assert sports.OTHER.glance(board, "Kilkenny 21 – 0 Cork", "2nd half", "live", []) == Glance([("Kilkenny", "21"), ("COR", "0")], "2nd half")
    assert sports.OTHER.glance({}, "", "", "live", []) == Glance()


def test_football_shows_the_minute_of_ninety_and_each_goal_for_its_side():
    # Newest first, as the source lists them: an own goal counts for the other side, a goal VAR ruled out for nobody.
    events = [goal("67'", "1"), goal("51'", "2", kind="DISALLOWED"), goal("45+2'", "2", own_goal=True), goal("23'", "2"), {"clock": "30'", "kind": "YELLOW", "team_id": "1"}]
    seen = sports.of("football").glance(football("2", "1"), "Arsenal 2 – 1 Chelsea", "67'", "live", events)
    assert seen == Glance([("ARS", "2"), ("CHE", "1")], "67'", (67, 90), [(23, 1), (47, 0), (67, 0)])
    # Added time runs on past 90 without the bar; extra time is of 120.
    assert sports.of("football").glance(football("1", "1"), "", "90+4'", "live", []).progress == (90, 90)
    assert sports.of("football").glance(football("1", "1", detail="Extra Time"), "", "105+1'", "live", [goal("98'", "1")]) == \
        Glance([("ARS", "1"), ("CHE", "1")], "105+1'", (106, 120), [(98, 0)])
    # A break stands where it falls; the clock's short words are said in full.
    assert sports.of("football").glance(football("0", "0"), "", "HT", "live", []).status == "Half-time"
    assert sports.of("football").glance(football("0", "0"), "", "Half-time", "live", []).progress == (45, 90)
    assert sports.of("football").glance(football("0", "0"), "", "Interrupted", "live", []).progress is None
    full = sports.of("football").glance(football("3", "1"), "", "FT", "final", [goal("93'", "1")])
    assert (full.status, full.progress, full.marks) == ("Full time", (90, 90), [(90, 0)])
    assert sports.of("football").glance(football("2", "2"), "", "After penalties", "final", []).progress == (120, 120)
    # A board without abbreviations names the sides in full.
    assert sports.of("football").glance({"kind": "football", "home": {"id": "1", "name": "Egypt", "score": "1"}, "away": {"id": "2", "name": "Ghana", "score": "0"}}, "", "12'", "live", []).sides == [("Egypt", "1"), ("Ghana", "0")]


def cricket(innings: list[dict], fmt: str | None = "ODI", scores: tuple[str, str] = ("", "")) -> dict:
    return {"kind": "cricket", "format": fmt, "innings": innings,
            "teams": [{"id": "6", "name": "India", "abbr": "IND", "score": scores[0]}, {"id": "2", "name": "Australia", "abbr": "AUS", "score": scores[1]}]}


def test_cricket_shows_the_batting_innings_overs_of_its_formats():
    first = {"team": "Australia", "abbr": "AUS", "runs": 287, "wickets": 9, "overs": 50.0, "batting": False}
    chase = {"team": "India", "abbr": "IND", "runs": 201, "wickets": 3, "overs": 38.2, "batting": True, "target": 288}
    seen = sports.of("cricket").glance(cricket([first, chase], scores=("201/3 (38.2/50 ov, target 288)", "287/9")), "", "India need 87 from 70 balls", "live", [])
    assert seen == Glance([("IND", "201/3"), ("AUS", "287/9")], "38.2 ov", (230, 300))
    # Between innings, the clock says where it's at, and the bar is the innings just ended.
    break_ = sports.of("cricket").glance(cricket([{**first, "overs": "50"}], fmt="T20I"), "", "Innings break", "live", [])
    assert (break_.status, break_.progress) == ("Innings break", (120, 120))
    assert sports.of("cricket").glance(cricket([chase], fmt="Other T20"), "", "", "live", []).progress == (120, 120)
    assert sports.of("cricket").glance(cricket([{**chase, "overs": "12"}], fmt="List A"), "", "", "live", []).status == "12 ov"
    # A Test, a first-class match or a format no word says has no length: no bar.
    test = sports.of("cricket").glance(cricket([{**chase, "overs": 122.0}], fmt="Test", scores=("240 & 291/7 (122 ov, target 479)", "346 & 372/7d")), "", "Day 5", "live", [])
    assert test == Glance([("IND", "240 & 291/7"), ("AUS", "346 & 372/7d")], "122 ov", None)
    assert sports.of("cricket").glance(cricket([chase], fmt=None), "", "", "live", []).progress is None
    assert sports.of("cricket").glance(cricket([first, {**chase, "batting": False}]), "", "India won by 7 wickets", "final", []).status == "Result"


def test_tennis_shows_each_sets_games_and_the_set_being_played():
    board = {"kind": "tennis", "set": 2, "players": [
        {"id": "1", "name": "Jannik Sinner", "short": "J. Sinner", "sets": [{"games": 6}, {"games": 2}]},
        {"id": "2", "name": "Carlos Alcaraz", "short": "C. Alcaraz", "sets": [{"games": 4, "tiebreak": None}, {"games": 1}]},
    ]}
    assert sports.of("tennis").glance(board, "", "2nd set", "live", []) == Glance([("J. Sinner", "6 2"), ("C. Alcaraz", "4 1")], "Set 2")
    assert sports.of("tennis").glance(board, "", "Sinner won", "final", []).status == "Final"
    assert sports.of("tennis").glance({"kind": "tennis", "players": []}, "", "", "live", []).sides == []


def chess(**more) -> dict:
    return {"kind": "chess", "white": {"id": "1", "name": "Gukesh D"}, "black": {"id": "2", "name": "Magnus Carlsen"}, "move": 34, **more}


def test_chess_shows_white_and_black_with_their_points_and_the_move():
    assert sports.of("chess").glance(chess(result=None), "Gukesh D vs Magnus Carlsen", "Move 34 · White to play", "live", []) == \
        Glance([("Gukesh D", ""), ("Magnus Carlsen", "")], "Move 34")
    assert sports.of("chess").glance(chess(result="½-½"), "", "Draw", "final", []) == Glance([("Gukesh D", "½"), ("Magnus Carlsen", "½")], "½–½")
    assert sports.of("chess").glance(chess(result="0-1"), "", "", "final", []).sides == [("Gukesh D", "0"), ("Magnus Carlsen", "1")]
    # A mini-match's game is a game of its own (BHA-675): its own points, and which game of the match it is.
    game3 = {"game": 3, "label": "Game 3", "games": 2, "score": "1½–½", "ahead": "2"}
    seen = sports.of("chess").glance(chess(result=None, mini_match=game3), "Gukesh D vs Magnus Carlsen", "Move 34 · White to play", "live", [])
    assert seen == Glance([("Gukesh D", ""), ("Magnus Carlsen", "")], "Game 3 · move 34")
    decided = chess(result="1-0", mini_match={**game3, "label": "Armageddon", "games": 3, "score": "1½–1½", "ahead": None})
    assert sports.of("chess").glance(decided, "Gukesh D 1–0 Magnus Carlsen", "Gukesh D won", "final", []) == Glance([("Gukesh D", "1"), ("Magnus Carlsen", "0")], "Armageddon · 1–0")
    assert sports.of("chess").glance({"kind": "chess", "round": "Round 3"}, "", "Pairings not published yet", "scheduled", []) == Glance([], "Pairings not published yet")


def test_basketball_shows_away_first_as_its_board_does():
    board = {"kind": "basketball", "home": {"id": "1", "name": "Boston Celtics", "abbr": "BOS", "score": "99"}, "away": {"id": "2", "name": "Los Angeles Lakers", "abbr": "LAL", "score": "101"}}
    assert sports.of("basketball").glance(board, "Los Angeles Lakers 101 – 99 Boston Celtics", "7:32 - 3rd", "live", []) == Glance([("LAL", "101"), ("BOS", "99")], "7:32 - 3rd")


def test_an_esports_series_shows_its_teams_maps_and_a_lobby_none():
    series = {"kind": "esports", "teams": [{"id": "1", "name": "Sentinels", "abbr": "SEN", "score": 1}, {"id": "2", "name": "Fnatic", "score": 0}], "maps": []}
    assert sports.of("valorant").glance(series, "", "Map 2", "live", []) == Glance([("SEN", "1"), ("Fnatic", "0")], "Map 2")
    lobby = {"kind": "battle_royale", "standings": [{"id": "a", "name": "GodLike", "place": 1, "points": 87}, {"id": "b", "name": "Soul", "place": 2, "points": 80}, {"id": "c", "name": "Hydra", "place": 3, "points": 71}]}
    assert sports.of("bgmi").glance(lobby, "GodLike lead · 87 pts", "After match 3 of 6", "live", []) == Glance([], "After match 3 of 6")
