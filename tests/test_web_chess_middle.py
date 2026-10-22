"""The big line between a chess board's players and the small one under it (web/src/sports/facts/chess.ts chessMiddle
and miniMatch, shared by the web and Android apps), on boards as the server reads them (lichess_chess.parse_board).
BUG-216: a mini-match's line was its score from the side of whichever player's fixture opened the page ("Match 2–6"
between Carlsen and Lazavik, with nothing saying whose 2). BHA-675: a mini-match's games are each a game of their own,
so the big line is the game's (its result, or its clock), and the match's score, by the leader's name, is the small one
under it ("Game 2 · Caruana leads 1–0"), not a big red "Match ½–½" that read as a finished draw. Run with Node (22.6 or
later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sports_follow.adapters import lichess_chess

FACTS = Path(__file__).resolve().parent.parent / "web" / "src" / "sports" / "facts" / "chess.ts"
FIXTURES = Path(__file__).parent / "fixtures"
CARLSEN, LAZAVIK = "1503014", "13515110"

SCRIPT = """
const { chessMiddle, miniMatch } = await import(%(module)s)
const boards = %(boards)s
console.log(JSON.stringify(boards.map(([state, clock]) => [chessMiddle(state, clock), miniMatch(state)])))
"""


def node() -> str | None:
    found = shutil.which("node")
    if found is None:
        return None
    ok = subprocess.run([found, "--experimental-strip-types", "-e", "0"], capture_output=True, timeout=30)
    return found if ok.returncode == 0 else None


NODE = node()
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")


def middles(boards: list[tuple[dict, str]]) -> list[list[str | None]]:
    script = SCRIPT % {"module": json.dumps(FACTS.as_uri()), "boards": json.dumps(boards)}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_mini_matchs_game_has_its_own_line_and_the_match_under_it_whichever_players_page_it_is():
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    # A World Championship's tiebreaks: Gukesh won games 1 and 2, game 3 is on with Sindarov White.
    gukesh, sindarov = {"fideId": 1, "name": "Gukesh D"}, {"fideId": 2, "name": "Sindarov, Javokhir"}
    tiebreak = {"round": {"id": "r1", "name": "Tiebreaks", "startsAt": 1, "ongoing": True}, "tour": {"name": "FIDE World Championship"},
                "games": [{"id": "g1", "players": [gukesh, sindarov], "status": "1-0", "fen": "8/8/8/8/8/8/8/8 w - - 0 40", "lastMove": "a1a2"},
                          {"id": "g2", "players": [sindarov, gukesh], "status": "0-1", "fen": "8/8/8/8/8/8/8/8 w - - 0 40", "lastMove": "a1a2"},
                          {"id": "g3", "players": [sindarov, gukesh], "status": "*", "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"}]}
    level = {**tiebreak, "games": [tiebreak["games"][0], {**tiebreak["games"][1], "status": "1-0"}, tiebreak["games"][2]]}
    # The recorded Esports World Cup grand final: Carlsen won it 6–2.
    with gzip.open(FIXTURES / "lichess/round-final.json.gz", "rt") as f:
        final = json.load(f)
    boards = []
    for data, fide, game in ((tiebreak, "1", 3), (tiebreak, "2", 3), (level, "1", 3), (level, "2", 2), (final, CARLSEN, 8), (final, LAZAVIK, 8)):
        snap = lichess_chess.parse_board(data, fide, now, game=game)
        boards.append((snap.state, snap.clock_label))
    single = lichess_chess.parse_board({**final, "games": [final["games"][0]]}, LAZAVIK, now)
    boards.append((single.state, single.clock_label))
    assert middles(boards) == [
        ["Move 1 · Black to play", "Game 3 · Gukesh D leads\u00a02–0"], ["Move 1 · Black to play", "Game 3 · Gukesh D leads\u00a02–0"],  # through either player's fixture (a long name may wrap, not the score)
        ["Move 1 · Black to play", "Game 3 · Level\u00a01–1"],
        ["1–0", "Game 2 · Level\u00a01–1"],  # a game over: its result, and the match as it leaves it
        ["1–0", "Game 8 · Magnus Carlsen leads\u00a06–2"], ["1–0", "Game 8 · Magnus Carlsen leads\u00a06–2"],
        ["1–0", None],  # one game: its result, White's first, and no match
    ]
