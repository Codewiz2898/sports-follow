"""The results history: results kept across reads (structured.merged_result), paging cursors
(results.py), older pages (adapters' history_pages), and form over the last few results (form.py)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from sports_follow import db as db_module
from sports_follow import form, results, sports, structured
from sports_follow.adapters import ADAPTERS, sportmonks_cricket
from sports_follow.adapters.base import PlayerRef
from sports_follow.sports.cricket import winner_outcome
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


def test_a_result_says_its_match_page_where_a_source_has_the_game():
    from sports_follow.models import Event

    kept = {"title": "Arsenal vs Chelsea", "result": "Won 2–1"}  # kept before results said their match page
    game = Event(id=4, key="sportmonks_football:19134567", start_utc=datetime(2026, 10, 12, 19, tzinfo=timezone.utc))
    assert results._item(game, kept)["match"] == "sportmonks_football/19134567"
    assert results._item(Event(id=5, key="a" * 64, start_utc=None), kept)["match"] is None  # the research agent's: no source has it
    assert results._item(Event(id=6, key="gone:1", start_utc=None), kept)["match"] is None  # no source by that name


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


def test_cricket_record_reads_the_winner_by_whole_name():
    # By substring, "Mumbai Indians won" was a win for anyone whose teams include India, and "India A
    # won" a win for India. The app's outcome() (web/src/format.ts) reads these the same way.
    kohli = ["India", "Royal Challengers Bengaluru"]
    cases = [
        ("Mumbai Indians won by 5 wkts (12b rem)", kohli, "L"),
        ("Royal Challengers Bengaluru won by 7 runs", kohli, "W"),
        ("India A won by 3 runs", ["India"], "L"),
        ("South Africa A won by 10 runs", ["South Africa"], "L"),
        ("India won by 8 wkts (50b rem)", ["India"], "W"),
        ("Australia A won by 4 wkts", ["Australia 'A'"], "W"),
        ("LIONS won by 6 wkts", ["Lions"], "W"),
        ("Match tied (India won the Super Over)", ["India"], "W"),
        ("Match tied (Mumbai Indians won the Super Over)", ["India"], "L"),
        ("Match drawn", ["India"], "D"),
        ("No result", ["India"], "D"),
        ("Match abandoned without a ball bowled", ["India"], None),
        ("West Indies won by 12 runs", [], None),
    ]
    assert [winner_outcome(text, teams) for text, teams, _ in cases] == [want for _, _, want in cases]
    stats, _ = form.summarize("cricket", [(result(text), None) for text, teams, _ in cases if teams == kohli], kohli)
    assert stats == [{"label": "W–D–L", "value": "1–0–1"}]


def test_cricket_form_counts_both_innings_of_a_test():
    # A line's stats are its latest innings; the headline has them all.
    stats, _ = form.summarize("cricket", [
        (result("India won by 120 runs"), line("103 (152) & 104* (140) · 2/30 (12 ov) & 3/45 (20 ov)", Runs="104*", Balls="140", Wickets="3", Runs_conceded="45", Overs="20")),
    ], ["India"])
    got = {s["label"]: s["value"] for s in stats}
    assert (got["Runs"], got["Average"], got["Highest"], got["100s / 50s"]) == ("207", "207.00", "104", "2 / 0")
    assert (got["Wickets"], got["Economy"]) == ("5", "2.34")


def test_tennis_and_chess_form():
    tennis, _ = form.summarize("Tennis", [(result("Won 6-4 6-3"), line("Won in 2 sets", Sets="2–0")), (result("Lost 4-6 6-7(5)"), line("Lost in 2 sets", Sets="0–2"))], [])
    assert {s["label"]: s["value"] for s in tennis} == {"W–L": "1–1", "Sets": "2–2"}
    chess, _ = form.summarize("Chess", [(result("Won 1–0"), None), (result("Drew ½–½"), None), (result("Won 3½–2½"), None)], [])
    assert {s["label"]: s["value"] for s in chess} == {"W–D–L": "2–1–0", "Score": "5/8", "Score %": "62%"}


def test_a_walkover_isnt_form():
    # BUG-040's follow-up: "Won (w/o)" wasn't played, as the season Win–loss already says.
    tennis, _ = form.summarize("Tennis", [(result("Won (w/o)"), line("Won by walkover")), (result("Won 6-4 6-3"), line("Won in 2 sets", Sets="2–0"))], [])
    assert {s["label"]: s["value"] for s in tennis}["W–L"] == "1–0"


def test_a_walkover_kept_before_walkovers_were_told_apart_isnt_form_either():
    # BUG-141: Alcaraz's walkover in Barcelona was kept as "Lost " (no score) before BUG-040's fix, and counted as a
    # loss in his W–L for good: a result with no score wasn't played. A retirement still counts.
    tennis = sports.of("tennis")
    assert [tennis.outcome(said, ["Carlos Alcaraz"]) for said in ("Lost ", "Won", "Lost 3-6 2-2 ret.", "Won 6-4 6-3")] == [None, None, "L", "W"]
    stats, _ = form.summarize("Tennis", [(result("Lost "), line("Lost in 0 sets", Sets="0–0")), (result("Won 6-4 6-3"), line("Won in 2 sets", Sets="2–0"))], [])
    assert {s["label"]: s["value"] for s in stats}["W–L"] == "1–0"


# ---------------------------------------------------------------- won or lost, from the side the player played for (BUG-084)


RCB, GT, SRH, PBKS, LSG, MI, DC = 8, 1, 4, 5, 10, 7, 3
SIDES = {RCB: "Royal Challengers Bengaluru", GT: "Gujarat Titans", SRH: "Sunrisers Hyderabad", PBKS: "Punjab Kings", LSG: "Lucknow Super Giants", MI: "Mumbai Indians", DC: "Delhi Capitals"}
KOHLI_ID, TRADED_ID = 3338, 9100


def _ipl(home: int, away: int, note: str, winner: int | None, lineup: dict[int, int]) -> dict:
    """A finished IPL match in Sportmonks' shape (made up, as tests/test_sportmonks_cricket.py's): its note, the side
    it marks the winner, and who played for which side."""
    def side(t: int) -> dict:
        return {"id": t, "name": SIDES[t], "code": SIDES[t][:3].upper()}

    return {
        "id": 69518, "status": "Finished", "type": "T20", "round": "Final", "season_id": 1689, "starting_at": "2026-05-31T14:00:00.000000Z",
        "league": {"name": "Indian Premier League", "type": "league"}, "stage": {"name": "Play Offs"}, "venue": {"name": "Narendra Modi Stadium", "city": "Ahmedabad"},
        "localteam": side(home), "visitorteam": side(away), "localteam_id": home, "visitorteam_id": away, "runs": [], "note": note, "winner_team_id": winner,
        "lineup": [{"id": pid, "fullname": f"Player {pid}", "lineup": {"team_id": t, "substitution": False}} for pid, t in lineup.items()],
        "batting": [], "bowling": [], "manofmatch": None,
    }


def _kept(x: dict, ref: PlayerRef, scorecard: bool = True) -> dict:
    """The result structured keeps for the player from this match: with its scorecard, or from the schedule alone."""
    f = sportmonks_cricket.parse_fixture(x)
    return structured.result_item(sportmonks_cricket.SportmonksCricket(), f, sportmonks_cricket.parse_match(x) if scorecard else None, None, ref)


def test_a_cricketers_result_is_won_or_lost_by_the_side_they_played_for():
    # Kohli's page lists only India, and every IPL win showed an "L". Whether he won is decided as the result is
    # kept, from the side the source has him playing for and the side it marks the winner, not from names.
    kohli = PlayerRef("sportmonks_cricket", str(KOHLI_ID), "Virat Kohli", [str(RCB)], [SIDES[RCB]], other_teams=["India"])
    final = _ipl(RCB, GT, "Royal Challengers Bengaluru won by 5 wickets (with 12 balls remaining)", RCB, {KOHLI_ID: RCB})
    lost = _ipl(SRH, RCB, "Sunrisers Hyderabad won by 55 runs", SRH, {KOHLI_ID: RCB})
    # Sportmonks' note for 69518, garbled; it marks Bengaluru the winner.
    garbled = _ipl(PBKS, RCB, "Royal Challengers won by 6Bengaluru  wickets (with 2 balls remaining)", RCB, {KOHLI_ID: RCB})
    assert [_kept(x, kohli).get("outcome") for x in (final, lost, garbled)] == ["W", "L", "W"]
    assert [_kept(x, kohli, scorecard=False).get("outcome") for x in (final, lost, garbled)] == ["W", "L", "W"]  # the schedule marks the winner too
    # A traded player: his old franchise's win is his, though his team now is another.
    traded = PlayerRef("sportmonks_cricket", str(TRADED_ID), "A Traded", [str(DC)], [SIDES[DC]])
    old = _ipl(LSG, MI, "Lucknow Super Giants won by 9 runs", LSG, {TRADED_ID: LSG})
    assert _kept(old, traded).get("outcome") == "W"
    # Without the scorecard nothing says which side was his, and his team now isn't in the game: no badge, not a loss.
    schedule_only = _kept(old, traded, scorecard=False)
    assert "outcome" in schedule_only and schedule_only["outcome"] is None


def test_a_result_is_a_loss_only_where_the_players_side_is_in_the_game():
    # Where no side is marked the winner, the words decide, and a winner that isn't theirs is a loss only where one
    # of the game's sides is one of their teams (the app's outcome() in web/src/format.ts reads them the same way).
    rcb_gt = [SIDES[RCB], SIDES[GT]]
    assert winner_outcome("Royal Challengers Bengaluru won by 5 wickets", ["India"], rcb_gt) is None
    assert winner_outcome("Lucknow Super Giants won by 9 runs", [SIDES[DC]], [SIDES[LSG], SIDES[MI]]) is None
    assert winner_outcome("Gujarat Titans won by 5 wickets", ["India", SIDES[RCB]], rcb_gt) == "L"
    assert winner_outcome("Royal Challengers Bengaluru won by 5 wickets", ["India", SIDES[RCB]], rcb_gt) == "W"
    cricket = sports.get("cricket")
    unmarked = {"teams": [{"id": str(RCB), "name": SIDES[RCB], "winner": None}, {"id": str(GT), "name": SIDES[GT], "winner": None}]}
    assert cricket.outcome("Royal Challengers Bengaluru won by 5 wickets", ["India"], unmarked) is None
    assert cricket.outcome("Royal Challengers Bengaluru won by 5 wickets", ["India"], unmarked, str(RCB)) == "W"
    marked = {"teams": [{"id": str(RCB), "name": SIDES[RCB], "winner": True}, {"id": str(GT), "name": SIDES[GT], "winner": False}]}
    assert cricket.outcome("Royal Challengers won by 6Bengaluru  wickets", [SIDES[RCB]], marked) == "W"  # the flag, by the side's name
    assert cricket.outcome("Royal Challengers won by 6Bengaluru  wickets", [], marked, str(GT)) == "L"


def test_a_reread_without_the_scorecard_keeps_the_outcome_it_told():
    # The scorecard has a traded player in his old side's XI; the schedule alone has only his team now to go by.
    item = {"title": "Lucknow Super Giants v Delhi Capitals", "result": "Lucknow Super Giants won by 9 runs"}
    with_card = merged_result(None, {**item, "outcome": "W"}, scorecard=True)
    assert merged_result(with_card, {**item, "outcome": "L"}, scorecard=False)["outcome"] == "W"
    assert merged_result(with_card, {**item, "outcome": "L"}, scorecard=True)["outcome"] == "L"  # a scorecard read again says


def test_form_counts_each_result_as_it_was_kept():
    # The Stats tab's record agrees with the Matches tab's badges: both are the outcome kept with the result.
    garbled = {**result("Royal Challengers won by 6Bengaluru  wickets"), "outcome": "W"}
    lost = {**result("Sunrisers Hyderabad won by 55 runs"), "outcome": "L"}
    india = {**result("India won by 8 wkts (50b rem)"), "outcome": "W"}
    stats, _ = form.summarize("cricket", [(garbled, None), (lost, None), (india, None)], [SIDES[RCB]])
    assert stats == [{"label": "W–D–L", "value": "2–0–1"}]


# ---------------------------------------------------------------- the same, on the real queries


TEST_DB = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")


@pytest.mark.skipif(not TEST_DB, reason="SPORTS_FOLLOW_TEST_DATABASE_URL isn't set (an empty database at alembic head)")
def test_kohlis_results_and_form_agree_on_who_won(monkeypatch):
    # Kohli's page lists only India (player.teams); his source has him at Bengaluru, and India besides. Results kept
    # before results said who won are judged as they're read, from the game's final board; the Stats tab's record
    # counts the same.
    engine = create_engine(TEST_DB)
    monkeypatch.setattr(db_module, "SessionLocal", db_module.sessionmaker(bind=engine, expire_on_commit=False))
    ref = PlayerRef("espn_cricket", "253802", "Virat Kohli", ["335970"], [SIDES[RCB]], other_teams=["India"])
    bengaluru = {"id": "335970", "name": SIDES[RCB]}

    def board(other: str, winner: str | None, summary: str) -> dict:
        sides = [bengaluru, {"id": "1", "name": other}] if other != "Australia" else [{"id": "6", "name": "India"}, {"id": "2", "name": other}]
        return {"kind": "cricket", "summary": summary, "teams": [{**s, "winner": None if winner is None else s["name"] == winner} for s in sides]}

    games = [  # newest first: (title, result, the final board, what was kept, want)
        ("Royal Challengers Bengaluru v Gujarat Titans · Final", "Royal Challengers Bengaluru won by 5 wickets", board(SIDES[GT], SIDES[RCB], ""), None, "W"),
        ("Sunrisers Hyderabad v Royal Challengers Bengaluru · 67th Match", "Sunrisers Hyderabad won by 55 runs", board(SIDES[SRH], SIDES[SRH], ""), None, "L"),
        ("Punjab Kings v Royal Challengers Bengaluru · 61st Match", "Royal Challengers won by 6Bengaluru  wickets", board(SIDES[PBKS], SIDES[RCB], ""), None, "W"),
        ("India v Australia · 2nd T20I", "India won by 8 wkts", board("Australia", None, "India won by 8 wkts"), None, "W"),  # no flag: the words, India his
        ("Royal Challengers Bengaluru v Kolkata Knight Riders · 57th Match", "Royal Challengers Bengaluru won by 23 runs", None, "W", "W"),  # kept with it
        ("Mumbai Indians v Royal Challengers Bengaluru · 52nd Match", "Mumbai Indians won by 5 wkts", None, None, "L"),  # no board: his teams
    ]
    with engine.begin() as c:
        c.execute(text("TRUNCATE player, event, follow RESTART IDENTITY CASCADE"))
        x = lambda sql, **p: c.execute(text(sql), p)  # noqa: E731
        x("INSERT INTO player (id, slug, name, sport, status, teams) VALUES (1, 'virat-kohli', 'Virat Kohli', 'Cricket', 'active', '[\"India\"]')")
        x("INSERT INTO source_binding (player_id, purpose, adapter, locator, confidence, priority, health) VALUES (1, 'fixtures', 'espn_cricket', CAST(:l AS jsonb), 1, 0, '{}')", l=json.dumps(asdict(ref)))
        x("INSERT INTO follow (fan_id, player_id, alert_rules, created_at) VALUES ('fan', 1, '{}', now())")
        for i, (title, said, final, kept, _) in enumerate(games, start=1):
            start = datetime(2026, 6, 1, tzinfo=timezone.utc).replace(day=20 - i)
            ids = [t["id"] for t in final["teams"]] if final else ["335970", "9"]
            x("INSERT INTO event (id, key, sport, competition, title, participants, status, start_utc) VALUES (:e, :k, 'Cricket', 'IPL', :t, CAST(:p AS jsonb), 'final', :s)",
              e=i, k=f"espn_cricket:{i}", t=title, p=json.dumps(ids), s=start)
            row = {"title": title, "result": said, "scorecard": final is not None, **({"outcome": kept} if kept else {})}
            x("INSERT INTO event_player (event_id, player_id, result) VALUES (:e, 1, CAST(:r AS jsonb))", e=i, r=json.dumps(row))
            if final:
                x("INSERT INTO event_state (event_id, as_of, status, score_label, clock_label, state) VALUES (:e, :a, 'final', '', :c, CAST(:s AS jsonb))", e=i, a=start, c=said, s=json.dumps(final))
    want = [w for *_, w in games]
    try:
        with db_module.session() as s:
            page = results.page(s, 1)
            assert [r.get("outcome") for r in page["results"]] == want
            assert [r.get("outcome") for r in results.latest_for_fan(s, "fan", 10)] == want
            assert results.detail(s, 1, 3)["outcome"] == "W"
            stats = results.player_form(s, 1, 10)["stats"]
            assert stats[0] == {"label": "W–D–L", "value": f"{want.count('W')}–0–{want.count('L')}"}
    finally:
        with engine.begin() as c:
            c.execute(text("TRUNCATE player, event, follow RESTART IDENTITY CASCADE"))
