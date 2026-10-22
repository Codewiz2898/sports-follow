"""The Android app's requests give up (mobile/src/deadline.ts, which mobile/src/api.ts runs every request through):
React Native's fetch never does on a connection gone quiet, so one stuck request froze a match page's live score with
no error and no next look (BUG-155). React Native's AbortSignal has neither timeout() nor any(), so the time limit
and the caller's own abort are joined by hand. Run with Node (22.6 or later, which reads TypeScript); skipped
without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_refetch import NODE

DEADLINE = Path(__file__).resolve().parent.parent / "mobile" / "src" / "deadline.ts"

pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

# The scenarios, run in Node against the module itself; it prints what each came to as JSON.
SCRIPT = """
import { REQUEST_MS, TIMED_OUT, within } from %(module)s

// A request whose connection went quiet: it never answers, and gives up only when its signal says so (or said so
// before it started), as fetch does.
const stuck = (signal) => new Promise((_, reject) => {
  const abort = () => reject(Object.assign(new Error('Aborted'), { name: 'AbortError' }))
  if (signal.aborted) abort()
  signal.addEventListener('abort', abort)
})
const timers = () => process.getActiveResourcesInfo().filter((r) => r === 'Timeout').length
const outcome = async (work) => {
  const started = Date.now()
  try { return { value: await work(), ms: Date.now() - started } } catch (e) { return { error: e.name, ms: Date.now() - started } }
}

const quiet = await outcome(() => within(stuck, null, 50))
// The caller gave up first (a search typed over): its own AbortError, as before, so the caller ignores it.
const caller = new AbortController()
setTimeout(() => caller.abort(), 20)
const mine = await outcome(() => within(stuck, caller.signal, 5_000))
const gone = new AbortController()
gone.abort()
const already = await outcome(() => within(stuck, gone.signal, 5_000))
// An answer in time is the answer, and its time limit stops with it (no timer left behind).
const before = timers()
const answered = await outcome(() => within(async (signal) => (signal.aborted ? 'aborted' : 'ok'), new AbortController().signal, 60_000))
const left = timers() - before

console.log(JSON.stringify({ limit: REQUEST_MS, timedOut: TIMED_OUT, quiet, mine, already, answered, left }))
"""


def run() -> dict:
    script = SCRIPT % {"module": json.dumps(DEADLINE.as_uri()) + ";"}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_request_that_never_answers_gives_up_and_says_so():
    found = run()
    assert 10_000 <= found["limit"] <= 20_000  # about 15 s: longer than any answer of the API's takes
    assert found["quiet"]["error"] == found["timedOut"] == "TimeoutError" and 50 <= found["quiet"]["ms"] < 1_000


def test_the_callers_own_abort_still_reads_as_one():
    found = run()
    assert found["mine"]["error"] == "AbortError" and found["mine"]["ms"] < 1_000
    assert found["already"]["error"] == "AbortError" and found["already"]["ms"] < 1_000


def test_an_answer_in_time_is_the_answer_and_leaves_no_timer():
    found = run()
    assert found["answered"] == {"value": "ok", "ms": found["answered"]["ms"]} and found["left"] == 0
