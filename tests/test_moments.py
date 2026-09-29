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
