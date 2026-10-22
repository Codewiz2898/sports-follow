"""Signing out or deleting the account on a device starts the app over once, saying so (web/src/account.tsx,
mobile/src/account.tsx). The server says on the account's channel that its sessions ended before it answers the
sign-out (BUG-162), so this tab's own stream heard it and the app asked who it was: signed out, it started over a
second time, as "signed out elsewhere", on the page it was on, and that notice was the one the fan was left with. On
Android an ask under way (the app back in front) did the same. While a sign-out or deletion of its own is under way,
an app takes no answer for a change of hands; if it fails, the app is as it was, and hears the server again.

The providers' own lines run here, taken from the two files (all but what they draw), with React, the browser or
React Native, and the API faked. Run with Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_refetch import NODE

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")


def provider_lines(path: str, first: str) -> str:
    """A provider's file from `first` (after its imports) to what it draws, its AccountProvider's body inline, and
    nothing exported: what runs as the app starts and as the fan signs out."""
    text = (ROOT / path).read_text()
    start = text.index(first)
    head = text.index("export function AccountProvider(", start)
    body = text.index("\n", head) + 1
    end = text.index("  const value = useMemo", body)
    return (text[start:head] + text[body:end]).replace("\nexport function ", "\nfunction ")


MODULE = """
import { changedHands, whoAsker } from %(resync)s
import { PHONE_KEPT, WEB_KEPT, welcome } from %(fan_change)s
export function web({ api, useState, useRef, useEffect, useCallback, createContext }: any) {
%(web)s
  return { signOut, deleteAccount, recheck }
}
export function phone({ api, useState, useRef, useEffect, useCallback, createContext, AsyncStorage, router, AppState, StannNotifications, forgetFeedSeen }: any) {
%(phone)s
  return { signOut, deleteAccount }
}
"""

SCRIPT = """
const { web, phone } = await import(%(module)s)
globalThis.setTimeout = () => 0  // no waits run here: the first start's longest wait, a failed ask's
globalThis.clearTimeout = () => {}
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((done) => setImmediate(done)) }

// Just enough of React for a provider: one render, its effects after it; state kept, read through `slots`.
const mount = (provider, env) => {
  const slots = []
  let i = 0
  const effects = []
  const react = {
    useState(v) { const k = i++; slots[k] = { v: typeof v === 'function' ? v() : v }; return [slots[k].v, (x) => { slots[k].v = typeof x === 'function' ? x(slots[k].v) : x }] },
    useRef(v) { i++; return { current: v } },
    useEffect(run) { i++; effects.push(run) },
    useCallback(f) { i++; return f },
    createContext: () => ({}),
  }
  const app = provider({ ...env, ...react })
  for (const run of effects) run()
  return { app, slots }
}

const sana = { signed_in: true, email: 'sana@example.com', google: false, created_at: '2026-10-13T00:00:00+00:00' }
const out = { signed_in: false, reason: 'signed_out' }
const held = () => { const h = {}; h.promise = new Promise((done, fail) => { h.done = done; h.fail = fail }); return h }

// The browser: the page the app is on, its storage, and each start over (where to, saying what).
const storage = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) } }
let replaced = []
globalThis.sessionStorage = storage()
globalThis.localStorage = storage()
globalThis.document = { visibilityState: 'visible', addEventListener() {}, removeEventListener() {} }
globalThis.window = { location: { pathname: '/player/14', search: '', replace: (to) => replaced.push([to, sessionStorage.getItem('sf-account-notice')]) } }

async function onTheWeb(action, fails) {
  replaced = []
  sessionStorage.removeItem('sf-account-notice')
  let answer = sana
  const leaving = held()
  const api = { account: async () => answer, config: async () => ({}), signOut: () => leaving.promise, deleteAccount: () => leaving.promise }
  const { app } = mount(web, { api })
  await settle()
  const done = app[action]().catch(() => 'failed')
  await settle()
  // The server ends the session and says so before it answers: this tab's stream says "signed out"
  // (following.tsx asks again at once), and the account reads as signed out.
  if (!fails) answer = out
  app.recheck(true)
  await settle()
  if (fails) leaving.fail(new Error('Request failed (502)'))
  else leaving.done({ signed_in: false })
  const said = await done
  await settle()
  const during = [...replaced]
  if (fails) {  // still signed in; signed out elsewhere later, it starts over as that
    answer = out
    app.recheck(true)
    await settle()
  }
  return { said: said ?? null, during, after: replaced }
}

async function onThePhone(action) {
  let answer = sana
  const leaving = held()
  const routed = []
  const AppState = { listeners: new Set(), addEventListener(_, f) { this.listeners.add(f); return { remove: () => this.listeners.delete(f) } },
    set(state) { for (const f of [...this.listeners]) f(state) } }
  const api = { start: async () => answer, account: async () => answer, config: async () => ({}), signOut: () => leaving.promise, deleteAccount: () => leaving.promise }
  const env = { api, AppState, AsyncStorage: { getItem: async () => '1', setItem: async () => {}, multiRemove: async () => {} },
    router: { replace: (to) => routed.push(to) }, StannNotifications: { takeDownLive() {} }, forgetFeedSeen() {} }
  const { app, slots } = mount(phone, env)
  await settle()
  const done = app[action]()
  await settle()
  // The server has ended the session; the app comes back to the front as it waits, and asks who it is.
  answer = out
  AppState.set('active')
  await settle()
  leaving.done({ signed_in: false })
  await done
  await settle()
  return { routed, notice: slots.find((s) => typeof s?.v === 'string')?.v ?? null }  // the one state that's words
}

console.log(JSON.stringify({
  webOut: await onTheWeb('signOut', false),
  webDeleted: await onTheWeb('deleteAccount', false),
  webFailed: await onTheWeb('signOut', true),
  phoneOut: await onThePhone('signOut'),
  phoneDeleted: await onThePhone('deleteAccount'),
}))
"""


def run(tmp_path: Path) -> dict:
    module = tmp_path / "providers.ts"
    module.write_text(MODULE % {
        "resync": json.dumps((ROOT / "web" / "src" / "resync.ts").as_uri()),
        "fan_change": json.dumps((ROOT / "web" / "src" / "fanChange.ts").as_uri()),
        "web": provider_lines("web/src/account.tsx", "const AccountContext"),
        "phone": provider_lines("mobile/src/account.tsx", "const HAS_FAN"),
    })
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", SCRIPT % {"module": json.dumps(module.as_uri())}],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_signing_out_or_deleting_on_the_web_starts_over_once_saying_so(tmp_path):
    found = run(tmp_path)
    assert found["webOut"] == {"said": None, "during": [["/", "Signed out. Your players are kept in your account."]], "after": [["/", "Signed out. Your players are kept in your account."]]}
    assert found["webDeleted"]["after"] == [["/", "Your account and everything kept with it are deleted."]]
    # A sign-out that failed leaves the app as it was, and the server's word is heard again after it.
    assert found["webFailed"] == {"said": "failed", "during": [], "after": [["/player/14", "This device was signed out. Sign in again to see your players."]]}


def test_signing_out_or_deleting_on_the_phone_starts_over_once_saying_so(tmp_path):
    found = run(tmp_path)
    assert found["phoneOut"] == {"routed": ["/"], "notice": "Signed out. Your players are kept in your account."}
    assert found["phoneDeleted"] == {"routed": ["/"], "notice": "Your account and everything kept with it are deleted."}
