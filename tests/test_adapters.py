"""Adapter parsers against real ESPN responses recorded on 2026-09-29 (tests/fixtures/espn).

No network: if ESPN changes shape, re-record the fixture and these tests say what moved.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from sports_follow.adapters import espn, espn_cricket, espn_soccer

FIXTURES = Path(__file__).parent / "fixtures" / "espn"
KOHLI = "253802"
RONALDO = "22774"
AL_NASSR = "817"


def load(name: str) -> dict:
    with gzip.open(FIXTURES / f"{name}.json.gz", "rt") as f:
        return json.load(f)


# ---------------------------------------------------------------- shared


def test_names_match_on_surname_and_accents():
    assert espn.names_match("Ronaldo", "Cristiano Ronaldo")
    assert espn.names_match("Kohli", "Virat Kohli")
    assert espn.names_match("Jokic", "Nikola Jokić")
    assert not espn.names_match("Ronaldo", "Ronald Araújo")


def test_search_hits_are_filtered_by_sport():
    data = load("search-kohli")
    hits = [
        item
        for group in data["results"]
        if group.get("type") == "player"
        for item in group["contents"]
        if (item.get("uid") or "").startswith(espn_cricket.SPORT_UID)
    ]
    assert any(h["uid"].endswith(f"~a:{KOHLI}") for h in hits)


# ---------------------------------------------------------------- cricket


def test_cricket_day_feed_gives_fixtures_with_real_result():
    fixtures = espn_cricket.parse_day(load("cricket-day-20260927"))
    odi = next(f for f in fixtures if f.source_id == "1529227")
    assert odi.title == "India v West Indies"
    assert odi.detail == "1st ODI (D/N)"
    assert odi.status == "final"
    assert odi.locator == {"league": "24289", "event": "1529227"}
    # The feed's top-level summary is just "Result"; the parser must reach the real one.
    assert odi.result["summary"] == "India won by 8 wkts (50b rem)"
    assert espn_cricket.match_format(odi) == "ODI"


def test_cricket_final_scorecard_gives_the_players_line():
    snap = espn_cricket.parse_summary(load("cricket-summary-final"))
    assert snap.status == "final"
    assert snap.score_label == "WI 295/7 (50.0 ov) · IND 300/2 (41.4 ov)"
    assert snap.clock_label == "India won by 8 wkts (50b rem)"
    assert snap.state["player_of_match"] == "Kuldeep Yadav"
    line = snap.lines[KOHLI]
    assert line.headline == "139* (88)"
    assert {s["label"]: s["value"] for s in line.stats} == {"Runs": "139*", "Balls": "88", "4s": "10", "6s": "9", "Strike rate": "158.0"}


def test_cricket_live_score_skips_the_innings_not_yet_batted():
    snap = espn_cricket.parse_summary(load("cricket-summary-live"))
    assert snap.status == "live"
    # ESPN gives the side that hasn't batted the match's overs and 0/0; that isn't an innings.
    assert snap.score_label == "AUS-A 282/7 (87.0 ov)"
    assert snap.clock_label == "Day 1 · Stumps"
    assert [i["abbr"] for i in snap.state["innings"]] == ["AUS-A"]
    assert snap.state["innings"][0]["batting"] is True


def test_cricket_squad_membership():
    # Squads announced before the match: Kohli is in the 2nd ODI squad.
    assert espn_cricket.squad_membership(load("cricket-summary-squads"), KOHLI) is True
    assert espn_cricket.squad_membership(load("cricket-summary-squads"), "1") is False
    # After the match, the playing XI decides.
    assert espn_cricket.squad_membership(load("cricket-summary-final"), KOHLI) is True
    # Neither announced yet: unknown, not excluded.
    assert espn_cricket.squad_membership({}, KOHLI) is None


def test_cricket_recent_form_from_scorecards():
    fixture = next(f for f in espn_cricket.parse_day(load("cricket-day-20260927")) if f.source_id == "1529227")
    snap = espn_cricket.parse_summary(load("cricket-summary-final"))
    stats, note = espn_cricket.recent_form(KOHLI, [(fixture, snap)])
    values = {s["label"]: s["value"] for s in stats}
    assert values["Runs"] == "139"
    assert values["Average"] == "–"  # not out: no dismissals to divide by
    assert values["Strike rate"] == "158.0"
    assert values["100s / 50s"] == "1 / 0"
    assert note == "Last 1 match (ODI), from ESPNcricinfo scorecards"


# ---------------------------------------------------------------- football


def test_soccer_schedule_results_and_labels():
    fixtures = espn_soccer.parse_schedule(load("soccer-schedule-results"))
    assert fixtures and all(f.status == "final" for f in fixtures)
    last = fixtures[-1]
    assert last.title == "Al Nassr vs Al Fateh"
    assert espn_soccer.result_label(last, [AL_NASSR]) == "Won 3–0"
    away_win = fixtures[-2]
    assert away_win.title == "Al Riyadh vs Al Nassr"
    assert espn_soccer.result_label(away_win, [AL_NASSR]) == "Won 4–0"


def test_soccer_schedule_fixtures_carry_a_league_locator():
    fixtures = espn_soccer.parse_schedule(load("soccer-schedule-fixtures"))
    first = fixtures[0]
    assert first.status == "scheduled"
    assert first.title == "Al Nassr vs Al Diriyah"
    assert first.locator == {"league": "ksa.1", "event": "401900907"}


def test_soccer_summary_scorers_and_unused_sub():
    snap = espn_soccer.parse_summary(load("soccer-summary-final"), "uefa.nations")
    assert snap.status == "final"
    assert snap.score_label == "Norway 1 – 2 Portugal"
    assert snap.state["away"]["scorers"] == ["João Félix 17'", "Gonçalo Ramos 54'"]
    # Ronaldo was an unused substitute: no zeros dressed up as stats.
    assert snap.lines[RONALDO].headline == "On the bench"
    assert snap.lines[RONALDO].stats == []
    assert snap.moments[0]["kind"] == "YELLOW"


def test_soccer_season_stats():
    stats, note = espn_soccer.parse_season_stats(load("soccer-athlete-stats"))
    values = {s["label"]: s["value"] for s in stats}
    assert values["Goals"] == "3"
    assert values["Starts"] == "5"
    assert note == "2026-27 Saudi Pro League"


def test_cricket_fixtures_found_through_the_other_sides_a_player_plays_for():
    # ESPN files Sam Harper under Australia (id 2); he is playing for Australia A (id 49).
    from sports_follow.adapters.base import PlayerRef

    fixtures = espn_cricket.parse_day(load("cricket-day-20260929"))
    test = next(f for f in fixtures if f.title == "India A v Australia A")
    women = next(f for f in fixtures if f.title == "India A Women v Australia A Women")
    by_espn_team_only = PlayerRef("espn_cricket", "772361", "Sam Harper", ["2"], ["Australia"])
    with_agent_teams = PlayerRef("espn_cricket", "772361", "Sam Harper", ["2"], ["Australia"], other_teams=["Victoria", "Melbourne Stars", "Australia 'A'"])
    assert not espn_cricket.involves_team(test, by_espn_team_only)
    assert espn_cricket.involves_team(test, with_agent_teams)
    # Names match exactly, never by prefix: Australia A is not Australia A Women.
    assert not espn_cricket.involves_team(women, with_agent_teams)
