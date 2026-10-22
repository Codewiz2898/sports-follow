"""When Today's "From your players" reads the fan's Feed again on Android (mobile/src/refetchFeed.ts, as
mobile/src/feedLayouts.tsx uses it). It read the Feed again at once on every hint of news (following.tsx's heard):
every notification, the app's in the background too, and every 30 s re-read that found a followed card or game moved
on, which in a live game is nearly every one. So every open phone following a player asked for its Feed at the same
instant, and the server works out a fan's whole Feed even for those two. Coming back to the front asked twice. The
activity list and This week had been given waits of their own (web/src/refetch.ts), this block hadn't (BUG-265, round
two). Now news reads it once, 2 to 10 minutes later at a moment of each phone's own and no more than every 20
minutes; the follow list coming in or changing, a pull and coming back to the front read it at once, once. Run with
Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

ROOT = Path(__file__).resolve().parent.parent
REFETCH_FEED = ROOT / "mobile" / "src" / "refetchFeed.ts"
FEED_LAYOUTS = ROOT / "mobile" / "src" / "feedLayouts.tsx"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

MIN = 60_000

# The scenarios, run in Node against the module itself; each prints what it found as JSON. A phone is a feedReads
# with Today's two ways of reading: `now` is a read at once (the follow list in or changed, a pull, back in front,
# each changing the key useMyFeed reads again on), `heard` is news (following.tsx's heard).
SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const { FEED, feedReads } = await import(%(module)s)

// The same random numbers each run.
let seed = 42
const random = () => ((seed = (seed * 1103515245 + 12345) %% 2147483648) / 2147483648)

// A clock the scenario moves on by hand.
const clock = { t: 0, timers: [], now: () => clock.t, later(run, ms) { const t = { at: clock.t + ms, run }; clock.timers.push(t); return t },
  cancel(t) { clock.timers = clock.timers.filter((x) => x !== t) } }
const until = (t) => { for (;;) { const due = clock.timers.filter((x) => x.at <= t).sort((a, b) => a.at - b.at)[0]; if (!due) break
  clock.timers = clock.timers.filter((x) => x !== due); clock.t = due.at; due.run() } clock.t = t }
const reset = () => { clock.t = 0; clock.timers = [] }
const MIN = 60_000

// A phone on Today: its reads, at once ('now') and after news ('news'), and whether the app is in front.
const phone = () => {
  const p = { reads: [], front: true }
  p.feed = feedReads(() => p.reads.push(['news', clock.t]), () => p.front, random, clock)
  p.now = () => { p.feed.now(); p.reads.push(['now', clock.t]) }
  return p
}

// 1,000 phones with Today open since the start hear one goal at 30 minutes: who reads at that instant, and when.
reset()
const phones = Array.from({ length: 1000 }, phone)
phones.forEach((p) => p.now())
until(30 * MIN)
phones.forEach((p) => p.feed.heard())
const atOnce = phones.filter((p) => p.reads.length > 1).length
until(120 * MIN)
const herd = phones.flatMap((p) => p.reads.filter(([why]) => why === 'news').map(([, t]) => t - 30 * MIN))
const busiest = Math.max(...Object.values(herd.reduce((n, ms) => ({ ...n, [Math.floor(ms / 1000)]: (n[Math.floor(ms / 1000)] ?? 0) + 1 }), {})))

// A live game for two hours: the 30 s re-read finds it moved on every time.
reset()
const live = phone()
live.now()
for (let t = 30_000; t <= 120 * MIN; t += 30_000) { until(t); live.feed.heard() }
until(150 * MIN)

// Coming back to the front. Today open at 0, the app put away at 5 minutes; notifications come while it's away, at 6
// and 35 minutes (the read after the first comes due while it's still away). Back at 40 minutes: a read at once, and
// the follow list and its games, read again on coming back, say there's news as they answer (here a slow 14 s). Then
// no news for half an hour.
reset()
const away = phone()
away.now()
until(5 * MIN); away.front = false
until(6 * MIN); away.feed.heard()
until(35 * MIN); away.feed.heard()
until(40 * MIN); away.front = true; away.now()
until(40 * MIN + 14_000); away.feed.heard()
until(70 * MIN)
const backReads = away.reads.filter(([, t]) => t > 5 * MIN)

// A pull while a read after news waits: the pull's read is that one.
reset()
const pulled = phone()
pulled.now()
until(30 * MIN); pulled.feed.heard()
until(31 * MIN); pulled.now()
until(50 * MIN)

// Today gone (another tab's stack, the fan signed out) before its read after news: it never reads.
reset()
const gone = phone()
gone.feed.heard(); gone.feed.stop()
until(60 * MIN)

console.log(JSON.stringify({ wait: FEED, atOnce, herd: [Math.min(...herd), Math.max(...herd), busiest, herd.length],
  live: live.reads, away: backReads, pulled: pulled.reads, gone: gone.reads }))
"""


def run(tmp_path: Path) -> dict:
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(REFETCH_FEED.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_phones_that_hear_the_same_goal_read_their_feeds_apart_and_later(tmp_path):
    found = run(tmp_path)
    assert found["atOnce"] == 0  # none at the goal's instant
    first, last, busiest, reads = found["herd"]
    assert reads == 1000  # each once
    assert 2 * MIN <= first and last < 10 * MIN and last - first > 7 * MIN  # 2 to 10 minutes later, over 8
    assert busiest < 15  # of 1,000: a couple a second, never all at once


def test_a_live_game_reads_the_feed_no_more_than_every_twenty_minutes(tmp_path):
    reads = run(tmp_path)["live"]
    # Was 240 reads in the two hours, each at the instant every other phone read too.
    assert reads[0] == ["now", 0]
    news = [t for why, t in reads if why == "news"]
    assert 4 <= len(news) <= 7
    assert all(b - a >= 20 * MIN for a, b in zip([0, *news], news, strict=False))


def test_coming_back_to_the_front_reads_once(tmp_path):
    # Nothing while away (the read after news that came due then is the one on coming back); once on coming back,
    # and the news the follow list says as it answers is that read's: nothing more in the half hour after. Was a
    # read for each notification, the app away or not, and two on coming back. (News 30 s after a read at once still
    # waits and reads: the live game's first.)
    assert run(tmp_path)["away"] == [["now", 40 * MIN]]


def test_a_pull_is_the_read_a_wait_after_news_was_for(tmp_path):
    found = run(tmp_path)
    assert found["pulled"] == [["now", 0], ["now", 31 * MIN]]
    assert found["gone"] == []


def from_your_players() -> str:
    """Today's "From your players", as feedLayouts.tsx has it."""
    text = FEED_LAYOUTS.read_text()
    start = text.index("export function FromYourPlayers(")
    return text[start:text.index("\n}\n", start)]


def test_from_your_players_reads_at_once_only_for_its_own_reasons_and_news_through_the_wait():
    body = from_your_players()
    # What it reads again on (useMyFeed's key) is who the fan follows, a pull and coming back to the front, and the
    # reads after news: never news itself.
    call = re.search(r"useMyFeed\(null, PICKED_FROM, ([^\n]+)\)\n", body)
    assert call, "From your players no longer reads its first few with useMyFeed(null, PICKED_FROM, …)"
    assert not re.search(r"\bheard\b", call[1])
    key = re.search(r"const key = ([^\n]+)\n", body)
    assert key and all(re.search(rf"\b{part}\b", key[1]) for part in ("who", "again", "back")) and not re.search(r"\bheard\b", key[1])
    # News goes to the wait (refetchFeed.ts) and nowhere else.
    assert "feedReads(" in body
    uses = [line.strip() for line in body.splitlines() if re.search(r"\bheard\b", line)]
    assert uses == ["const { cards, loaded, heard } = useFollowing()", "useEffect(() => { if (heard) reads.current?.heard() }, [heard])"]
    # Coming back to the front reads at once in place of a read after news still waiting, before the next frame's
    # timers could run it too.
    assert re.search(r"state === 'active'\) \{ r\.now\(\); setBack\(", body)
