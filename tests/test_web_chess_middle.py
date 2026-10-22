"""The big line between a chess board's players (web/src/sports/facts/chess.ts chessMiddle, shared by the web and
Android apps), on boards as the server reads them (lichess_chess.parse_board). BUG-216: a mini-match's line was its
score from the side of whichever player's fixture opened the page ("Match 2–6" between Carlsen and Lazavik, with
nothing saying whose 2), and turning the board moved the names, not the score. Run with Node (22.6 or later, which
reads TypeScript); skipped without it."""

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
const { chessMiddle } = await import(%(module)s)
const boards = %(boards)s
console.log(JSON.stringify(boards.map(([state, clock, on]) => chessMiddle(state, clock, on))))
"""


def node() -> str | None:
    found = shutil.which("node")
    if found is None:
        return None
    ok = subprocess.run([found, "--experimental-strip-types", "-e", "0"], capture_output=True, timeout=30)
    return found if ok.returncode == 0 else None


NODE = node()
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")


def middles(boards: list[tuple[dict, str, bool]]) -> list[str]:
    script = SCRIPT % {"module": json.dumps(FACTS.as_uri()), "boards": json.dumps(boards)}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_mini_matchs_line_names_who_leads_whichever_players_page_it_is():
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
    for data, fide in ((tiebreak, "1"), (tiebreak, "2"), (level, "1"), (level, "2"), (final, CARLSEN), (final, LAZAVIK)):
        snap = lichess_chess.parse_board(data, fide, now)
        boards.append((snap.state, snap.clock_label, snap.status == "live"))
    single = lichess_chess.parse_board({**final, "games": [final["games"][0]]}, LAZAVIK, now)
    boards.append((single.state, single.clock_label, False))
    assert middles(boards) == [
        "Gukesh D leads\u00a02–0", "Gukesh D leads\u00a02–0",  # through either player's fixture (a long name may wrap, not the score)
        "Match\u00a01–1", "Match\u00a01–1",
        "Match\u00a06–2", "Match\u00a06–2",  # over: the leader's points first, the note under it ("Magnus Carlsen won the match") names him
        "1–0",  # one game: its result, White's first
    ]
