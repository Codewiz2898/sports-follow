"""What an open app takes from the server when it catches up (web/src/resync.ts, shared by the web and Android apps):
an app signed out elsewhere starts over as who it is now (BUG-162), and the fan's list as the server sends it is the
list, a player followed or dropped on another device coming or going, but for a level being changed here (BUG-165).
A failed ask of who the device is (offline, a deploy's restart) leaves it as it was, or unknown, never signed out,
and it's asked again until the server answers: a signed-in app that started offline showed "Sign in", then took the
account's answer for a change of hands, reloaded and forgot its markers (BUG-306, BUG-281).
Run with Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_refetch import NODE

ROOT = Path(__file__).resolve().parent.parent
RESYNC = ROOT / "web" / "src" / "resync.ts"
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

# Who the device is, asked as both apps ask it (whoAsker), with the clock and the server faked: `heard` is what the
# apps do with an answer, starting over only when it's someone else now than the app has shown (changedHands).
WHO = """
import { askAgainIn, changedHands, whoAsker } from %(module)s

const clock = { t: 0, timers: [] }
globalThis.setTimeout = (run, ms) => { const x = { at: clock.t + ms, run }; clock.timers.push(x); return x }
globalThis.clearTimeout = (x) => { clock.timers = clock.timers.filter((y) => y !== x) }
const settle = () => new Promise((done) => setImmediate(done))
const until = async (t) => { for (;;) { const x = clock.timers.filter((y) => y.at <= t).sort((a, b) => a.at - b.at)[0]
  if (!x) break; clock.timers = clock.timers.filter((y) => y !== x); clock.t = x.at; x.run(); await settle() } clock.t = t; await settle() }

const sana = { signed_in: true, email: 'sana@example.com', google: false, created_at: '2026-10-13T00:00:00+00:00' }
const out = { signed_in: false, reason: 'signed_out' }
let server = 'down'  // what the server answers: an account, or nothing (a deploy's restart, offline)
const asked = []
let held = null  // an answer held back, to come later
const ask = () => { asked.push(clock.t); if (held) return held.promise
  return server === 'down' ? Promise.reject(new TypeError('Failed to fetch')) : Promise.resolve(server) }
let shown = null  // who the app shows: null till it knows
const said = []
const heard = (now) => { if (changedHands(shown, now)) said.push('start over'); else { shown = now; said.push(now.signed_in ? now.email : 'signed out') } }
const who = whoAsker(ask, heard, (n) => [2000, 4000, 8000][n] ?? 60000)

await who.ask()  // the app starts as the API restarts in a deploy: the call fails
const start = { shown, said: [...said] }
await until(3000)  // asked again at 2 s, still down
server = sana
await until(5000)  // not yet: the next ask is at 6 s
const before = asked.length
await until(6000)  // asked again: the account
const back = { shown: shown?.email ?? null, said: [...said], asked: [...asked] }
// Signed in and shown, a recheck that fails changes nothing; the ask after it is the server's word.
server = 'down'
who.ask()
await until(7000)
const kept = shown?.email ?? null
server = out
who.ask()  // the app woke before the timer: asked at once, the timer dropped
await settle()
const woke = { said: said.slice(-1)[0], asked: asked.length, timers: clock.timers.length }
// An answer under way when a fresh ask is made (a sign-in or out on the phone) is dropped: the fresh one is the word.
let release
held = { promise: new Promise((r) => { release = r }) }
shown = null
who.ask()
held = null
server = sana
await who.ask(true)
release(out)
await settle()
const fresh = { said: said.slice(-2), shown: shown?.email ?? null }
who.stop()
server = 'down'
const stopped = { asks: asked.length, timers: clock.timers.length }
console.log(JSON.stringify({ start, before, back, kept, woke, fresh, stopped,
  waits: [0, 1, 2, 3, 10].map((n) => [askAgainIn(n, () => 0), askAgainIn(n, () => 1)]) }))
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


def who() -> dict:
    script = WHO % {"module": json.dumps(RESYNC.as_uri()) + ";"}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_failed_ask_of_who_the_device_is_leaves_it_unknown_and_asks_again():
    """BUG-306: an app started offline, or in a deploy's restart, took the failure for "signed out", showed "Sign in",
    and when the account came back took it for a change of hands: it reloaded and forgot its markers."""
    found = who()
    assert found["start"] == {"shown": None, "said": []}  # unknown, not signed out
    assert found["before"] == 2  # at start and at 2 s, not again till 6 s
    assert found["back"] == {"shown": "sana@example.com", "said": ["sana@example.com"], "asked": [0, 2000, 6000]}  # no start over
    assert found["kept"] == "sana@example.com"  # a failed recheck changes nothing
    # Waking asks at once, the timer dropped; signed out elsewhere, the answer starts the app over.
    assert found["woke"] == {"said": "start over", "asked": 5, "timers": 0}
    assert found["fresh"] == {"said": ["start over", "sana@example.com"], "shown": "sana@example.com"}  # the answer from before dropped
    assert found["stopped"] == {"asks": 7, "timers": 0}
    # 2 s, doubling to a minute, somewhere in the second half of each, so a restart isn't met by every app at once.
    assert found["waits"] == [[1000, 2000], [2000, 4000], [4000, 8000], [8000, 16000], [30000, 60000]]


def test_both_apps_ask_who_they_are_till_the_server_says_and_never_make_up_a_sign_out():
    for path in ("web/src/account.tsx", "mobile/src/account.tsx"):
        text = (ROOT / path).read_text()
        assert "whoAsker(" in text, path
        assert "{ signed_in: false }" not in text, path  # a failed call isn't an answer (BUG-306, BUG-281)
