"""What a finished game's match page says of its result (web/src/match.ts, shared by the web and Android apps): a
cricket match's result is its board's line under the score, and the page said it twice more, as a four-line display
headline and again in Your players (BUG-088). Each is said once: the big verdict where the board's line doesn't
already say it ("Arsenal won" over 2-1 FT), and a fan's player's result where it's their own ("Won 2-1"). Run with
Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

MATCH = Path(__file__).resolve().parent.parent / "web" / "src" / "match.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const { pageResult, ownResult } = await import(%(module)s)
const said = 'Royal Challengers Bengaluru won by 5 wickets (with 12 balls remaining)'
const game = (clock, verdict, status = 'final') => ({ status, verdict, live: { is_live: false, score: '1-0', clock, state: null } })
const cricket = game(said, said)
const football = game('FT', 'Arsenal won')
console.log(JSON.stringify({
  verdicts: [pageResult(cricket), pageResult(football), pageResult(game('FT', 'Arsenal won', 'live')), pageResult({ ...football, live: null })],
  yours: [ownResult(said, cricket), ownResult('Won 2-1', football), ownResult('Arsenal won', football), ownResult(null, football)],
}))
"""


def run(tmp_path: Path) -> dict:
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(MATCH.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_finished_games_result_is_said_once(tmp_path):
    found = run(tmp_path)
    # Cricket's is the board's line; football's isn't on its board ("FT"); none while it's on; a game read before
    # its board came (the league card's words) has it.
    assert found["verdicts"] == ["", "Arsenal won", "", "Arsenal won"]
    # A fan's player's result: none where it's the board's line or the verdict again; theirs where it's their own.
    assert found["yours"] == [None, "Won 2-1", None, None]
