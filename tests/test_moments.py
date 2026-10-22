"""Moments: what's worth a notification between two polls (moments.py), who hears about it, and
quiet hours and the push itself (notify.py)."""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import threading
import time
import zoneinfo
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

import pytest
import pywebpush
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from sports_follow import moments, notify, server
from sports_follow.sports import chess
from sports_follow.moments import Seen, detect

from .test_new_adapters import CARLSEN, LAZAVIK
from .test_predictions import board


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


def test_an_upset_is_a_win_over_a_much_better_seed():
    # BUG-240: any win over a lower seed number was a loud upset: [2] Rybakina beating [1] Sabalenka in a final.
    def upset(mine: int | None, theirs: int) -> bool:
        players = [{"id": "1", "name": "A", "winner": True, "seed": mine}, {"id": "2", "name": "B", "winner": False, "seed": theirs}]
        final = Seen("final", state={"kind": "tennis", "players": players, "ending": None}, line=line("Won in 2 sets", Sets="2–0"))
        return [f.kind for f in detect("Tennis", "A", "1", Seen("live"), final)] == ["final", "upset"]

    assert [upset(2, 1), upset(4, 1), upset(12, 9), upset(1, 2)] == [False] * 4
    assert [upset(5, 1), upset(20, 3), upset(None, 32)] == [True] * 3  # unseeded beating any seed is one


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


def look(cp: int, at: int, fen: str) -> dict:
    """A position's first look, as the engine's latest has it (engine.py): shown, and what the band goes by."""
    return {"cp": cp, "mate": None, "look": {"cp": cp, "mate": None, "depth": 20}, "at": at, "fen": fen}


def test_a_chess_swing_counts_once_two_evaluations_agree():
    first = chess.settle(None, look(20, 1, "8/8/8/8/8/8/8/8 w - - 0 30"))
    glitch = chess.settle(first, look(520, 2, "8/8/8/8/8/8/8/8 b - - 0 31"))
    assert (first["band"], glitch["band"]) == (0, 0)  # one reading isn't enough
    back = chess.settle(glitch, look(30, 3, "8/8/8/8/8/8/8/8 w - - 0 32"))
    assert back["band"] == 0
    real = chess.settle(chess.settle(back, look(480, 4, "8/8/8/8/8/8/8/8 w - - 0 33")), look(510, 5, "8/8/8/8/8/8/8/8 w - - 0 34"))
    assert real["band"] == 2 and real["move"] == 34 and real["since"] == 34
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
    decided = Seen("live", state={**white_winning.state, "result": "1-0"}, line=white_winning.line)  # a mini-match, live after its game
    assert detect("Chess", "Magnus Carlsen", "1503014", level, decided) == []


def test_a_swing_says_the_engine_score_as_the_board_does():
    # Halves of a tenth round away from zero either way, as the app's evaluation bar rounds them (facts/chess.ts).
    score = lambda cp, side=1: chess._score({"cp": cp, "mate": None}, side)  # noqa: E731
    assert [score(125), score(-125), score(135), score(-135), score(-4), score(0)] == ["+1.3", "-1.3", "+1.4", "-1.4", "0.0", "0.0"]
    # Past ten pawns, whole ones, as the bar shows them.
    assert (score(360, side=-1), score(-994), score(-1049), score(1250), score(5)) == ("-3.6", "-9.9", "-11", "+13", "+0.1")
    assert (chess._score({"mate": -3}, -1), chess._score({"mate": -3}, 1)) == ("mate in 3", "facing mate in 3")


def test_both_sides_of_a_chess_board_name_the_side_that_tells_its_moments():
    # A board is a fixture of each player's, so each finds its moments from its own side (BHA-378); both name the one
    # a fan who follows both hears it from: a swing's the side it went toward, the result's the winner, the start's
    # and a draw's White. The recorded Esports World Cup grand final: Carlsen won game 1 with White, game 2 was drawn.
    def seen(fide: str, snap, band: int | None = None) -> Seen:
        state = {**snap.state, **({"eval": {"cp": band * 200, "band": band, "raw": band, "told": band if abs(band) == 2 else 0, "move": 40}} if band is not None else {})}
        return Seen(snap.status, state, {"headline": snap.lines[fide].headline, "stats": snap.lines[fide].stats}, snap.score_label, snap.clock_label)

    names = {CARLSEN: "Magnus Carlsen", LAZAVIK: "Denis Lazavik"}

    def found(fide: str, before: Seen, now: Seen) -> tuple[str, str]:
        [f] = detect("Chess", names[fide], fide, before, now)
        return f.title, f.voice

    for fide in names:
        to_come, on, over = board(status="*", fide=fide), board(status="*", ongoing=True, fide=fide), board(0, fide=fide)
        told = [found(fide, seen(fide, to_come), seen(fide, on)), found(fide, seen(fide, on), seen(fide, over)), found(fide, seen(fide, on, 0), seen(fide, on, 2)),
                found(fide, seen(fide, on, 2), seen(fide, on, -2)), found(fide, seen(fide, on, -2), seen(fide, on, 0))]
        drawn = found(fide, seen(fide, board(1, status="*", ongoing=True, fide=fide)), seen(fide, board(1, fide=fide)))
        assert [voice for _, voice in [*told, drawn]] == [CARLSEN, CARLSEN, CARLSEN, LAZAVIK, CARLSEN, LAZAVIK]  # Lazavik had White in game 2
        if fide == LAZAVIK:
            assert [title for title, _ in told] == ["Denis Lazavik is playing", "Denis Lazavik: Lost with Black vs Magnus Carlsen", "Denis Lazavik is in trouble",
                                                     "Turnaround: Denis Lazavik is winning", "Denis Lazavik's advantage is gone"]
    # Each side words a swing's play the same (its band from White's side, its move), so the two are told as one.
    on = {fide: board(status="*", ongoing=True, fide=fide) for fide in names}
    [a], [b] = (detect("Chess", names[fide], fide, seen(fide, on[fide], 0), seen(fide, on[fide], 2)) for fide in names)
    assert a.detail == b.detail == "mJnv1YBUw2m40"
    # And both name the same meeting of theirs; where the board can't say which (no round), none.
    assert chess.SPORT.pairing(on[CARLSEN].state) == chess.SPORT.pairing(on[LAZAVIK].state) != ""
    assert chess.SPORT.pairing({**on[CARLSEN].state, "round": None}) == ""
    # Before the pairing's out there's no board: only its player says it.
    assert detect("Chess", "Magnus Carlsen", CARLSEN, Seen("scheduled"), Seen("live", state={"round": "Round 5"}, line=line("Round 5")))[0].voice == ""


def test_both_sides_word_a_swing_by_the_move_the_band_moved_at():
    # Carlsen's side polled after move 42's look, which moved the band; Lazavik's after move 43's, settled from the
    # board's (engine.settled), the band still the one that moved at 42. Both word the swing as the play at 42.
    on = {fide: board(status="*", ongoing=True, fide=fide) for fide in (CARLSEN, LAZAVIK)}
    level = {"cp": 20, "band": 0, "raw": 0, "told": 0, "move": 41, "since": 30, "at": 1}
    at_42 = {"cp": 340, "band": 2, "raw": 2, "told": 2, "move": 42, "look": {"cp": 340}, "since": 42, "since_look": {"cp": 340}, "at": 2}
    at_43 = {**at_42, "cp": 345, "look": {"cp": 345}, "move": 43, "at": 3}

    def swing(fide: str, name: str, ev: dict):
        line = {"headline": on[fide].lines[fide].headline, "stats": on[fide].lines[fide].stats}
        [f] = detect("Chess", name, fide, Seen("live", {**on[fide].state, "eval": level}, line), Seen("live", {**on[fide].state, "eval": ev}, line))
        return f

    carlsen, lazavik = swing(CARLSEN, "Magnus Carlsen", at_42), swing(LAZAVIK, "Denis Lazavik", at_43)
    assert carlsen.detail == lazavik.detail == "mJnv1YBUw2m42" and carlsen.voice == lazavik.voice == CARLSEN
    # And by the same score, each from its own side: move 42's first look (+3.4), not the later position's (+3.45).
    assert (carlsen.body.split(" · ")[:2], lazavik.body.split(" · ")[:2]) == (["Engine +3.4", "move 42"], ["Engine -3.4", "move 42"])


def test_winning_is_told_once_while_the_evaluation_hovers_on_the_line():
    # H4: an evaluation hovering between +2.6 and +3.4 told "Magnus Carlsen is winning" at moves 33, 37
    # and 41, each a key push. Each reading comes twice here: a band moves once two agree.
    def titles(colour: str, cps: list[int]) -> list[str]:
        ev, found = None, []
        for at, cp in enumerate(cps, 1):
            new = chess.settle(ev, look(cp, at, f"8/8/8/8/8/8/8/8 w - - 0 {30 + at}"))
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
    assert chess.settle(old, look(260, 1, "8/8/8/8/8/8/8/8 w - - 0 41"))["told"] == 0


def reading(move: int, cp: int, at: int, look: int | None = None, depth: int = 20) -> dict:
    """The engine's latest evaluation of move `move`'s position as a poll reads it: what it shows (cp, depth) and
    the position's first look (look, by default this one: a first look)."""
    return {"cp": cp, "mate": None, "depth": depth, "look": {"cp": cp if look is None else look, "mate": None, "depth": 20}, "at": at, "fen": f"8/8/8/8/8/8/8/8 w - - 0 {move}"}


def swings(looks: list[tuple[int, int]], colour: str = "White") -> list[tuple[int, str]]:
    """The swings a fan hears from a stream of the engine's readings, each (move, centipawns), as polls read them:
    a move's first look, then deeper readings while the game stays on it and someone has it open (engine.py)."""
    ev, found, first = None, [], {}
    for at, (move, cp) in enumerate(looks, 1):
        first.setdefault(move, cp)
        new = chess.settle(ev, reading(move, cp, at, look=first[move], depth=18 + at))
        if ev is not None:
            before, now = (Seen("live", state={"game_id": "g1", "eval": e}, line=line(f"{colour} vs Gukesh D", Colour=colour, Opponent="Gukesh D")) for e in (ev, new))
            found += [(move, f.title) for f in detect("Chess", "Magnus Carlsen", "1503014", before, now)]
        ev = new
    return found


def test_deeper_readings_never_move_the_band():
    # Move 32's first look agrees with 31's: winning, told at once. Deeper readings of it at +2.4, then level, are
    # shown but don't take it back on the same move (two in a row agreeing would have).
    assert swings([(30, 20), (31, 340), (32, 340), (32, 240), (32, 30), (32, 30)]) == [(32, "Magnus Carlsen is winning")]
    # Nor do they tell a swing the first looks don't: a glitched board searched on, or deeper readings agreeing with
    # the move before where the first look didn't.
    assert swings([(30, 20), (31, 520), (31, 530), (31, 540), (32, 30)]) == []
    assert swings([(30, 20), (31, 340), (32, 250), (32, 350), (32, 360)]) == []
    # So a game's alerts are its first looks', whether anyone watched it (deeper readings) or not.
    watched = [(30, 20), (30, 60), (31, 340), (31, 250), (32, 340), (32, 120), (33, 30), (33, 300)]
    unwatched = [(m, cp) for i, (m, cp) in enumerate(watched) if i == 0 or watched[i - 1][0] != m]
    assert swings(watched) == swings(unwatched) == [(32, "Magnus Carlsen is winning")]
    # What's shown is the deepest reading, beside the band its first look settled.
    told = chess.settle(None, reading(31, 20, 1))
    first = chess.settle(told, reading(32, 360, 2))
    deeper = chess.settle(first, reading(32, 900, 3, look=360, depth=30))
    assert (deeper["cp"], deeper["depth"], deeper["band"], deeper["raw"]) == (900, 30, first["band"], first["raw"])


def test_a_swing_on_a_new_move_is_told_as_fast_as_before():
    # Told at the second position whose first look agrees, deeper readings or not.
    assert swings([(30, 20), (31, 340), (32, 350)]) == [(32, "Magnus Carlsen is winning")]
    assert swings([(30, 20), (31, -340), (32, -350)], colour="Black") == [(32, "Magnus Carlsen is winning")]
    assert swings([(30, 20), (30, 25), (31, 340), (31, 345), (32, 350)]) == [(32, "Magnus Carlsen is winning")]
    # The alert quotes the position's first look, not a deeper reading shown since.
    level = chess.settle(chess.settle(None, reading(30, 20, 1)), reading(31, 360, 2))
    ahead = chess.settle(level, reading(32, 360, 3))
    shown = {**ahead, "cp": 900, "depth": 30}
    seen = lambda ev: Seen("live", state={"game_id": "g1", "eval": ev}, line=line("White vs Gukesh D", Colour="White", Opponent="Gukesh D"))  # noqa: E731
    [f] = detect("Chess", "Magnus Carlsen", "1503014", seen(level), seen(shown))
    assert f.body == "Engine +3.6 · move 32 · vs Gukesh D"


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


def _zone_file(key: str) -> bytes:
    """A zone's TZif file as this machine has it: from the system's zone database, or the tzdata package's."""
    for root in zoneinfo.TZPATH:
        if Path(root, key).is_file():
            return Path(root, key).read_bytes()
    package, _, name = f"tzdata.zoneinfo.{key}".replace("/", ".").rpartition(".")
    return resources.files(package).joinpath(name).read_bytes()


@pytest.fixture
def current_zone_names_only(tmp_path, monkeypatch):
    """A zone database with only the current names (the production image's: Debian 13's tzdata, its old names
    split off into tzdata-legacy), as PYTHONTZPATH would point at it, and no tzdata package to fall back to.
    Call it with the zones it should have."""
    for name in [m for m in sys.modules if m == "tzdata" or m.startswith("tzdata.")] + ["tzdata"]:
        monkeypatch.setitem(sys.modules, name, None)  # importing it fails, as where it isn't installed

    def install(*keys: str) -> None:
        for key in keys:
            (tmp_path / key).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / key).write_bytes(_zone_file(key))
        zoneinfo.reset_tzpath([str(tmp_path)])
        zoneinfo.ZoneInfo.clear_cache()

    yield install
    zoneinfo.reset_tzpath()
    zoneinfo.ZoneInfo.clear_cache()


@pytest.mark.parametrize("sent, stored", [
    ("Asia/Calcutta", "Asia/Kolkata"),
    ("Asia/Katmandu", "Asia/Kathmandu"),
    ("Asia/Saigon", "Asia/Ho_Chi_Minh"),
    ("Europe/Kiev", "Europe/Kyiv"),
    ("America/Buenos_Aires", "America/Argentina/Buenos_Aires"),
])
def test_a_browsers_old_zone_name_keeps_its_quiet_hours_without_old_names_installed(current_zone_names_only, sent, stored):
    # Chrome gives its zone's old name (in India "Asia/Calcutta"), which the image's zone database doesn't have:
    # the device's 22:00-07:00 ran in UTC, loud at night in India and silent in the morning (BUG-267).
    current_zone_names_only(stored, "UTC")
    with pytest.raises(zoneinfo.ZoneInfoNotFoundError):
        zoneinfo.ZoneInfo(sent)
    tz, start, end = server._device_settings({"timezone": sent, "quiet_start": "22:00", "quiet_end": "07:00"})
    assert (tz, start, end) == (stored, "22:00", "07:00")
    night = datetime(2026, 11, 14, 0, 0, tzinfo=zoneinfo.ZoneInfo(stored))
    assert notify.quiet(tz, start, end, night + timedelta(hours=1)) is True  # 01:00 there
    assert notify.quiet(tz, start, end, night + timedelta(hours=9)) is False  # 09:00 there
    assert notify.quiet(tz, start, end, night + timedelta(hours=23)) is True  # 23:00 there


def test_each_old_zone_name_stands_for_a_current_one():
    for old, current in server.OLD_ZONE_NAMES.items():
        assert current not in server.OLD_ZONE_NAMES and current != old
        assert zoneinfo.ZoneInfo(current).utcoffset(datetime(2026, 11, 14)) == zoneinfo.ZoneInfo(old).utcoffset(datetime(2026, 11, 14)), old


def test_a_batchs_line_has_its_numbers_for_the_notifications_dashboard(monkeypatch, caplog):
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    goal = SimpleNamespace(id=1, key="5:7:goal:goal1", player_id=7, event_id=5, kind="goal", level="key", priority=90, title="Goal", body="", url="/player/7", created_at=now - timedelta(seconds=3), corrects=None, about=None)
    answers = iter([[1], [goal], []])  # the claim, the claimed moments, then what corrections took back

    @contextmanager
    def session():
        yield SimpleNamespace(scalars=lambda stmt: SimpleNamespace(all=lambda: next(answers)), execute=lambda stmt: [])  # no boards (structured.games)

    devices = [(11, (1, "webpush", "https://fcm.googleapis.com/fcm/send/a", "k", "a"), {}, 60, None), (11, (2, "webpush", "https://web.push.apple.com/b", "k", "a"), {}, 60, None)]
    monkeypatch.setattr(notify, "session", session)
    monkeypatch.setattr(notify, "_plan", lambda db, items, at, taken_back: devices)
    monkeypatch.setattr(notify, "push", lambda endpoint, *rest: None if "fcm" in endpoint else 410)
    monkeypatch.setattr(notify, "_settle", lambda results: None)
    monkeypatch.setattr(notify, "_proven", lambda device_ids: device_ids)
    monkeypatch.setattr(notify.bus, "publish", lambda *args: None)
    caplog.set_level(logging.INFO, "sports_follow")
    assert notify.send([1], now) == 1
    [line] = [r for r in caplog.records if r.name == "sports_follow.notify"]
    assert (line.moments, line.pushes, line.devices, line.delivered, line.waited_ms) == (1, 1, 2, 1, 3000)


def test_a_failed_push_says_which_service_turned_it_down(monkeypatch, caplog):
    def gone(**kwargs):
        raise pywebpush.WebPushException("Push failed: 410 Gone", response=SimpleNamespace(status_code=410, text=""))

    monkeypatch.setattr(pywebpush, "webpush", gone)
    monkeypatch.setattr(notify, "signer", lambda: None)
    caplog.set_level(logging.INFO, "sports_follow")
    assert notify.push("https://web.push.apple.com/QGuOc3Z8", "key", "auth", {"title": "Goal"}, 600) == 410
    [line] = [r for r in caplog.records if r.name == "sports_follow.notify"]
    assert (line.push_service, line.status) == ("web.push.apple.com", 410)


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


def test_a_push_address_is_a_real_services_own_on_its_usual_port():
    # BUG-181: the host alone was checked, so a subscription could name a port of Google's that never answers, and
    # every push to it waited out its timeout on each of the host's addresses (BUG-089: and any subdomain minted a series).
    from sports_follow.server import _push_host_ok

    for endpoint in ("https://fcm.googleapis.com/fcm/send/abc", "https://fcm.googleapis.com:443/fcm/send/abc", "https://fcm.googleapis.com/wp/abc",
                     "https://android.googleapis.com/gcm/send/abc", "https://updates.push.services.mozilla.com/wpush/v2/gAAAAAB",
                     "https://web.push.apple.com/QGuOc3Z8", "https://wns2-par02p.notify.windows.com/w/?token=BQYAAAB"):
        assert _push_host_ok(endpoint), endpoint
    for endpoint in ("https://fcm.googleapis.com:8443/fcm/send/abc", "https://fcm.googleapis.com:80/fcm/send/abc", "https://fcm.googleapis.com:/fcm/send/abc",
                     "https://user@fcm.googleapis.com/fcm/send/abc", "https://user:secret@fcm.googleapis.com/fcm/send/abc",
                     "https://fcm.googleapis.com/v1/projects/x/messages:send", "https://fcm.googleapis.com", "https://fcm.googleapis.com./fcm/send/abc",
                     "https://updates.push.services.mozilla.com/other", "https://anything.push.apple.com/QGx", "https://a.b.notify.windows.com/w/?token=x",
                     "https://wns2-par02p.notify.windows.com/x"):
        assert not _push_host_ok(endpoint), endpoint


class Wire:
    """Push services for notify.deliver: a device whose endpoint says "hang" never answers till `release`; every
    other takes its push at once. `took` is when each took one; `settled` what was written back, batch or late."""

    def __init__(self):
        self.release, self.reached = threading.Event(), threading.Condition()
        self.took: dict[int, float] = {}
        self.settled: list[tuple[int, int, int | str | None]] = []
        self.proven: set[int] = set()
        self.senders: list[threading.Thread] = []
        self.start = time.monotonic()

    def deliver(self, device, payload, ttl, topic=None):
        if "hang" in device[2]:
            self.release.wait(30)
            return "Read timed out."
        with self.reached:
            self.took[device[0]] = time.monotonic() - self.start
            self.reached.notify_all()
        return None

    def took_by(self, device_id: int, seconds: float) -> bool:
        with self.reached:
            return self.reached.wait_for(lambda: device_id in self.took, seconds)


@pytest.fixture
def wire(monkeypatch):
    """Fresh push threads, sending to Wire's push services; the devices in `wire.proven` took their last push."""
    fake = Wire()
    pools = {"_wire": ThreadPoolExecutor(max_workers=16), "_doubt": ThreadPoolExecutor(max_workers=8)}
    for name, pool in pools.items():
        monkeypatch.setattr(notify, name, pool, raising=False)
    monkeypatch.setattr(notify, "deliver", fake.deliver)
    monkeypatch.setattr(notify, "_settle", fake.settled.extend)
    monkeypatch.setattr(notify, "_proven", lambda device_ids: fake.proven & set(device_ids), raising=False)
    monkeypatch.setattr(notify.metrics, "pushes", lambda *args: None)
    monkeypatch.setattr(notify, "DELIVER_WAIT", 1.0, raising=False)
    yield fake
    fake.release.set()
    for sender in fake.senders:  # done before the fakes go: a batch's sender writes back after its wait
        sender.join(10)
    for pool in pools.values():
        pool.shutdown(wait=True)


def _hanging(fans: range, devices: int = notify.MAX_DEVICES) -> list[tuple]:
    """Each fan's push (notification id = the fan) to that many devices that never answer, newest first, as _plan lists them."""
    return [(fan, (fan * 100 + d, "webpush", f"https://hang.example/{fan}/{d}", "k", "a"), {}, 60, "e1") for fan in fans for d in range(devices, 0, -1)]


def _sending(wire: Wire, jobs: list[tuple], now: datetime) -> tuple[threading.Thread, list[int]]:
    """_deliver on a thread of its own, as a batch's sender runs it; what it returned, once it has."""
    returned: list[int] = []
    item = SimpleNamespace(id=1, created_at=now)
    sender = threading.Thread(target=lambda: returned.append(notify._deliver(jobs, [item], now)), daemon=True)
    wire.senders.append(sender)
    sender.start()
    return sender, returned


def test_devices_that_never_answer_hold_up_no_device_that_takes_pushes(wire):
    # BUG-181, H3's repro: four anonymous fans with ten devices each that never answer follow Saka, as a real fan
    # does. Newest first, theirs went ahead of the real fan's, sixteen at a time on the push threads every batch
    # shares: the real fan's goal started after 160 s, and the next batch's waited too.
    now = datetime.now(timezone.utc)
    real = (9, (7, "webpush", "https://fcm.googleapis.com/fcm/send/real", "k", "a"), {}, 60, "e1")
    wire.proven = {7, 8}
    sender, returned = _sending(wire, [*_hanging(range(1, 5)), real], now)
    assert wire.took_by(7, 2.0)
    # Another player's goal, a batch of its own, while those devices still hang.
    other = (10, (8, "webpush", "https://web.push.apple.com/other", "k", "a"), {}, 60, "e2")
    _, also = _sending(wire, [other], now)
    assert wire.took_by(8, 2.0)
    # The batch waits only so long for the rest (DELIVER_WAIT): it says what it knows, and its sender goes on.
    sender.join(3.0)
    assert not sender.is_alive() and returned == [1] and also == [1]
    assert sorted((n, d, e) for n, d, e in wire.settled if d in (7, 8)) == [(9, 7, None), (10, 8, None)]
    # The others are written back as they end, as having failed.
    wire.release.set()
    deadline = time.monotonic() + 5
    while len(wire.settled) < 42 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert sorted(d for _, d, e in wire.settled if e is not None) == sorted(device[0] for _, device, *_ in _hanging(range(1, 5)))


def test_one_fans_many_devices_never_go_ahead_of_another_fans_first(wire):
    # Two fans' twenty devices that never answer, and a third fan's new phone (none of them has taken a push yet):
    # every fan's first device goes before any fan's second, so the phone isn't behind the twenty.
    now = datetime.now(timezone.utc)
    phone = (3, (5, "webpush", "https://fcm.googleapis.com/fcm/send/phone", "k", "a"), {}, 60, "e1")
    _sending(wire, [*_hanging(range(1, 3)), phone], now)
    assert wire.took_by(5, 2.0)


def test_a_push_service_gets_two_seconds_to_connect(monkeypatch):
    # BUG-181: ten seconds a push, on each of a host's addresses: ~80 s for a device that never answers.
    asked: list = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **kwargs: asked.append(kwargs["timeout"]))
    monkeypatch.setattr(notify, "signer", lambda: None)
    assert notify.push("https://fcm.googleapis.com/fcm/send/abc", "key", "auth", {"title": "Goal"}, 600) is None
    [(connect, read)] = asked
    assert connect <= 2 and read <= 10


FOOTBALL ={"kind": "football", "home": {"id": "1", "name": "Egypt", "score": "1"}, "away": {"id": "2", "name": "South Africa", "score": "1"}}


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
    """What a fan hears over these polls: each moment once, by its key, as notify.record stores them, with
    what was stored before to take back (notify.told); a correction only of a moment that was stored."""
    stored: list[tuple[str, str]] = []
    told = []
    for before, now in zip(polls, polls[1:]):
        for f in detect("Football", "Mohamed Salah", "173896", before, now, list(stored)):
            if (f.kind, f.detail) in stored or (f.kind == "correction" and tuple(f.detail.split(":", 1)) not in stored):
                continue
            stored.append((f.kind, f.detail))
            told.append((f.title, f.body))
    return told


RULED_OUT = ("Goal ruled out: Mohamed Salah", "Egypt 0 – 0 South Africa · 26'")


def test_a_goal_after_one_var_rules_out_is_told():
    # 23': a goal; 25': VAR rules it out, the count drops and the play leaves the events; 26': still so,
    # so it's taken back; 70': a real one. Keyed by the count, the 70' goal was "goal1" again and never told.
    ruled_out, real = goal("23'", id="101"), goal("70'", id="102")
    polls = [scored(0, 0, [], goals=0, clock="20'"), scored(1, 0, [ruled_out], clock="23'"), scored(0, 0, [], goals=0, clock="25'"), scored(0, 0, [], goals=0, clock="26'"), scored(1, 0, [real], clock="70'")]
    assert _told(polls) == [("Goal: Mohamed Salah puts Egypt ahead", "Egypt 1 – 0 South Africa · 23'"), RULED_OUT, ("Goal: Mohamed Salah puts Egypt ahead", "Egypt 1 – 0 South Africa · 70'")]
    # A source without play ids (Sportmonks): by the goal's minute.
    polls = [scored(0, 0, [], goals=0), scored(1, 0, [goal("23'")]), scored(0, 0, [], goals=0), scored(0, 0, [], goals=0), scored(1, 0, [goal("70'")])]
    assert [body for _, body in _told(polls)] == ["Egypt 1 – 0 South Africa · 23'", "Egypt 0 – 0 South Africa · 90'", "Egypt 1 – 0 South Africa · 70'"]
    # The count dropping and coming back for the same goal (a glitch in the line) isn't a new goal, and,
    # the goal still in the events, isn't one ruled out either.
    polls = [scored(0, 0, [], goals=0), scored(1, 0, [ruled_out]), scored(1, 0, [ruled_out], goals=0), scored(1, 0, [ruled_out], goals=0), scored(1, 0, [ruled_out])]
    assert len(_told(polls)) == 1
    # The assist on a goal VAR rules out doesn't hide the next one.
    def assisted(n: int, plays: list[dict]) -> Seen:
        return Seen("live", state=FOOTBALL, line=line("Started", Assists=str(n)), score="Egypt 1 – 0 South Africa", events=plays)

    polls = [assisted(0, []), assisted(1, [goal("23'", id="1", athlete_ids=["9", "173896"])]), assisted(0, []), assisted(0, []), assisted(1, [goal("70'", id="2", athlete_ids=["9", "173896"])])]
    assert [title for title, _ in _told(polls)] == ["Assist: Mohamed Salah", "Goal ruled out: no assist for Mohamed Salah", "Assist: Mohamed Salah"]


def test_a_goal_var_rules_out_is_corrected_once():
    told = [("goal", "goal@101")]
    gone, still = scored(0, 0, [], goals=0, clock="25'"), scored(0, 0, [], goals=0, clock="26'")
    [f] = detect("Football", "Mohamed Salah", "173896", gone, still, told)
    assert (f.kind, f.level, f.title, f.body, f.detail, f.priority) == ("correction", "key", *RULED_OUT, "goal:goal@101", 85)
    # Never told (scored and ruled out between two polls), or corrected already: nothing to take back.
    assert detect("Football", "Mohamed Salah", "173896", gone, still) == []
    assert detect("Football", "Mohamed Salah", "173896", gone, still, [*told, ("correction", "goal:goal@101")]) == []
    # Of two goals, the one gone from the events.
    one = scored(1, 0, [goal("23'", id="101")])
    assert kinds(detect("Football", "Mohamed Salah", "173896", one, one, [*told, ("goal", "goal@102")])) == [("correction", "goal:goal@102")]
    # Still in the events, credited to someone else: not ruled out.
    credited = scored(1, 0, [goal("23'", id="101", athlete_ids=["9"])], goals=0)
    assert detect("Football", "Mohamed Salah", "173896", credited, credited, told) == []
    # The source marks it ruled out (Sportmonks keeps it, rescinded): corrected as soon as the count goes
    # down too, without waiting for a second poll, and not again.
    marked = goal("23'", kind="DISALLOWED")
    polls = [scored(0, 0, [], goals=0, clock="20'"), scored(1, 0, [goal("23'")], clock="23'"), scored(0, 0, [marked], clock="25'"), scored(0, 0, [marked], goals=0, clock="26'"), scored(0, 0, [marked], goals=0, clock="30'")]
    assert _told(polls) == [("Goal: Mohamed Salah puts Egypt ahead", "Egypt 1 – 0 South Africa · 23'"), RULED_OUT]
    # Re-credited to a teammate (Sportmonks rescinds the event and adds one at the same minute): not ruled out.
    recredited = scored(1, 0, [goal("23'", athlete="9"), marked], goals=0)
    assert detect("Football", "Mohamed Salah", "173896", recredited, recredited, [("goal", "goal@23'")]) == []
    # Or as an own goal: its team is the other side's, the one it was scored against.
    own_goal = scored(1, 0, [goal("23'", athlete="5", team="2", own_goal=True), marked], goals=0)
    assert detect("Football", "Mohamed Salah", "173896", own_goal, own_goal, [("goal", "goal@23'")]) == []


def test_one_poll_short_takes_nothing_back():
    # One poll from an older copy of the game: the count down and the play gone, then both back. The goal
    # stands, and nobody hears it was ruled out.
    told = goal("23'", id="101")
    polls = [scored(0, 0, [], goals=0, clock="20'"), scored(1, 0, [told], clock="23'"), scored(0, 0, [], goals=0, clock="24'"), scored(1, 0, [told], clock="24'"), scored(1, 0, [told], clock="25'")]
    assert _told(polls) == [("Goal: Mohamed Salah puts Egypt ahead", "Egypt 1 – 0 South Africa · 23'")]
    # From a source without events (goals keyed by count) too; two polls short is a goal ruled out.
    polls = [scored(0, 0, [], goals=0), scored(1, 0, []), scored(0, 0, [], goals=0), scored(1, 0, [])]
    assert len(_told(polls)) == 1
    assert _told([*polls, scored(0, 0, [], goals=0, clock="30'"), scored(0, 0, [], goals=0, clock="31'")])[1:] == [("Goal ruled out: Mohamed Salah", "Egypt 0 – 0 South Africa · 31'")]
    # The count drops a poll before the play leaves the events: taken back once both say so.
    polls = [scored(0, 0, [], goals=0, clock="20'"), scored(1, 0, [told], clock="23'"), scored(0, 0, [told], goals=0, clock="25'"), scored(0, 0, [], goals=0, clock="26'")]
    assert _told(polls)[1:] == [RULED_OUT]


def test_the_goal_taken_back_is_found_by_its_play():
    # The 23' goal was told by its play (101); the 60' one by its count (goal2), its play not yet in the
    # events. VAR rules out the 23' one, and the events now hold only play 102: the 60' goal stands.
    told = [("goal", "goal@101"), ("goal", "goal2")]
    two, one = scored(2, 0, [goal("23'", id="101")], goals=2, clock="60'"), scored(1, 0, [goal("60'", id="102")], clock="62'")
    assert detect("Football", "Mohamed Salah", "173896", two, one, told) == []  # short for one poll only
    assert kinds(detect("Football", "Mohamed Salah", "173896", one, one, told)) == [("correction", "goal:goal@101")]


def test_a_play_the_source_moves_is_the_same_goal():
    # Without play ids (Sportmonks) a goal is keyed by its minute, and the minute can be revised (23' to
    # 24', 45' to 45+1' once its added time is in). The count hasn't dropped: nothing is taken back.
    polls = [scored(0, 0, [], goals=0, clock="20'"), scored(1, 0, [goal("23'")], clock="23'"), scored(1, 0, [goal("24'")], clock="25'"), scored(1, 0, [goal("24'")], clock="26'")]
    assert _told(polls) == [("Goal: Mohamed Salah puts Egypt ahead", "Egypt 1 – 0 South Africa · 23'")]

    def assisted(n: int, minute: str | None) -> Seen:
        plays = [goal(minute, athlete_ids=["9", "173896"])] if minute else []
        return Seen("live", state=FOOTBALL, line=line("Started", Assists=str(n)), score="Egypt 1 – 0 South Africa", events=plays)

    polls = [assisted(0, None), assisted(1, "45'"), assisted(1, "45+1'"), assisted(1, "45+1'")]
    assert [title for title, _ in _told(polls)] == ["Assist: Mohamed Salah"]


def test_a_goal_the_source_rescinds_and_adds_again_stands():
    # Sportmonks moves a goal by rescinding its event and adding another (23' to 24', 45' to 45+1'), the
    # count unchanged: the same goal, not one ruled out.
    told = [("Goal: Mohamed Salah puts Egypt ahead", "Egypt 1 – 0 South Africa · 23'")]
    for old, new in (("23'", "24'"), ("45'", "45+1'")):
        moved = [goal(new), goal(old, kind="DISALLOWED")]
        polls = [scored(0, 0, [], goals=0, clock="20'"), scored(1, 0, [goal(old)], clock="23'"), scored(1, 0, moved, clock="46'"), scored(1, 0, moved, clock="47'")]
        assert _told(polls)[1:] == [], old
    # Rescinded one poll and added again at the same minute the next.
    polls = [scored(0, 0, [], goals=0, clock="20'"), scored(1, 0, [goal("23'")], clock="23'"), scored(1, 0, [goal("23'", kind="DISALLOWED")], clock="24'"),
             scored(1, 0, [goal("23'"), goal("23'", kind="DISALLOWED")], clock="25'"), scored(1, 0, [goal("23'"), goal("23'", kind="DISALLOWED")], clock="26'")]
    assert _told(polls) == told


def test_a_moved_play_is_paired_with_the_nearest_goal():
    # Goals at 23' and 60' told; Sportmonks revises 60' to 61'; then VAR rules out the 23' one, the count
    # going down before its event is marked. The 61' play is the 60' goal, so the 23' one is taken back.
    polls = [scored(0, 0, [], goals=0, clock="20'"), scored(1, 0, [goal("23'")], clock="23'"), scored(2, 0, [goal("60'"), goal("23'")], goals=2, clock="60'"),
             scored(2, 0, [goal("61'"), goal("23'")], goals=2, clock="62'"), scored(1, 0, [goal("61'")], clock="63'"), scored(1, 0, [goal("61'")], clock="64'"),
             scored(1, 0, [goal("61'"), goal("23'", kind="DISALLOWED")], clock="65'")]
    assert _told(polls)[2:] == [("Goal ruled out: Mohamed Salah", "Egypt 1 – 0 South Africa · 64'")]
    stored = [("goal", "goal@23'"), ("goal", "goal@60'")]
    assert kinds(detect("Football", "Mohamed Salah", "173896", polls[4], polls[5], stored)) == [("correction", "goal:goal@23'")]


def test_a_goal_ruled_out_as_the_game_ends_is_corrected():
    # The drop first shows in the poll that says it's over, and polling stops there: no second poll to wait for.
    over = Seen("final", state=FOOTBALL, line=line("Started", Goals="0"), score="Egypt 0 – 0 South Africa", clock="FT")
    found = detect("Football", "Mohamed Salah", "173896", scored(1, 0, [goal("88'", id="101")], clock="88'"), over, [("goal", "goal@101")])
    assert kinds(found) == [("final", ""), ("correction", "goal:goal@101")]


def test_a_goal_taken_back_from_older_copies_is_told_again():
    # Two polls in a row from an older copy of the game take the goal back. When the source shows it again,
    # as his, with the count back in both polls, it's told again, once; and it can still be taken back.
    play = goal("23'", id="101")
    polls = [scored(0, 0, [], goals=0, clock="20'"), scored(1, 0, [play], clock="23'"), scored(0, 0, [], goals=0, clock="24'"), scored(0, 0, [], goals=0, clock="24'"),
             scored(1, 0, [play], clock="25'"), scored(1, 0, [play], clock="26'"), scored(1, 0, [play], clock="27'"), scored(0, 0, [], goals=0, clock="30'"), scored(0, 0, [], goals=0, clock="31'")]
    assert _told(polls) == [
        ("Goal: Mohamed Salah puts Egypt ahead", "Egypt 1 – 0 South Africa · 23'"), ("Goal ruled out: Mohamed Salah", "Egypt 0 – 0 South Africa · 24'"),
        ("Goal: Mohamed Salah puts Egypt ahead", "Egypt 1 – 0 South Africa · 23'"), ("Goal ruled out: Mohamed Salah", "Egypt 0 – 0 South Africa · 31'"),
    ]


def test_priorities_rank_moments():
    def ranked(sport, before, now):
        return [(f.kind, f.priority) for f in detect(sport, "X", "1", before, now)]

    assert ranked("Cricket", Seen("live", line=line("90*", Runs="90*")), Seen("live", line=line("151*", Runs="151*"))) == [("hundred", 90), ("hundred", 95)]
    assert ranked("Cricket", Seen("live", line=line("0*", Runs="0*")), Seen("live", line=line("0", Runs="0", Out="b Starc"))) == [("out", 65)]
    assert ranked("Cricket", Seen("live", line=line("4/30", Wickets="4")), Seen("live", line=line("5/31", Wickets="5"))) == [("wickets", 90)]
    assert ranked("Basketball", Seen("live", line=line("38 PTS")), Seen("live", line=line("41 PTS"))) == [("points", 85)]
    assert ranked("Football", Seen("live", line=line("Started")), Seen("live", line=line("Started", Red="1", Yellow="1"))) == [("red", 80), ("yellow", 20)]
    assert ranked("Football", Seen("live"), Seen("final", score="Egypt 2–0 South Africa")) == [("final", 80)]
