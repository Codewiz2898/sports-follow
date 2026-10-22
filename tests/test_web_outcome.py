"""A result's win, draw or loss as the apps read it from its words (web/src/format.ts outcome, shared by the web and
Android apps), where the server didn't tell it: a team's results, a card from before. BUG-320: a forfeit or a walkover
wasn't played, so it's no W or L, as the server's own reading has it (sports/base.py outcome, BUG-142; tennis, BUG-141).
Run with Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE

FORMAT = Path(__file__).resolve().parent.parent / "web" / "src" / "format.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
const { outcome, resultOutcome } = await import(%(module)s)
const read = (result, title = '6666 vs Revise') => resultOutcome({ result, title }, ['6666'])
console.log(JSON.stringify({
  unplayed: ['Won by forfeit', 'Lost by forfeit', 'Won (w/o)', 'Lost (w/o)', 'Lost ', 'Won'].map((r) => read(r)),
  played: ['Won 2–1', 'Lost 0–2', 'Drew 1–1', 'Won 6-4 6-3', 'Lost 4-6 3-6 ret.', '6666 won by 8 wickets', 'Match drawn'].map((r) => read(r)),
  bare: outcome('Lost (w/o)'),
}))
"""


def test_a_forfeit_or_a_walkover_is_no_win_or_loss(tmp_path):
    script = SCRIPT % {"module": json.dumps(FORMAT.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)
    assert found["unplayed"] == [None] * 6  # by forfeit, a walkover, and one kept before walkovers were told apart ("Lost ")
    assert found["played"] == ["W", "L", "D", "W", "L", "W", "D"]
    assert found["bare"] is None
