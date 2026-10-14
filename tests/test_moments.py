"""Moments: what's worth a notification between two polls (moments.py), who hears about it, and
quiet hours and the push itself (notify.py)."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pywebpush
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from sports_follow import moments, notify
from sports_follow.sports import chess
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


def test_a_second_innings_has_moments_of_its_own():
    # A line's stats are the latest innings; its headline lists every innings, so says which one.
    first = Seen("live", line=line("103 (152)", Runs="103", Balls="152", Out="c Holder b Joseph"))
    # Back in for the second innings, and past 50 by the next poll: from nothing, not from 103.
    second = Seen("live", line=line("103 (152) & 52* (70)", Runs="52*", Balls="70"), score="IND 120/1")
    [fifty] = detect("Cricket", "Virat Kohli", "253802", first, second)
    assert (fifty.kind, fifty.detail, fifty.body) == ("fifty", "i2:50", "52* (70) · 2nd innings · IND 120/1")
    hundred = Seen("live", line=line("103 (152) & 104* (140)", Runs="104*", Balls="140"))
    assert kinds(detect("Cricket", "Virat Kohli", "253802", second, hundred)) == [("hundred", "i2:100")]
    out = Seen("live", line=line("103 (152) & 104 (141)", Runs="104", Balls="141", Out="lbw b Joseph"))
    [f] = detect("Cricket", "Virat Kohli", "253802", hundred, out)
    assert (f.detail, f.title, f.body) == ("i2:out", "Virat Kohli out for 104", "104 (141) · 2nd innings · lbw b Joseph")
    # Out again before a poll saw him in: the first innings' dismissal isn't this one.
    duck = Seen("live", line=line("103 (152) & 0 (1)", Runs="0", Balls="1", Out="b Starc"))
    assert kinds(detect("Cricket", "Virat Kohli", "253802", first, duck)) == [("out", "i2:out")]
    # A five-for in each innings.
    five = Seen("live", line=line("5/55 (21 ov)", Wickets="5", Runs_conceded="55", Overs="21"))
    again = Seen("live", line=line("5/55 (21 ov) & 5/40 (15 ov)", Wickets="5", Runs_conceded="40", Overs="15"))
    assert kinds(detect("Cricket", "Hasan Mahmud", "926629", five, again)) == [("wickets", "i2:w3"), ("wickets", "i2:w4"), ("wickets", "i2:w5")]
    # A last line that held only the latest innings isn't the innings before: no fifty an hour late.
    assert detect("Cricket", "Virat Kohli", "253802", Seen("live", line=line("70* (90)", Runs="70*")), Seen("live", line=line("103 (152) & 71* (92)", Runs="71*"))) == []
    # The first innings keeps the details it always had, so moments stored before still match.
    assert kinds(detect("Cricket", "Virat Kohli", "253802", Seen("live", line=line("48* (70)", Runs="48*")), Seen("live", line=line("103* (150)", Runs="103*")))) == [("fifty", "50"), ("hundred", "100")]


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


def test_no_upset_for_a_walkover_or_a_retirement():
    # H3: a qualifier through when the top seed withdrew heard "Upset: Qualifier Q beats [1] Jannik
    # Sinner", at priority 90, which always makes a sound. The state is espn_tennis.parse_match's.
    players = [{"id": "77", "name": "Qualifier Q", "winner": True, "seed": None}, {"id": "9", "name": "Jannik Sinner", "winner": False, "seed": 1}]
    for ending, headline in (("walkover", "Won by walkover"), ("retired", "Won, opponent retired")):
        final = Seen("final", state={"kind": "tennis", "players": players, "ending": ending}, line=line(headline, Sets="1–0"))
        assert kinds(detect("Tennis", "Qualifier Q", "77", Seen("armed"), final)) == [("final", "")]
    played = Seen("final", state={"kind": "tennis", "players": players, "ending": None}, line=line("Won in 2 sets", Sets="2–0"))
    assert kinds(detect("Tennis", "Qualifier Q", "77", Seen("live"), played)) == [("final", ""), ("upset", "")]


def test_chess_game_start_and_result():
    start = detect("Chess", "Magnus Carlsen", "1503014", Seen("scheduled"), Seen("live", state={"round": "Round 5"}, line=line("Playing White vs Gukesh D", Opponent="Gukesh D")))
    assert [(f.kind, f.title, f.body) for f in start] == [("start", "Magnus Carlsen is playing", "Round 5 · vs Gukesh D")]
    [final] = detect("Chess", "Magnus Carlsen", "1503014", Seen("live"), Seen("final", line=line("Won with White vs Gukesh D"), score="1–0"))
    assert (final.title, final.body) == ("Magnus Carlsen: Won with White vs Gukesh D", "1–0")


def test_chess_bands_need_a_margin_to_change():
    assert chess.band({"cp": 310}, current=1) == 1  # past +3.0 but not by the margin
    assert chess.band({"cp": 335}, current=1) == 2
    assert chess.band({"cp": 290}, current=2) == 2
    assert chess.band({"cp": 250}, current=2) == 1
    assert chess.band({"cp": -20, "mate": 4}) == 2 and chess.band({"mate": -2}) == -2


def test_a_chess_swing_counts_once_two_evaluations_agree():
    first = chess.settle(None, {"cp": 20, "fen": "8/8/8/8/8/8/8/8 w - - 0 30", "at": 1})
    glitch = chess.settle(first, {"cp": 520, "fen": "8/8/8/8/8/8/8/8 b - - 0 31", "at": 2})
    assert (first["band"], glitch["band"]) == (0, 0)  # one reading isn't enough
    back = chess.settle(glitch, {"cp": 30, "fen": "8/8/8/8/8/8/8/8 w - - 0 32", "at": 3})
    assert back["band"] == 0
    real = chess.settle(chess.settle(back, {"cp": 480, "at": 4, "fen": "8/8/8/8/8/8/8/8 w - - 0 33"}), {"cp": 510, "at": 5, "fen": "8/8/8/8/8/8/8/8 w - - 0 34"})
    assert real["band"] == 2 and real["move"] == 34
    assert chess.settle(real, {"cp": 510, "at": 5}) is real  # the same evaluation again: unchanged


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


def test_winning_is_told_once_while_the_evaluation_hovers_on_the_line():
    # H4: an evaluation hovering between +2.6 and +3.4 told "Magnus Carlsen is winning" at moves 33, 37
    # and 41, each a key push. Each reading comes twice here: a band moves once two agree.
    def titles(colour: str, cps: list[int]) -> list[str]:
        ev, found = None, []
        for at, cp in enumerate(cps, 1):
            new = chess.settle(ev, {"cp": cp, "mate": None, "at": at, "fen": f"8/8/8/8/8/8/8/8 w - - 0 {30 + at}"})
            if ev is not None:
                before, now = (Seen("live", state={"game_id": "g1", "eval": e}, line=line(f"{colour} vs Gukesh D", Colour=colour, Opponent="Gukesh D")) for e in (ev, new))
                found += detect("Chess", "Magnus Carlsen", "1503014", before, now)
            ev = new
        return [f.title for f in found]

    hovering = [20, 20, 340, 340, 260, 260, 345, 345, 255, 255, 350, 350]
    assert titles("White", hovering) == ["Magnus Carlsen is winning"]
    assert titles("Black", hovering) == ["Magnus Carlsen is in trouble"]  # the same game, from the other side
    # Back to level (+1.0), then winning again: that's news again.
    assert titles("White", [*hovering, 100, 100, 340, 340]) == ["Magnus Carlsen is winning", "Magnus Carlsen's advantage is gone", "Magnus Carlsen is winning"]
    # Turned around, and hovering on the other side: once each way.
    turned = [*hovering, -340, -340, -260, -260, -345, -345]
    assert titles("White", turned) == ["Magnus Carlsen is winning", "Turnaround: Magnus Carlsen is in trouble"]
    # An evaluation stored before "told" was kept counts as nothing told yet: told once more, then kept.
    old = {"cp": 340, "at": 0, "band": 2, "raw": 2, "move": 40}
    assert chess.settle(old, {"cp": 260, "at": 1})["told"] == 0


def test_levels_decide_who_hears():
    assert moments.wanted("key", "key", "goal") and not moments.wanted("key", "minor", "on")
    assert moments.wanted("everything", "minor", "on")
    assert moments.wanted("results", "key", "final") and not moments.wanted("results", "key", "goal")
    assert not moments.wanted("off", "key", "final")


def test_news_kinds_are_matched_whole():
    assert moments.news_kind(" Injury ") == "injury"
    assert moments.news_kind("Retirement") == "retirement"
    # By substring, "contract extension" was transfer news and "record" a milestone.
    assert moments.news_kind("contract extension") is None and moments.news_kind("loan move") is None
    assert moments.news_kind("record") is None and moments.news_kind("report") is None and moments.news_kind(None) is None


def test_quiet_hours_in_the_devices_timezone():
    at = lambda h, m=0: datetime(2026, 9, 29, h, m, tzinfo=timezone.utc)  # noqa: E731
    # 22:00–07:00 in India (UTC+5:30): 17:00 UTC is 22:30 there, 02:00 UTC is 07:30.
    assert notify.quiet("Asia/Kolkata", "22:00", "07:00", at(17)) is True
    assert notify.quiet("Asia/Kolkata", "22:00", "07:00", at(2)) is False
    assert notify.quiet("UTC", "13:00", "15:00", at(14)) is True
    assert notify.quiet("UTC", None, None, at(3)) is False
    assert notify.quiet("Not/AZone", "22:00", "07:00", at(23)) is False


def test_a_long_push_is_cut_to_fit_and_written_as_utf8(monkeypatch):
    # H3: a 1,440-character Hindi headline made a 7,732-byte push (json.dumps wrote each letter as a
    # 6-byte escape); the push service answered 413, which counted against the device.
    sent: list[str] = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **kwargs: sent.append(kwargs["data"]))
    monkeypatch.setattr(notify, "signer", lambda: None)
    headline = ("भारत ने वेस्टइंडीज को आठ विकेट से हराया, कोहली ने नाबाद शतक जड़ा। " * 30)[:1440]
    payload = {"id": 1, "title": "विराट कोहली: injury news", "body": headline, "url": "/player/1", "tag": "p1", "event_id": None, "player_id": 1, "kind": "news", "loud": True, "quiet": False}
    assert notify.push("https://fcm.googleapis.com/fcm/send/abc", "key", "auth", payload, 600) is None
    data = sent[0]
    assert len(data.encode()) <= notify.PAYLOAD_BYTES and "\\u" not in data
    got = json.loads(data)
    assert {**got, "body": None} == {**payload, "body": None}  # only the body is cut, on a whole letter
    assert got["body"].endswith("…") and headline.startswith(got["body"][:-1]) and len(got["body"]) > 1000
    # Encrypted for a real subscription, it's under 3.5 KB (push services take 4 KB).
    receiver = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    keys = {"p256dh": base64.urlsafe_b64encode(receiver).decode(), "auth": base64.urlsafe_b64encode(os.urandom(16)).decode()}
    assert len(pywebpush.WebPusher({"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": keys}).encode(data.encode())["body"]) <= 3584
    # A short push goes as it is; a title too long on its own is cut after the body.
    assert notify._fit({"title": "Goal: Salah", "body": "Liverpool 1–0"}) == '{"title": "Goal: Salah", "body": "Liverpool 1–0"}'
    assert len(notify._fit({"title": "क" * 2000, "body": "ख" * 2000}).encode()) <= notify.PAYLOAD_BYTES


def test_a_push_too_big_for_the_service_isnt_the_devices_fault():
    device = SimpleNamespace(failures=0)
    db = SimpleNamespace(get=lambda model, key: device, delete=lambda row: None, execute=lambda statement: None)
    notify._health(db, 7, 413)
    assert device.failures == 0  # five of these deleted a healthy device
    notify._health(db, 7, 500)
    assert device.failures == 1


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


def _told(polls: list[Seen]) -> list[tuple[str, str]]:
    """What a fan hears over these polls: each moment once, by its key, as notify.record stores them."""
    stored, told = set(), []
    for before, now in zip(polls, polls[1:]):
        for f in detect("Football", "Mohamed Salah", "173896", before, now):
            if (f.kind, f.detail) not in stored:
                stored.add((f.kind, f.detail))
                told.append((f.title, f.body))
    return told


def test_a_goal_after_one_var_rules_out_is_told():
    # 23': a goal; 25': VAR rules it out, the count drops and the play leaves the events; 70': a real
    # one. Keyed by the count, the 70' goal was "goal1" again and never told.
    ruled_out, real = goal("23'", id="101"), goal("70'", id="102")
    polls = [scored(0, 0, [], goals=0, clock="20'"), scored(1, 0, [ruled_out], clock="23'"), scored(0, 0, [], goals=0, clock="25'"), scored(1, 0, [real], clock="70'")]
    assert _told(polls) == [("Goal: Mohamed Salah puts Egypt ahead", "Egypt 1 – 0 South Africa · 23'"), ("Goal: Mohamed Salah puts Egypt ahead", "Egypt 1 – 0 South Africa · 70'")]
    # A source without play ids (Sportmonks): by the goal's minute.
    polls = [scored(0, 0, [], goals=0), scored(1, 0, [goal("23'")]), scored(0, 0, [], goals=0), scored(1, 0, [goal("70'")])]
    assert [body for _, body in _told(polls)] == ["Egypt 1 – 0 South Africa · 23'", "Egypt 1 – 0 South Africa · 70'"]
    # The count dropping and coming back for the same goal (a glitch in the line) isn't a new goal.
    polls = [scored(0, 0, [], goals=0), scored(1, 0, [ruled_out]), scored(1, 0, [ruled_out], goals=0), scored(1, 0, [ruled_out])]
    assert len(_told(polls)) == 1
    # The assist on a goal VAR rules out doesn't hide the next one.
    def assisted(n: int, plays: list[dict]) -> Seen:
        return Seen("live", state=FOOTBALL, line=line("Started", Assists=str(n)), score="Egypt 1 – 0 South Africa", events=plays)

    polls = [assisted(0, []), assisted(1, [goal("23'", id="1", athlete_ids=["9", "173896"])]), assisted(0, []), assisted(1, [goal("70'", id="2", athlete_ids=["9", "173896"])])]
    assert [title for title, _ in _told(polls)] == ["Assist: Mohamed Salah", "Assist: Mohamed Salah"]


def test_priorities_rank_moments():
    def ranked(sport, before, now):
        return [(f.kind, f.priority) for f in detect(sport, "X", "1", before, now)]

    assert ranked("Cricket", Seen("live", line=line("90*", Runs="90*")), Seen("live", line=line("151*", Runs="151*"))) == [("hundred", 90), ("hundred", 95)]
    assert ranked("Cricket", Seen("live", line=line("0*", Runs="0*")), Seen("live", line=line("0", Runs="0", Out="b Starc"))) == [("out", 65)]
    assert ranked("Cricket", Seen("live", line=line("4/30", Wickets="4")), Seen("live", line=line("5/31", Wickets="5"))) == [("wickets", 90)]
    assert ranked("Basketball", Seen("live", line=line("38 PTS")), Seen("live", line=line("41 PTS"))) == [("points", 85)]
    assert ranked("Football", Seen("live", line=line("Started")), Seen("live", line=line("Started", Red="1", Yellow="1"))) == [("red", 80), ("yellow", 20)]
    assert ranked("Football", Seen("live"), Seen("final", score="Egypt 2–0 South Africa")) == [("final", 80)]
