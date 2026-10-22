"""The Android app's pages read the server again only while someone can see them (BUG-161).

A player's page and a team's read their card every 30 s while open, and went on while the app was away (the phone in
a pocket, its JavaScript kept awake by a notification), as the match page didn't. Now they stop as the app goes away,
and read at once, once, as it comes back. Today's look at every game on (today/live.tsx's useLiveAnswer) went on every
minute under any page opened over Today; now it looks only while its page is shown, and again once shown if a minute
has passed.

The pages' own lines run here, taken from their files, with React, React Native's AppState, the API and the clock
faked. Run with Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from .test_mobile_lists import REACT
from .test_web_news_lines import NODE, RESOLVE

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")


def focus_effect(path: str, deps: str) -> tuple[str, str]:
    """A page's read while it's in focus (the function its useFocusEffect is handed), and its file's text."""
    text = (ROOT / path).read_text()
    start = text.index("useFocusEffect(useCallback(") + len("useFocusEffect(useCallback(")
    return text[start:text.index(f", {deps}))", start)], text


def number(text: str, name: str) -> str:
    """A file's `const NAME = 30_000`."""
    found = re.search(rf"const {name} = ([\d_]+)", text)
    assert found, f"no {name}"
    return found[1]


def live_lines() -> str:
    """today/live.tsx from its constants to the end of useLiveAnswer, nothing exported."""
    text = (ROOT / "mobile" / "src" / "today" / "live.tsx").read_text()
    start = text.index("const EVERY")
    head = text.index("export function useLiveAnswer(")
    return text[start:text.index("\n}\n", head) + 3].replace("export ", "")


MODULE = """
import { EVERY, nextLook } from %(match)s
export function player({ api, AppState, setCard, setError, playerId }: any) {
  const EVERY_MS = %(every_ms)s
  const BUILDING_MS = %(building_ms)s
  return %(player)s
}
export function team({ api, AppState, ApiError, setTeam, setError, teamId, asked }: any) {
  return %(team)s
}
export function live(_: any, { useState, useEffect, useRef, useIsFocused, AppState, api }: any) {
%(live)s
  return useLiveAnswer()
}
"""

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const { player, team, live } = await import(%(module)s)
%(react)s

class ApiError extends Error { constructor(status) { super(`Request failed (${status})`); this.status = status } }
// The timers held while the app was away, run now as it wakes.
const due = async () => { for (const x of clock.timers) x.at = Math.max(x.at, clock.t); await until(clock.t) }

// A page open in front at 0; the app put away at 75 s, back at 10 minutes. `woke`: whether its timers ran while it
// was away (its JavaScript kept awake), or were held and ran as it came back, before or after AppState said so.
async function away(open, woke) {
  reset()
  const reads = []
  const close = open(() => reads.push(clock.t / 1000))
  await settle()
  await until(75_000)
  AppState.set('background')
  if (woke === 'awake') await until(600_000)
  else clock.t = 600_000
  if (woke === 'timers') { await due(); AppState.set('active') } else { AppState.set('active'); await due() }
  await settle()
  await until(660_000)
  close()
  return reads
}

const playerPage = (read) => player({ playerId: 7, AppState, setCard() {}, setError() {},
  api: { card: async () => { read(); return { version: 1, status: 'ready' } } } })()
const teamPage = (read) => team({ teamId: 3, asked: 0, AppState, ApiError, setTeam() {}, setError() {},
  api: { team: async () => { read(); return { team_id: 3, refreshing: false } } } })()

// Today open at 0; a player's page opened over it at 90 s, back on Today at 5 minutes; over it again at 370 s, back
// at 390 s.
async function under() {
  reset()
  const reads = []
  const page = { shown: true }
  const today = mount(live, { useIsFocused: () => page.shown, api: { live: async () => { reads.push(clock.t / 1000); return { sports: [], refreshing: false } } } })
  const show = async (on) => { page.shown = on; today.render(); await settle() }
  await settle()
  await until(90_000); await show(false)
  await until(300_000); await show(true)
  await until(370_000); await show(false)
  await until(390_000); await show(true)
  await until(500_000)
  return reads
}

// One at a time: they share the clock.
const found = { player: {}, team: {} }
for (const woke of ['awake', 'state', 'timers']) {
  found.player[woke] = await away(playerPage, woke)
  found.team[woke] = await away(teamPage, woke)
}
found.under = await under()
console.log(JSON.stringify(found))
"""


@pytest.fixture(scope="module")
def found(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("polls")
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    player, player_text = focus_effect("mobile/src/app/player/[id].tsx", "[playerId]")
    team, _ = focus_effect("mobile/src/app/team/[id].tsx", "[teamId, asked]")
    module = tmp_path / "pages.mts"
    module.write_text(MODULE % {
        "match": json.dumps((ROOT / "web" / "src" / "match.ts").as_uri()),
        "every_ms": number(player_text, "EVERY_MS"),
        "building_ms": number(player_text, "BUILDING_MS"),
        "player": player,
        "team": team,
        "live": live_lines(),
    })
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(module.as_uri()), "react": REACT}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.parametrize("page", ["player", "team"])
@pytest.mark.parametrize("woke", ["awake", "state", "timers"])
def test_a_page_reads_nothing_while_the_app_is_away_and_once_as_it_comes_back(found, page, woke):
    # Every 30 s in front; nothing from 75 s to 10 minutes away; at once on coming back, then every 30 s. Was a read
    # every 30 s while away (awake), or two as it came back (held timers).
    assert found[page][woke] == [0, 30, 60, 600, 630, 660]


def test_todays_look_at_the_games_on_waits_while_a_page_is_over_it(found):
    # Every minute while Today's shown; nothing while a player's page is over it; at once back on it after a while,
    # and not before a minute has passed after a quick look away. Was every minute throughout.
    assert found["under"] == [0, 60, 300, 360, 420, 480]
