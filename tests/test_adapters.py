"""Adapter parsers against real ESPN responses recorded on 2026-09-29 (tests/fixtures/espn).

No network: if ESPN changes shape, re-record the fixture and these tests say what moved.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import httpx
import pytest

from sports_follow import moments, naming, structured
from sports_follow.adapters import espn, espn_cricket, espn_soccer, lichess_chess, liquipedia, opendota, pandascore, pubg, sportmonks
from sports_follow.adapters.base import AdapterError, Fixture

FIXTURES = Path(__file__).parent / "fixtures" / "espn"
KOHLI = "253802"
RONALDO = "22774"
AL_NASSR = "817"
MATSUDA, HENDERSON = "270555", "127262"
BURNLEY, BRADFORD = "379", "387"


def load(name: str) -> dict:
    with gzip.open(FIXTURES / f"{name}.json.gz", "rt") as f:
        return json.load(f)


# ---------------------------------------------------------------- shared


def test_names_match_on_surname_and_accents():
    assert naming.names_match("Ronaldo", "Cristiano Ronaldo")
    assert naming.names_match("Kohli", "Virat Kohli")
    assert naming.names_match("Jokic", "Nikola Jokić")
    assert not naming.names_match("Ronaldo", "Ronald Araújo")


def test_names_match_needs_every_word_of_the_shorter_name():
    # The surname alone used to do: "Ahmed Salah" bound Liverpool's Mohamed Salah (BUG-032).
    assert not naming.names_match("Ahmed Salah", "Mohamed Salah")
    assert not naming.names_match("Mohamed Salah", "Ahmed Salah")
    assert naming.names_match("Jasprit Bumrah", "Jasprit Jasbirsingh Bumrah")  # ESPNcricinfo's longer legal name
    assert naming.names_match("Cristiano Ronaldo", "Ronaldo")
    assert naming.names_match("L. Messi", "Lionel Messi")  # an initial stands for its word
    assert naming.names_match("MS Dhoni", "M. S. Dhoni")
    assert naming.names_match("Trent Alexander-Arnold", "Trent Alexander Arnold")
    assert naming.names_match("N’Golo Kanté", "N'Golo Kante")


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


# A captive portal's sign-in page or a CDN's error page, served with a 200; then JSON of the wrong shape.
PORTAL = "<html><body>Sign in to continue</body></html>"
NOT_JSON = [
    (espn, PORTAL, lambda: espn.get_json("https://site.api.espn.com/apis/x", 0)),  # as it always did
    (pandascore, PORTAL, lambda: pandascore.get_json("/matches", 0, **{"filter[id]": "1"})),
    (pandascore, '"maintenance"', lambda: pandascore.get_json("/matches", 0)),
    (lichess_chess, PORTAL, lambda: lichess_chess.get_json("https://lichess.org/api/broadcast/-/-/abcdefgh", 0)),
    (liquipedia, PORTAL, lambda: liquipedia.get("match", 0, wiki="pubgmobile")),
    (liquipedia, "[]", lambda: liquipedia.get("match", 0, wiki="pubgmobile")),
    (liquipedia, '{"result": "none"}', lambda: liquipedia.get("match", 0, wiki="pubgmobile")),
    (sportmonks, PORTAL, lambda: sportmonks.get_json("/football/fixtures/1", 0)),
    (sportmonks, "[]", lambda: sportmonks.get_json("/football/fixtures/1", 0)),
    (pubg, PORTAL, lambda: pubg._get("/tournaments", limited=False)),
    (pubg, "[]", lambda: pubg._get("/tournaments", limited=False)),
    (opendota, PORTAL, opendota.live),
    (opendota, "{}", opendota.live),
    (opendota, "[]", lambda: opendota._get("/matches/1", dict)),
]


@pytest.mark.parametrize("module, body, read", NOT_JSON, ids=[f"{m.__name__.rsplit('.', 1)[1]}:{b[:6]}" for m, b, _ in NOT_JSON])
def test_a_200_that_isnt_the_json_asked_for_is_an_adapter_error(monkeypatch, module, body, read):
    # BUG-068: a raw JSONDecodeError (or an AttributeError, from .get on a list) got past every
    # "except AdapterError" and stopped an esports lane or a refresh, recording no binding health.
    monkeypatch.setattr(module, "_client", httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body))))
    monkeypatch.setattr(module, "token", lambda: "example", raising=False)
    for name, value in (("_cache", {}), ("_memo", {}), ("_state", {"paused_until": 0.0, "last": 0.0}), ("_take", lambda: None), ("ENABLED", True)):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
    with pytest.raises(AdapterError):
        read()


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


def test_cricket_postponed_and_abandoned_matches_are_not_results():
    # 13 Oct 2026: Arunachal Pradesh v Nagaland was postponed (status type 113, "post", with the old
    # start time as its summary); it was stored as a result, "Starts at 09:30 local time".
    day = {f.source_id: f for f in espn_cricket.parse_day(load("cricket-day-20261013"))}
    assert (day["1546542"].status, day["1546542"].result) == ("postponed", None)
    assert (day["1555670"].status, day["1555670"].result["summary"]) == ("final", "Match drawn")  # a draw is a result
    # 29 Sep 2026: three matches abandoned without a ball bowled (type 6).
    abandoned = [f for f in espn_cricket.parse_day(load("cricket-day-20260929")) if f.source_id in ("1551842", "1552774", "1552775")]
    assert [(f.status, f.result) for f in abandoned] == [("postponed", None)] * 3
    snap = espn_cricket.parse_summary(load("cricket-summary-postponed"))
    assert (snap.status, snap.score_label, snap.clock_label) == ("postponed", "Arunachal Pradesh v Nagaland", "Postponed")
    snap = espn_cricket.parse_summary(load("cricket-summary-abandoned"))  # Nepal v Sri Lanka, Asian Games
    assert (snap.status, snap.score_label, snap.clock_label) == ("postponed", "Nepal v Sri Lanka", "Abandoned")
    # Abandoned after play is a no result, with a scorecard to keep.
    rained_off = load("cricket-summary-final")
    rained_off["header"]["competitions"][0]["status"].update(summary="No result", type={"id": "6", "description": "Abandoned", "state": "post"})
    assert espn_cricket.parse_summary(rained_off).status == "final"


def test_cricket_final_scorecard_gives_the_players_line():
    snap = espn_cricket.parse_summary(load("cricket-summary-final"))
    assert snap.status == "final"
    assert snap.score_label == "WI 295/7 (50.0 ov) · IND 300/2 (41.4 ov)"
    assert snap.clock_label == "India won by 8 wkts (50b rem)"
    assert snap.state["player_of_match"] == "Kuldeep Yadav"
    line = snap.lines[KOHLI]
    assert line.headline == "139* (88)"
    assert {s["label"]: s["value"] for s in line.stats} == {"Runs": "139*", "Balls": "88", "4s": "10", "6s": "9", "Strike rate": "158.0"}


def test_cricket_lines_cover_the_side_that_batted_first():
    # The summary's matchcards hold only India's chase; the rosters hold both innings.
    snap = espn_cricket.parse_summary(load("cricket-summary-final"))
    greaves, kuldeep = snap.lines["510823"], snap.lines["559235"]
    assert greaves.headline == "101 (108)"
    assert {"label": "Out", "value": "lbw b Kuldeep Yadav"} in greaves.stats
    assert kuldeep.headline == "4/40 (10 ov)"  # the player of the match, bowling first
    assert kuldeep.stats == [{"label": "Wickets", "value": "4"}, {"label": "Overs", "value": "10"}, {"label": "Runs conceded", "value": "40"}, {"label": "Economy", "value": "4.00"}]
    assert {s["label"]: s["value"] for s in snap.lines["34102"].stats}["Out"] == "c Andrew b Joseph"
    assert structured.contribution(snap, "510823") == "101 (108)"


def test_cricket_test_lines_have_every_innings_as_the_score_stands():
    # A live Test (recorded 2026-10-13), fourth innings: the matchcards were 16 overs behind the score,
    # with Atal still on 104*; the rosters have him out for 111, and his first innings too.
    snap = espn_cricket.parse_summary(load("cricket-summary-test-live"))
    assert snap.score_label == "BAN 346 (92.3 ov) · AFG 240 (89.1 ov) · BAN 372/7d (105.2 ov) · AFG 291/7 (122.0 ov)"
    atal = snap.lines["1161050"]
    assert atal.headline == "17 (31) & 111 (332)"
    assert {s["label"]: s["value"] for s in atal.stats} == {"Runs": "111", "Balls": "332", "4s": "9", "6s": "3", "Strike rate": "33.4", "Out": "c Tanzid Hasan b Taijul Islam"}
    # Both innings with the bat and the ball; the stats are the latest of each, as the alerts read them.
    hasan = snap.lines["926629"]
    assert hasan.headline == "2 (5) & 1 (6) · 5/55 (21 ov) & 1/66 (26 ov)"
    assert [s["value"] for s in hasan.stats if s["label"] in ("Runs", "Wickets")] == ["1", "1"]
    assert snap.lines["440968"].headline == "0 (11) & 6* (45) · 3/79 (20.3 ov) & 2/96 (20 ov)"  # at the crease
    # Form counts every innings, not only the latest.
    test = Fixture("1555116", "Afghanistan v Bangladesh", "Bangladesh tour of United Arab Emirates", None, None, "final", ["40", "25"], {}, detail="Only Test")
    stats, note = espn_cricket.recent_form("533956", [(test, snap)])
    assert snap.lines["533956"].headline == "105* (216) & 51 (124)"
    assert {s["label"]: s["value"] for s in stats} == {"Runs": "156", "Average": "156.00", "Strike rate": "45.9", "Highest": "105", "100s / 50s": "1 / 1"}
    assert note == "Last 1 match (Test), from ESPNcricinfo scorecards"


def test_cricket_only_the_innings_on_now_is_batting_and_a_declaration_keeps_its_d():
    # The live Test above: isBatting marks every innings a side batted in, so all four read "batting";
    # isCurrent marks the one on now. Bangladesh declared their second innings, 372/7d.
    data = load("cricket-summary-test-live")
    snap = espn_cricket.parse_summary(data)
    innings = [(i["abbr"], i["period"], i["batting"], i["declared"]) for i in snap.state["innings"]]
    assert innings == [("BAN", 1, False, False), ("AFG", 2, False, False), ("BAN", 3, False, True), ("AFG", 4, True, False)]
    # Between innings the one just over is still the current one: all out, nobody is batting.
    afg = next(c for c in data["header"]["competitions"][0]["competitors"] if c["team"]["abbreviation"] == "AFG")
    afg["linescores"][-1].update(wickets=10, description="all out")
    assert [i["batting"] for i in espn_cricket.parse_summary(data).state["innings"]] == [False] * 4


def test_cricket_results_read_without_html_entities():
    # 13 Oct 2026: "Chattogram won by an inns &amp; 1 runs" in the result, the alert and the history, as
    # every innings victory read.
    day = {f.source_id: f for f in espn_cricket.parse_day(load("cricket-day-20261013"))}
    assert espn_cricket.EspnCricket().result_label(day["1555672"], ["679"]) == "Chattogram won by an inns & 1 runs"
    snap = espn_cricket.parse_summary(load("cricket-summary-innings-win"))
    assert (snap.status, snap.clock_label) == ("final", "Chattogram won by an inns & 1 runs")
    assert snap.state["summary"] == snap.clock_label  # the board shows the summary once, beside the clock
    [final] = moments.detect("Cricket", "Yasir Ali", "629059", moments.Seen("live"), moments.Seen("final", snap.state, None, snap.score_label, snap.clock_label))
    assert final.title == "Yasir Ali: Chattogram won by an inns & 1 runs"


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


def _seen(snap, athlete_id: str) -> moments.Seen:
    line = snap.lines[athlete_id]
    return moments.Seen("live", snap.state, {"headline": line.headline, "stats": line.stats}, snap.score_label, snap.clock_label, events=snap.moments)


def test_soccer_goals_of_every_kind_are_told_with_their_minute():
    # Kyoto Sanga 1–0 Ratchaburi (recorded 2026-10-13): the 88' winner was a volley ("goal---volley"),
    # which the events left out, so it went out as a plain "Goal" at the clock's 90'+1'.
    snap = espn_soccer.parse_summary(load("soccer-summary-volley"), "all")
    [goal] = [m for m in snap.moments if m["kind"] == "GOAL"]
    assert (goal["id"], goal["clock"], goal["athlete_ids"][0], goal["assist"]) == ("53579366", "88'", MATSUDA, "Haruki Arai")
    [f] = moments.detect("Football", "Temma Matsuda", MATSUDA, moments.Seen("live", line={"headline": "Started", "stats": [{"label": "Goals", "value": "0"}]}), _seen(snap, MATSUDA))
    assert (f.title, f.body, f.priority) == ("Late goal: Temma Matsuda puts Kyoto Sanga ahead", "Kyoto Sanga 1 – 0 Ratchaburi FC · 88' · assist Haruki Arai", 95)
    # Chelsea 5–1 Bournemouth (recorded 2026-10-13): Henderson's second was a free kick
    # ("goal---free-kick"); without it his second goal had no minute of its own.
    snap = espn_soccer.parse_summary(load("soccer-summary-free-kick"), "eng.1")
    assert [m["clock"] for m in snap.moments if m["kind"] == "GOAL"] == ["70'", "67'", "51'", "46'", "3'", "2'"]
    before = {"headline": "1 goal, 1 assist", "stats": [{"label": "Goals", "value": "1"}, {"label": "Assists", "value": "1"}]}
    [f] = moments.detect("Football", "Jordan Henderson", HENDERSON, moments.Seen("live", line=before), _seen(snap, HENDERSON))
    assert (f.title, f.body, f.detail) == ("Goal: Jordan Henderson (2)", "Chelsea 5 – 1 AFC Bournemouth · 70'", "goal@53391731")


def test_soccer_shootouts_say_who_won_on_penalties():
    # Bradford City 0–0 Burnley in the Carabao Cup (recorded 2026-10-13): Bradford won 4–2 on penalties.
    snap = espn_soccer.parse_summary(load("soccer-summary-shootout"), "eng.league_cup")
    assert (snap.status, snap.score_label, snap.clock_label) == ("final", "Bradford City 0 – 0 Burnley (4–2 pens)", "FT-Pens")
    fixtures = {f.source_id: f for f in espn_soccer.parse_schedule(load("soccer-schedule-shootouts"))}
    cup = fixtures["401908136"]
    assert espn_soccer.result_label(cup, [BURNLEY]) == "Lost 0–0 (2–4 pens)"
    assert espn_soccer.result_label(cup, [BRADFORD]) == "Won 0–0 (4–2 pens)"
    assert espn_soccer.result_label(cup, ["1"]) == "Bradford City 0–0 Burnley (4–2 pens)"
    assert espn_soccer.result_label(fixtures["401880603"], [BURNLEY]) == "Won 1–1 (4–3 pens)"
    assert espn_soccer.result_label(fixtures["401880254"], [BURNLEY]) == "Drew 1–1"  # no shootout
    # No winner flag: the score decides, not "Drew".
    unflagged = Fixture("1", "Burnley vs Derby County", "", None, None, "final", [BURNLEY, "374"], {}, result={
        "home": {"id": BURNLEY, "name": "Burnley", "score": "2", "winner": None}, "away": {"id": "374", "name": "Derby County", "score": "1", "winner": None}})
    assert (espn_soccer.result_label(unflagged, [BURNLEY]), espn_soccer.result_label(unflagged, ["374"])) == ("Won 2–1", "Lost 1–2")


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
