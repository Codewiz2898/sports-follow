"""Who the Android app is as it starts (mobile/src/account.tsx), and what it does with that.

A phone signed out elsewhere while the app was closed (everywhere from the laptop, which takes the phone's devices with
its session, so no end comes for its live cards) said so as it started, but kept the old account's live card frozen
on the lock screen: only the check on coming back to the front took the cards down (BUG-278). Now a start that finds
the phone signed out takes them down too.

The provider's own lines run here, taken from its file (all but what it draws), with React, React Native and the API
faked. Run with Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_mobile_lists import REACT, provider
from .test_web_news_lines import NODE, RESOLVE

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")


MODULE = """
import { changedHands, whoAsker } from %(resync)s
import { PHONE_KEPT, welcome } from %(fan_change)s
export function account(props: any, { useState, useRef, useEffect, useCallback, useMemo, createContext, api, AppState, AsyncStorage, router, StannNotifications, forgetFeedSeen }: any) {
%(account)s
  return AccountProvider(props)
}
"""

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const { account } = await import(%(module)s)
%(react)s

const sana = { signed_in: true, email: 'sana@example.com', google: false, created_at: '2026-10-13T00:00:00+00:00' }
const out = { signed_in: false, reason: 'signed_out' }
const nobody = { signed_in: false }

// The phone: its storage, its screens' start over (where to), its live cards taken down (which: null for all).
const phone = (kept = {}) => {
  const storage = new Map(Object.entries(kept))
  const p = { storage, routed: [], takenDown: [] }
  p.env = {
    AsyncStorage: { getItem: async (k) => storage.get(k) ?? null, setItem: async (k, v) => { storage.set(k, String(v)) },
      multiRemove: async (keys) => { for (const k of keys) storage.delete(k) } },
    router: { replace: (to) => p.routed.push(to) },
    StannNotifications: { takeDownLive: (which) => p.takenDown.push(which) },
    forgetFeedSeen() {},
  }
  return p
}

// The app starts on a phone that had a fan, the server answering who it is as `answer`.
async function starts(answer) {
  reset()
  const p = phone({ 'stann.has-fan': '1' })
  const api = { start: async () => answer, account: async () => answer, config: async () => ({}) }
  const app = mount(account, { ...p.env, api })
  await until(10_000)
  return { takenDown: p.takenDown, routed: p.routed, notice: app.out.notice, me: app.out.me }
}

console.log(JSON.stringify({ signedOut: await starts(out), anonymous: await starts(nobody), signedIn: await starts(sana) }))
"""


@pytest.fixture(scope="module")
def found(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("account")
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    module = tmp_path / "account.mts"
    module.write_text(MODULE % {
        "resync": json.dumps((ROOT / "web" / "src" / "resync.ts").as_uri()),
        "fan_change": json.dumps((ROOT / "web" / "src" / "fanChange.ts").as_uri()),
        "account": provider("mobile/src/account.tsx", "const HAS_FAN", "Account", "{ ...value, ready }"),
    })
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(module.as_uri()), "react": REACT}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_start_signed_out_elsewhere_takes_the_old_accounts_live_cards_down(found):
    # Said once, as before, with every live card taken down (there's no end coming from the server for them); no
    # start over, as there's nothing shown yet to forget. Was: the notice, and the cards left up.
    assert found["signedOut"] == {"takenDown": [None], "routed": [], "notice": "This device was signed out. Sign in again to see your players.",
                                  "me": {"signed_in": False, "reason": "signed_out"}}
    # A phone that was never signed in, or still is, keeps its cards.
    assert found["anonymous"]["takenDown"] == [] and found["anonymous"]["notice"] is None
    assert found["signedIn"]["takenDown"] == [] and found["signedIn"]["notice"] is None
