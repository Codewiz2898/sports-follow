"""When the apps ask again after news (web/src/refetch.ts, shared by the web and Android apps): every app following a
player hears of a goal at the same instant, and each asked for its activity list and This week 1.5 s later, so 40
open apps were 80 requests at once (BUG-265). Now each waits 2-12 s of its own, This week waits a minute or two
(the server keeps it a minute: week.mine) and asks no more than every five minutes, and news heard while an ask
waits is that ask's. Run with Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REFETCH = Path(__file__).resolve().parent.parent / "web" / "src" / "refetch.ts"


def node() -> str | None:
    found = shutil.which("node")
    if found is None:
        return None
    ok = subprocess.run([found, "--experimental-strip-types", "-e", "0"], capture_output=True, timeout=30)
    return found if ok.returncode == 0 else None


NODE = node()
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

# The scenarios, run in Node against the module itself; each prints what it found as JSON.
SCRIPT = """
import { ACTIVITY, WEEK, refetcher, waitFor } from %(module)s

// The same random numbers each run.
let seed = 42
const random = () => ((seed = (seed * 1103515245 + 12345) %% 2147483648) / 2147483648)

// A clock the scenario moves on by hand.
const clock = { t: 0, timers: [], now: () => clock.t, later(run, ms) { const t = { at: clock.t + ms, run }; clock.timers.push(t); return t },
  cancel(t) { clock.timers = clock.timers.filter((x) => x !== t) } }
const until = (t) => { for (;;) { const due = clock.timers.filter((x) => x.at <= t).sort((a, b) => a.at - b.at)[0]; if (!due) break
  clock.timers = clock.timers.filter((x) => x !== due); clock.t = due.at; due.run() } clock.t = t }

// 1,000 open apps hear one goal at once: when does each ask for its activity list, and This week?
const activity = Array.from({ length: 1000 }, () => waitFor(ACTIVITY, 0, -Infinity, random))
const week = Array.from({ length: 1000 }, () => waitFor(WEEK, 0, -Infinity, random))
const busiest = (asks) => Math.max(...Object.values(asks.reduce((n, ms) => ({ ...n, [Math.floor(ms / 1000)]: (n[Math.floor(ms / 1000)] ?? 0) + 1 }), {})))

// One app: five moments in a second and a half (one poll's: a goal, the lead, a brace...), then another a minute later.
const asks = []
const one = refetcher(ACTIVITY, () => asks.push(clock.t), random, clock)
for (const t of [0, 300, 600, 1000, 1500]) { until(t); one.heard() }
until(60_000); one.heard(); until(200_000)

// This week: loaded at 0; a goal at 10 s, three more over the next minutes; one at 20 minutes.
clock.t = 0; clock.timers = []
const weekAsks = []
const w = refetcher(WEEK, () => weekAsks.push(clock.t), random, clock)
w.asked()
for (const t of [10_000, 30_000, 120_000, 290_000]) { until(t); w.heard() }
until(1_200_000); w.heard(); until(2_000_000)

// Stopped (the page closed) before it asked: it never asks.
const never = []
const gone = refetcher(ACTIVITY, () => never.push(clock.t), random, clock)
gone.heard(); gone.stop(); until(3_000_000)

console.log(JSON.stringify({ activity: [Math.min(...activity), Math.max(...activity), busiest(activity)],
  week: [Math.min(...week), Math.max(...week), busiest(week)], asks, weekAsks, never }))
"""


def run() -> dict:
    script = SCRIPT % {"module": json.dumps(REFETCH.as_uri()) + ";"}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_apps_that_hear_the_same_goal_ask_apart():
    found = run()
    first, last, busiest = found["activity"]
    assert 2_000 <= first and last < 12_000 and last - first > 9_000  # spread over 10 s, none before 2 s
    assert busiest < 150  # of 1,000: about a tenth a second, never all at once
    first, last, busiest = found["week"]
    assert 60_000 <= first and last < 120_000 and busiest < 40  # a minute or two later, over a minute


def test_moments_heard_together_are_one_ask_and_this_week_waits_its_five_minutes():
    found = run()
    # The five moments are one ask, 2-12 s after the first; the one a minute later is another.
    assert len(found["asks"]) == 2
    assert 2_000 <= found["asks"][0] < 12_000 and 62_000 <= found["asks"][1] < 72_000
    # This week: the four heard in its first five minutes are one ask, at five to six minutes; then the one at 20.
    assert len(found["weekAsks"]) == 2
    assert 300_000 <= found["weekAsks"][0] < 360_000 and 1_260_000 <= found["weekAsks"][1] < 1_320_000
    assert found["never"] == []
