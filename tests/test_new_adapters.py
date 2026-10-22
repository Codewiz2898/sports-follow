"""Basketball, tennis and chess parsers against real responses recorded on 2026-09-29.

ESPN responses are in tests/fixtures/espn, Lichess ones in tests/fixtures/lichess; the tennis
scoreboards are trimmed to the matches the tests read.
"""

from __future__ import annotations

import gzip
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from sports_follow.adapters import budget, cache, espn_basketball, espn_tennis, lichess_chess
from sports_follow.adapters.base import AdapterError, PlayerRef

FIXTURES = Path(__file__).parent / "fixtures"
CLARK, FEVER = "4433403", "5"
ALCARAZ, SABALENKA = "3782", "3038"
CARLSEN, LAZAVIK = "1503014", "13515110"


def load(name: str) -> dict:
    with gzip.open(FIXTURES / f"{name}.json.gz", "rt") as f:
        return json.load(f)


# ---------------------------------------------------------------- basketball


def test_basketball_schedule_titles_phases_and_results():
    fixtures = espn_basketball.parse_schedule(load("espn/basketball-schedule"), "wnba")
    game1 = next(f for f in fixtures if f.source_id == "401918015")
    assert game1.title == "Indiana Fever at Las Vegas Aces"
    assert game1.competition == "WNBA Postseason"
    assert game1.detail == "First Round - Game 1"
    assert game1.locator == {"league": "wnba", "event": "401918015"}
    assert espn_basketball.result_label(game1, [FEVER]) == "Lost 85–102"
    game2 = next(f for f in fixtures if f.source_id == "401918018")
    assert game2.status == "scheduled"
    assert game2.team_names == ["Las Vegas Aces", "Indiana Fever"]


def test_basketball_box_score_line_and_dnp():
    snap = espn_basketball.parse_summary(load("espn/basketball-summary-final"))
    assert snap.status == "final"
    assert snap.score_label == "Indiana Fever 85 – 102 Las Vegas Aces"
    assert snap.state["away"]["periods"] == ["23", "24", "18", "20"]
    line = snap.lines[CLARK]
    assert line.headline == "15 PTS · 3 REB · 10 AST"
    assert {s["label"]: s["value"] for s in line.stats}["FG"] == "6-17"
    assert any(l.headline == "Did not play (coach's decision)" for l in snap.lines.values())


def test_basketball_season_averages():
    stats, note = espn_basketball.parse_season_stats(load("espn/basketball-athlete-stats"))
    assert {s["label"]: s["value"] for s in stats}["Points"] == "20.9"
    assert note == "2025-26 regular season averages per game"


# ---------------------------------------------------------------- tennis


def test_tennis_scoreboard_gives_a_players_matches_with_set_scores():
    fixtures = espn_tennis.parse_scoreboard(load("espn/tennis-scoreboard-usopen"), "atp", "20260903")
    assert all(not f.locator["match"].count("-") for f in fixtures)  # singles only
    qf = next(f for f in fixtures if ALCARAZ in f.team_ids and f.detail == "Quarterfinal")
    assert qf.title == "Ben Shelton vs Carlos Alcaraz"
    assert qf.competition == "US Open"
    # From Alcaraz's side, loser's tiebreak points in brackets.
    assert espn_tennis.result_label(qf, [ALCARAZ]) == "Lost 7-6(5) 1-6 3-6 6-1 6-7(7)"
    final = next(f for f in fixtures if SABALENKA in f.team_ids and f.detail == "Final")
    assert espn_tennis.result_label(final, [SABALENKA]) == "Lost 4-6 7-5 2-6"


def test_tennis_match_snapshot_final_and_live():
    data = load("espn/tennis-scoreboard-usopen")
    qf = next(f for f in espn_tennis.parse_scoreboard(data, "atp", "20260903") if ALCARAZ in f.team_ids and f.detail == "Quarterfinal")
    snap = espn_tennis.parse_match(*espn_tennis.find_match(data, qf.source_id))
    assert snap.status == "final"
    assert snap.lines[ALCARAZ].headline == "Lost in 5 sets"
    assert snap.clock_label.startswith("(8) Ben Shelton (USA) bt (2) Carlos Alcaraz")

    live = load("espn/tennis-scoreboard-live")
    event = live["events"][0]
    singles = next(g for g in event["groupings"] if g["grouping"]["slug"] == "womens-singles")["competitions"][0]
    snap = espn_tennis.parse_match(event, singles)
    assert snap.status == "live"
    assert snap.score_label == "K. Efremova 3-6 6-4 3-4 M. Kubka"
    assert snap.clock_label == "3rd Set"
    assert all(l.headline == "Level in sets" for l in snap.lines.values())
    doubles = next(g for g in event["groupings"] if g["grouping"]["slug"] == "womens-doubles")["competitions"][0]
    assert "TBD" not in espn_tennis.parse_match(event, doubles).score_label


def test_tennis_ranking_row():
    row, updated = espn_tennis.parse_ranking(load("espn/tennis-rankings"), "3623")
    assert row["current"] == 1 and row["points"] == 11500.0
    assert updated.startswith("2026-09-17")


DE_MINAUR, MOLCAN, MANNARINO, BASILASHVILI = "2651", "2635", "1266", "2498"
# ESPN's status type for a cancelled match (a Lisbon doubles match, 13 Oct 2026): "post", not completed.
CANCELED = {"id": "5", "name": "STATUS_CANCELED", "state": "post", "completed": False, "description": "Canceled", "detail": "Canceled", "shortDetail": "Canceled"}


def test_tennis_walkovers_and_retirements_say_so():
    # Shanghai, recorded 2026-10-13: de Minaur went through when Molcan withdrew (no sets), Mannarino when
    # Basilashvili retired at 6-3 2-2. They read "Won " with nothing after it and "Won in 1 sets".
    data = load("espn/tennis-scoreboard-walkover")
    fixtures = {f.source_id: f for f in espn_tennis.parse_scoreboard(data, "atp", "20261013")}
    walkover, retired = fixtures["184927"], fixtures["184881"]
    assert (walkover.status, walkover.result["ending"], retired.result["ending"]) == ("final", "walkover", "retired")
    assert [espn_tennis.result_label(walkover, [p]) for p in (DE_MINAUR, MOLCAN)] == ["Won (w/o)", "Lost (w/o)"]
    assert [espn_tennis.result_label(retired, [p]) for p in (MANNARINO, BASILASHVILI)] == ["Won 6-3 2-2 ret.", "Lost 3-6 2-2 ret."]
    assert espn_tennis.result_label(fixtures["184929"], [DE_MINAUR]) == "Won 7-6(6) 6-4"  # played out: as before

    snap = espn_tennis.parse_match(*espn_tennis.find_match(data, "184927"))
    assert (snap.status, snap.state["ending"]) == ("final", "walkover")
    assert (snap.lines[DE_MINAUR].headline, snap.lines[MOLCAN].headline) == ("Won by walkover", "Withdrew")
    assert snap.clock_label == "(9) Alex de Minaur (AUS) bt Alex Molcan (SVK) w/o"
    snap = espn_tennis.parse_match(*espn_tennis.find_match(data, "184881"))
    assert snap.state["ending"] == "retired" and snap.score_label == "A. Mannarino 6-3 2-2 N. Basilashvili"
    assert (snap.lines[MANNARINO].headline, snap.lines[BASILASHVILI].headline) == ("Won, opponent retired", "Lost, retired from the match")
    snap = espn_tennis.parse_match(*espn_tennis.find_match(data, "184950"))
    assert (snap.state["ending"], snap.lines[DE_MINAUR].headline) == (None, "Won in 2 sets")
    # One set, should a match be one: no "1 sets".
    event, comp = espn_tennis.find_match(data, "186292")
    comp["status"]["type"].update(name="STATUS_FINAL", description="Final")
    comp["competitors"][0]["linescores"] = comp["competitors"][0]["linescores"][:1]
    comp["competitors"][1]["linescores"] = comp["competitors"][1]["linescores"][:1]
    assert {l.headline for l in espn_tennis.parse_match(event, comp).lines.values()} == {"Won in 1 set", "Lost in 1 set"}


def test_a_player_retiring_mid_match_is_told_as_a_loss_not_a_retirement():
    # BUG-240: Basilashvili retiring at 3-6 2-2 was pushed as "Nikoloz Basilashvili: Retired", which reads as the end
    # of his career.
    from sports_follow import moments

    snap = espn_tennis.parse_match(*espn_tennis.find_match(load("espn/tennis-scoreboard-walkover"), "184881"))
    line = snap.lines[BASILASHVILI]
    now = moments.Seen("final", snap.state, {"headline": line.headline, "stats": line.stats}, snap.score_label, snap.clock_label)
    [final] = moments.detect("Tennis", "Nikoloz Basilashvili", BASILASHVILI, moments.Seen("live"), now)
    assert (final.kind, final.title) == ("final", "Nikoloz Basilashvili: Lost, retired from the match")


def test_a_cancelled_tennis_match_is_no_result():
    data = load("espn/tennis-scoreboard-walkover")
    event, comp = espn_tennis.find_match(data, "184954")  # de Minaur's quarterfinal, not yet played
    comp["status"]["type"] = CANCELED
    [qf] = [f for f in espn_tennis.parse_scoreboard(data, "atp", "20261013") if f.source_id == "184954"]
    assert (qf.status, qf.result) == ("postponed", None)
    snap = espn_tennis.parse_match(event, comp)
    assert (snap.status, snap.clock_label) == ("postponed", "Canceled")
    # Any match ESPN has over but not completed is no result either (an abandoned one, say).
    comp["status"]["type"] = {**CANCELED, "name": "STATUS_ABANDONED", "description": "Abandoned"}
    assert espn_tennis.parse_match(event, comp).status == "postponed"


def test_tennis_win_loss_leaves_walkovers_out(monkeypatch):
    # The tours count a walkover in no one's win–loss: de Minaur is 2–0, not 3–0, and Molcan 1–0, not
    # 1–1. A retirement counts (Mannarino's win).
    data = load("espn/tennis-scoreboard-walkover")
    monkeypatch.setattr(espn_tennis.espn, "get_json", lambda url, ttl: data if "/scoreboard" in url else {"rankings": [{"ranks": []}]})
    adapter = espn_tennis.EspnTennis()

    def win_loss(athlete_id: str) -> str:
        ref = PlayerRef("espn_tennis", athlete_id, "", [athlete_id], [""], league="atp")
        return {s["label"]: s["value"] for s in adapter.stats(ref, [])[0]}["Win–loss"]

    assert (win_loss(DE_MINAUR), win_loss(MOLCAN), win_loss(MANNARINO)) == ("2–0", "1–0", "1–0")


# ---------------------------------------------------------------- chess


def test_fide_names_resolve_with_initials_and_order():
    assert lichess_chess.pick_player(load("lichess/fide-search-carlsen"), "Carlsen")["id"] == int(CARLSEN)
    # FIDE lists Gukesh as "Gukesh D", and the search also returns Chaithanya Ramkumar Dommaraju:
    # every part of the name must match, the initial "D" counting for "Dommaraju".
    dommaraju = load("lichess/fide-search-dommaraju")
    assert any(r["name"] != "Gukesh D" for r in dommaraju)
    assert lichess_chess.pick_player(dommaraju, "Gukesh Dommaraju")["name"] == "Gukesh D"
    assert lichess_chess.pick_player(load("lichess/fide-search-gukesh"), "Gukesh Dommaraju")["name"] == "Gukesh D"
    assert lichess_chess.name_score("R Praggnanandhaa", "Praggnanandhaa, R") == 2
    assert lichess_chess.display_name("Carlsen, Magnus") == "Magnus Carlsen"


def test_chess_knockout_round_is_a_match():
    # The Esports World Cup grand final is one broadcast round holding eight Carlsen–Lazavik games.
    data = load("lichess/round-final")
    assert len(lichess_chess.games_of(data, CARLSEN)) == 8
    snap = lichess_chess.parse_board(data, CARLSEN)
    assert snap.status == "final"
    assert snap.score_label == "Magnus Carlsen 6–2 Denis Lazavik"
    assert snap.clock_label == "Magnus Carlsen won the match (8 games)"
    assert snap.state["match"] == {"games": 8, "score": "6–2", "first": "mJnv1YBU", "ahead": CARLSEN}  # its first game, and who's ahead whoever's board it is
    assert snap.state["fen"]  # the board shown is the last game's
    assert snap.lines[CARLSEN].headline == "Won the match 6–2 vs Denis Lazavik"
    assert snap.lines[LAZAVIK].headline == "Lost the match 2–6 vs Magnus Carlsen"


def test_chess_fixture_and_result_label_from_each_side():
    from datetime import datetime, timezone

    data = load("lichess/round-final")
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    mine = lichess_chess.fixture_for(data["tour"], data["round"], lichess_chess.games_of(data, CARLSEN), CARLSEN, now)
    assert mine.source_id == f"{data['round']['id']}-{CARLSEN}"
    assert mine.title == "Magnus Carlsen vs Denis Lazavik"
    assert mine.status == "final"
    assert lichess_chess.result_label(mine, [CARLSEN]) == "Won 6–2"
    theirs = lichess_chess.fixture_for(data["tour"], data["round"], lichess_chess.games_of(data, LAZAVIK), LAZAVIK, now)
    assert lichess_chess.result_label(theirs, [LAZAVIK]) == "Lost 2–6"
    assert lichess_chess.fmt_points(2.5) == "2½" and lichess_chess.fmt_points(0.5) == "½" and lichess_chess.fmt_points(0) == "0"


def test_a_chess_games_result_is_the_boards_own_white_first():
    # BUG-144: a game's result was put from the player's side, so Lazavik's loss with Black read "Lost 0–1", which in
    # chess (White's points first) says Black won. A game's result is the board's own, from either side; a match's
    # stays the player's, as colours change through it.
    data = load("lichess/round-final")
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    first, second = data["games"][0], data["games"][1]  # Carlsen won game 1 with White; game 2 was drawn
    labels = lambda games: [lichess_chess.result_label(lichess_chess.fixture_for(data["tour"], data["round"], lichess_chess.games_of({"games": games}, fide), fide, now), [fide])  # noqa: E731
                            for fide in (CARLSEN, LAZAVIK)]
    assert labels([first]) == ["Won 1–0", "Lost 1–0"]
    assert labels([{**first, "players": first["players"][::-1], "status": "0-1"}]) == ["Won 0–1", "Lost 0–1"]  # Carlsen's win with Black
    assert labels([second]) == ["Drew ½–½", "Drew ½–½"]
    assert labels([first, second]) == ["Won 1½–½", "Lost ½–1½"]  # a mini-match: the player's points first


def test_chess_match_page_is_one_players_board_with_both_players():
    data = load("lichess/round-final")
    rnd = data["round"]["id"]
    game = lichess_chess.parse_game(data, LAZAVIK, "lichess_chess")
    assert game is not None and game.source_id == f"{rnd}-{LAZAVIK}"
    # Both players, white first in the board shown, each with the fixture their own card has it under.
    assert {(p.athlete_id, p.name) for p in game.players} == {(CARLSEN, "Magnus Carlsen"), (LAZAVIK, "Denis Lazavik")}
    assert sorted(game.fixtures) == sorted(f"{rnd}-{p}" for p in (CARLSEN, LAZAVIK))
    assert game.snapshot.state["match"] == {"games": 8, "score": "2–6", "first": "mJnv1YBU", "ahead": CARLSEN} and game.snapshot.status == "final"
    assert lichess_chess.parse_game(data, "1", "lichess_chess") is None  # not paired in this round
    assert lichess_chess.LichessChess.fixture_pattern.fullmatch(f"{rnd}-{CARLSEN}") and not lichess_chess.LichessChess.fixture_pattern.fullmatch(f"{rnd}:abc")


def test_an_armageddon_drawn_is_blacks_win(monkeypatch):
    # BUG-212 (H17 arm1): Norway Chess's form, a classical draw then an armageddon in the same round, drawn with Gukesh
    # as Black. A draw in an armageddon is Black's win, but it was scored ½ each: "Drew the match 1–1", "Match drawn",
    # a draw in form, no Win poster, and Call it settled Draw.
    from sports_follow import sports

    now = datetime.now(timezone.utc)
    gukesh = {"name": "Gukesh, D", "fideId": 46616543, "rating": 2767, "title": "GM", "fed": "IND"}
    sindarov = {"name": "Sindarov, Javokhir", "fideId": 14205483, "rating": 2726, "title": "GM", "fed": "UZB"}
    rnd = {"id": "AbCdEf12", "name": "Round 3", "finished": True, "startsAt": int((now - timedelta(hours=4)).timestamp() * 1000)}
    tour = {"id": "T1", "name": "Norway Chess 2027"}
    end = "8/8/8/4k3/8/4K3/8/8 w - - 0 61"
    classical = {"id": "game0001", "name": "Gukesh, D - Sindarov, Javokhir", "players": [gukesh, sindarov], "fen": end, "status": "½-½"}
    armageddon = {"id": "game0002", "name": "Sindarov, Javokhir - Gukesh, D (Armageddon)", "players": [sindarov, gukesh], "fen": end, "status": "½-½"}
    data = {"round": rnd, "tour": tour, "games": [classical, armageddon]}
    g, s = str(gukesh["fideId"]), str(sindarov["fideId"])
    snap = lichess_chess.parse_board(data, g, now)
    assert (snap.status, snap.score_label, snap.clock_label) == ("final", "D Gukesh 1½–½ Javokhir Sindarov", "D Gukesh won on armageddon (2 games)")
    assert snap.lines[g].headline == "Won the match 1½–½ vs Javokhir Sindarov on armageddon"
    assert snap.lines[s].headline == "Lost the match ½–1½ vs D Gukesh on armageddon"
    assert snap.state["match"]["ahead"] == g and sports.get("chess").answer("result", "final", snap.state) == g  # Call it: Gukesh
    assert sports.get("chess").finished_poster({"headline": snap.lines[g].headline}, "", "") is not None  # his Win poster
    mine, theirs = (lichess_chess.fixture_for(tour, rnd, lichess_chess.games_of(data, fide), fide, now) for fide in (g, s))
    assert (lichess_chess.result_label(mine, [g]), lichess_chess.result_label(theirs, [s])) == ("Won 1½–½ on armageddon", "Lost ½–1½ on armageddon")
    # A round of its own for the armageddon, or one whose scoring gives Black more for a draw (Lichess's custom scoring).
    alone = {"round": {**rnd, "name": "Round 3 Armageddon"}, "tour": tour, "games": [{**armageddon, "name": "Sindarov, Javokhir - Gukesh, D"}]}
    one = lichess_chess.parse_board(alone, g, now)
    assert (one.clock_label, one.lines[g].headline) == ("D Gukesh won on armageddon", "Won with Black vs Javokhir Sindarov on armageddon")
    assert sports.get("chess").answer("result", "final", one.state) == g
    scored = {**alone, "round": {**rnd, "customScoring": {"white": {"win": 1, "draw": 0}, "black": {"win": 1, "draw": 1}}}}
    assert lichess_chess.parse_board(scored, s, now).lines[s].headline == "Lost with White vs D Gukesh on armageddon"
    # Its tournament's PGN (a finished round's games, as a refresh reads them) names it in the game's event.
    pgn = ('[Event "Norway Chess 2027 Armageddon"]\n[White "Sindarov, Javokhir"]\n[Black "Gukesh, D"]\n[Result "1/2-1/2"]\n[WhiteFideId "14205483"]\n'
           '[BlackFideId "46616543"]\n[GameURL "https://lichess.org/broadcast/t/r/AbCdEf12/game0002"]\n\n1. d4 Nf6 2. c4 e6 1/2-1/2\n')
    assert lichess_chess.match_score(lichess_chess.games_of({"games": lichess_chess.parse_pgn_rounds(pgn)["AbCdEf12"]}, g), g) == (1.0, 1)
    # Any other game drawn is half a point each, as before.
    assert lichess_chess.match_score(lichess_chess.games_of({**data, "games": [classical]}, g), g) == (0.5, 1)


def test_chess_single_game_round():
    data = load("lichess/round-final")
    first = data["games"][0]  # Carlsen–Lazavik 1-0, as if it were the round's only game
    single = {**data, "games": [first]}
    snap = lichess_chess.parse_board(single, CARLSEN)
    assert snap.score_label == "Magnus Carlsen 1–0 Denis Lazavik"
    assert snap.clock_label == "Magnus Carlsen won"
    assert snap.lines[CARLSEN].headline == "Won with White vs Denis Lazavik"
    assert snap.state["match"] is None


def test_a_finished_chess_games_match_page_has_its_moves(monkeypatch):
    from sports_follow import sports

    data = load("lichess/round-final")
    single = {**data, "games": [data["games"][0]]}  # Carlsen–Lazavik 1-0, as if it were the round's only game
    rnd, game_id = data["round"]["id"], data["games"][0]["id"]
    with gzip.open(FIXTURES / "lichess" / "game-final.pgn.gz", "rt") as f:
        pgn = f.read()  # that game's PGN, as Lichess exports a broadcast board (a study chapter)
    asked = []

    def get_json(url, ttl, text=False):
        asked.append(url)
        return pgn if url.endswith(".pgn") else single

    monkeypatch.setattr(lichess_chess, "get_json", get_json)
    game = lichess_chess.LichessChess().match(f"{rnd}-{LAZAVIK}")
    assert asked == [f"{lichess_chess.API}broadcast/-/-/{rnd}", f"{lichess_chess.API}study/{rnd}/{game_id}.pgn"]  # the round, then the board's moves
    assert game is not None and game.snapshot.status == "final" and not game.partial  # its round is over and its moves read: whole
    state = game.snapshot.state
    assert (len(state["moves"]), state["moves"][:2], state["moves"][-1]) == (86, [[1, "d4", "Nf6"], [2, "c4", "e6"]], [86, "Ke6", None])  # comments and side lines left out
    assert (state["opening"], state["ending"]) == ("Queen's Gambit Declined: Ragozin Defense", "resigned")
    # Lichess's analysis of it, by ply: every move's evaluation from White's side, a mistake marked with what was best.
    evals = state["evals"]
    assert (len(evals), evals["1"], evals["171"], state["evals_from"]) == (171, {"cp": 15}, {"mate": 3}, "Lichess")
    assert {k: v for k, v in evals.items() if "mark" in v} == {"160": {"mark": "??", "cp": 8115, "best": "Rf8"}, "162": {"mark": "?!", "mate": 18, "best": "Ke7"}}
    chess = sports.get("chess")
    page = chess.match_page(state, [], {}, {})
    assert (page["moves"][0], page["moves"][-1], len(page["moves"]), page["game"]) == ("1. d4 Nf6", "86. Ke6", 86, None)
    assert (page["evals"], page["evals_from"]) == (evals, "Lichess")

    # Read before Lichess analysed it: read again once the copy is ANALYSED old, and partial (read again later) while
    # it's still not there.
    bare = re.sub(r"\{[^}]*\}|\([^()]*\)", " ", pgn)  # as it is a minute after the game, before its analysis
    asked.clear()
    ttls: list[float] = []
    monkeypatch.setattr(lichess_chess, "get_json", lambda url, ttl, text=False: asked.append(url) or ttls.append(ttl) or (bare if url.endswith(".pgn") else single))
    early = lichess_chess.LichessChess().match(f"{rnd}-{LAZAVIK}")
    assert early is not None and early.partial and len(early.snapshot.state["moves"]) == 86
    assert (early.snapshot.state["evals"], early.snapshot.state["evals_from"], ttls[1:]) == ({}, None, [24 * 3600, lichess_chess.ANALYSED])
    monkeypatch.setattr(lichess_chess, "get_json", lambda url, ttl, text=False: (pgn if ttl == lichess_chess.ANALYSED else bare) if url.endswith(".pgn") else single)
    later = lichess_chess.LichessChess().match(f"{rnd}-{LAZAVIK}")
    assert later is not None and not later.partial and later.snapshot.state["evals"] == evals
    monkeypatch.setattr(lichess_chess, "get_json", get_json)
    assert chess.verdict(state, game.snapshot.score_label, game.snapshot.clock_label) == "Magnus Carlsen won"

    # A game still being played: its moves so far, from its PGN without clocks (never the finished game's copy), as
    # the page's detail, not as a finished game's moves.
    asked.clear()
    playing = {**single, "games": [{**data["games"][0], "status": "*"}], "round": {**data["round"], "finished": False, "ongoing": True}}
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: Kept())
    monkeypatch.setattr(lichess_chess, "get_json", lambda url, ttl, text=False: asked.append(url) or (pgn if ".pgn" in url else playing))
    live = lichess_chess.LichessChess().match(f"{rnd}-{LAZAVIK}").snapshot.state
    assert "moves" not in live and len(live["page_detail"]["moves"]) == 86 and asked[1].endswith(f"{game_id}.pgn?clocks=false&comments=false&variations=false")

    # A decided game while its round is still on (a mini-match's next game may follow it, BUG-210): live till GRACE
    # after it was first seen over; then over, and partial while Lichess doesn't say the round is.
    on = {**single, "round": {**data["round"], "finished": False, "ongoing": True}}
    kept = Kept()
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: kept)
    monkeypatch.setattr(lichess_chess, "get_json", lambda url, ttl, text=False: pgn if ".pgn" in url else on)
    assert lichess_chess.LichessChess().match(f"{rnd}-{LAZAVIK}").snapshot.status == "live"  # type: ignore[union-attr]
    kept[f"lichess:over:{game_id}"] = str(time.time() - lichess_chess.GRACE)
    decided = lichess_chess.LichessChess().match(f"{rnd}-{LAZAVIK}")
    assert decided.snapshot.status == "final" and decided.partial

    # Its moves not read (Lichess timed out, or answered 5xx): partial, to be read again; refused, the read fails.
    def failing(url, ttl, text=False):
        if url.endswith(".pgn"):
            raise AdapterError("Lichess HTTP 502")
        return single

    monkeypatch.setattr(lichess_chess, "get_json", failing)
    unread = lichess_chess.LichessChess().match(f"{rnd}-{LAZAVIK}")
    assert unread.partial and "moves" not in unread.snapshot.state

    def refused(url, ttl, text=False):
        if url.endswith(".pgn"):
            raise lichess_chess.RateLimited("Lichess rate limit (429)")
        return single

    monkeypatch.setattr(lichess_chess, "get_json", refused)
    with pytest.raises(lichess_chess.RateLimited):
        lichess_chess.LichessChess().match(f"{rnd}-{LAZAVIK}")


def tiebreak(*results: str, to_come: bool = False, finished: bool = False, ago: float | None = None) -> dict:
    """The recorded grand final's games as a World Championship's tiebreak round shows them (BUG-210): one at a time,
    each game's chapter made as it starts, with these results ("*" for one being played); the round on till it's
    finished. Carlsen has White in games 1 and 3, Lazavik in game 2 and 4. Finished, when Lichess says it was
    (finishedAt): `ago` seconds since, else the recording's, long since; not finished, it says no time, as Lichess."""
    data = load("lichess/round-final")
    rnd = {**{k: v for k, v in data["round"].items() if k != "finishedAt"}, "name": "Tiebreaks", "finished": finished, "ongoing": not (to_come or finished),
           "startsAt": int((time.time() + (86400 if to_come else -3600)) * 1000)}
    if finished:
        rnd["finishedAt"] = int((time.time() - ago) * 1000) if ago is not None else data["round"]["finishedAt"]
    tour = {**data["tour"], "name": "FIDE World Championship 2026", "info": {"players": "Carlsen, Lazavik"}}
    return {**data, "tour": tour, "round": rnd, "games": [{**g, "status": r} for g, r in zip(data["games"], results, strict=False)]}


def test_a_tiebreak_rounds_first_game_over_isnt_the_end_of_its_round(monkeypatch):
    # Game 1 won by Carlsen with White, game 2's chapter not out yet (BUG-210): the round may hold a mini-match, so
    # the board stays live (no "Won with White", polling goes on), and a refresh reading the round says so too.
    round_now: dict = {}

    def get_json(url, ttl, text=False):
        if ".pgn" in url:
            return LIVE_PGN  # the moves so far, for the match page
        return {"tour": round_now["tour"], "rounds": [round_now["round"]]} if url.endswith("broadcast/WorldCh1") else round_now

    monkeypatch.setattr(lichess_chess, "get_json", get_json)
    kept = Kept()
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: kept)
    chess = lichess_chess.LichessChess()
    rnd = load("lichess/round-final")["round"]["id"]
    locator = {"round": rnd, "fide": CARLSEN}
    round_now.update(tiebreak("1-0"))
    round_now["tour"]["id"] = "WorldCh1"
    snap = chess.snapshot(locator)
    assert snap.status == "live" and snap.lines[CARLSEN].headline == "White vs Denis Lazavik"  # not "Won with White"
    assert chess.snapshot({"round": rnd, "fide": LAZAVIK}).status == "live"
    assert chess.match(f"{rnd}-{CARLSEN}").snapshot.status == "live"  # type: ignore[union-attr]
    monkeypatch.setattr(lichess_chess.LichessChess, "_tours", lambda self, ref, now: [round_now["tour"]])
    [fixture] = chess.fixtures(PlayerRef("lichess_chess", CARLSEN, "Magnus Carlsen", [CARLSEN], ["Magnus Carlsen"]))
    assert fixture.status == "live" and fixture.result is None  # a refresh doesn't call it over
    # Game 2 out and won by Lazavik, game 3 too, and the round's over: the match, 1–2, once.
    round_now.update(tiebreak("1-0", "1-0", "*"))
    assert chess.snapshot(locator).status == "live"
    round_now.update(tiebreak("1-0", "1-0", "0-1", finished=True))
    done = chess.snapshot(locator)
    assert (done.status, done.lines[CARLSEN].headline, done.clock_label) == ("final", "Lost the match 1–2 vs Denis Lazavik", "Denis Lazavik won the match (3 games)")
    assert done.state["match"]["ahead"] == LAZAVIK  # who won it, whichever player's fixture is read
    # Lichess never saying the round's over and no game 2 coming, game 1 is over GRACE after it was first seen so.
    round_now.update(tiebreak("1-0"))
    assert chess.snapshot(locator).status == "live"
    kept[f"lichess:over:{round_now['games'][0]['id']}"] = str(time.time() - lichess_chess.GRACE)
    assert chess.snapshot(locator).status == "final"

    # In a round robin's round (other pairings in it, no knockout in its names), a game over is over at once, as before.
    other = {"id": "otherbrd", "status": "*", "fen": "", "players": [{"name": "Caruana, Fabiano", "fideId": 2020009}, {"name": "So, Wesley", "fideId": 5202213}]}
    robin = tiebreak("1-0")
    robin.update(round={**robin["round"], "name": "Round 5"}, tour={**robin["tour"], "name": "Tata Steel Masters 2027", "info": {"format": "14-player round robin"}},
                 games=[*robin["games"], other])
    assert lichess_chess.parse_board(robin, CARLSEN).status == "final" and not lichess_chess.may_follow(robin, robin["games"][:1])
    # A knockout's round, by its event's format or its round's name, or with a mini-match in it already: one may follow.
    for knockout in ({"tour": {**robin["tour"], "info": {"format": "8-player Knockout"}}},
                     {"round": {**robin["round"], "name": "Rapid tiebreakers"}}, {"round": {**robin["round"], "name": "Sudden death"}},
                     {"games": [*robin["games"], {**other, "id": "otherbr2"}]}):
        assert lichess_chess.may_follow({**robin, **knockout}, robin["games"][:1]), knockout
    # Not after an armageddon, which decides (R4): a round of them, each game one.
    assert not lichess_chess.may_follow({**robin, "round": {**robin["round"], "name": "Armageddon"}}, robin["games"][:1])
    # Nor in a rapid event's round, though its name says rapid.
    rapid = {**robin, "tour": {**robin["tour"], "name": "Tata Steel India Rapid 2027", "info": {"format": "10-player round robin"}}, "round": {**robin["round"], "name": "Rapid Round 3"}}
    assert not lichess_chess.may_follow(rapid, robin["games"][:1])
    # An open's boards of unrated players, with no FIDE ids, are each their own pairing, by name: not a mini-match.
    unrated = [{"id": f"open{i}", "status": "*", "fen": "", "players": [{"name": f"Player, A{i}"}, {"name": f"Player, B{i}"}]} for i in range(2)]
    assert not lichess_chess.may_follow({**robin, "games": [*robin["games"], *unrated]}, robin["games"][:1])


def test_a_world_championship_classical_game_is_over_once_decided():
    # A match's classical game (BUG-210 review): "Game 5", one board, the round its pairing's alone. Decided, it's over at
    # once, with no wait for a next game (only a tiebreak's or knockout's round has one), though Lichess hasn't said yet
    # that the round is.
    game5 = tiebreak("1-0")
    game5.update(round={**game5["round"], "name": "Game 5"}, tour={**game5["tour"], "info": {"players": "Carlsen, Lazavik", "format": "14-game match"}})
    assert game5["round"]["ongoing"] and not game5["round"]["finished"]
    assert not lichess_chess.may_follow(game5, game5["games"])
    snap = lichess_chess.parse_board(game5, CARLSEN)
    assert (snap.status, snap.lines[CARLSEN].headline) == ("final", "Won with White vs Denis Lazavik")
    assert lichess_chess.LichessChess._waited(game5, CARLSEN) is None  # nothing waits: no clock started


GUKESH, SINDAROV, NAKAMURA, FIROUZJA = "46616543", "14205483", "2016192", "12573981"
PLAYED = "8/8/8/4k3/8/4K3/8/8 b - - 0 41"  # a game's last position
UNDER_WAY = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"  # after 1. e4
NOT_STARTED = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def fide_player(name: str, fide: str, rating: int) -> dict:
    return {"name": name, "fideId": int(fide), "rating": rating, "title": "GM"}


G, S = fide_player("Gukesh, D", GUKESH, 2767), fide_player("Sindarov, Javokhir", SINDAROV, 2726)
C, L = fide_player("Carlsen, Magnus", CARLSEN, 2830), fide_player("Lazavik, Denis", LAZAVIK, 2620)
N, F = fide_player("Nakamura, Hikaru", NAKAMURA, 2800), fide_player("Firouzja, Alireza", FIROUZJA, 2760)


def board_of(game_id: str, white: dict, black: dict, status: str, moved: bool = True, name: str = "") -> dict:
    """A board in a round as Lichess's round JSON has it: its result, or "*" under way, or made ahead with no move
    yet (moved False: a mini-match's next game, an armageddon that may be needed)."""
    fen, last = (PLAYED, "e2e3") if status != "*" else (UNDER_WAY, "e2e4") if moved else (NOT_STARTED, None)
    return {"id": game_id, "name": name or f"{white['name']} - {black['name']}", "players": [white, black], "fen": fen, "lastMove": last, "status": status}


def lichess_round(round_id: str, name: str, tour: dict, games: list[dict], finished_ago: float | None = None) -> dict:
    """A broadcast round as Lichess serves it: on (ongoing) while a game in it is being played; once every game in it
    has a result (or hasn't started), finished, and when (finishedAt: `finished_ago` seconds since), till a next game's
    first moves come and it's on again (lila's RelayFetch: autoFinishNow, then resume)."""
    rnd = {"id": round_id, "name": name, "startsAt": int((time.time() - 3600) * 1000), "url": f"https://lichess.org/broadcast/-/-/{round_id}"}
    rnd.update({"ongoing": True} if finished_ago is None else {"finished": True, "finishedAt": int((time.time() - finished_ago) * 1000)})
    return {"round": rnd, "tour": tour, "games": games}


WCH = {"id": "WChT2026", "name": "FIDE World Championship 2026", "info": {"players": "Gukesh, Sindarov", "format": "14-game match"}}
WCH_TIEBREAKS = "WChTb001"


def wch_tiebreak(*results: str, finished_ago: float | None = None) -> dict:
    """The World Championship's "Tiebreaks" round, the launch's (BUG-210): Gukesh v Sindarov, Gukesh White in games 1
    and 3, each game's chapter made as it starts, with these results ("*" for one being played)."""
    games = [board_of(f"wchtb00{n + 1}", *((G, S) if n % 2 == 0 else (S, G)), result) for n, result in enumerate(results)]
    return lichess_round(WCH_TIEBREAKS, "Tiebreaks", WCH, games, finished_ago)


def as_pgn(data: dict) -> str:
    """A round's games in its tournament's PGN export, as a refresh reads a finished round's (parse_pgn_rounds)."""
    out = []
    for g in data["games"]:
        white, black = g["players"]
        result = {"½-½": "1/2-1/2"}.get(g["status"], g["status"])
        out.append(f'[Event "{data["tour"]["name"]}"]\n[White "{white["name"]}"]\n[Black "{black["name"]}"]\n[Result "{result}"]\n'
                   f'[WhiteFideId "{white["fideId"]}"]\n[BlackFideId "{black["fideId"]}"]\n[GameURL "{data["round"]["url"]}/{g["id"]}"]\n\n'
                   f'{"1. e4 e5 " if g.get("lastMove") else ""}{result}\n')
    return "\n".join(out)


def test_a_tiebreak_round_lichess_calls_finished_between_its_games_is_on_till_its_match_is_won(monkeypatch):
    # BUG-210 reopened (R4): Lichess calls a round finished as soon as every game in it has a result, and on again as
    # the next game's first moves come, so a tiebreak's round says it's finished between its games. The World
    # Championship's, as Lichess serves it: Gukesh wins game 1 with White, Sindarov games 2 and 3. Between games the
    # board is on, by its polls and a refresh alike: no "Won with White" after game 1, no "Match drawn" at 1–1. Once
    # no game has followed the last for GRACE, the match, 1–2, from either side.
    round_now: dict = {}

    def get_json(url, ttl, text=False):
        if url.endswith(".pgn"):
            return as_pgn(round_now)  # a finished round's games, for a refresh
        return {"tour": round_now["tour"], "rounds": [round_now["round"]]} if url.endswith(f"broadcast/{WCH['id']}") else round_now

    monkeypatch.setattr(lichess_chess, "get_json", get_json)
    kept = Kept()
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: kept)
    monkeypatch.setattr(lichess_chess.LichessChess, "_tours", lambda self, ref, now: [round_now["tour"]])
    chess = lichess_chess.LichessChess()

    def look(*results: str, finished_ago: float | None = None) -> list[tuple[str, str, str, str, str]]:
        """Each player's board as a poll reads it and their fixture as a refresh does: the board's status, their line
        and its clock; the fixture's status and result."""
        round_now.clear()
        round_now.update(wch_tiebreak(*results, finished_ago=finished_ago))
        out = []
        for fide, name in ((GUKESH, "Gukesh D"), (SINDAROV, "Javokhir Sindarov")):
            snap = chess.snapshot({"round": WCH_TIEBREAKS, "fide": fide})
            [fixture] = chess.fixtures(PlayerRef("lichess_chess", fide, name, [fide], [name]))
            out.append((snap.status, snap.lines[fide].headline, snap.clock_label, fixture.status, lichess_chess.result_label(fixture, [fide])))
        return out

    assert look("*") == [("live", "White vs Javokhir Sindarov", "Move 1 · Black to play", "live", ""), ("live", "Black vs D Gukesh", "Move 1 · Black to play", "live", "")]
    # Game 1 over: Lichess calls the round finished till game 2's first moves.
    assert [(status, line, refreshed) for status, line, _, refreshed, _ in look("1-0", finished_ago=0)] == [("live", "White vs Javokhir Sindarov", "live"), ("live", "Black vs D Gukesh", "live")]
    assert {s[0] for s in look("1-0", "*")} == {"live"}  # game 2 under way: on again
    assert look("1-0", "1-0", finished_ago=0) == [("live", "Black vs Javokhir Sindarov", "Game 2 done", "live", ""), ("live", "White vs D Gukesh", "Game 2 done", "live", "")]
    assert {s[0] for s in look("1-0", "1-0", "*")} == {"live"}
    assert {(s[0], s[3]) for s in look("1-0", "1-0", "0-1", finished_ago=0)} == {("live", "live")}  # game 3 over: a game 4 may follow
    # No game 4, GRACE on: the match is over, as its polls read it; a refresh says so once its copies' age is past too.
    over = [("final", "Lost the match 1–2 vs Javokhir Sindarov", "Javokhir Sindarov won the match (3 games)"),
            ("final", "Won the match 2–1 vs D Gukesh", "Javokhir Sindarov won the match (3 games)")]
    assert [(s[:3], s[3]) for s in look("1-0", "1-0", "0-1", finished_ago=lichess_chess.GRACE)] == [(over[0], "live"), (over[1], "live")]
    assert look("1-0", "1-0", "0-1", finished_ago=lichess_chess.GRACE + lichess_chess.ROUNDS_TTL) == [(*over[0], "final", "Lost 1–2"), (*over[1], "final", "Won 2–1")]


def test_a_refresh_calls_a_tiebreak_over_no_sooner_than_its_polls_and_from_its_games_as_they_ended(monkeypatch):
    # A refresh's copies of a tournament's rounds and PGN may be older than a poll's round (kept ten minutes; the PGN a
    # day once every round is finished), and a refresh calling a board over that its polls have live closes it with
    # nothing told (structured.finish). The World Championship's tiebreak is its event's last round: game 3 over,
    # Lichess calls every round finished, and the PGN copy at hand is from while game 3 was played. The refresh has
    # the board live, as its polls do, and the PGN isn't kept a day while the round's last games may be missing from
    # it. Its copies past their age and GRACE on, it's over: 1–2, not the "Drew 1–1" of the old copy.
    copy = {"pgn": wch_tiebreak("1-0", "1-0", "*")}  # the PGN as last read: game 3 being played
    round_now = wch_tiebreak("1-0", "1-0", "0-1", finished_ago=0)
    asked: list[tuple[str, float]] = []

    def get_json(url, ttl, text=False):
        asked.append((url, ttl))
        if url.endswith(".pgn"):
            return as_pgn(copy["pgn"])
        return {"tour": WCH, "rounds": [round_now["round"]]} if url.endswith(f"broadcast/{WCH['id']}") else round_now

    monkeypatch.setattr(lichess_chess, "get_json", get_json)
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: Kept())
    monkeypatch.setattr(lichess_chess.LichessChess, "_tours", lambda self, ref, now: [WCH])
    chess = lichess_chess.LichessChess()
    gukesh = PlayerRef("lichess_chess", GUKESH, "Gukesh D", [GUKESH], ["Gukesh D"])
    [fixture] = chess.fixtures(gukesh)
    assert (fixture.status, fixture.result) == ("live", None)
    assert [ttl for url, ttl in asked if url.endswith(".pgn")] == [lichess_chess.ROUNDS_TTL]  # not a day's copy
    copy["pgn"] = round_now
    for ago, status, result in ((lichess_chess.GRACE, "live", ""), (lichess_chess.GRACE + lichess_chess.ROUNDS_TTL, "final", "Lost 1–2")):
        round_now.update(wch_tiebreak("1-0", "1-0", "0-1", finished_ago=ago))
        [fixture] = chess.fixtures(gukesh)
        assert (fixture.status, lichess_chess.result_label(fixture, [GUKESH])) == (status, result), ago
    # The event over: a copy is taken if it was read after its last round ended, up to a day old.
    url, ttl = asked[-1]
    assert url == f"{lichess_chess.API}broadcast/{WCH['id']}.pgn" and abs(ttl - lichess_chess.GRACE) < 5  # ended GRACE + ROUNDS_TTL ago
    round_now.update(wch_tiebreak("1-0", "1-0", "0-1", finished_ago=2 * 24 * 3600))
    chess.fixtures(gukesh)
    assert asked[-1] == (url, 24 * 3600)


def test_a_refresh_long_after_a_tiebreak_ended_doesnt_take_a_pgn_copy_from_before_its_last_game(monkeypatch):
    # The reviewer's case, through the source cache itself: the World Championship's tiebreak, its event's last round. A
    # refresh read the event's PGN five minutes before game 3 ended, game 3 still being played; the next comes 45
    # minutes after the round ended (a tick skipped, a 429, a restart). Every round over long enough, a day-old copy
    # would do, and that one said "Drew 1–1", final, kept a day. A copy is taken only if it was read after the round
    # ended (and its rounds' copy, ROUNDS_TTL old at most, could say so): this one is read again, and the match is 1–2.
    clock = {"now": time.time()}
    monkeypatch.setattr(cache, "time", SimpleNamespace(time=lambda: clock["now"]))
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: Kept())
    monkeypatch.setattr(lichess_chess.LichessChess, "_tours", lambda self, ref, now: [WCH])
    pgn_url = f"{lichess_chess.API}broadcast/{WCH['id']}.pgn"
    ended = 45 * 60
    answers = {pgn_url: httpx.Response(200, text=as_pgn(wch_tiebreak("1-0", "1-0", "*"))),
               f"{lichess_chess.API}broadcast/{WCH['id']}": httpx.Response(200, json={"tour": WCH, "rounds": [wch_tiebreak("1-0", "1-0", "0-1", finished_ago=ended)["round"]]})}
    lichess_answering(monkeypatch, answers)
    clock["now"] -= ended + 5 * 60
    lichess_chess.get_json(pgn_url, ttl=lichess_chess.ROUNDS_TTL, text=True)  # the copy a refresh read, game 3 on
    clock["now"] += ended + 5 * 60
    answers[pgn_url] = httpx.Response(200, text=as_pgn(wch_tiebreak("1-0", "1-0", "0-1")))
    [fixture] = lichess_chess.LichessChess().fixtures(PlayerRef("lichess_chess", GUKESH, "Gukesh D", [GUKESH], ["Gukesh D"]))
    assert (fixture.status, lichess_chess.result_label(fixture, [GUKESH])) == ("final", "Lost 1–2")


def test_the_recorded_grand_finals_board_between_its_sets_isnt_won(monkeypatch):
    # BUG-210 reopened (R4): the Esports World Cup's grand final, sets of 4, 4 and 2 games in one round. After set 1
    # (Carlsen 3–1) every game in the round had a result, so Lichess called the round finished till set 2's first move,
    # and the board read "Magnus Carlsen won the match (4 games)"; he won it 6–2. It's on between sets, by a poll and a
    # refresh alike; the whole final, finished long since, is over, and nothing waits on it.
    data = load("lichess/round-final")
    now = datetime.now(timezone.utc)
    kept = Kept()
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: kept)
    set1 = {**data, "round": {**data["round"], "finishedAt": int(now.timestamp() * 1000)}, "games": data["games"][:4]}
    for fide in (CARLSEN, LAZAVIK):
        mine = lichess_chess.games_of(set1, fide)
        snap = lichess_chess.parse_board(set1, fide, now, lichess_chess.LichessChess._waited(set1, fide))
        fixture = lichess_chess.fixture_for(set1["tour"], set1["round"], mine, fide, now, lichess_chess.may_follow(set1, mine), lichess_chess.LichessChess._waited(set1, fide))
        assert (snap.status, snap.clock_label, fixture.status, fixture.result) == ("live", "Game 4 done", "live", None)
    on = {**data, "round": {**{k: v for k, v in data["round"].items() if k not in ("finished", "finishedAt")}, "ongoing": True},
          "games": [*data["games"][:4], {**data["games"][4], "status": "*"}]}
    assert lichess_chess.parse_board(on, CARLSEN, now).status == "live"  # set 2 under way
    kept.clear()
    whole = lichess_chess.parse_board(data, CARLSEN, now, lichess_chess.LichessChess._waited(data, CARLSEN))
    assert (whole.status, whole.score_label, whole.clock_label) == ("final", "Magnus Carlsen 6–2 Denis Lazavik", "Magnus Carlsen won the match (8 games)")
    assert kept == {}  # no clock started for a round Lichess says ended long since


def test_a_mini_match_won_while_its_round_plays_on_is_over_grace_after_its_last_game(monkeypatch):
    # BUG-274 (R4): a knockout's quarterfinal round of two pairings. Carlsen beats Lazavik 2–0 while Nakamura and
    # Firouzja play on: his board stayed live ("Game 2 done") till the whole round ended, hours on, holding its result.
    # A mini-match is over GRACE after its last game with no next one, as one game is; the board being played is live.
    tour = {"id": "EwcPo026", "name": "Esports World Cup 2026 | Playoffs", "info": {"format": "8-player Knockout"}}
    round_now = lichess_round("EwcQf001", "Quarterfinals", tour, [board_of("qf000001", C, L, "1-0"), board_of("qf000002", L, C, "0-1"), board_of("qf000003", N, F, "*")])
    monkeypatch.setattr(lichess_chess, "get_json", lambda url, ttl, text=False: round_now)
    kept = Kept()
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: kept)
    chess = lichess_chess.LichessChess()
    snap = chess.snapshot({"round": "EwcQf001", "fide": CARLSEN})
    assert (snap.status, snap.clock_label) == ("live", "Game 2 done")  # a game 3 may follow
    kept["lichess:over:qf000002"] = str(time.time() - lichess_chess.GRACE)  # none has, GRACE on
    snap = chess.snapshot({"round": "EwcQf001", "fide": LAZAVIK})
    assert (snap.status, snap.clock_label, snap.lines[LAZAVIK].headline) == ("final", "Magnus Carlsen won the match (2 games)", "Lost the match 0–2 vs Magnus Carlsen")
    assert chess.snapshot({"round": "EwcQf001", "fide": NAKAMURA}).status == "live"

    # A next game's board made ahead, no move yet, is no game under way, though Lichess then says the round's finished
    # (each game in it has a result or hasn't started): Nakamura and Firouzja at 1–1, their armageddon's board out. On
    # till it starts and while it's played; decided, over at once (an armageddon decides).
    armageddon = board_of("qf000006", F, N, "*", moved=False, name="Firouzja, Alireza - Nakamura, Hikaru (Armageddon)")
    pair = [board_of("qf000003", N, F, "1-0"), board_of("qf000004", F, N, "1-0")]
    round_now.update(lichess_round("EwcQf001", "Quarterfinals", tour, [*round_now["games"][:2], *pair, armageddon], finished_ago=0))
    assert chess.snapshot({"round": "EwcQf001", "fide": NAKAMURA}).status == "live"
    round_now.update(lichess_round("EwcQf001", "Quarterfinals", tour, [*round_now["games"][:4], {**armageddon, "status": "½-½", "fen": PLAYED, "lastMove": "e2e3"}], finished_ago=0))
    snap = chess.snapshot({"round": "EwcQf001", "fide": NAKAMURA})
    assert (snap.status, snap.clock_label) == ("final", "Hikaru Nakamura won on armageddon (3 games)")
    # And one made ahead that wasn't needed: Nakamura 2–0, GRACE on, over, its board the last game played.
    round_now.update(lichess_round("EwcQf001", "Quarterfinals", tour, [*round_now["games"][:2], pair[0], {**pair[1], "status": "0-1"}, armageddon], finished_ago=lichess_chess.GRACE))
    snap = chess.snapshot({"round": "EwcQf001", "fide": NAKAMURA})
    assert (snap.status, snap.clock_label, snap.state["game_id"]) == ("final", "Hikaru Nakamura won the match (2 games)", "qf000004")


def test_a_round_robins_decided_game_isnt_held_for_an_armageddon_that_wont_follow():
    # R4's cross-talk on BUG-274: Norway Chess, a round robin whose drawn games go to an armageddon in the same round,
    # says so in its format, which was taken for a knockout's: a decisive classical game was held live GRACE (30
    # minutes) for an armageddon that never follows a win. It's over at once; a draw waits for its armageddon, and the
    # armageddon, which decides, is over at once.
    tour = {"id": "NorChs27", "name": "Norway Chess 2027", "info": {"format": "6-player double round robin, Armageddon after a draw"}}
    classical = [board_of("nc000001", G, S, "1-0"), board_of("nc000002", C, N, "½-½"), board_of("nc000003", L, F, "*")]  # the third still on
    data = lichess_round("NorRnd03", "Round 3", tour, classical)
    won = lichess_chess.parse_board(data, GUKESH)
    assert (won.status, won.lines[GUKESH].headline) == ("final", "Won with White vs Javokhir Sindarov")
    assert not lichess_chess.may_follow(data, lichess_chess.games_of(data, GUKESH))
    assert lichess_chess.parse_board(data, CARLSEN).status == "live" and lichess_chess.may_follow(data, lichess_chess.games_of(data, CARLSEN))
    armageddon = board_of("nc000004", N, C, "*", name="Nakamura, Hikaru - Carlsen, Magnus (Armageddon)")
    assert lichess_chess.parse_board(lichess_round("NorRnd03", "Round 3", tour, [*classical, armageddon]), CARLSEN).status == "live"
    decided = lichess_round("NorRnd03", "Round 3", tour, [*classical, {**armageddon, "status": "½-½", "fen": PLAYED}])
    snap = lichess_chess.parse_board(decided, CARLSEN)
    assert (snap.status, snap.clock_label) == ("final", "Magnus Carlsen won on armageddon (2 games)")  # a draw, Black's win


def test_a_swiss_game_and_a_matchs_classical_game_are_over_once_decided_whatever_the_round_says():
    # What round one got right holds with Lichess calling a round finished once every game in it has a result: a Swiss
    # open's game is over as soon as it's decided, the round on or not; so is a World Championship's classical game
    # ("Game 5", its pairing's alone).
    tour = {"id": "SevOpn26", "name": "Sevilla Open 2026", "info": {"format": "9-round Swiss"}}
    swiss = lichess_round("SevRnd05", "Round 5", tour, [board_of("sv000001", G, S, "0-1"), board_of("sv000002", C, N, "*"), board_of("sv000003", L, F, "1-0")])
    for data in (swiss, {**swiss, "games": swiss["games"][::2], "round": lichess_round("SevRnd05", "Round 5", tour, [], finished_ago=0)["round"]}):
        snap = lichess_chess.parse_board(data, SINDAROV)
        assert (snap.status, snap.lines[SINDAROV].headline) == ("final", "Won with Black vs D Gukesh")
        assert lichess_chess.LichessChess._waited(data, SINDAROV) is None
    game5 = lichess_round("WChG0005", "Game 5", WCH, [board_of("wchg0005", G, S, "½-½")], finished_ago=0)
    snap = lichess_chess.parse_board(game5, GUKESH)
    assert (snap.status, snap.lines[GUKESH].headline) == ("final", "Drew with White vs Javokhir Sindarov")


def test_a_positions_moves_count_from_its_fen_and_a_games_from_its_last_move():
    assert lichess_chess.fen_ply("r4rk1/5q1p/p5p1/npN1Bn2/2pP3b/P3N3/1P3PQ1/3R1RK1 b - - 4 25") == 49  # White's 25th played, Black's 24th
    assert lichess_chess.fen_ply("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1") == 0
    assert lichess_chess.fen_ply("8/8/8/8/8/8/8/8 w") is None and lichess_chess.fen_ply(None) is None  # no move number: can't say
    assert lichess_chess.moves_ply([[1, "d4", "Nf6"], [2, "c4", None]]) == 3
    assert lichess_chess.moves_ply([[12, None, "e5"]]) == 24  # a game set up with Black to move counts as its FEN does
    assert lichess_chess.moves_ply([]) == 0


def test_a_live_boards_kept_moves_take_its_last_move_when_one_short():
    kept = {"moves": [[1, "d4", "Nf6"], [2, "c4", "e6"]], "more": [], "opening": "Indian", "fen": None}
    at = "rnbqkb1r/pppp1ppp/4pn2/8/2PP4/8/PP2PPPP/RNBQKBNR w KQkq - 0 3"  # after 2... e6: 4 plies
    reached = lichess_chess.moves_to(kept, at, "e7e6")
    assert reached == {**kept, "fen": at} and lichess_chess.moves_to(reached, at, "e7e6") is reached  # the position it reached
    one_on = "rnbqkb1r/pppp1ppp/4pn2/8/2PP4/5N2/PP2PPPP/RNBQKB1R b KQkq - 1 3"
    assert lichess_chess.moves_to(reached, one_on, "g1f3") == {**kept, "more": ["g1f3"], "fen": one_on}
    assert lichess_chess.moves_to({**kept, "more": ["g1f3"]}, "rnbqkb1r/ppp2ppp/4pn2/3p4/2PP4/5N2/PP2PPPP/RNBQKB1R w KQkq - 0 4", "d7d5")["more"] == ["g1f3", "d7d5"]
    two_on = "rnbqkb1r/ppp2ppp/4pn2/3p4/2PP4/5N2/PP2PPPP/RNBQKB1R w KQkq - 0 4"
    assert lichess_chess.moves_to(kept, two_on, "d7d5") is None  # a move missed: its PGN is read again
    # A relay correcting the last move keeps the count: the position it was reached at says so.
    corrected = "rnbqkb1r/pppp1ppp/4pn2/8/2PP4/2N5/PP2PPPP/R1BQKBNR b KQkq - 1 3"  # 3. Nc3, not 3. Nf3
    assert lichess_chess.moves_to({**kept, "more": ["g1f3"], "fen": one_on}, corrected, "b1c3") is None
    # Ahead of the board (a look at an older copy of the round than the one they were kept up by): as they are.
    behind = "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1"
    assert lichess_chess.moves_to(reached, behind, "d2d4") is reached
    assert lichess_chess.moves_to(None, at, "e7e6") is None  # none kept yet


# A Two Knights (3. Bc4 Nf6 4. Ng5 d5) that a relay first shows as a Ruy Lopez (3. Bb5), and a Ruy Lopez (3. Bb5 a6
# 4. Ba4 Nf6) whose 4. Ba4 was really 4. Bxc6.
OPENING = [[1, "e4", "e5"], [2, "Nf3", "Nc6"]]
AFTER_NC6 = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
AFTER_BB5 = "r1bqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
AFTER_BC4 = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
AFTER_NF6 = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
AFTER_NG5 = "r1bqkb1r/pppp1ppp/2n2n2/4p1N1/2B1P3/8/PPPP1PPP/RNBQK2R b KQkq - 5 4"
AFTER_D5 = "r1bqkb1r/ppp2ppp/2n2n2/3pp1N1/2B1P3/8/PPPP1PPP/RNBQK2R w KQkq - 0 5"
AFTER_A6 = "r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4"
AFTER_BXC6 = "r1bqkbnr/1ppp1ppp/p1B5/4p3/4P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 0 4"


def test_kept_moves_a_relay_corrects_or_takes_back_are_read_again_never_added_to():
    # BUG-273: the moves kept reached 3. Bb5, which the relay then corrects to 3. Bc4.
    kept = lichess_chess.moves_to({"moves": OPENING, "more": [], "opening": None, "fen": None}, AFTER_NC6, "b8c6")
    assert kept is not None
    wrong = lichess_chess.moves_to(kept, AFTER_BB5, "f1b5")
    assert wrong is not None and wrong["more"] == ["f1b5"]
    assert lichess_chess.moves_to(wrong, AFTER_BC4, "f1c4") is None  # as many moves, another position
    # Corrected and answered (3... Nf6) between two looks: one move on, but not from where the moves kept are.
    assert lichess_chess.moves_to(wrong, AFTER_NF6, "g8f6") is None
    right = lichess_chess.moves_to(kept, AFTER_BC4, "f1c4")
    assert right is not None
    assert (lichess_chess.moves_to(right, AFTER_NF6, "g8f6") or {}).get("more") == ["f1c4", "g8f6"]
    # Ahead of the board (a look at an older copy of the round): as they are while the board's last move is theirs at
    # its place, by the square it went to where the PGN wrote it ("a6"); not when it was taken back or corrected (R3).
    ruy = {"moves": [*OPENING, [3, "Bb5", "a6"]], "more": ["b5a4", "g8f6"], "opening": None, "fen": None}
    assert lichess_chess.moves_to(ruy, AFTER_A6, "a7a6") is ruy
    assert lichess_chess.moves_to(ruy, AFTER_BXC6, "b5c6") is None  # their 7th is 4. Ba4
    assert lichess_chess.moves_to(ruy, AFTER_BC4, "f1c4") is None  # their 5th is 3. Bb5


def test_a_board_one_move_on_is_told_by_the_squares_its_move_touches():
    assert lichess_chess.one_move_on(AFTER_BC4, AFTER_NF6, "g8f6")
    assert not lichess_chess.one_move_on(AFTER_BB5, AFTER_NF6, "g8f6")  # the bishop is on c4, not b5
    assert not lichess_chess.one_move_on(AFTER_NC6, AFTER_NF6, "g8f6")  # a move between
    castled = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQ1RK1 b kq - 5 4"
    assert lichess_chess.one_move_on(AFTER_NF6, castled, "e1g1") and lichess_chess.one_move_on(AFTER_NF6, castled, "e1h1")  # the rook moves too
    before, taken = "rnbqkb1r/ppp1pppp/5n2/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3", "rnbqkb1r/ppp1pppp/3P1n2/8/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 3"
    assert lichess_chess.one_move_on(before, taken, "e5d6")  # en passant: the pawn taken is beside it
    assert lichess_chess.one_move_on(None, AFTER_NF6, "g8f6") and lichess_chess.one_move_on("g1f3", AFTER_NF6, "g8f6")  # nothing to tell by


LIVE_PGN ="""[Event "Made-up"]
[White "Carlsen, Magnus"]
[Black "Lazavik, Denis"]
[Opening "Queen's Gambit Declined"]

1. d4 {[%clk 1:59:58]} Nf6 2. c4 e6 3. Nf3 d5 *"""
LIVE_PGN_LATER = LIVE_PGN.replace(" *", " 4. Nc3 Be7 5. Bg5 *")


class Kept(dict):
    """Redis as a dict, for what the worker and the API keep between looks."""

    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value, ex=None):
        self[key] = value

    def delete(self, key):
        self.pop(key, None)


def test_a_live_boards_moves_are_read_once_then_kept_up_a_move_at_a_time(monkeypatch):
    data = load("lichess/round-final")
    rnd = data["round"]["id"]
    board = {"fen": "rnbqkb1r/ppp2ppp/4pn2/3p4/2PP4/5N2/PP2PPPP/RNBQKB1R w KQkq - 0 4", "lastMove": "d7d5", "pgn": LIVE_PGN}  # after 3... d5
    asked = []

    def get_json(url, ttl, text=False):
        asked.append(url)
        if ".pgn" in url:
            return board["pgn"]
        game = {**data["games"][0], "status": "*", "fen": board["fen"], "lastMove": board["lastMove"]}
        return {**data, "round": {**data["round"], "finished": False, "ongoing": True}, "games": [game]}

    kept = Kept()
    monkeypatch.setattr(lichess_chess, "get_json", get_json)
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: kept)
    chess = lichess_chess.LichessChess()
    pgns = lambda: [u for u in asked if ".pgn" in u]  # noqa: E731

    # A followed player's poll before anyone opened the page keeps nothing and reads no PGN.
    assert chess.snapshot({"round": rnd, "fide": LAZAVIK}).status == "live"
    assert not pgns() and not kept
    # The page's first read reads the PGN once, without clocks (not the finished game's copy), and keeps it.
    game = chess.match(f"{rnd}-{LAZAVIK}")
    assert game is not None and game.snapshot.status == "live"
    game_id = data["games"][0]["id"]
    assert game.snapshot.state["page_detail"] == {"game_id": game_id, "moves": [[1, "d4", "Nf6"], [2, "c4", "e6"], [3, "Nf3", "d5"]], "more": [], "opening": "Queen's Gambit Declined"}
    assert pgns() == [f"{lichess_chess.API}study/{rnd}/{game_id}.pgn?clocks=false&comments=false&variations=false"]
    # 4. Nc3: a poll adds it as the board gives it, and the next read takes it from there, neither asking Lichess.
    board.update(fen="rnbqkb1r/ppp2ppp/4pn2/3p4/2PP4/2N2N2/PP2PPPP/R1BQKB1R b KQkq - 1 4", lastMove="b1c3")
    asked.clear()
    chess.snapshot({"round": rnd, "fide": LAZAVIK})
    game = chess.match(f"{rnd}-{LAZAVIK}")
    assert game is not None and game.snapshot.state["page_detail"]["more"] == ["b1c3"] and not pgns()
    # 4... Be7 5. Bg5 between two looks: the read reads the PGN again, and its moves stand alone.
    board.update(fen="rnbqk2r/ppp1bppp/4pn2/3p2B1/2PP4/2N2N2/PP2PPPP/R2QKB1R b KQkq - 3 5", lastMove="c1g5", pgn=LIVE_PGN_LATER)
    game = chess.match(f"{rnd}-{LAZAVIK}")
    assert game is not None and len(pgns()) == 1
    assert game.snapshot.state["page_detail"]["moves"][-2:] == [[4, "Nc3", "Be7"], [5, "Bg5", None]] and game.snapshot.state["page_detail"]["more"] == []


def test_a_game_kept_from_its_first_move_shows_the_moves_added_since(monkeypatch):
    # A page opened before the first move keeps an empty PGN; the moves each look adds are the list then.
    data = load("lichess/round-final")
    rnd = data["round"]["id"]
    board = {"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "lastMove": None}
    asked = []

    def get_json(url, ttl, text=False):
        asked.append(url)
        if ".pgn" in url:
            return '[Event "Made-up"]\n[White "Carlsen, Magnus"]\n[Black "Lazavik, Denis"]\n\n*'
        game = {**data["games"][0], "status": "*", "fen": board["fen"], "lastMove": board["lastMove"]}
        return {**data, "round": {**data["round"], "finished": False, "ongoing": True}, "games": [game]}

    monkeypatch.setattr(lichess_chess, "get_json", get_json)
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda kept=Kept(): kept)  # noqa: B008  one store for every look
    chess = lichess_chess.LichessChess()
    game = chess.match(f"{rnd}-{LAZAVIK}")
    assert game is not None and "page_detail" not in game.snapshot.state  # nothing to list yet
    for fen, last in (("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1", "e2e4"),
                      ("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", "e7e5"),
                      ("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2", "g1f3")):
        board.update(fen=fen, lastMove=last)
        chess.snapshot({"round": rnd, "fide": LAZAVIK})
    game = chess.match(f"{rnd}-{LAZAVIK}")
    assert game is not None and game.snapshot.state["page_detail"]["more"] == ["e2e4", "e7e5", "g1f3"]
    assert len([u for u in asked if ".pgn" in u]) == 1  # the one PGN, at the start
    from sports_follow import sports

    page = sports.get("chess").match_page(game.snapshot.state, [], {}, {})
    assert (page["moves"], page["more"]) == ([], ["e2e4", "e7e5", "g1f3"])


def test_a_live_boards_moves_lichess_wont_give_leave_the_board_alone(monkeypatch):
    data = load("lichess/round-final")
    rnd = data["round"]["id"]

    def get_json(url, ttl, text=False):
        if ".pgn" in url:
            raise lichess_chess.RateLimited("the minute is spent")
        game = {**data["games"][0], "status": "*", "fen": "rnbqkb1r/ppp2ppp/4pn2/3p4/2PP4/5N2/PP2PPPP/RNBQKB1R w KQkq - 0 4", "lastMove": "d7d5"}
        return {**data, "round": {**data["round"], "finished": False, "ongoing": True}, "games": [game]}

    monkeypatch.setattr(lichess_chess, "get_json", get_json)
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: Kept())
    game = lichess_chess.LichessChess().match(f"{rnd}-{LAZAVIK}")
    assert game is not None and game.snapshot.status == "live" and "page_detail" not in game.snapshot.state  # the board still shows


def test_a_move_a_relay_corrects_isnt_kept_for_the_rest_of_the_game(monkeypatch):
    # BUG-273 (R4): a page read at 2... Nc6; the worker's polls see 3. Bb5, its correction to 3. Bc4, then 3... Nf6
    # 4. Ng5 d5. The page's next read has the game as it was played, read again, not 3. Bb5 and the moves after it.
    pgn = {"text": '[Event "x"]\n\n1. e4 e5 2. Nf3 Nc6 *'}
    asked = []

    def get_json(url, ttl, text=False):
        asked.append(url)
        return pgn["text"]

    kept = Kept()
    monkeypatch.setattr(lichess_chess, "get_json", get_json)
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: kept)

    def look(fen, last, read=False):
        return lichess_chess.LichessChess._moves_so_far("r4round1", {"game_id": "g1", "fen": fen, "last_move": last}, read=read)

    first = look(AFTER_NC6, "b8c6", read=True)
    assert first is not None and first["moves"] == OPENING and len(asked) == 1
    for fen, last in ((AFTER_BB5, "f1b5"), (AFTER_BC4, "f1c4"), (AFTER_NF6, "g8f6"), (AFTER_NG5, "f3g5"), (AFTER_D5, "d7d5")):
        look(fen, last)
    pgn["text"] = '[Event "x"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5 d5 *'
    now = look(AFTER_D5, "d7d5", read=True)
    assert now is not None and len(asked) == 2
    assert (now["moves"], now["more"]) == ([*OPENING, [3, "Bc4", "Nf6"], [4, "Ng5", "d5"]], [])


def test_a_games_moves_are_placed_by_their_move_numbers():
    pgn = """[Event "x"]
[FEN "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"]

1... e5 2. Nf3 $1 Nc6 3. e8Q -- 4. 0-0 {castled} (4. d4 exd4) 4...0-0-0 5.Bb5 1/2-1/2"""
    # A game set up with Black to move, a glyph, odd moves and zeros castling: each move on its own side.
    assert lichess_chess.parse_pgn_game(pgn)["moves"] == [[1, None, "e5"], [2, "Nf3", "Nc6"], [3, "e8Q", "--"], [4, "O-O", "O-O-O"], [5, "Bb5", None]]
    assert lichess_chess.parse_pgn_game(pgn)["evals"] == {}  # a glyph of an annotator's, not Lichess's analysis


def test_lichess_analysis_of_a_game_is_read_move_by_move():
    # As Lichess exports a broadcast board it has analysed: an evaluation on each move, a move it judged badly marked
    # (glyph or NAG) with its word and the line it preferred, whose own comments aren't the game's.
    pgn = """[Event "x"]

1. e4 { [%eval 0.18] [%clk 1:30:58] } 1... e5 { [%eval 0.25] } 2. Qh5?! { [%eval -0.31] } { Inaccuracy. Nf3 was best. }
(2. Nf3 { [%eval 0.3] } { (a quiet move) } 2... Nc6) 2... Nc6 { [%eval -0.2] } 3. Bc4 { [%eval -0.1] } 3... Nf6 $4 { [%eval #2] }
{ Checkmate is now unavoidable. g6 was best. } 4. Qxf7# 1-0"""
    read = lichess_chess.parse_pgn_game(pgn)
    assert read["moves"] == [[1, "e4", "e5"], [2, "Qh5?!", "Nc6"], [3, "Bc4", "Nf6"], [4, "Qxf7#", None]]
    assert read["evals"] == {"1": {"cp": 18}, "2": {"cp": 25}, "3": {"mark": "?!", "cp": -31, "best": "Nf3"}, "4": {"cp": -20}, "5": {"cp": -10},
                             "6": {"mark": "??", "mate": 2, "best": "g6"}}


def test_chess_rate_limit_fails_the_whole_read(monkeypatch):
    """A 429 mid-read must not return a fixture list with holes (the card would lose results)."""
    import pytest
    from sports_follow.adapters.base import PlayerRef

    calls = {"n": 0}

    def fake_get_json(url, ttl, text=False):
        calls["n"] += 1
        if url.endswith("/broadcast/top"):
            raise lichess_chess.RateLimited("Lichess rate limit (429)")
        if "search" in url:
            return {"currentPageResults": []}
        return ""

    monkeypatch.setattr(lichess_chess, "get_json", fake_get_json)
    ref = PlayerRef("lichess_chess", CARLSEN, "Magnus Carlsen", [CARLSEN], ["Magnus Carlsen"], "https://lichess.org/fide/1503014/Carlsen_Magnus")
    with pytest.raises(lichess_chess.RateLimited):
        lichess_chess.LichessChess().fixtures(ref)


def test_every_lichess_request_goes_to_an_address_lichess_has(monkeypatch):
    # 15 Oct 2026: API was set twice, the second time ending in "/", so every request went to
    # https://lichess.org/api//… and Lichess answered 404: chess had no fixtures, games, ratings or search.
    from sports_follow.adapters.base import PlayerRef

    urls = []

    def fake_get_json(url, ttl, text=False):
        urls.append(url)
        return "" if text else {"currentPageResults": [], "rounds": [], "tour": {}}

    monkeypatch.setattr(lichess_chess, "get_json", fake_get_json)
    chess = lichess_chess.LichessChess()
    ref = PlayerRef("lichess_chess", CARLSEN, "Magnus Carlsen", [CARLSEN], ["Magnus Carlsen"], "https://lichess.org/fide/1503014/Carlsen_Magnus")
    for read in (lambda: chess.fixtures(ref), lambda: chess.find_player("Magnus Carlsen"), lambda: chess.snapshot({"round": "abc", "game": "x"})):
        try:
            read()
        except Exception:  # noqa: BLE001 — empty answers may not parse; only the addresses matter here
            pass
    assert urls
    assert all(url.startswith("https://lichess.org/") and "//" not in url.split("://", 1)[1] for url in urls), urls
    assert any(url.startswith("https://lichess.org/api/fide/player") for url in urls)
    # The addresses the reads above don't reach (a tournament's rounds, its PGN, a player's stats): API ends in "/".
    assert lichess_chess.API.endswith("/") and 'f"{API}/' not in Path(lichess_chess.__file__).read_text()


CARLSEN_PAGE = "https://lichess.org/fide/1503014/Carlsen_Magnus"


def lichess_answering(monkeypatch, answers: dict[str, httpx.Response]) -> None:
    """Lichess as a fake: each URL's answer, an empty one for the rest; nothing cached, no spacing."""
    monkeypatch.setattr(lichess_chess, "_client", httpx.Client(transport=httpx.MockTransport(lambda request: answers.get(str(request.url)) or httpx.Response(200, json={"games": []}))))
    monkeypatch.setattr(lichess_chess, "SPACING", 0.0)


def test_a_429_on_lichess_fide_pages_pauses_only_them(monkeypatch):
    # 15 Oct 2026: Lichess's FIDE player pages and endpoint ignore the token, and one limit holds every
    # caller from a datacenter. Its 429s paused the broadcasts live boards read as well, and chess went blank.
    lichess_answering(monkeypatch, {CARLSEN_PAGE: httpx.Response(429, text="Too many requests. Try again later.")})
    with pytest.raises(lichess_chess.RateLimited, match=r"\(429\) on /fide/1503014/Carlsen_Magnus; lichess-fide paused for 60 s: Too many requests"):
        lichess_chess.get_json(CARLSEN_PAGE, ttl=0, text=True)
    with pytest.raises(lichess_chess.RateLimited, match="lichess-fide asked us to slow down"):
        lichess_chess.get_json("https://lichess.org/api/fide/player/1503014", ttl=0)  # the same limit
    assert lichess_chess.get_json("https://lichess.org/api/broadcast/-/-/abcdefgh", ttl=0) == {"games": []}  # live boards still read
    assert lichess_chess.get_json("https://lichess.org/api/fide/player?q=Carlsen", ttl=0) == {"games": []}  # FIDE search has no such limit


def test_a_429_on_lichess_broadcasts_pauses_them_for_its_three_minute_window(monkeypatch):
    said = "Use an oauth token to get 120 requests per minute. Contact us for more."
    lichess_answering(monkeypatch, {"https://lichess.org/api/broadcast/top": httpx.Response(429, json={"error": said})})
    with pytest.raises(lichess_chess.RateLimited, match=r"on /api/broadcast/top; lichess paused for 180 s: .*oauth token"):
        lichess_chess.get_json("https://lichess.org/api/broadcast/top", ttl=0)
    with pytest.raises(lichess_chess.RateLimited, match="lichess asked us to slow down"):
        lichess_chess.get_json("https://lichess.org/api/broadcast/-/-/abcdefgh", ttl=0)
    paused_until = budget.STORE.get("budget:lichess:paused")
    assert paused_until and 170 < paused_until - time.time() <= 180


def test_lichess_reads_fide_records_through_search_by_id(monkeypatch):
    # FIDE search answers an id with that one player and isn't behind the limit datacenters share.
    urls = []

    def fake_get_json(url, ttl, text=False):
        urls.append(url)
        if url == "https://lichess.org/api/fide/player?q=46616543":
            return load("lichess/fide-search-id-gukesh")
        if url.startswith("https://lichess.org/api/fide/player?q="):
            return []
        raise AdapterError(f"Lichess returned HTTP 404 for {url}")

    monkeypatch.setattr(lichess_chess, "get_json", fake_get_json)
    chess = lichess_chess.LichessChess()
    gukesh = chess.player("46616543")
    assert gukesh and gukesh.name == "Gukesh D"
    stats, _ = chess.stats(gukesh, [])
    assert {"label": "Classical", "value": "2710"} in stats and {"label": "Title", "value": "GM"} in stats
    assert not any("/fide/player/" in url for url in urls)
    # Were search to stop answering ids, the player endpoint is asked, and its 404 still means no such player.
    with pytest.raises(AdapterError, match="HTTP 404"):
        chess.player("1")
    assert urls[-2:] == ["https://lichess.org/api/fide/player?q=1", "https://lichess.org/api/fide/player/1"]


def test_a_refused_fide_page_gives_what_it_last_listed_and_search_still_finds_events(monkeypatch, source_cache):
    page = '<a href="/broadcast/tata-steel-2026/round-1/abcdefgh">Tata Steel 2026</a>'
    answers = {CARLSEN_PAGE: httpx.Response(200, text=page)}
    lichess_answering(monkeypatch, answers)
    kept: dict[str, tuple[str, int | None]] = {}
    redis = SimpleNamespace(get=lambda key: kept[key][0] if key in kept else None, set=lambda key, value, ex=None: kept.__setitem__(key, (value, ex)))
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: redis)
    chess = lichess_chess.LichessChess()
    ref = PlayerRef("lichess_chess", CARLSEN, "Magnus Carlsen", [CARLSEN], ["Magnus Carlsen"], CARLSEN_PAGE)
    assert chess._page_rounds(ref) == ["abcdefgh"]
    assert kept == {f"lichess:fide-page:{CARLSEN}": ('["abcdefgh"]', lichess_chess.STALE_PAGE)}
    cache.forget()  # a restart once the page's copy is gone from the source cache too: nothing read is fresh
    source_cache.data.clear()
    answers[CARLSEN_PAGE] = httpx.Response(429, text="Too many requests.")
    assert chess._page_rounds(ref) == ["abcdefgh"]  # what the last copy listed stands in
    kept.clear()
    assert chess._page_rounds(ref) == []  # never read: Lichess's FIDE pages are paused, and that's no error
    # The player's events are still found by search, and no FIDE page refusal fails the read.
    answers["https://lichess.org/api/broadcast/search?q=carlsen"] = httpx.Response(200, json={"currentPageResults": []})
    answers["https://lichess.org/api/broadcast/top"] = httpx.Response(200, json={"active": [], "upcoming": []})
    assert chess.fixtures(ref) == []


def test_a_player_fide_names_without_a_comma_is_found_by_their_name_not_its_initial(monkeypatch):
    # FIDE writes Gukesh "Gukesh D" and Praggnanandhaa "Praggnanandhaa R", with no comma (BUG-209): their events are
    # searched by "gukesh" and "praggnanandhaa", never "d" or "r" (every "Group D" and "Open R" on Lichess), and an
    # event's players line names them so ("Carlsen, Gukesh, Praggnanandhaa"). Their FIDE pages refused, as the limit
    # every datacenter shares does: search and what's on now find the event. Carlsen's "Carlsen, Magnus" as before.
    now = datetime.now(timezone.utc)
    ms = lambda d: int(d.timestamp() * 1000)  # noqa: E731
    gukesh, pragg, carlsen, sindarov = (
        {"name": name, "fideId": fide, "rating": rating, "title": "GM"}
        for name, fide, rating in (("Gukesh D", 46616543, 2767), ("Praggnanandhaa R", 25059530, 2758), ("Carlsen, Magnus", 1503014, 2837), ("Sindarov, Javokhir", 14205483, 2726)))
    tour = {"id": "TataStel", "name": "Tata Steel Masters 2027", "info": {"players": "Carlsen, Gukesh, Praggnanandhaa, Sindarov"},
            "dates": [ms(now + timedelta(hours=3)), ms(now + timedelta(days=12))]}
    r1 = {"id": "Round001", "name": "Round 1", "ongoing": False, "finished": False, "startsAt": ms(now + timedelta(hours=3))}
    r2 = {"id": "Round002", "name": "Round 2", "ongoing": False, "finished": False, "startsAt": ms(now + timedelta(days=1, hours=3))}
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    round1 = {"round": r1, "tour": tour, "games": [{"id": "gameaaaa", "players": [gukesh, sindarov], "fen": start, "status": "*"},
                                                    {"id": "gamebbbb", "players": [carlsen, pragg], "fen": start, "status": "*"}]}
    group_d = {"id": "GroupDxx", "name": "Club Championship 2027 | Group D", "info": {"players": "Someone, Else"}, "dates": [ms(now - timedelta(days=1)), ms(now + timedelta(days=2))]}
    asked: list[str] = []

    def get_json(url, ttl, text=False):
        asked.append(url.removeprefix("https://lichess.org"))
        if url.startswith("https://lichess.org/fide/"):
            raise lichess_chess.RateLimited("Lichess rate limit (429) on /fide/…")
        if "/broadcast/search?q=" in url:
            q = url.split("q=")[1]
            # Lichess's search finds the event by Gukesh's and Carlsen's names, not yet by Praggnanandhaa's: the
            # broadcasts on now and to come (top) list it.
            return {"currentPageResults": [{"tour": tour}] if q in ("gukesh", "carlsen") else [{"tour": group_d}] if len(q) == 1 else []}
        if url.endswith("/broadcast/top"):
            return {"active": [], "upcoming": [{"tour": tour, "round": r1}]}
        if url.endswith(f"/broadcast/{tour['id']}"):
            return {"tour": tour, "rounds": [r1, r2]}
        if url.endswith(f"/broadcast/-/-/{r1['id']}"):
            return round1
        raise AdapterError(f"not faked: {url}")

    monkeypatch.setattr(lichess_chess, "get_json", get_json)
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: Kept())  # the FIDE page never read before
    chess = lichess_chess.LichessChess()
    for player, query, opponent in ((gukesh, "gukesh", "Javokhir Sindarov"), (pragg, "praggnanandhaa", "Magnus Carlsen"), (carlsen, "carlsen", "Praggnanandhaa R")):
        asked.clear()
        ref = lichess_chess.ref_for({"id": player["fideId"], "name": player["name"]})
        fixtures = chess.fixtures(ref)
        assert [a for a in asked if "/search?" in a] == [f"/api/broadcast/search?q={query}"], ref.name
        assert not any(group_d["id"] in a for a in asked), ref.name  # no "Group D" event read
        # Their game in round 1 (paired), and round 2, not paired yet, as theirs: the event lists them.
        assert [(f.source_id, f.status) for f in fixtures] == [(f"Round001-{player['fideId']}", "scheduled"), (f"Round002-{player['fideId']}", "scheduled")], ref.name
        assert opponent in fixtures[0].title and fixtures[1].detail == "Pairings not published yet"
    # The words each is known by: FIDE's surname for "Surname, Given"; for a name with no comma, each part but an initial.
    names = ("Gukesh D", "Praggnanandhaa R", "Carlsen, Magnus", "Nihal Sarin", "Van Foreest, Jorden", "So, Wesley")
    assert [lichess_chess.known_as(lichess_chess.ref_for({"id": 1, "name": n})) for n in names] == [
        ["gukesh"], ["praggnanandhaa"], ["carlsen"], ["nihal", "sarin"], ["foreest"], ["so"]]

    # Events that only mention him (newer, more of them than MAX_TOURS) don't push out the one his FIDE page lists.
    own = {"id": "OwnEvent", "name": "Gukesh's event", "dates": [ms(now - timedelta(days=12)), ms(now - timedelta(days=3))]}
    others = [{"id": f"Mention{i}", "name": f"Open {i}", "info": {"players": "Someone, Else"}, "dates": [ms(now - timedelta(days=1)), ms(now + timedelta(days=2))]}
              for i in range(lichess_chess.MAX_TOURS)]

    def page_and_search(url, ttl, text=False):
        if url.startswith("https://lichess.org/fide/"):
            return '<a href="/broadcast/x/round-9/AAAAAAAA">'
        if url.endswith("/broadcast/-/-/AAAAAAAA"):
            return {"tour": own}
        if "/broadcast/search?q=" in url:
            return {"currentPageResults": [{"tour": t} for t in others]}
        return {"active": [], "upcoming": []}

    monkeypatch.setattr(lichess_chess, "get_json", page_and_search)
    tours = chess._tours(lichess_chess.ref_for({"id": gukesh["fideId"], "name": "Gukesh D"}), now)
    assert len(tours) == lichess_chess.MAX_TOURS and tours[0]["id"] == "OwnEvent"


def test_a_matchs_rounds_to_come_are_one_game_under_both_players(monkeypatch):
    # BUG-215: a World Championship's games further off than 12 hours have no pairing yet, so no board: each player's
    # fixture of one was a game of its own, and a fan of both saw each one twice in Coming up. In a match between two
    # players, a round to come is their meeting: under both, it's the round's.
    now = datetime.now(timezone.utc)
    ms = lambda d: int(d.timestamp() * 1000)  # noqa: E731
    gukesh = {"name": "Gukesh D", "fideId": 46616543, "rating": 2767, "title": "GM"}
    sindarov = {"name": "Sindarov, Javokhir", "fideId": 14205483, "rating": 2726, "title": "GM"}
    carlsen = {"name": "Carlsen, Magnus", "fideId": 1503014, "rating": 2837, "title": "GM"}
    so = {"name": "So, Wesley", "fideId": 5202213, "rating": 2750, "title": "GM"}
    tour = {"id": "WorldCh1", "name": "FIDE World Championship 2026", "info": {"players": "Gukesh, Sindarov", "format": "14-game match"},
            "dates": [ms(now - timedelta(days=1)), ms(now + timedelta(days=20))]}
    rounds = [{"id": "Game0001", "name": "Game 1", "finished": True, "startsAt": ms(now - timedelta(days=1))},
              {"id": "Game0002", "name": "Game 2", "startsAt": ms(now + timedelta(hours=3))},
              *({"id": f"Game000{n}", "name": f"Game {n}", "startsAt": ms(now + timedelta(days=n - 2, hours=3))} for n in (3, 4, 5))]
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    pgn = ('[White "Gukesh D"]\n[Black "Sindarov, Javokhir"]\n[Result "1/2-1/2"]\n[WhiteFideId "46616543"]\n[BlackFideId "14205483"]\n'
           '[GameURL "https://lichess.org/broadcast/t/r/Game0001/game1aaa"]\n\n1. d4 d5 1/2-1/2\n')
    game2 = {"round": rounds[1], "tour": tour, "games": [{"id": "game2aaa", "players": [sindarov, gukesh], "fen": start, "status": "*"}]}

    def get_json(url, ttl, text=False):
        if "/broadcast/search?q=" in url:
            return {"currentPageResults": [{"tour": tour}]}
        if url.endswith("/broadcast/top"):
            return {"active": [], "upcoming": []}
        if url.endswith(f"/broadcast/{tour['id']}"):
            return {"tour": tour, "rounds": rounds}
        if url.endswith(f"/broadcast/{tour['id']}.pgn"):
            return pgn
        if url.endswith("/broadcast/-/-/Game0002"):
            return game2
        raise AdapterError(f"not faked: {url}")

    monkeypatch.setattr(lichess_chess, "get_json", get_json)
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: Kept())
    chess = lichess_chess.LichessChess()

    def boards(*players) -> list[dict[str, str]]:
        return [{f.source_id.split("-")[0]: f.board for f in chess.fixtures(lichess_chess.ref_for({"id": p["fideId"], "name": p["name"]}))} for p in players]

    mine, theirs = boards(gukesh, sindarov)
    assert mine == theirs and (mine["Game0001"], mine["Game0002"]) == ("game1aaa", "game2aaa")  # a board once it's paired
    assert mine["Game0003"] == "round:Game0003" and len(set(mine.values())) == 5  # each game to come one of its own, the same under both
    from sports_follow import structured  # Coming up's game: one for both players' fixtures of it

    assert structured.board_key(f"lichess_chess:Game0003-{gukesh['fideId']}", mine["Game0003"]) == structured.board_key(f"lichess_chess:Game0003-{sindarov['fideId']}", theirs["Game0003"])
    # Before its first game is paired too, as its format says it's a match of the two it names.
    rounds[0].update(finished=False, startsAt=ms(now + timedelta(days=1)))
    rounds[1]["startsAt"] = ms(now + timedelta(days=2))
    pgn, game2["games"] = "", []
    mine, theirs = boards(gukesh, sindarov)
    assert mine == theirs and mine["Game0001"] == "round:Game0001"
    # Not in a round robin, where a round's boards pair others: a round to come there is each player's own.
    tour["info"] = {"players": "Gukesh, Sindarov, Carlsen, So", "format": "4-player double round robin"}
    rounds[0].update(finished=True, startsAt=ms(now - timedelta(days=1)))
    pgn = pgn + ('[White "Gukesh D"]\n[Black "Sindarov, Javokhir"]\n[Result "1-0"]\n[WhiteFideId "46616543"]\n[BlackFideId "14205483"]\n'
                 '[GameURL "https://lichess.org/broadcast/t/r/Game0001/game1aaa"]\n\n1. d4 1-0\n\n'
                 '[White "So, Wesley"]\n[Black "Carlsen, Magnus"]\n[Result "0-1"]\n[WhiteFideId "5202213"]\n[BlackFideId "1503014"]\n'
                 '[GameURL "https://lichess.org/broadcast/t/r/Game0001/game1bbb"]\n\n1. e4 0-1\n')
    assert {b["Game0003"] for b in boards(gukesh, sindarov, carlsen, so)} == {""}
    # Nor where its players line names two of a bigger field (no "match" in its format), with no round read yet.
    tour["info"] = {"players": "Gukesh, Sindarov", "format": "10-player round robin"}
    rounds[0].update(finished=False, startsAt=ms(now + timedelta(days=1)))
    assert {b["Game0001"] for b in boards(gukesh, sindarov)} == {""}


def test_a_round_set_to_start_after_the_previous_one_is_read_and_said_to_be_next_once_that_one_is_on(monkeypatch):
    # BUG-211 (H17): a blitz day's round 7 has no start time ("startsAfterPrevious"): it was taken as unpaired, its
    # pairings never read, and its fixture no more than "scheduled" with no start, which nothing ever armed or polled.
    now = datetime.now(timezone.utc)
    ms = lambda d: int(d.timestamp() * 1000)  # noqa: E731
    gukesh = {"name": "Gukesh D", "fideId": 46616543, "rating": 2767, "title": "GM"}
    sindarov = {"name": "Sindarov, Javokhir", "fideId": 14205483, "rating": 2726, "title": "GM"}
    tour = {"id": "BlitzDay", "name": "Grand Chess Tour Blitz 2027", "info": {"players": "Gukesh, Sindarov"}, "dates": [ms(now - timedelta(hours=3)), ms(now + timedelta(hours=5))]}
    r6 = {"id": "Round006", "name": "Round 6", "ongoing": True, "startsAt": ms(now - timedelta(minutes=10))}
    r7 = {"id": "Round007", "name": "Round 7", "startsAfterPrevious": True}
    r8 = {"id": "Round008", "name": "Round 8", "startsAfterPrevious": True}
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    boards = {"Round006": [{"id": "game6aaa", "players": [sindarov, gukesh], "fen": start, "status": "*"}], "Round007": []}
    asked: list[str] = []

    def get_json(url, ttl, text=False):
        asked.append(url.removeprefix("https://lichess.org/api/"))
        if "/broadcast/search?q=" in url:
            return {"currentPageResults": [{"tour": tour}]}
        if url.endswith("/broadcast/top"):
            return {"active": [], "upcoming": []}
        if url.endswith(f"/broadcast/{tour['id']}"):
            return {"tour": tour, "rounds": [r6, r7, r8]}
        if url.endswith(f"/broadcast/{tour['id']}.pgn"):  # the finished rounds' games
            return ('[White "Sindarov, Javokhir"]\n[Black "Gukesh D"]\n[Result "0-1"]\n[WhiteFideId "14205483"]\n[BlackFideId "46616543"]\n'
                    '[GameURL "https://lichess.org/broadcast/t/r/Round006/game6aaa"]\n\n1. e4 0-1\n')
        for rnd in (r6, r7, r8):
            if url.endswith(f"/broadcast/-/-/{rnd['id']}"):
                return {"round": rnd, "tour": tour, "games": boards[rnd["id"]]}
        raise AdapterError(f"not faked: {url}")

    monkeypatch.setattr(lichess_chess, "get_json", get_json)
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: Kept())
    chess = lichess_chess.LichessChess()
    ref = lichess_chess.ref_for({"id": gukesh["fideId"], "name": gukesh["name"]})
    # Round 6 on: round 7 is next, its pairings asked for (none yet); round 8 isn't, nor read.
    fixtures = {f.source_id.split("-")[0]: f for f in chess.fixtures(ref)}
    assert "broadcast/-/-/Round007" in asked and "broadcast/-/-/Round008" not in asked
    assert [(r, f.status, f.start_utc, getattr(f, "next_up", None)) for r, f in fixtures.items()] == [
        ("Round006", "live", datetime.fromtimestamp(r6["startsAt"] / 1000, timezone.utc), False), ("Round007", "scheduled", None, True), ("Round008", "scheduled", None, False)]
    # Paired once round 6 is over; round 7 under way with no board of Gukesh's is no game of his (not live, no start
    # told): his board may not be up yet, or he isn't paired; with it, it's his game, live.
    r6.update(ongoing=False, finished=True, finishedAt=ms(now - timedelta(minutes=2)))
    boards["Round006"][0]["status"] = "0-1"
    boards["Round007"] = [{"id": "game7aaa", "players": [gukesh, sindarov], "fen": start, "status": "*"}]
    assert {f.source_id.split("-")[0]: f.next_up for f in chess.fixtures(ref)}["Round007"] is True
    r7["ongoing"] = True
    others = {"round": r7, "tour": tour, "games": [{"id": "game7bbb", "players": [{"name": "A", "fideId": 1}, {"name": "B", "fideId": 2}], "fen": start, "status": "*"}]}
    unpaired = lichess_chess.parse_board(others, str(gukesh["fideId"]), now)
    assert (unpaired.status, unpaired.clock_label) == ("scheduled", "Not paired this round")
    assert lichess_chess.parse_board({"round": r7, "tour": tour, "games": boards["Round007"]}, str(gukesh["fideId"]), now).status == "live"
    # Round 6 long over (a day's last round, the next on another day): round 7 isn't next.
    r7["ongoing"] = False
    r6["finishedAt"] = ms(now - lichess_chess.AFTER_PREVIOUS - timedelta(minutes=1))
    assert lichess_chess.next_up([r6, r7, r8], r7, now) is False


def test_tournament_pgn_gives_every_round_in_one_read():
    with gzip.open(FIXTURES / "lichess/tour-ewc-playoffs.pgn.gz", "rt") as f:
        rounds = lichess_chess.parse_pgn_rounds(f.read())
    assert len(rounds) == 6
    final = lichess_chess.games_of({"games": rounds["ohv5F74c"]}, CARLSEN)
    assert len(final) == 8
    assert lichess_chess.match_score(final, CARLSEN) == (6.0, 8)  # same as the round JSON says
    assert final[0]["players"][0] == {"name": "Carlsen, Magnus", "fideId": 1503014, "rating": 2823, "title": "GM"}


def test_an_unplayed_chess_game_isnt_a_game():
    # BUG-214: the recorded Esports World Cup third-place match. Nakamura won it 3–1 after four games, and the
    # broadcast lists a fifth, "1-0" with no move and Termination "Unplayed": it was scored as his win, "Won 4–1"
    # (5 games), and in form.
    with gzip.open(FIXTURES / "lichess/tour-ewc-playoffs.pgn.gz", "rt") as f:
        games = lichess_chess.parse_pgn_rounds(f.read())["Py6uVlbB"]
    nakamura, firouzja = "2016192", "12573981"
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    rnd, tour = {"id": "Py6uVlbB", "name": "3rd Place Match", "finished": True}, {"name": "Esports World Cup 2026 | Playoffs"}
    mine, theirs = (lichess_chess.fixture_for(tour, rnd, lichess_chess.games_of({"games": games}, fide), fide, now) for fide in (nakamura, firouzja))
    assert (lichess_chess.result_label(mine, [nakamura]), lichess_chess.result_label(theirs, [firouzja])) == ("Won 3–1", "Lost 1–3")
    assert mine.result["games"] == 4
    # A game with a result and no move (a forfeit) wasn't played either, whatever its Termination says; one with a move was.
    forfeit = ('[White "A"]\n[Black "B"]\n[Result "1-0"]\n[WhiteFideId "1"]\n[BlackFideId "2"]\n[Termination "forfeit"]\n'
               '[GameURL "https://lichess.org/broadcast/t/r/abcdefgh/ijklmnop"]\n\n 1-0\n\n'
               '[White "C"]\n[Black "D"]\n[Result "0-1"]\n[WhiteFideId "3"]\n[BlackFideId "4"]\n[GameURL "https://lichess.org/broadcast/t/r/abcdefgh/qrstuvwx"]\n\n'
               '1. f3 { [%eval -0.5] } 1... e5 2. g4 Qh4# 0-1\n')
    pgn_games = lichess_chess.parse_pgn_rounds(forfeit)["abcdefgh"]
    assert [lichess_chess.games_of({"games": pgn_games}, fide) != [] for fide in "1234"] == [False, False, True, True]
    # A round's JSON doesn't say how a game ended: one with a result and no move made (its board at the start) wasn't played.
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    played = [{**g, "fen": "8/8/4k3/8/8/4K3/8/8 w - - 0 50", "lastMove": "e2e3"} for g in games if g["status"] != "1-0"]
    data = {"round": {**rnd, "url": "https://lichess.org/broadcast/x/y/Py6uVlbB"}, "tour": tour,
            "games": [*played, {"id": "Q3GR2FUs", "players": games[-1]["players"], "fen": start, "status": "1-0"}]}
    snap = lichess_chess.parse_board(data, nakamura, now)
    assert (snap.clock_label, snap.lines[nakamura].headline) == ("Hikaru Nakamura won the match (4 games)", "Won the match 3–1 vs Alireza Firouzja")
    assert snap.state["match"]["games"] == 4 and snap.state["game_id"] == played[-1]["id"]  # the board shown is the last game played


def test_tournament_pgn_reads_tags_with_escaped_quotes():
    # Campeonato de Sevilla 2026 (recorded 2026-10-13): its name ends in an escaped quote
    # (Guadaíra\"), and every game of the broadcast lost its tags, so no player was found in it.
    with gzip.open(FIXTURES / "lichess/tour-sevilla-round1.pgn.gz", "rt") as f:
        rounds = lichess_chess.parse_pgn_rounds(f.read())
    games = rounds["6gyT8UBN"]
    assert len(games) == 6 and all(g["players"][0]["fideId"] and g["status"] != "*" for g in games)
    [game] = lichess_chess.games_of({"games": games}, "24543241")
    assert game["players"][0] == {"name": "Garcia Jimenez, Santiago", "fideId": 24543241, "rating": 2278, "title": None}
    assert game["status"] == "1-0"
    # A value is unescaped: \" is a quote, \\ a backslash.
    pgn = '[White "O\\"Neill, Pat \\\\ IM"]\n[Black "B"]\n[GameURL "https://lichess.org/broadcast/t/r/abcdefgh/ijklmnop"]\n'
    assert lichess_chess.parse_pgn_rounds(pgn)["abcdefgh"][0]["players"][0]["name"] == 'O"Neill, Pat \\ IM'


def test_chess_live_now_shows_the_top_boards_still_being_played(monkeypatch):
    def board(gid: str, white: tuple, black: tuple, status: str = "*") -> dict:
        side = lambda p: {"name": p[0], "fideId": p[1], "rating": p[2], "title": "GM", "clock": 360000}  # noqa: E731
        return {"id": gid, "status": status, "fen": "8/8/8/8/8/8/8/8 w - - 0 40", "players": [side(white), side(black)]}

    starts = int(time.time() * 1000) - 3600 * 1000
    round_now = {
        "round": {"id": "r5", "name": "Round 5", "ongoing": True, "startsAt": starts, "url": "https://lichess.org/broadcast/x/round-5/r5"},
        "tour": {"id": "t1", "name": "Grand Swiss"},
        "games": [
            board("g1", ("Gukesh, D", 46616543, 2777), ("Carlsen, Magnus", 1503014, 2837)),
            board("g2", ("Erigaisi, Arjun", 35009192, 2780), ("Nakamura, Hikaru", 2016192, 2800), status="1-0"),  # over
            board("g3", ("Praggnanandhaa, R", 25059530, 2758), ("Caruana, Fabiano", 2020009, 2795)),
            board("g4", ("Lower, Rated", 1, 2400), ("Also, Lower", 2, 2390)),
        ],
    }
    asked = []

    def fake_get_json(url, ttl, text=False):
        asked.append(url.removeprefix(lichess_chess.API))
        if url.endswith("broadcast/top"):
            return {"active": [{"tour": round_now["tour"], "round": round_now["round"]}, {"tour": {"name": "Done"}, "round": {"id": "r9", "finished": True}}]}
        return round_now

    monkeypatch.setattr(lichess_chess, "get_json", fake_get_json)
    games = lichess_chess.LichessChess().live_now()
    assert asked == ["broadcast/top", "broadcast/-/-/r5"]  # a finished round isn't read
    # The highest rated pairs still playing, LIVE_BOARDS of them, each with both players followable by FIDE id.
    assert [(g.title, g.competition, [p.athlete_id for p in g.players]) for g in games] == [
        ("D Gukesh v Magnus Carlsen", "Grand Swiss · Round 5", ["46616543", "1503014"]),
        ("R Praggnanandhaa v Fabiano Caruana", "Grand Swiss · Round 5", ["25059530", "2020009"])]
    assert all(g.snapshot.status == "live" and g.snapshot.state["kind"] == "chess" for g in games)
    # A board is a fixture of each of its players, as a followed player's card has it: Today shows it once, as theirs.
    assert games[0].fixtures == ["r5-46616543", "r5-1503014"]


def test_chess_live_now_reads_the_highest_tier_rounds_on_and_only_the_strong_boards(monkeypatch):
    def board(gid: str, white: tuple, black: tuple) -> dict:
        side = lambda p: {"name": p[0], "fideId": p[1], "rating": p[2], "clock": 360000}  # noqa: E731
        return {"id": gid, "status": "*", "fen": "8/8/8/8/8/8/8/8 w - - 0 40", "players": [side(white), side(black)]}

    starts = int(time.time() * 1000) - 3600 * 1000

    def entry(rid: str, name: str, tier: int | None, ongoing: bool | None) -> dict:
        return {"tour": {"id": f"t-{rid}", "name": name, **({"tier": tier} if tier else {})},
                "round": {"id": rid, "name": "Round 9", "ongoing": ongoing, "startsAt": starts, "url": f"https://lichess.org/broadcast/x/r/{rid}"}}

    # Lichess's order, as on 15 Oct: a seniors section first (tier 4, as an official FIDE event), then events
    # between rounds, and the elite one further down.
    top = {"active": [entry("seniors", "Continental 65+", 4, True), entry("youth", "World U17", 4, None), entry("uni", "University", 4, None),
                      entry("open", "Open", 3, True), entry("elite", "Grand Swiss", 5, True), entry("women", "Women's Grand Prix", 4, True)]}
    rounds = {
        "seniors": [board("s1", ("Ledesma, Otoniel", 1, 2186), ("Leskovar, Mario", 2, 2170)), board("s2", ("Pinal Borges, Nelson", 3, 2265), ("Velasquez, Hector", 4, 1932))],
        "elite": [board("e1", ("Gukesh, D", 46616543, 2777), ("Carlsen, Magnus", 1503014, 2837)), board("e2", ("Strong, One", 5, 2560), ("Lower, Two", 6, 2540))],
        "women": [board("w1", ("Ju, Wenjun", 8603006, 2560), ("Humpy, Koneru", 5008123, 2540)), board("w2", ("Just, Short", 7, 2550), ("Of, It", 9, 2549))],
        "open": [board("o1", ("Never, Read", 10, 2700), ("Fourth, Round", 11, 2700))],
    }
    asked = []

    def fake_get_json(url, ttl, text=False):
        asked.append(url.removeprefix(lichess_chess.API))
        if url.endswith("broadcast/top"):
            return top
        rid = url.rsplit("/", 1)[-1]
        return {"round": top["active"][[e["round"]["id"] for e in top["active"]].index(rid)]["round"], "tour": {"name": rid}, "games": rounds[rid]}

    monkeypatch.setattr(lichess_chess, "get_json", fake_get_json)
    games = lichess_chess.LichessChess().live_now()
    # The rounds under way, highest tier first and of equals in Lichess's order, LIVE_TOURS of them: the open isn't read.
    assert asked == ["broadcast/top", "broadcast/-/-/elite", "broadcast/-/-/seniors", "broadcast/-/-/women"]
    # Only boards averaging LIVE_RATING or more: none of the seniors', and 2550 counts but 2549.5 doesn't.
    assert [g.title for g in games] == ["D Gukesh v Magnus Carlsen", "One Strong v Two Lower", "Wenjun Ju v Koneru Humpy"]
