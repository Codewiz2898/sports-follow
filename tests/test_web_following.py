"""The fan's lists as the apps keep them (web/src/resync.ts, shared by the web and Android apps): a follow's answer
(POST /api/follows) carries the player's card but not the fan's level for them, so Retry on a failed page, which
follows again, showed "Key moments" and filtered the in-app toasts at key after the fan had chosen another; the
level they chose stays (BUG-170). Run with Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

RESYNC = Path(__file__).resolve().parent.parent / "web" / "src" / "resync.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const { withFollowed } = await import(%(module)s)
const card = (id, version, alerts) => ({ player_id: id, version, status: 'ready', name: `Player ${id}`, ...(alerts ? { alerts } : {}) })
const shown = [card(1, 4, 'off'), card(2, 9, 'everything')]
const take = (cards) => cards.map((c) => [c.player_id, c.version, c.alerts ?? null])
console.log(JSON.stringify({
  again: take(withFollowed(shown, card(1, 5))),  // Retry on 1's failed page: its new card, no level in it
  fresh: take(withFollowed(shown, card(3, 1))),  // a new follow: added, at the server's default
  said: take(withFollowed(shown, card(2, 10, 'results'))),  // an answer that says the level: the server's word
}))
"""


def run(tmp_path: Path) -> dict:
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(RESYNC.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_following_again_keeps_the_level_the_fan_chose(tmp_path):
    found = run(tmp_path)
    assert found["again"] == [[1, 5, "off"], [2, 9, "everything"]]
    assert found["fresh"] == [[1, 4, "off"], [2, 9, "everything"], [3, 1, None]]
    assert found["said"] == [[1, 4, "off"], [2, 10, "results"]]
