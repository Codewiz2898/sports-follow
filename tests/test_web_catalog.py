"""The sports as the web app knows them (web/src/sports/catalog.ts): a sport switched on or off in /admin reached
open apps late, on the third load after (the copy kept on the device was used, and the fresh answer only kept for
next time) and never in an app left open, so search went on offering a sport that was off (BUG-166). The server's
answer is now applied as it comes, the app told so it draws again, the HTTP cache is skipped, and an app coming
back to the front asks again, at most every ten minutes. Run with Node (22.6 or later, which reads TypeScript);
skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

CATALOG = Path(__file__).resolve().parent.parent / "web" / "src" / "sports" / "catalog.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const sport = (key, on) => ({ key, label: key, on, live: on, pattern: key, exclude: '', group: '', stats_title: 'Season', team_results: false,
  keeps_names: false, colour: null, alerts: { key: ['', ''], more: ['', ''] } })
const catalog = (football, chess) => ({ sports: [sport('football', football), sport('cricket', true), sport('chess', chess)], other: sport('other', true), groups: [] })
// The device's kept copy says football is on and chess off; the server, since, the other way round.
const store = new Map([['sf-sports', JSON.stringify(catalog(true, false))]])
globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => { store.set(k, v) } }
let server = catalog(false, true)
const asked = []
globalThis.fetch = async (url, init) => { asked.push(init?.cache ?? 'default'); return new Response(JSON.stringify(server), { status: 200 }) }
let now = 0
Date.now = () => now
const on = {}
globalThis.document = { visibilityState: 'visible', addEventListener: (type, f) => { on[type] = f } }
const settle = () => new Promise((done) => setTimeout(done, 20))

const c = await import(%(module)s)
let told = 0
c.onSports?.(() => { told++ })
const running = () => c.runningSports().map((s) => s.key)
await c.loadSports()
const first = running()  // the kept copy, at once: nothing waits on the network
await settle()
const fresh = running()
const toldFresh = told
// Back to the app a minute later: not asked again. Eleven minutes later, chess switched off again: asked.
server = catalog(false, false)
now = 60_000; on.visibilitychange?.(); await settle()
const soon = [running(), asked.length]
now = 11 * 60_000; on.visibilitychange?.(); await settle()
const later = [running(), asked.length, told]
console.log(JSON.stringify({ first, fresh, toldFresh, soon, later, asked, kept: JSON.parse(store.get('sf-sports')).sports.filter((s) => s.on).map((s) => s.key) }))
"""


def run(tmp_path: Path) -> dict:
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(CATALOG.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_sport_switched_on_or_off_reaches_an_open_app_on_its_next_load(tmp_path):
    found = run(tmp_path)
    assert found["first"] == ["football", "cricket"]
    assert found["fresh"] == ["cricket", "chess"] and found["toldFresh"] == 1  # applied as it came, and the app told
    assert found["asked"][0] == "no-store"  # not a copy the browser kept for five minutes


def test_an_app_coming_back_to_the_front_asks_again_at_most_every_ten_minutes(tmp_path):
    found = run(tmp_path)
    assert found["soon"] == [["cricket", "chess"], 1]
    assert found["later"] == [["cricket"], 2, 2]
    assert found["kept"] == ["cricket"]  # and kept for the next start
