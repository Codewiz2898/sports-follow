"""Sportmonks football parsers, against small made-up responses in Sportmonks' shapes (their terms
don't allow republishing real ones, so none are committed)."""

from __future__ import annotations

from datetime import date

from sports_follow.adapters import sportmonks, sportmonks_football as sm

HOME, AWAY = 688, 34
STAR, SUB, UNUSED, KEEPER = 4125, 7001, 7002, 7003


def _detail(kind: str, value) -> dict:
    return {"type": {"developer_name": kind}, "data": {"value": value}}


def _team(team_id: int, name: str, code: str, location: str, winner: bool | None) -> dict:
    return {"id": team_id, "name": name, "short_code": code, "meta": {"location": location, "winner": winner}}


def _match(state: str, home_goals: int, away_goals: int, **extra) -> dict:
    return {
        "id": 555,
        "starting_at_timestamp": 1788710400,
        "state": {"developer_name": state, "name": {"FT": "Full Time", "INPLAY_2ND_HALF": "2nd Half", "NS": "Not Started", "HT": "Half-Time"}.get(state, state)},
        "league": {"name": "Super Lig"},
        "round": {"name": "7"},
        "venue": {"name": "Papara Park"},
        "participants": [_team(AWAY, "Galatasaray", "GAL", "away", state == "FT" and away_goals > home_goals), _team(HOME, "Trabzonspor", "TRA", "home", state == "FT" and home_goals > away_goals)],
        "scores": [
            {"description": "1ST_HALF", "score": {"goals": 1, "participant": "home"}},
            {"description": "CURRENT", "score": {"goals": home_goals, "participant": "home"}},
            {"description": "CURRENT", "score": {"goals": away_goals, "participant": "away"}},
        ],
        **extra,
    }


LINEUPS = [
    {"player_id": STAR, "team_id": HOME, "type_id": 11, "details": [_detail("GOALS", 2), _detail("ASSISTS", 1), _detail("SHOTS_TOTAL", 4), _detail("MINUTES_PLAYED", 67), _detail("YELLOWCARDS", 1), _detail("ACCURATE_PASSES", 21)]},
    {"player_id": SUB, "team_id": HOME, "type_id": 12, "details": [_detail("MINUTES_PLAYED", 12)]},
    {"player_id": UNUSED, "team_id": HOME, "type_id": 12, "details": []},
    {"player_id": KEEPER, "team_id": AWAY, "type_id": 11, "details": [_detail("MINUTES_PLAYED", 67), _detail("YELLOWREDCARDS", 1)]},
]
EVENTS = [
    {"type": {"developer_name": "GOAL"}, "minute": 4, "participant_id": HOME, "player_id": STAR, "player_name": "Mohamed Salah", "related_player_id": SUB, "related_player_name": "Ozan Tufan"},
    {"type": {"developer_name": "OWNGOAL"}, "minute": 30, "participant_id": AWAY, "player_id": KEEPER, "player_name": "A. Keeper"},
    {"type": {"developer_name": "PENALTY"}, "minute": 45, "extra_minute": 2, "participant_id": HOME, "player_id": STAR, "player_name": "Mohamed Salah"},
    {"type": {"developer_name": "YELLOWCARD"}, "minute": 50, "participant_id": AWAY, "player_name": None, "coach_id": 9},
    {"type": {"developer_name": "YELLOWREDCARD"}, "minute": 60, "participant_id": AWAY, "player_id": KEEPER, "player_name": "A. Keeper"},
    {"type": {"developer_name": "GOAL"}, "minute": 62, "participant_id": HOME, "player_id": SUB, "player_name": "Ozan Tufan", "rescinded": True},
    {"type": {"developer_name": "SUBSTITUTION"}, "minute": 66, "participant_id": HOME, "player_id": SUB, "player_name": "Ozan Tufan"},
]


def test_fixtures_read_teams_round_and_result():
    finished, upcoming = sm.parse_fixtures([_match("FT", 3, 0), {**_match("NS", 0, 0), "id": 556, "round": {"name": "Semi-finals"}}])
    assert finished.title == "Trabzonspor vs Galatasaray"
    assert (finished.competition, finished.detail, finished.venue) == ("Super Lig", "Matchday 7", "Papara Park")
    assert finished.team_ids == [str(HOME), str(AWAY)] and finished.locator == {"fixture": "555"}
    assert finished.start_utc.isoformat() == "2026-09-06T16:00:00+00:00"
    assert sm.SportmonksFootball().result_label(finished, [str(HOME)]) == "Won 3–0"
    assert sm.SportmonksFootball().result_label(finished, [str(AWAY)]) == "Lost 0–3"
    assert (upcoming.status, upcoming.detail, upcoming.result) == ("scheduled", "Semi-finals", None)
    friendly = sm.parse_fixtures([{**_match("FT", 1, 0), "league": {"name": "Club Friendlies 3"}, "round": None}])[0]
    assert (friendly.competition, friendly.detail) == ("Club Friendlies", None)


def test_states_map_to_our_four():
    assert [sm._status({"developer_name": s}) for s in ("NS", "INPLAY_1ST_HALF", "HT", "FT_PEN", "POSTPONED", "TBA")] == ["scheduled", "live", "live", "final", "postponed", "scheduled"]


def test_a_live_match_has_score_clock_scorers_lines_and_moments():
    periods = [{"ticking": False, "minutes": 47, "counts_from": 0, "period_length": 45}, {"ticking": True, "minutes": 67, "counts_from": 45, "period_length": 45}]
    snap = sm.parse_match(_match("INPLAY_2ND_HALF", 3, 0, periods=periods, lineups=LINEUPS, events=EVENTS))
    assert (snap.status, snap.score_label, snap.clock_label) == ("live", "Trabzonspor 3 – 0 Galatasaray", "67'")
    assert snap.state["home"] == {"id": str(HOME), "name": "Trabzonspor", "abbr": "TRA", "score": "3", "scorers": ["Mohamed Salah 4'", "A. Keeper 30' (OG)", "Mohamed Salah 45+2' (pen)"]}
    assert snap.state["away"]["scorers"] == []

    star = snap.lines[str(STAR)]
    assert star.headline == "2 goals, 1 assist"
    # The labels moments.py reads for goal, assist and card alerts.
    assert star.stats == [{"label": "Goals", "value": "2"}, {"label": "Assists", "value": "1"}, {"label": "Shots", "value": "4"}, {"label": "Yellow", "value": "1"}, {"label": "Minutes", "value": "67"}]
    assert snap.lines[str(SUB)].headline == "Came on"
    assert snap.lines[str(UNUSED)].headline == "On the bench"
    assert {"label": "Red", "value": "1"} in snap.lines[str(KEEPER)].stats  # a second yellow is a red

    # Newest first; the coach's card and the ruled-out goal aren't moments.
    assert [(m["clock"], m["kind"], m["text"]) for m in snap.moments] == [
        ("60'", "RED", "A. Keeper (Galatasaray), second yellow"),
        ("45+2'", "GOAL", "Mohamed Salah (Trabzonspor), penalty"),
        ("30'", "GOAL", "A. Keeper (Galatasaray), own goal"),
        ("4'", "GOAL", "Mohamed Salah (Trabzonspor), assist Ozan Tufan"),
    ]
    assert snap.moments[-1]["athlete_ids"] == [str(STAR), str(SUB)]


def test_clock_in_stoppage_time_and_breaks():
    stoppage = [{"ticking": True, "minutes": 93, "counts_from": 45, "period_length": 45}]
    assert sm.clock(_match("INPLAY_2ND_HALF", 1, 1, periods=stoppage)) == "90+3'"
    assert sm.clock(_match("HT", 1, 1)) == "Half-time"
    assert sm.clock(_match("FT", 1, 1)) == "Full time"


def test_no_lines_before_kickoff():
    assert sm.parse_match(_match("NS", 0, 0, lineups=LINEUPS)).lines == {}


def test_player_keeps_current_teams_club_first():
    player = {
        "id": STAR, "display_name": "Mohamed Salah", "date_of_birth": "1992-06-15",
        "teams": [
            {"team_id": 18546, "end": None, "team": {"name": "Egypt", "type": "national"}},
            {"team_id": 8, "end": "2026-07-01", "team": {"name": "Liverpool", "type": "domestic"}},
            {"team_id": HOME, "end": "2029-06-30", "team": {"name": "Trabzonspor", "type": "domestic"}},
        ],
    }
    ref = sm.parse_player(player, today=date(2026, 9, 30))
    assert (ref.system, ref.athlete_id, ref.born) == ("sportmonks_football", str(STAR), "1992-06-15")
    assert ref.team_ids == [str(HOME), "18546"] and ref.team_names == ["Trabzonspor", "Egypt"]


def test_season_stats_add_up_this_seasons_competitions_for_the_players_teams():
    def row(team, league, current, **values):
        return {"team_id": team, "season": {"name": "2026/2027", "is_current": current, "league": {"name": league}}, "details": [{"type": {"developer_name": k}, "value": {"total": v}} for k, v in values.items()]}

    player = {"statistics": [
        row(HOME, "Super Lig", True, APPEARANCES=6, GOALS=7, ASSISTS=2, MINUTES_PLAYED=481),
        row(HOME, "Turkish Cup", True, APPEARANCES=1, GOALS=1),
        row(8, "Premier League", False, APPEARANCES=38, GOALS=29),
        row(99, "Club Friendlies", True, APPEARANCES=3),
    ]}
    stats, note = sm.parse_season_stats(player, [str(HOME)])
    assert stats == [{"label": "Apps", "value": "7"}, {"label": "Goals", "value": "8"}, {"label": "Assists", "value": "2"}, {"label": "Minutes", "value": "481"}]
    assert note == "2026/2027 season, all competitions"
    assert sm.parse_season_stats({"statistics": [row(HOME, "Super Lig", True, GOALS=7)]}, [str(HOME)])[1] == "Super Lig 2026/2027"
    assert sm.parse_season_stats({}, [str(HOME)]) == ([], "")


def test_history_pages_step_back_ninety_days_per_team():
    ref = sm.parse_player({"id": 1, "display_name": "X", "teams": [{"team_id": 1, "team": {"type": "domestic"}}, {"team_id": 2, "team": {"type": "national"}}]})
    pages = sm.SportmonksFootball().history_pages(ref, date(2026, 9, 30))
    assert pages[:2] == [{"team": "1", "from": "2026-03-04", "to": "2026-06-01"}, {"team": "2", "from": "2026-03-04", "to": "2026-06-01"}]
    assert pages[-1]["from"] == "2025-06-07" and len(pages) == 8


def test_background_reads_stop_at_the_live_reserve(monkeypatch):
    monkeypatch.setattr(sportmonks, "remaining", {})
    assert sportmonks.spare("Fixture")  # nothing read yet
    sportmonks.remaining["Fixture"] = (sportmonks.LIVE_RESERVE, 1e12)
    assert not sportmonks.spare("Fixture")
    sportmonks.remaining["Fixture"] = (sportmonks.LIVE_RESERVE, 0.0)  # the hour has reset since
    assert sportmonks.spare("Fixture")
