"""What an open app takes from the server when it catches up (web/src/resync.ts, shared by the web and Android apps):
an app signed out elsewhere starts over as who it is now (BUG-162), and the fan's list as the server sends it is the
list, a player followed or dropped on another device coming or going, but for a level being changed here (BUG-165).
Run with Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_refetch import NODE

RESYNC = Path(__file__).resolve().parent.parent / "web" / "src" / "resync.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { changedHands, listed } from %(module)s

const sana = { signed_in: true, email: 'sana@example.com', google: false, created_at: '2026-10-13T00:00:00+00:00' }
const kiran = { ...sana, email: 'kiran@example.com' }
const out = { signed_in: false, reason: 'signed_out' }
const nobody = { signed_in: false }
const hands = [[sana, out], [sana, nobody], [sana, sana], [sana, kiran], [nobody, sana], [out, nobody], [nobody, nobody], [null, sana]]
  .map(([shown, now]) => changedHands(shown, now))

const card = (id, version, alerts) => ({ player_id: id, version, alerts, status: 'ready', name: `Player ${id}` })
const shown = [card(1, 4, 'key'), card(2, 9, 'off'), card(3, 1, 'everything')]
// The server's list: 2 dropped elsewhere, 4 followed elsewhere, 1's level changed there, 3's card older than shown.
const server = [card(1, 4, 'results'), card(3, 0, 'key'), card(4, 2, 'key')]
const plain = listed(server, shown, () => false)
const changingThree = listed(server, shown, (id) => id === 3)

console.log(JSON.stringify({ hands, plain: plain.map((c) => [c.player_id, c.version, c.alerts]), changingThree: changingThree.map((c) => [c.player_id, c.alerts]) }))
"""


def run() -> dict:
    script = SCRIPT % {"module": json.dumps(RESYNC.as_uri()) + ";"}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_an_app_signed_out_elsewhere_or_in_as_someone_else_starts_over():
    # Signed out (said so, or the cookie already gone), still the same, another account, signed in in another tab;
    # nobody both times; and before the first answer.
    assert run()["hands"] == [True, True, False, True, True, False, False, False]


def test_the_servers_list_is_the_list_but_for_a_level_being_changed_here():
    found = run()
    # 2 goes, 4 comes; 1 has the level set elsewhere; 3 keeps its newer card as shown, with the server's level.
    assert found["plain"] == [[1, 4, "results"], [3, 1, "key"], [4, 2, "key"]]
    assert found["changingThree"] == [[1, "results"], [3, "everything"], [4, "key"]]
