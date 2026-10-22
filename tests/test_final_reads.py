"""A finished game's scorecard is the source's final one (BHA-450): a source's copy of a match is kept a day
whatever the ttl it was read with, so the same match read while it was on (a live poll, its match page, a squad
check) is in the cache when its final read comes. Every adapter's final read takes a day-old copy only if that
copy is over, else reads it again. Against recorded ESPN and Lichess responses and made-up ones in the other
sources' shapes; no source is asked."""

from __future__ import annotations

import copy
import gzip
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from sports_follow.adapters import espn, espn_basketball, espn_cricket, espn_soccer, espn_tennis, lichess_chess, liquipedia, liquipedia_esports, pandascore
from sports_follow.adapters import pandascore_esports, sportmonks, sportmonks_cricket, sportmonks_football
from sports_follow.adapters.base import Adapter

from . import test_liquipedia, test_pandascore, test_sportmonks, test_sportmonks_cricket

FIXTURES = Path(__file__).parent / "fixtures"
CARLSEN = "1503014"
ALCARAZ = "3782"


def load(name: str) -> Any:
    with gzip.open(FIXTURES / f"{name}.json.gz", "rt") as f:
        return json.load(f)


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1_800_000_000.0}
    monkeypatch.setattr(time, "time", lambda: now["t"])
    return now


class Source:
    """A source's client, behind its cache: what the source says now (answer(*request)), and how often it was asked."""

    def __init__(self, answer: Callable[..., Any]) -> None:
        self.answer, self.asked = answer, 0

    def __call__(self, *request: Any) -> Any:
        self.asked += 1
        return copy.deepcopy(self.answer(*request))


def in_play(summary: dict[str, Any]) -> dict[str, Any]:
    """An ESPN summary as it was during the game."""
    doc = copy.deepcopy(summary)
    doc["header"]["competitions"][0]["status"]["type"] = {"state": "in", "name": "STATUS_IN_PROGRESS", "completed": False, "shortDetail": "In Progress"}
    return doc


def _tennis() -> tuple[dict[str, Any], dict[str, Any], str]:
    board = load("espn/tennis-scoreboard-usopen")
    qf = next(f for f in espn_tennis.parse_scoreboard(board, "atp", "20260903") if ALCARAZ in f.team_ids and f.detail == "Quarterfinal")
    live = copy.deepcopy(board)
    _, comp = espn_tennis.find_match(live, qf.source_id)  # type: ignore[misc]
    comp["status"]["type"] = {"state": "in", "name": "STATUS_IN_PROGRESS", "completed": False}
    return live, board, qf.source_id


def _lichess() -> tuple[dict[str, Any], dict[str, Any]]:
    final = load("lichess/round-final")
    live = {**final, "round": {**final["round"], "finished": False, "ongoing": True}, "games": [{**g, "status": "*"} for g in final["games"]]}
    return live, final


def _sportmonks_football() -> tuple[dict[str, Any], dict[str, Any]]:
    sm = test_sportmonks
    mid = copy.deepcopy(sm.LINEUPS)
    mid[0]["details"] = [sm._detail("GOALS", 1), sm._detail("MINUTES_PLAYED", 60)]
    return {"data": sm._match("INPLAY_2ND_HALF", 1, 0, lineups=mid)}, {"data": sm._match("FT", 3, 0, lineups=sm.LINEUPS)}


def _sportmonks_cricket() -> tuple[dict[str, Any], dict[str, Any]]:
    sc = test_sportmonks_cricket
    return {"data": sc._match("2nd Innings", [sc.FIRST])}, {"data": sc._match("Finished", [sc.FIRST, sc.CHASE], note="India won by 127 runs", winner_team_id=sc.IND)}


def _cases() -> list[Any]:
    tennis_live, tennis_final, tennis_match = _tennis()
    chess_live, chess_final = _lichess()
    football_live, football_final = _sportmonks_football()
    cricket_live, cricket_final = _sportmonks_cricket()
    soccer, basketball = load("espn/soccer-summary-final"), load("espn/basketball-summary-final")
    lobby_start = f"{datetime.now(timezone.utc) - timedelta(hours=1):%Y-%m-%d %H:%M:%S}"  # on now, by the clock Liquipedia's parser reads
    return [
        pytest.param(espn, espn_cricket.EspnCricket, {"league": "8048", "event": "1"}, load("espn/cricket-summary-live"), load("espn/cricket-summary-final"), id="espn_cricket"),
        pytest.param(espn, espn_soccer.EspnSoccer, {"league": "eng.1", "event": "1"}, in_play(soccer), soccer, id="espn_soccer"),
        pytest.param(espn, espn_basketball.EspnBasketball, {"league": "wnba", "event": "1"}, in_play(basketball), basketball, id="espn_basketball"),
        # Not on the current scoreboard any more (the tournament has moved on): read from the day it was found on.
        pytest.param(espn, espn_tennis.EspnTennis, {"tour": "atp", "match": tennis_match, "day": "20260903"}, tennis_live, tennis_final, id="espn_tennis"),
        pytest.param(lichess_chess, lichess_chess.LichessChess, {"round": chess_final["round"]["id"], "fide": CARLSEN}, chess_live, chess_final, id="lichess_chess"),
        pytest.param(sportmonks, sportmonks_football.SportmonksFootball, {"fixture": "555"}, football_live, football_final, id="sportmonks_football"),
        pytest.param(sportmonks, sportmonks_cricket.SportmonksCricket, {"fixture": "71215"}, cricket_live, cricket_final, id="sportmonks_cricket"),
        pytest.param(pandascore, pandascore_esports.PandascoreEsports, {"match": "777"}, test_pandascore.LIVE, test_pandascore.FINAL, id="pandascore_esports"),
        pytest.param(liquipedia, liquipedia_esports.LiquipediaEsports, {"wiki": test_liquipedia.WIKI, "match": "aBcDeFgHiJ_0003"}, [test_liquipedia._lobby(2, date=lobby_start)],
                     [test_liquipedia._lobby(3, finished=True, date=lobby_start)], id="liquipedia_esports"),
    ]


@pytest.mark.parametrize(("client", "make", "locator", "live", "final"), _cases())
def test_a_final_read_after_a_mid_game_copy_gets_the_final_board(monkeypatch, clock, client, make: Callable[[], Adapter], locator, live, final):
    # A fan watches the game live (its poll's copy, kept a day), and after full time the player's refresh, the
    # history lane or a preview reads its scorecard: the result, not the board as it was in play.
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: {})  # Lichess keeps a live board's moves there
    now = {"doc": live}

    def answer(*request: Any) -> Any:
        current_board = make is espn_tennis.EspnTennis and "dates=" not in str(request[0])
        return {"events": []} if current_board else now["doc"]

    source = Source(answer)
    monkeypatch.setattr(client, "_fetch", source)
    assert make().snapshot(locator).status == "live"
    clock["t"] += 1800  # the game ends; the source now says so
    now["doc"] = final
    asked = source.asked
    snap = make().snapshot(locator, final=True)
    assert snap.status == "final", f"the final read took the copy from the game: {snap.status} {snap.score_label}"
    assert make().snapshot(locator, final=True).status == "final"
    assert source.asked - asked == (2 if make is espn_tennis.EspnTennis else 1)  # read again once (and the current scoreboard), then kept


def test_a_squad_checks_copy_from_before_the_match_isnt_its_scorecard(monkeypatch, clock):
    # A refresh before the match reads its summary for the squad (kept a day); the match is played, and its
    # scorecard is read (a preview, the history lane): the result, with the player's line.
    now = {"doc": load("espn/cricket-summary-squads")}
    source = Source(lambda url: now["doc"])
    monkeypatch.setattr(espn, "_fetch", source)
    cricket, locator = espn_cricket.EspnCricket(), {"league": "8048", "event": "1"}
    cricket._summary(locator, final=False)
    clock["t"] += 6 * 3600
    now["doc"] = load("espn/cricket-summary-final")
    snap = cricket.snapshot(locator, final=True)
    assert snap.status == "final" and snap.lines and source.asked == 2


def test_a_final_read_with_a_finished_copy_asks_nothing(monkeypatch, clock):
    # The usual case stays one request a day: a copy already over is taken, however it was read.
    source = Source(lambda url: load("espn/soccer-summary-final"))
    monkeypatch.setattr(espn, "_fetch", source)
    locator = {"league": "eng.1", "event": "1"}
    assert espn_soccer.EspnSoccer().snapshot(locator).status == "final"
    clock["t"] += 6 * 3600
    assert espn_soccer.EspnSoccer().snapshot(locator, final=True).status == "final" and source.asked == 1


def test_a_copy_just_read_in_play_isnt_read_again(monkeypatch, clock):
    # A final read of a match the source still has in play (its listing ran ahead) costs what a live poll does:
    # a copy younger than a live read's ttl is taken, not fetched again.
    source = Source(lambda url: in_play(load("espn/soccer-summary-final")))
    monkeypatch.setattr(espn, "_fetch", source)
    locator = {"league": "eng.1", "event": "1"}
    espn_soccer.EspnSoccer().snapshot(locator)
    clock["t"] += 2
    assert espn_soccer.EspnSoccer().snapshot(locator, final=True).status == "live" and source.asked == 1
