"""What the Android app's shared lists show as the server changes under them (mobile/src/following.tsx,
mobile/src/activity.tsx).

A player's alert level tapped while the 30 s re-read of the follow list was on its way flipped back on screen: the
answer, read before the level was saved, replaced the cards whole. Team levels and follows were kept from it, a
player's level wasn't (BUG-280). Now, as on the web (resync.listed), a level being changed stays as tapped, and a
re-read that overlapped the save is dropped, as one that overlapped a follow is.

A game muted from its notification's button (native code, the app away) or on another device still said "Mute this
game" in the app, and Unmute was out of reach till a restart: the muted games were read only when the follow list
changed (BUG-279). Now they're read again on coming back to the front, and with the activity list after news; a read
that overlapped a mute or unmute tapped here is dropped.

The providers' own lines run here, taken from the two files (all but what they draw), with React, React Native and
the API faked. Run with Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

# A clock moved on by hand, React Native's AppState, and just enough of React for a provider or a hook: state, refs,
# and callbacks and memos kept while their deps are the same; effects run after a render when theirs changed (an
# effect's cleanup first). A state that changed renders again as promises settle (`settle`). Shared by the Android
# tests that run a provider or a hook (test_mobile_account.py).
REACT = """
const clock = { t: 0, timers: [] }
globalThis.setTimeout = (run, ms = 0) => { const x = { at: clock.t + ms, run }; clock.timers.push(x); return x }
globalThis.setInterval = (run, ms) => { const x = { at: clock.t + ms, run, every: ms }; clock.timers.push(x); return x }
globalThis.clearTimeout = globalThis.clearInterval = (x) => { clock.timers = clock.timers.filter((y) => y !== x) }
Date.now = () => clock.t

const AppState = { currentState: 'active', listeners: new Set(),
  addEventListener(_, f) { this.listeners.add(f); return { remove: () => this.listeners.delete(f) } },
  set(state) { this.currentState = state; for (const f of [...this.listeners]) f(state) } }

const mounted = new Set()
const same = (a, b) => Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((d, j) => Object.is(d, b[j]))
const mount = (hook, env, props = {}) => {
  const slots = []
  let i = 0
  let effects = []
  const m = { dirty: false, out: undefined }
  const react = {
    useState(v) {
      const k = i++
      if (!slots[k]) {
        const s = (slots[k] = { v: typeof v === 'function' ? v() : v })
        s.set = (x) => { const n = typeof x === 'function' ? x(s.v) : x; if (!Object.is(n, s.v)) { s.v = n; m.dirty = true } }
      }
      return [slots[k].v, slots[k].set]
    },
    useRef(v) { const k = i++; return (slots[k] ??= { current: v }) },
    useEffect(run, deps) {
      const k = i++
      const was = slots[k]
      if (was && same(deps, was.deps)) return
      const s = (slots[k] = { effect: true, deps, cleanup: was?.cleanup })
      effects.push(() => { s.cleanup?.(); s.cleanup = run() })
    },
    useCallback(f, deps) { const k = i++; if (!slots[k] || !same(deps, slots[k].deps)) slots[k] = { v: f, deps }; return slots[k].v },
    useMemo(f, deps) { const k = i++; if (!slots[k] || !same(deps, slots[k].deps)) slots[k] = { v: f(), deps }; return slots[k].v },
    createContext: () => ({}),
  }
  m.render = () => { i = 0; effects = []; m.dirty = false; m.out = hook(props, { AppState, ...env, ...react }); for (const run of effects) run() }
  m.unmount = () => { mounted.delete(m); for (const s of slots) if (s?.effect) s.cleanup?.() }
  mounted.add(m)
  m.render()
  return m
}
const settle = async () => {
  for (let n = 0; n < 30; n++) {
    await new Promise((done) => setImmediate(done))
    for (const m of mounted) while (m.dirty) m.render()
  }
}
const until = async (t) => {
  for (;;) {
    const x = clock.timers.filter((y) => y.at <= t).sort((a, b) => a.at - b.at)[0]
    if (!x) break
    clock.t = x.at
    if (x.every) x.at += x.every; else clock.timers = clock.timers.filter((y) => y !== x)
    x.run()
    await settle()
  }
  clock.t = t
  await settle()
}
const reset = () => { for (const m of [...mounted]) m.unmount(); clock.t = 0; clock.timers = []; AppState.currentState = 'active'; AppState.listeners.clear() }
const held = () => { const h = {}; h.promise = new Promise((done, fail) => { h.done = done; h.fail = fail }); return h }
"""


def provider(path: str, first: str, name: str) -> str:
    """A provider's file from `first` (after its imports) to what it draws, nothing exported, its value returned."""
    text = (ROOT / path).read_text()
    start = text.index(first)
    end = text.index(f"  return <{name}Context", start)
    return text[start:end].replace("\nexport function ", "\nfunction ") + "  return value\n}\n"


MODULE = """
import { listed, withFollowed } from %(resync)s
import { followsKey } from %(alerts)s
import { ACTIVITY, refetcher } from %(refetch)s
export function following(props: any, { useAccount, useState, useRef, useEffect, useCallback, useMemo, createContext, api, AppState, useNotificationsArriving }: any) {
%(following)s
  return FollowingProvider(props)
}
export function activity(props: any, { useFollowing, useAccount, useState, useRef, useEffect, useCallback, useMemo, createContext, AsyncStorage, api, AppState, StannNotifications }: any) {
%(activity)s
  return ActivityProvider(props)
}
"""

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const { following, activity } = await import(%(module)s)
%(react)s

// A followed player's level, as the phone shows it, through a tap and the follow list's re-reads. The server keeps
// the level once its PUT is done; a re-read answers with what the server had when it was asked.
async function levels(scenario) {
  reset()
  const server = { level: 'key' }
  let holdList = false
  let heldList = null
  let put = null
  const api = {
    following: () => {
      const answer = { players: [{ player_id: 7, version: 1, status: 'ready', alerts: server.level }], teams: [] }
      if (!holdList) return Promise.resolve(answer)
      holdList = false
      heldList = held()
      return heldList.promise.then(() => answer)
    },
    matches: async () => ({ matches: [] }),
    mySports: async () => ({ sports: [] }),
    setAlerts: (_, level) => { put = held(); return put.promise.then(() => { server.level = level }) },
  }
  const env = { api, useAccount: () => ({ epoch: 0 }), useNotificationsArriving() {} }
  const phone = mount(following, env)
  await settle()
  const shown = () => phone.out.cards.find((c) => c.player_id === 7)?.alerts
  const seen = [shown()]
  if (scenario === 'during') {
    // The fan taps "Everything"; its PUT is slow, and the 30 s re-read comes and goes meanwhile.
    phone.out.setAlerts(7, 'everything').catch(() => {})
    await settle(); seen.push(shown())
    await until(30_000); seen.push(shown())
    put.done(); await settle(); seen.push(shown())
    await until(60_000); seen.push(shown())
  } else {
    // The 30 s re-read is asked, and answers only after the fan's tap was saved.
    holdList = true
    await until(30_000)
    phone.out.setAlerts(7, 'everything').catch(() => {})
    await settle(); put.done(); await settle(); seen.push(shown())
    heldList.done(); await settle(); seen.push(shown())
    await until(60_000); seen.push(shown())
  }
  return seen
}

// The games the fan muted, as the phone has them, while the server's change: a game muted from its notification's
// button with the app away, then one on another device with the app in front, then one tapped here whose PUT is slow
// while the phone comes back to the front.
async function mutes() {
  reset()
  const server = new Set()
  let put = null
  const api = {
    activity: async () => ({ items: [], next: null }),
    mutes: async () => ({ event_ids: [...server] }),
    mute: (id, on) => { put = held(); return put.promise.then(() => { if (on) server.add(id); else server.delete(id) }) },
  }
  const lists = { cards: [{ player_id: 7, alerts: 'key' }], loaded: true, heard: 0, matches: [], teams: [] }
  const env = { api, useFollowing: () => lists, useAccount: () => ({ epoch: 0 }), AsyncStorage: { getItem: async () => '1', setItem: async () => {} },
    StannNotifications: { takeDownLive() {} } }
  const phone = mount(activity, env)
  await settle()
  const muted = () => [...phone.out.muted].sort((a, b) => a - b)
  const seen = { first: muted() }
  // Muted from the notification while the app was away (straight to the server, from native code); back in front.
  await until(60_000); AppState.set('background')
  server.add(42)
  await until(120_000); AppState.set('active'); await settle()
  seen.back = muted()
  // Muted on the fan's laptop; the phone in front hears there's news (a followed game moved on), and reads the
  // activity list after its wait.
  server.add(43)
  await until(150_000); lists.heard++; phone.render(); await settle()
  await until(165_000)
  seen.elsewhere = muted()
  // Muted here, its PUT slow; the app goes away and comes back meanwhile: a read from before the mute isn't taken.
  phone.out.setMuted(44, true).catch(() => {})
  await settle()
  AppState.set('background'); AppState.set('active'); await settle()
  seen.during = muted()
  put.done(); await settle()
  await until(200_000); AppState.set('background'); AppState.set('active'); await settle()
  seen.after = muted()
  return seen
}

console.log(JSON.stringify({ during: await levels('during'), before: await levels('before'), mutes: await mutes() }))
"""


def run(tmp_path: Path) -> dict:
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    module = tmp_path / "providers.mts"
    module.write_text(MODULE % {
        "resync": json.dumps((ROOT / "web" / "src" / "resync.ts").as_uri()),
        "alerts": json.dumps((ROOT / "web" / "src" / "alerts.ts").as_uri()),
        "refetch": json.dumps((ROOT / "web" / "src" / "refetch.ts").as_uri()),
        "following": provider("mobile/src/following.tsx", "const EVERY_MS", "Following"),
        "activity": provider("mobile/src/activity.tsx", "const ActivityContext", "Activity"),
    })
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(module.as_uri()), "react": REACT}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture(scope="module")
def found(tmp_path_factory) -> dict:
    return run(tmp_path_factory.mktemp("lists"))


def test_a_level_tapped_while_the_list_is_read_again_stays_as_tapped(found):
    # Before the tap, after it, the 30 s re-read during its PUT, the PUT done, the next re-read. Was flipped back to
    # "key" by the re-read, and stayed there till the next one.
    assert found["during"] == ["key", "everything", "everything", "everything", "everything"]


def test_a_re_read_asked_before_a_level_was_saved_doesnt_undo_it(found):
    # Before; the tap saved; the re-read asked before it answers; the next re-read. Was flipped back by that answer.
    assert found["before"] == ["key", "everything", "everything", "everything"]


def test_a_game_muted_away_from_the_app_shows_as_muted(found):
    mutes = found["mutes"]
    assert mutes["first"] == []
    assert mutes["back"] == [42]  # muted from the notification, the app away: read again on coming back
    assert mutes["elsewhere"] == [42, 43]  # muted on another device: read with the activity list after news


def test_a_read_of_the_muted_games_from_before_a_mute_tapped_here_isnt_taken(found):
    mutes = found["mutes"]
    assert mutes["during"] == [42, 43, 44]  # shown at once, and not taken off by a read from before it was saved
    assert mutes["after"] == [42, 43, 44]
