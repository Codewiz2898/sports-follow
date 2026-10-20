"""Predictions with friends (predictions.py, BHA-319): what each sport asks of a game's board, when a question
closes and what answers it, read off a real Lichess round (recorded in tests/fixtures/lichess) and PandaScore
series in its documented shapes (made up here, as tests/test_pandascore.py's are); and how a right pick scores."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sports_follow import predictions, sports
from sports_follow.adapters import lichess_chess, pandascore_esports as ps
from sports_follow.sports.base import Ask

from .test_new_adapters import CARLSEN, LAZAVIK, load
from .test_pandascore import FINAL, LIVE, NRG, SEN, _game, _match

NOW = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
CHESS = sports.get("chess")


def board(game: int = 0, *, status: str | None = None, ongoing: bool = False, fide: str = CARLSEN):
    """The recorded Esports World Cup grand final's board of one of its games, as if it were the round's only game:
    to come (status "*", the round starting tomorrow), under way (ongoing), or over with its recorded result; as one
    player's fixture has it (Carlsen's, else Lazavik's)."""
    data = load("lichess/round-final")
    played = data["games"][game]
    one = {**played, "status": status or played["status"]}
    to_come = one["status"] == "*"
    rnd = {**data["round"], "finished": not to_come and not ongoing, "ongoing": ongoing,
           "startsAt": int((NOW + (timedelta(days=1) if to_come and not ongoing else -timedelta(hours=1))).timestamp() * 1000)}
    return lichess_chess.parse_board({**data, "round": rnd, "games": [one]}, fide, NOW)


# ---------------------------------------------------------------- chess: who wins the game


def test_chess_asks_who_wins_once_the_pairing_is_out():
    snap = board(status="*")
    assert snap.status == "scheduled"
    [ask] = CHESS.asks(snap.state)
    assert ask == Ask("result", "Who wins?", ((CARLSEN, "Magnus Carlsen"), ("draw", "Draw"), (LAZAVIK, "Denis Lazavik")))
    assert ask.first  # it closes at the game's start time too
    # One game however many fixtures show it: its board's id at Lichess, the same under each player's.
    assert CHESS.board(snap.state) == "mJnv1YBU" == CHESS.board(board(status="*", fide=LAZAVIK).state)
    assert not CHESS.closed("result", snap.status, snap.state) and CHESS.answer("result", snap.status, snap.state) is None
    # Before the pairing's out (no board yet), and a knockout round's mini-match: nothing to ask.
    assert CHESS.asks({"kind": "chess", "round": "Round 1"}) == []
    match = lichess_chess.parse_board(load("lichess/round-final"), CARLSEN, NOW)
    assert match.state["match"] and CHESS.asks(match.state) == []


def test_chess_closes_at_the_first_move_and_settles_from_the_recorded_result():
    live = board(status="*", ongoing=True)
    assert live.status == "live" and CHESS.closed("result", live.status, live.state)
    assert CHESS.answer("result", live.status, live.state) is None  # still being played
    # Game 1: Carlsen won with White. Game 2: a draw. Game 4: Carlsen won with Black.
    assert [CHESS.answer("result", s.status, s.state) for s in (board(0), board(1), board(3))] == [CARLSEN, "draw", CARLSEN]
    assert all(board(g).status == "final" for g in (0, 1, 3))


# ---------------------------------------------------------------- esports: who takes each map


def test_a_series_asks_who_takes_each_map_it_lists():
    rules = sports.get("valorant")
    snap = ps.parse_match(_match("not_started"))
    asks = rules.asks(snap.state)
    assert [(a.key, a.title, a.order, a.first) for a in asks] == [("map1", "Who takes map 1?", 1, True), ("map2", "Who takes map 2?", 2, False), ("map3", "Who takes map 3?", 3, False)]
    assert asks[0].options == ((str(SEN), "Sentinels"), (str(NRG), "NRG"))
    assert rules.board(snap.state) == ""  # the series is its fixture
    # A lobby (battle royale) asks nothing yet; nor does a series with a side still to be decided.
    assert rules.asks({"kind": "battle_royale", "standings": []}) == []
    assert rules.asks({**snap.state, "teams": snap.state["teams"][:1]}) == []


def test_each_map_closes_as_it_starts_and_settles_from_its_winner():
    rules = sports.get("cs2")
    live = ps.parse_match(LIVE)  # map 1 to Sentinels, map 2 on, map 3 to come
    assert [rules.closed(f"map{n}", live.status, live.state) for n in (1, 2, 3)] == [True, True, False]
    assert [rules.answer(f"map{n}", live.status, live.state) for n in (1, 2, 3)] == [str(SEN), None, None]
    final = ps.parse_match(FINAL)
    assert [rules.answer(f"map{n}", final.status, final.state) for n in (1, 2, 3)] == [str(SEN), str(NRG), str(SEN)]
    # A 2–0: map 3 was never played, so its question is voided.
    two_nil = ps.parse_match(_match("finished", 2, 0, [_game(1, "finished", SEN), _game(2, "finished", SEN), _game(3, "not_played")], winner_id=SEN))
    assert [rules.answer(f"map{n}", two_nil.status, two_nil.state) for n in (1, 2, 3)] == [str(SEN), str(SEN), sports.VOID]
    assert rules.closed("map3", two_nil.status, two_nil.state)


def test_only_sports_that_ask_ask():
    asking = {s.key for s in sports.ALL if s.predicts}
    assert "chess" in asking and "lol" in asking and "valorant" in asking  # every esports game shares esports' rule
    assert {"football", "cricket", "tennis", "basketball"}.isdisjoint(asking)  # later (BHA-318): winners first
    assert sports.get("football").asks({"kind": "football", "home": {"id": "1", "name": "A"}, "away": {"id": "2", "name": "B"}}) == []


# ---------------------------------------------------------------- scoring and the split


def test_a_right_pick_scores_more_the_fewer_made_it():
    assert predictions.points(71, 100) == 29 and predictions.points(7, 100) == 93 and predictions.points(22, 100) == 78
    assert predictions.points(1, 1) == predictions.FLOOR  # everyone was right: still worth something
    assert predictions.points(0, 0) == 0


def test_the_split_adds_up_to_a_hundred():
    assert predictions.shares({"a": 1, "b": 1, "c": 1}, ["a", "b", "c"]) == {"a": 34, "b": 33, "c": 33}
    assert predictions.shares({"a": 852, "b": 264, "c": 88}, ["a", "b", "c"]) == {"a": 71, "b": 22, "c": 7}
    assert predictions.shares({}, ["a", "b"]) == {"a": 0, "b": 0}
