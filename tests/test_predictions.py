"""Predictions with friends (predictions.py, BHA-319): what each sport asks of a game's board, when a question
closes and what answers it, read off a real Lichess round and real ESPN football and cricket games (recorded in
tests/fixtures), and PandaScore series and Sportmonks matches in their documented shapes (made up here, as
tests/test_pandascore.py's and tests/test_sportmonks*.py's are); and how a right pick scores."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sports_follow import predictions, share, sports
from sports_follow.adapters import espn_cricket, espn_soccer, lichess_chess, pandascore_esports as ps, sportmonks_cricket as sc, sportmonks_football as sm
from sports_follow.sports.base import Ask

from .test_new_adapters import CARLSEN, LAZAVIK, load
from .test_pandascore import FINAL, LIVE, NRG, SEN, _game, _match
from .test_sportmonks import AWAY, HOME, _match as football_match
from .test_sportmonks_cricket import AFG, CHASE, FIRST, IND, _match as cricket_match

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


def test_a_series_called_off_voids_what_it_never_played():
    rules = sports.get("lol")
    off = ps.parse_match(_match("canceled", 1, 0, [_game(1, "finished", SEN), _game(2, "not_started"), _game(3, "not_started")]))
    assert off.status == "postponed" and off.state["cancelled"]
    assert [rules.answer(f"map{n}", off.status, off.state) for n in (1, 2, 3)] == [str(SEN), sports.VOID, sports.VOID]
    assert rules.closed("map2", off.status, off.state)
    # Put back to a later date, it's still to come: its questions wait.
    later = ps.parse_match(_match("postponed"))
    assert not later.state["cancelled"] and rules.answer("map1", later.status, later.state) is None and not rules.closed("map2", later.status, later.state)


# ---------------------------------------------------------------- football: who wins, or a draw

FOOTBALL = sports.get("football")


def test_football_asks_who_wins_or_a_draw_home_side_first():
    snap = sm.parse_match(football_match("NS", 0, 0))
    assert snap.status == "scheduled"
    [ask] = FOOTBALL.asks(snap.state)
    assert ask == Ask("result", "Who wins?", ((str(HOME), "Trabzonspor"), ("draw", "Draw"), (str(AWAY), "Galatasaray")))
    assert ask.first and FOOTBALL.board(snap.state) == ""  # it closes at kick-off; the fixture is the game
    assert not FOOTBALL.closed("result", snap.status, snap.state) and FOOTBALL.answer("result", snap.status, snap.state) is None
    # A side still to be named: nothing to ask yet.
    assert FOOTBALL.asks({**snap.state, "away": {"id": "None", "name": None, "score": ""}}) == []
    # ESPN's board asks the same (Norway v Portugal, recorded).
    espn = espn_soccer.parse_summary(load("espn/soccer-summary-final"), "uefa.nations")
    assert [key for key, _ in FOOTBALL.asks(espn.state)[0].options] == ["464", "draw", "482"]


def test_football_closes_at_kick_off_and_settles_from_the_final_score():
    live = sm.parse_match(football_match("INPLAY_2ND_HALF", 1, 0))
    assert FOOTBALL.closed("result", live.status, live.state) and FOOTBALL.answer("result", live.status, live.state) is None
    finals = [sm.parse_match(football_match(state, home, away)) for state, home, away in (("FT", 3, 0), ("FT", 0, 2), ("FT", 1, 1), ("AET", 2, 1), ("FT_PEN", 1, 1))]
    # Extra time's goals count; a shootout isn't part of the score, so a game level after extra time is a draw.
    assert [FOOTBALL.answer("result", s.status, s.state) for s in finals] == [str(HOME), str(AWAY), "draw", str(HOME), "draw"]
    # Recorded: Norway 1–2 Portugal; Bradford City 0–0 Burnley, which Bradford won 4–2 on penalties.
    norway = espn_soccer.parse_summary(load("espn/soccer-summary-final"), "uefa.nations")
    shootout = espn_soccer.parse_summary(load("espn/soccer-summary-shootout"), "eng.league_cup")
    assert [FOOTBALL.answer("result", s.status, s.state) for s in (norway, shootout)] == ["482", "draw"]


def test_a_football_match_called_off_is_voided_and_one_put_back_waits():
    for state in ("ABANDONED", "CANCELLED", "DELETED", "WO", "AWARDED"):
        snap = sm.parse_match(football_match(state, 3, 0))
        assert snap.state["cancelled"] and FOOTBALL.answer("result", snap.status, snap.state) == sports.VOID, state
    put_back = sm.parse_match(football_match("POSTPONED", 0, 0))
    assert (put_back.status, put_back.state["cancelled"]) == ("postponed", False)
    assert FOOTBALL.answer("result", put_back.status, put_back.state) is None  # its new date, or the sweep, settles it
    # ESPN calls an abandoned match over ("post"), with the score it stopped at: that's no result.
    data = load("espn/soccer-summary-final")
    data["header"]["competitions"][0]["status"]["type"] = {"name": "STATUS_ABANDONED", "state": "post", "description": "Abandoned"}
    abandoned = espn_soccer.parse_summary(data, "uefa.nations")
    assert abandoned.status == "final" and FOOTBALL.answer("result", abandoned.status, abandoned.state) == sports.VOID


# ---------------------------------------------------------------- cricket: who wins a limited-overs match

CRICKET = sports.get("cricket")


def test_cricket_asks_who_wins_a_limited_overs_match():
    snap = sc.parse_match(cricket_match("NS", []))
    assert snap.status == "scheduled"
    [ask] = CRICKET.asks(snap.state)
    assert ask == Ask("result", "Who wins?", ((str(IND), "India"), (str(AFG), "Afghanistan")))
    assert ask.first and CRICKET.board(snap.state) == ""
    assert not CRICKET.closed("result", snap.status, snap.state) and CRICKET.answer("result", snap.status, snap.state) is None
    # ESPN's board of an ODI before the start (India v West Indies, recorded): the same question.
    squads = espn_cricket.parse_summary(load("espn/cricket-summary-squads"))
    assert squads.status == "scheduled" and [key for key, _ in CRICKET.asks(squads.state)[0].options] == ["6", "4"]
    # A Test or a first-class match may be drawn, and lasts longer than a question waits for its answer: none asked.
    assert CRICKET.asks(sc.parse_match({**cricket_match("NS", []), "type": "Test/5day"}).state) == []
    assert CRICKET.asks(espn_cricket.parse_summary(load("espn/cricket-summary-test-live")).state) == []
    assert CRICKET.asks(espn_cricket.parse_summary(load("espn/cricket-summary-innings-win")).state) == []


def test_cricket_closes_at_the_first_ball_and_settles_from_the_winner():
    live = sc.parse_match(cricket_match("1st Innings", [FIRST]))
    assert CRICKET.closed("result", live.status, live.state) and CRICKET.answer("result", live.status, live.state) is None
    won = sc.parse_match(cricket_match("Finished", [FIRST, CHASE], note="India won by 127 runs", winner_team_id=IND))
    assert CRICKET.answer("result", won.status, won.state) == str(IND)
    # A tie a Super Over settled is its winner's, as the result says.
    level = {**CHASE, "score": 221, "overs": 20}
    super_over = sc.parse_match(cricket_match("Finished", [FIRST, level], note="Match tied (Afghanistan won the Super Over)", winner_team_id=AFG, super_over=True))
    assert CRICKET.answer("result", super_over.status, super_over.state) == str(AFG)
    # The board marking nobody, the summary names the winner; with no summary yet ("Result"), it waits.
    unmarked = {**won.state, "teams": [{**t, "winner": None} for t in won.state["teams"]]}
    assert CRICKET.answer("result", "final", unmarked) == str(IND)
    assert CRICKET.answer("result", "final", {**unmarked, "summary": ""}) is None
    # Recorded: India won by 8 wkts.
    final = espn_cricket.parse_summary(load("espn/cricket-summary-final"))
    assert CRICKET.answer("result", final.status, final.state) == "6"


def test_a_cricket_tie_no_result_or_washout_is_voided_and_one_put_back_waits():
    level = {**CHASE, "score": 221, "overs": 20}
    boards = [sc.parse_match(cricket_match("Finished", [FIRST, level], note="Match tied")), sc.parse_match(cricket_match("Finished", [FIRST], note="No result")),
              sc.parse_match(cricket_match("Aban.", [])), sc.parse_match(cricket_match("Cancl.", []))]
    assert [CRICKET.answer("result", s.status, s.state) for s in boards] == [sports.VOID] * 4
    put_back = sc.parse_match(cricket_match("Postp.", []))
    assert (put_back.status, put_back.state["cancelled"], CRICKET.answer("result", put_back.status, put_back.state)) == ("postponed", False, None)
    # Recorded: Nepal v Sri Lanka, abandoned without a ball bowled; and India v West Indies as if rained off after
    # play, a no result with its scorecard. A match put back (Arunachal Pradesh v Nagaland) waits.
    washed_out = espn_cricket.parse_summary(load("espn/cricket-summary-abandoned"))
    rained_off = load("espn/cricket-summary-final")
    rained_off["header"]["competitions"][0]["status"].update(summary="No result", type={"id": "6", "description": "Abandoned", "state": "post"})
    no_result = espn_cricket.parse_summary(rained_off)
    later = espn_cricket.parse_summary(load("espn/cricket-summary-postponed"))
    assert [(s.status, CRICKET.answer("result", s.status, s.state)) for s in (washed_out, no_result, later)] == [("postponed", sports.VOID), ("final", sports.VOID), ("postponed", None)]


def test_only_sports_that_ask_ask():
    asking = {s.key for s in sports.ALL if s.predicts}
    assert "chess" in asking and "lol" in asking and "valorant" in asking  # every team esports game shares esports' rule
    assert "bgmi" not in asking and "freefire" not in asking  # a lobby asks nothing yet, so its games aren't read ahead
    assert {"football", "cricket"} <= asking  # their winners, after chess and team esports (BHA-317)
    assert {"tennis", "basketball"}.isdisjoint(asking)  # later
    assert sports.get("tennis").asks({"kind": "tennis", "players": [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]}) == []


# ---------------------------------------------------------------- scoring and the split


def test_a_right_pick_scores_more_the_fewer_made_it():
    assert predictions.points(71, 100) == 29 and predictions.points(7, 100) == 93 and predictions.points(22, 100) == 78
    assert predictions.points(1, 1) == predictions.FLOOR  # everyone was right: still worth something
    assert predictions.points(0, 0) == 0


def test_the_split_adds_up_to_a_hundred():
    assert predictions.shares({"a": 1, "b": 1, "c": 1}, ["a", "b", "c"]) == {"a": 34, "b": 33, "c": 33}
    assert predictions.shares({"a": 852, "b": 264, "c": 88}, ["a", "b", "c"]) == {"a": 71, "b": 22, "c": 7}
    assert predictions.shares({}, ["a", "b"]) == {"a": 0, "b": 0}


def test_a_shared_question_previews_its_split_or_how_few_called_it():
    q = {"id": 12, "title": "Who wins?", "game": "Gukesh D vs Javokhir Sindarov", "sport": "chess", "match": "lichess_chess/r-1", "status": "open", "answer": None, "picks": 1204,
         "options": [{"key": "g", "label": "Gukesh D", "share": 71}, {"key": "draw", "label": "Draw", "share": 22}, {"key": "s", "label": "Javokhir Sindarov", "share": 7}]}
    shown = share.of_question(q)
    assert shown.title.startswith("71% of ") and shown.title.endswith(" fans say Gukesh D: Who wins? Gukesh D vs Javokhir Sindarov")
    assert (shown.picture.word, shown.picture.name, shown.picture.tag) == ("71%", "say Gukesh D", "Chess")
    assert shown.picture.lines == ("Who wins? · Gukesh D vs Javokhir Sindarov", "Draw 22% · Javokhir Sindarov 7% · 1,204 picks")
    assert (shown.path, shown.image) == ("/match/lichess_chess/r-1?q=12", "/api/predictions/12/share.png")
    assert "Bragging rights only" in shown.description
    settled = share.of_question({**q, "status": "settled", "answer": "s"})
    assert (settled.picture.word, settled.picture.name) == ("7%", "called it: Javokhir Sindarov") and settled.title.startswith("Only 7% of ")
    # Too few picks to show the split (predictions.public leaves it out): the question itself.
    few = share.of_question({**q, "picks": 3, "options": [{k: v for k, v in o.items() if k != "share"} for o in q["options"]]})
    assert (few.picture.word, few.picture.name, few.title) == ("Who wins?", "", "Who wins? Gukesh D vs Javokhir Sindarov")
