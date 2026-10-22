"""Coming back to the front asks once for the activity list and once for This week on Android (mobile/src/activity.tsx,
mobile/src/today/week.tsx). Their asks after news wait a while of their own (web/src/refetch.ts), and React Native holds
an app's timers while it's away: a wait that a notification started then ran as the app woke, and the news that coming
back brings (following.tsx's heard) started another, so each asked twice (BUG-265, round two).

The hooks' own lines run here, taken from the two files, with React, React Native's AppState, the API and the clock
faked. Run with Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_refetch import NODE

ROOT = Path(__file__).resolve().parent.parent
REFETCH = ROOT / "web" / "src" / "refetch.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")


def activity_lines() -> str:
    """ActivityProvider's asks after news: from its refetcher's ref to the effect that hears news."""
    text = (ROOT / "mobile" / "src" / "activity.tsx").read_text()
    start = text.index("  const later = useRef<Refetcher | null>(null)")
    end = text.index("\n", text.index("later.current?.heard()", start))
    return text[start:end]


def week_lines() -> str:
    """useWeek's body."""
    text = (ROOT / "mobile" / "src" / "today" / "week.tsx").read_text()
    start = text.index("{\n", text.index("export function useWeek(): Week")) + 2
    return text[start:text.index("\n}\n", start)]


# The two hooks, each as a function of what it reads: the file's lines inside, the rest handed in.
HOOKS = """
import { ACTIVITY, WEEK, refetcher } from %(refetch)s
export function activity({ epoch, heard }: any, { useRef, useEffect, AppState, api, setItems, readMutes }: any) {
%(activity)s
}
export function week(_: any, { useRef, useEffect, useState, AppState, api, useFollowing, NONE }: any) {
%(week)s
}
"""

SCRIPT = """
// The clock: timers run as it's moved on, but not while the app is away (React Native holds them till it's back).
const clock = { t: 0, timers: [] }
globalThis.setTimeout = (run, ms) => { const x = { at: clock.t + ms, run }; clock.timers.push(x); return x }
globalThis.clearTimeout = (x) => { clock.timers = clock.timers.filter((y) => y !== x) }
Date.now = () => clock.t
const due = () => { for (;;) { const x = clock.timers.filter((y) => y.at <= clock.t).sort((a, b) => a.at - b.at)[0]; if (!x) return
  clock.timers = clock.timers.filter((y) => y !== x); x.run() } }
const until = (t) => { for (;;) { const x = AppState.currentState === 'active' && clock.timers.filter((y) => y.at <= t).sort((a, b) => a.at - b.at)[0]
  if (!x) break; clock.timers = clock.timers.filter((y) => y !== x); clock.t = x.at; x.run() } clock.t = t }

// React Native's AppState: its state is new before any listener hears of it.
const AppState = { currentState: 'active', listeners: new Set(),
  addEventListener(_, f) { this.listeners.add(f); return { remove: () => this.listeners.delete(f) } },
  set(state) { this.currentState = state; for (const f of [...this.listeners]) f(state) } }

// Just enough of React for a hook: refs, state, and effects that run when their deps change.
const mount = (hook, env) => {
  const slots = []
  let i = 0
  const react = {
    useRef(v) { const k = i++; return (slots[k] ??= { current: v }) },
    useState(v) { const k = i++; slots[k] ??= { v, set: (x) => { slots[k].v = typeof x === 'function' ? x(slots[k].v) : x } }; return [slots[k].v, slots[k].set] },
    useEffect(run, deps) { const k = i++; const was = slots[k]; if (was && deps.every((d, j) => d === was.deps[j])) return
      effects.push(() => { was?.cleanup?.(); slots[k] = { deps, cleanup: run() } }) },
  }
  let effects = []
  return (props) => { i = 0; effects = []; hook(props, { ...env, ...react, AppState }); effects.forEach((f) => f()) }
}

const { activity, week } = await import(%(hooks)s)
const MIN = 60_000

// One phone: Today open and in front, its lists in at 0; a notification at 30 s; put away at 4 minutes, while This
// week's wait for that notification still runs; another notification at 6 minutes, while it's away; back at 30
// minutes, the follow list read again a second later saying there's news. `woke`: which React Native runs first as the
// app comes back, the AppState change or the timers it held.
const run = (which, woke) => {
  clock.t = 0; clock.timers = []; AppState.currentState = 'active'; AppState.listeners.clear()
  const asks = []
  const api = { activity: () => { asks.push(clock.t); return Promise.resolve({ items: [], next: null }) },
    week: () => { asks.push(clock.t); return Promise.resolve({ posters: [], wire: [] }) } }
  const following = { cards: [{ player_id: 7 }], loaded: true, heard: 0 }
  const render = which === 'activity'
    ? ((r) => () => r({ epoch: 0, heard: following.heard }))(mount(activity, { api, setItems: () => {}, readMutes: () => {} }))
    : ((r) => () => r({}))(mount(week, { api, useFollowing: () => following, NONE: { posters: [], wire: [] } }))
  const heard = () => { following.heard++; render() }
  render()
  until(30_000); heard()
  until(4 * MIN); AppState.set('background')
  until(6 * MIN); heard()
  until(30 * MIN)
  if (woke === 'state') { AppState.set('active'); due() } else { due(); AppState.set('active') }
  until(30 * MIN + 1000); heard()
  until(60 * MIN)
  return { inFront: asks.filter((t) => t < 4 * MIN), back: asks.filter((t) => t >= 4 * MIN) }
}

console.log(JSON.stringify(Object.fromEntries(['activity', 'week'].flatMap((which) =>
  ['state', 'timers'].map((woke) => [`${which} ${woke}`, run(which, woke)])))))
"""


def run(tmp_path: Path) -> dict:
    hooks = tmp_path / "hooks.mts"
    hooks.write_text(HOOKS % {"refetch": json.dumps(REFETCH.as_uri()), "activity": activity_lines(), "week": week_lines()})
    script = SCRIPT % {"hooks": json.dumps(hooks.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.parametrize("woke", ["state", "timers"])
def test_coming_back_to_the_front_asks_for_the_activity_list_once(tmp_path, woke):
    found = run(tmp_path)[f"activity {woke}"]
    assert len(found["inFront"]) == 1  # the notification in front: one ask, 2 to 12 s later
    assert 32_000 <= found["inFront"][0] < 42_000
    # Back at 30 minutes: one ask, after coming back's news; none for the notification while away.
    assert len(found["back"]) == 1 and 30 * 60_000 + 1000 < found["back"][0] < 30 * 60_000 + 14_000


@pytest.mark.parametrize("woke", ["state", "timers"])
def test_coming_back_to_the_front_asks_for_this_week_once(tmp_path, woke):
    found = run(tmp_path)[f"week {woke}"]
    assert found["inFront"] == [0]  # the lists in; the notification at 30 s waits out the five minutes from that ask
    # Back at 30 minutes: one ask, after coming back's news; none for the notifications while away.
    assert len(found["back"]) == 1 and 30 * 60_000 + 1000 < found["back"][0] < 37 * 60_000
