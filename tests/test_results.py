"""The results history: results kept across reads (structured.merged_result), paging cursors
(results.py), older pages (adapters' history_pages), and form over the last few results (form.py)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sports_follow import form, results
from sports_follow.adapters import ADAPTERS
from sports_follow.adapters.base import PlayerRef
from sports_follow.structured import merged_result


def line(headline: str, **stats: str) -> dict:
    return {"headline": headline, "stats": [{"label": k.replace("_", " "), "value": v} for k, v in stats.items()]}


def result(text: str, scorecard: bool | str = True) -> dict:
    return {"title": "A vs B", "result": text, "scorecard": scorecard}


def test_a_reread_without_the_scorecard_keeps_what_it_added():
    item = {"title": "Egypt vs Angola", "result": "Drew 0–0", "player_contribution": None, "source_url": None}
    with_card = merged_result(None, {**item, "player_contribution": "Started", "source_url": "https://espn.com/x"}, scorecard=True)
    again = merged_result(with_card, item, scorecard=False)
    assert again == {**item, "player_contribution": "Started", "source_url": "https://espn.com/x", "scorecard": True}
    assert merged_result(None, item, scorecard=False)["scorecard"] is False


def test_cursors_round_trip_and_bad_ones_are_ignored():
    when = datetime(2026, 9, 27, 19, 0, tzinfo=timezone.utc)
    assert results.decode(results.encode(when, 412)) == (when, 412)
    assert results.decode("not a cursor") is None


def test_older_pages_are_read_a_few_requests_at_a_time():
    anchor = date(2026, 9, 30)
    tennis = ADAPTERS["espn_tennis"].history_pages(PlayerRef("espn_tennis", "3782", "Carlos Alcaraz", ["3782"], ["Carlos Alcaraz"], None, league="atp"), anchor)
    assert tennis[0] == {"days": ["20260812", "20260805", "20260729", "20260722"], "cost": 4}  # weeks 7-10 back
    assert sum(p["cost"] for p in tennis) == 46  # weeks 7 to 52, each read once
    cricket = ADAPTERS["espn_cricket"].history_pages(PlayerRef("espn_cricket", "253802", "Virat Kohli", ["6"], ["India"], None), anchor)
    assert cricket[0]["days"][0] == "20260830" and len(cricket) == 15 and all(p["cost"] <= 10 for p in cricket)
    assert ADAPTERS["lichess_chess"].history_pages(PlayerRef("lichess_chess", "1503014", "Magnus Carlsen", ["1503014"], ["Magnus Carlsen"], None), anchor) == []


def test_football_form():
    stats, note = form.summarize("Football", [
        (result("Won 4–0"), line("3 goals, 1 assist", Goals="3", Assists="1", Shots="6", On_target="4")),
        (result("Lost 0–1"), line("Started", Shots="2", On_target="0")),
        (result("Drew 0–0"), line("On the bench")),
        (result("Won 2–1", scorecard=False), None),
    ], ["Trabzonspor"])
    assert {s["label"]: s["value"] for s in stats} == {"W–D–L": "2–1–1", "Apps": "2", "Goals": "3", "Assists": "1", "Shots (on target)": "8 (4)"}
    assert note == "Last 4 games (1 still loading)"


def test_basketball_form_averages_per_game():
    stats, _ = form.summarize("Basketball", [
        (result("Won 96–77"), line("27 PTS · 7 REB · 9 AST", Minutes="36", FG="10-20", **{"3PT": "3-8"}, FT="4-4")),
        (result("Lost 66–86"), line("13 PTS · 3 REB · 7 AST", Minutes="30", FG="5-15", **{"3PT": "1-7"}, FT="2-2")),
        (result("Won 90–80"), line("Did not play (rest)")),
    ], [])
    got = {s["label"]: s["value"] for s in stats}
    assert got == {"W–L": "2–1", "Games": "2", "PTS": "20.0", "REB": "5.0", "AST": "8.0", "FG%": "42.9", "3P%": "26.7", "FT%": "100.0", "MIN": "33.0"}


def test_cricket_form_and_record_from_the_teams_side():
    stats, note = form.summarize("cricket", [
        (result("India won by 8 wkts (50b rem)"), line("139* (88)", Runs="139*", Balls="88")),
        (result("West Indies won by 12 runs"), line("45 (50)", Runs="45", Balls="50", Out="caught")),
        (result("Match drawn"), line("0/12", Wickets="0", Runs_conceded="12", Overs="2")),
    ], ["India"])
    got = {s["label"]: s["value"] for s in stats}
    assert got["W–D–L"] == "1–1–1" and got["Runs"] == "184" and got["Average"] == "184.00" and got["Economy"] == "6.00"
    assert note == "Last 3 matches"


def test_tennis_and_chess_form():
    tennis, _ = form.summarize("Tennis", [(result("Won 6-4 6-3"), line("Won in 2 sets", Sets="2–0")), (result("Lost 4-6 6-7(5)"), line("Lost in 2 sets", Sets="0–2"))], [])
    assert {s["label"]: s["value"] for s in tennis} == {"W–L": "1–1", "Sets": "2–2"}
    chess, _ = form.summarize("Chess", [(result("Won 1–0"), None), (result("Drew ½–½"), None), (result("Won 3½–2½"), None)], [])
    assert {s["label"]: s["value"] for s in chess} == {"W–D–L": "2–1–0", "Score": "5/8", "Score %": "62%"}
