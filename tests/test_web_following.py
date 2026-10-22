"""The fan's lists as the apps keep them (web/src/resync.ts, shared by the web and Android apps): a follow's answer
(POST /api/follows) carries the player's card but not the fan's level for them, so Retry on a failed page, which
follows again, showed "Key moments" and filtered the in-app toasts at key after the fan had chosen another; the
level they chose stays (BUG-170). A level tapped fast, a player's, a team's or a game's, saves one at a time, the last
tap last, so the server ends on the level the fan last tapped (BUG-307). Run with Node (22.6 or later, which reads
TypeScript); skipped without it."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

RESYNC = Path(__file__).resolve().parent.parent / "web" / "src" / "resync.ts"
FOLLOWING = RESYNC.parent / "following.tsx"
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


# Taps on one level's control, with the server's PUTs answered when the script says (saveLevel): what's sent, what
# shows, and whether the level reads as being changed (a list from the server leaves it as shown meanwhile).
LEVELS = """
import { register } from 'node:module'
register(%(hook)s)
const { saveLevel } = await import(%(module)s)
const settle = () => new Promise((done) => setImmediate(done))
const puts = []  // each PUT sent: its level, and how it ends
const save = (level) => new Promise((ok, fail) => puts.push({ level, ok, fail: () => fail(new Error('No connection')) }))
const writes = new Map()
let shown = 'key'
const tap = (level) => saveLevel(writes, 7, level, () => shown, (l) => { shown = l }, save).then(() => 'ok', (e) => e.message)
const sent = () => puts.map((p) => p.level)

// Off, then Everything and Results while Off is on its way: Everything is never sent, Results goes once Off is in.
const off = tap('off')
await settle()
const everything = tap('everything')
const results = tap('results')
await settle()
const first = { sent: sent(), shown, changing: writes.has(7) }
puts[0].ok()
await settle()
const second = { sent: sent(), shown, changing: writes.has(7) }
puts[1].ok()
await settle()
const done = { sent: sent(), shown, changing: writes.has(7), answers: [await off, await everything, await results] }

// Everything, saved; then Off fails: back to Everything, the last one saved, and the caller hears why.
const kept = tap('everything')
await settle()
puts[2].ok()
const lost = tap('off')
await settle()
puts[3].fail()
await settle()
const failed = { shown, changing: writes.has(7), answers: [await kept, await lost] }
console.log(JSON.stringify({ first, second, done, failed }))
"""


def run(tmp_path: Path, script: str = SCRIPT) -> dict:
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = script % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(RESYNC.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_following_again_keeps_the_level_the_fan_chose(tmp_path):
    found = run(tmp_path)
    assert found["again"] == [[1, 5, "off"], [2, 9, "everything"]]
    assert found["fresh"] == [[1, 4, "off"], [2, 9, "everything"], [3, 1, None]]
    assert found["said"] == [[1, 4, "off"], [2, 10, "results"]]


def test_fast_taps_on_a_level_save_one_at_a_time_and_end_on_the_last(tmp_path):
    found = run(tmp_path, LEVELS)
    assert found["first"] == {"sent": ["off"], "shown": "results", "changing": True}  # the last tap shows at once
    assert found["second"] == {"sent": ["off", "results"], "shown": "results", "changing": True}  # Everything's replaced
    assert found["done"] == {"sent": ["off", "results"], "shown": "results", "changing": False, "answers": ["ok", "ok", "ok"]}
    assert found["failed"] == {"shown": "everything", "changing": False, "answers": ["ok", "No connection"]}


def test_a_teams_and_a_games_level_save_as_a_players_does():
    """Each PUT of a team's or a game's level went at once, and the server kept whichever committed last (BUG-307)."""
    source = FOLLOWING.read_text()
    for setter in ("setAlerts", "setTeamAlerts", "setMatchAlerts"):
        body = re.search(rf"const {setter} = useCallback\((.*?)\n\n", source, re.S)
        assert body and "saveLevel(" in body[1], setter
