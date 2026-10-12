"""Moments: what's worth a notification between two polls (moments.py), who hears about it, and
quiet hours (notify.py)."""

from __future__ import annotations

from datetime import datetime, timezone

from sports_follow import moments, notify
from sports_follow.moments import Seen, detect


def line(headline: str, **stats: str) -> dict:
    return {"headline": headline, "stats": [{"label": k.replace("_", " "), "value": v} for k, v in stats.items()]}


def kinds(found) -> list[tuple[str, str]]:
    return [(f.kind, f.detail) for f in found]


def test_cricket_fifty_hundred_wickets_and_out():
    before = Seen("live", line=line("48* (39)", Runs="48*", Balls="39"), score="IND 160/2")
    now = Seen("live", line=line("103* (80)", Runs="103*", Balls="80"), score="IND 240/2")
    found = detect("Cricket", "Virat Kohli", "253802", before, now)
    assert kinds(found) == [("fifty", "50"), ("hundred", "100")]
    assert found[1].title == "Virat Kohli reaches 100" and found[1].body == "103* (80) · IND 240/2"
    out = Seen("live", line=line("103 (82)", Runs="103", Balls="82", Out="c Holder b Joseph"))
    [f] = detect("Cricket", "Virat Kohli", "253802", now, out)
    assert (f.kind, f.title, f.level) == ("out", "Virat Kohli out for 103", "key")
    bowling = detect("Cricket", "Jasprit Bumrah", "1", Seen("live", line=line("2/20", Wickets="2")), Seen("live", line=line("4/25", Wickets="4", Runs_conceded="25", Overs="7")))
    assert kinds(bowling) == [("wickets", "w3"), ("wickets", "w4")]


def test_a_first_look_mid_game_is_the_baseline():
    now = Seen("live", line=line("88* (70)", Runs="88*"))
    assert detect("Cricket", "Virat Kohli", "253802", None, now) == []  # no "reaches 50" an hour late


def test_game_start_needs_the_player_in_it():
    before = Seen("scheduled")
    playing = Seen("live", line=line("Started", Goals="0"), score="Egypt 0–0 South Africa", clock="1'")
    assert kinds(detect("Football", "Mohamed Salah", "173896", before, playing)) == [("start", "")]
    benched = Seen("live", line=line("On the bench"))
    assert detect("Football", "Mohamed Salah", "173896", before, benched) == []


def test_no_result_for_a_game_the_player_was_left_out_of():
    # A national team game he wasn't called up for: the line-ups are out and he isn't in them.
    left_out = Seen("final", score="Egypt 2–0 South Africa", lineups=True)
    assert detect("Football", "Mohamed Salah", "173896", Seen("live", lineups=True), left_out) == []
    assert detect("Basketball", "Caitlin Clark", "4433403", None, Seen("final", score="Fever 90–88 Aces", lineups=True)) == []
    # An unused substitute was in the squad, and without line-ups nobody can tell.
    bench = Seen("final", line=line("On the bench"), score="Egypt 2–0 South Africa", lineups=True)
    assert kinds(detect("Football", "Mohamed Salah", "173896", Seen("live", lineups=True), bench)) == [("final", "")]
    assert kinds(detect("Football", "Mohamed Salah", "173896", Seen("live"), Seen("final", score="Egypt 2–0 South Africa"))) == [("final", "")]
    # In cricket no line only means he didn't bat or bowl.
    assert kinds(detect("Cricket", "Virat Kohli", "253802", Seen("live", lineups=True), Seen("final", clock="India won by 5 wickets", lineups=True))) == [("final", "")]


def test_football_goals_assists_cards_and_coming_on():
    before = Seen("live", line=line("On the bench"))
    now = Seen("live", line=line("1 goal", Goals="1", Assists="1", Yellow="1"), score="Egypt 2–0 South Africa", clock="67'")
    found = detect("Football", "Mohamed Salah", "173896", before, now)
    assert kinds(found) == [("goal", "goal1"), ("assist", "assist1"), ("yellow", "yellow1"), ("on", "")]
    assert found[0].title == "Goal: Mohamed Salah" and found[0].body == "Egypt 2–0 South Africa · 67'"
    assert [f.level for f in found] == ["key", "key", "minor", "minor"]
    brace = detect("Football", "Mohamed Salah", "173896", now, Seen("live", line=line("2 goals", Goals="2", Assists="1", Yellow="1")))
    assert brace[0].title == "Goal: Mohamed Salah (2)"


def test_basketball_points_and_doubles():
    before = Seen("live", line=line("28 PTS · 9 REB · 9 AST"))
    now = Seen("live", line=line("31 PTS · 10 REB · 11 AST", Steals="1"), score="Fever 90–88 Aces", clock="Q4 1:02")
    found = detect("Basketball", "Caitlin Clark", "4433403", before, now)
    assert kinds(found) == [("points", "30"), ("double", ""), ("triple", "")]
    assert [f.level for f in found] == ["key", "minor", "key"]


def test_tennis_sets_and_an_upset():
    before = Seen("live", line=line("Level in sets", Sets="1–1"))
    now = Seen("live", line=line("Leads 2–1 in sets", Sets="2–1"), score="6-4 3-6 6-2")
    [f] = detect("Tennis", "Carlos Alcaraz", "3782", before, now)
    assert (f.kind, f.title, f.level) == ("set", "Carlos Alcaraz wins the 3rd set", "minor")
    final = Seen("final", state={"players": [{"id": "3782", "winner": True, "seed": 5}, {"id": "9", "name": "Jannik Sinner", "seed": 1}]}, line=line("Won in 3 sets", Sets="2–1"), score="6-4 3-6 6-2")
    found = detect("Tennis", "Carlos Alcaraz", "3782", now, final)
    assert kinds(found) == [("final", ""), ("upset", "")]
    assert found[1].title == "Upset: Carlos Alcaraz beats [1] Jannik Sinner"


def test_chess_game_start_and_result():
    start = detect("Chess", "Magnus Carlsen", "1503014", Seen("scheduled"), Seen("live", state={"round": "Round 5"}, line=line("Playing White vs Gukesh D", Opponent="Gukesh D")))
    assert [(f.kind, f.title, f.body) for f in start] == [("start", "Magnus Carlsen is playing", "Round 5 · vs Gukesh D")]
    [final] = detect("Chess", "Magnus Carlsen", "1503014", Seen("live"), Seen("final", line=line("Won with White vs Gukesh D"), score="1–0"))
    assert (final.title, final.body) == ("Magnus Carlsen: Won with White vs Gukesh D", "1–0")


def test_chess_bands_need_a_margin_to_change():
    assert moments.band({"cp": 310}, current=1) == 1  # past +3.0 but not by the margin
    assert moments.band({"cp": 335}, current=1) == 2
    assert moments.band({"cp": 290}, current=2) == 2
    assert moments.band({"cp": 250}, current=2) == 1
    assert moments.band({"cp": -20, "mate": 4}) == 2 and moments.band({"mate": -2}) == -2


def test_a_chess_swing_counts_once_two_evaluations_agree():
    first = moments.settle(None, {"cp": 20, "fen": "8/8/8/8/8/8/8/8 w - - 0 30", "at": 1})
    glitch = moments.settle(first, {"cp": 520, "fen": "8/8/8/8/8/8/8/8 b - - 0 31", "at": 2})
    assert (first["band"], glitch["band"]) == (0, 0)  # one reading isn't enough
    back = moments.settle(glitch, {"cp": 30, "fen": "8/8/8/8/8/8/8/8 w - - 0 32", "at": 3})
    assert back["band"] == 0
    real = moments.settle(moments.settle(back, {"cp": 480, "at": 4, "fen": "8/8/8/8/8/8/8/8 w - - 0 33"}), {"cp": 510, "at": 5, "fen": "8/8/8/8/8/8/8/8 w - - 0 34"})
    assert real["band"] == 2 and real["move"] == 34
    assert moments.settle(real, {"cp": 510, "at": 5}) is real  # the same evaluation again: unchanged


def test_chess_swings_from_the_players_side():
    def seen(band, cp, colour="Black", game="g1", status="live", move=34):
        ev = {"band": band, "cp": cp, "mate": None, "move": move}
        return Seen(status, state={"game_id": game, "eval": ev}, line=line(f"{colour} vs Gukesh D", Colour=colour, Opponent="Gukesh D"))

    level, white_winning = seen(0, 10), seen(2, 360)
    [f] = detect("Chess", "Magnus Carlsen", "1503014", level, white_winning)
    assert (f.kind, f.level, f.title, f.body) == ("swing", "key", "Magnus Carlsen is in trouble", "Engine -3.6 · move 34 · vs Gukesh D")
    [f] = detect("Chess", "Magnus Carlsen", "1503014", seen(2, 400), seen(-1, -180))  # losing -> better
    assert (f.level, f.title) == ("key", "Turnaround: Magnus Carlsen is better")
    [f] = detect("Chess", "Magnus Carlsen", "1503014", level, seen(1, 200, colour="White"))
    assert (f.level, f.title) == ("minor", "Magnus Carlsen is better")
    assert detect("Chess", "Magnus Carlsen", "1503014", seen(2, 350, colour="White"), seen(1, 250, colour="White")) == []  # drifting back
    assert detect("Chess", "Magnus Carlsen", "1503014", seen(0, 0, game="g0"), white_winning) == []  # the next game of a match
    assert detect("Chess", "Magnus Carlsen", "1503014", Seen("live", state={"game_id": "g1"}), white_winning) == []  # first reading


def test_levels_decide_who_hears():
    assert moments.wanted("key", "key", "goal") and not moments.wanted("key", "minor", "on")
    assert moments.wanted("everything", "minor", "on")
    assert moments.wanted("results", "key", "final") and not moments.wanted("results", "key", "goal")
    assert not moments.wanted("off", "key", "final")


def test_news_kinds_from_free_text():
    assert moments.news_kind("Injury") == "injury"
    assert moments.news_kind("loan move") == "transfer"
    assert moments.news_kind("Retirement") == "retirement"
    assert moments.news_kind("report") is None


def test_quiet_hours_in_the_devices_timezone():
    at = lambda h, m=0: datetime(2026, 9, 29, h, m, tzinfo=timezone.utc)  # noqa: E731
    # 22:00–07:00 in India (UTC+5:30): 17:00 UTC is 22:30 there, 02:00 UTC is 07:30.
    assert notify.quiet("Asia/Kolkata", "22:00", "07:00", at(17)) is True
    assert notify.quiet("Asia/Kolkata", "22:00", "07:00", at(2)) is False
    assert notify.quiet("UTC", "13:00", "15:00", at(14)) is True
    assert notify.quiet("UTC", None, None, at(3)) is False
    assert notify.quiet("Not/AZone", "22:00", "07:00", at(23)) is False


def test_only_real_push_services_are_accepted():
    from sports_follow.server import _push_host_ok

    assert _push_host_ok("https://fcm.googleapis.com/fcm/send/abc")
    assert _push_host_ok("https://web.push.apple.com/QGx")
    assert not _push_host_ok("http://fcm.googleapis.com/fcm/send/abc")
    assert not _push_host_ok("https://127.0.0.1:8421/api")
    assert not _push_host_ok("https://evil.example/fcm.googleapis.com")


FOOTBALL = {"kind": "football", "home": {"id": "1", "name": "Egypt", "score": "1"}, "away": {"id": "2", "name": "South Africa", "score": "1"}}


def test_the_line_up_before_the_start():
    starts = detect("Football", "Mohamed Salah", "173896", None, Seen("scheduled", state=FOOTBALL, role="starting"))
    assert [(f.kind, f.detail, f.title, f.body, f.priority) for f in starts] == [("lineup", "starting", "Mohamed Salah starts", "Egypt v South Africa · line-ups are out", 50)]
    [bench] = detect("Football", "Mohamed Salah", "173896", Seen("scheduled"), Seen("scheduled", state=FOOTBALL, role="bench"))
    assert (bench.title, bench.level, bench.priority) == ("Mohamed Salah is on the bench", "key", 45)
    cricket = {"kind": "cricket", "teams": [{"name": "India"}, {"name": "Australia"}]}
    [xi] = detect("Cricket", "Virat Kohli", "253802", None, Seen("scheduled", state=cricket, role="starting"))
    assert (xi.title, xi.body) == ("Virat Kohli is in the XI", "India v Australia · line-ups are out")
    # Once the game is on, the start says it; and no team sheet, or not on it, says nothing.
    assert detect("Football", "Mohamed Salah", "173896", None, Seen("live", state=FOOTBALL, role="starting")) == []
    assert detect("Football", "Mohamed Salah", "173896", None, Seen("scheduled", state=FOOTBALL)) == []


def goal(minute: str, athlete: str = "173896", team: str = "1", **extra) -> dict:
    return {"kind": "GOAL", "clock": minute, "athlete_ids": [athlete], "team_id": team, **extra}


def scored(home: int, away: int, events: list[dict], goals: int = 1, clock: str = "90'") -> Seen:
    state = {**FOOTBALL, "home": {**FOOTBALL["home"], "score": str(home)}, "away": {**FOOTBALL["away"], "score": str(away)}}
    return Seen("live", state=state, line=line(f"{goals} goal", Goals=str(goals)), score=f"Egypt {home} – {away} South Africa", clock=clock, events=events)


def test_a_goal_says_what_it_did():
    before = Seen("live", line=line("Started"))
    late_equaliser = scored(1, 1, [goal("88'", assist="Trezeguet"), goal("20'", athlete="9", team="2")])
    [f] = detect("Football", "Mohamed Salah", "173896", before, late_equaliser)
    assert (f.title, f.body, f.priority) == ("Late equaliser: Mohamed Salah", "Egypt 1 – 1 South Africa · 88' · assist Trezeguet", 95)
    [f] = detect("Football", "Mohamed Salah", "173896", before, scored(2, 1, [goal("30'"), goal("20'", athlete="9", team="2"), goal("5'", athlete="7")], clock="31'"))
    assert (f.title, f.priority) == ("Goal: Mohamed Salah puts Egypt ahead", 90)
    [f] = detect("Football", "Mohamed Salah", "173896", before, scored(3, 0, [goal("70'"), goal("20'", athlete="7"), goal("5'", athlete="7")]))
    assert (f.title, f.body, f.priority) == ("Goal: Mohamed Salah", "Egypt 3 – 0 South Africa · 70'", 85)
    # Scored since: what his goal did can't be read off the score, so it's just a goal at its minute.
    [f] = detect("Football", "Mohamed Salah", "173896", before, scored(1, 1, [goal("89'", athlete="9", team="2"), goal("85'")]))
    assert (f.title, f.body) == ("Goal: Mohamed Salah", "Egypt 1 – 1 South Africa · 85'")
    # His own goal isn't one of his goals.
    hat_trick = scored(4, 1, [goal("80'"), goal("60'"), goal("50'", own_goal=True), goal("10'")], goals=3)
    found = detect("Football", "Mohamed Salah", "173896", Seen("live", line=line("1 goal", Goals="1")), hat_trick)
    assert [(f.title, f.body.split(" · ")[1]) for f in found] == [("Goal: Mohamed Salah (2)", "60'"), ("Hat-trick: Mohamed Salah", "80'")]


def test_priorities_rank_moments():
    def ranked(sport, before, now):
        return [(f.kind, f.priority) for f in detect(sport, "X", "1", before, now)]

    assert ranked("Cricket", Seen("live", line=line("90*", Runs="90*")), Seen("live", line=line("151*", Runs="151*"))) == [("hundred", 90), ("hundred", 95)]
    assert ranked("Cricket", Seen("live", line=line("0*", Runs="0*")), Seen("live", line=line("0", Runs="0", Out="b Starc"))) == [("out", 65)]
    assert ranked("Cricket", Seen("live", line=line("4/30", Wickets="4")), Seen("live", line=line("5/31", Wickets="5"))) == [("wickets", 90)]
    assert ranked("Basketball", Seen("live", line=line("38 PTS")), Seen("live", line=line("41 PTS"))) == [("points", 85)]
    assert ranked("Football", Seen("live", line=line("Started")), Seen("live", line=line("Started", Red="1", Yellow="1"))) == [("red", 80), ("yellow", 20)]
    assert ranked("Football", Seen("live"), Seen("final", score="Egypt 2–0 South Africa")) == [("final", 80)]
