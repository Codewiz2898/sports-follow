"""Today's results rail (web/src/today.ts results, lines.ts resultCard; shared by the web and Android apps) dates a
result by when it was over, where the server says (ended_utc): BUG-319, a Test that ended yesterday was missing from
"Results since yesterday", or shown under its first day, as the rail went by its start. A fan in India, Saturday
noon. Run with Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

TODAY = Path(__file__).resolve().parent.parent / "web" / "src" / "today.ts"
LINES = TODAY.parent / "lines.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const { results } = await import(%(today)s)
const { resultCard } = await import(%(lines)s)
const now = Date.parse('2026-11-22T12:00:00+05:30')
const card = (id, name, sport, recent) => ({ player_id: id, player: { name, sport, teams: ['India'] }, recent_results: recent, live: { is_live: false } })
// Bumrah's 2nd Test, India v South Africa: started Mon 17 Nov, won on Fri 21 Nov, yesterday.
const test = { title: 'India v South Africa · 2nd Test', result: 'India won by 8 wickets', outcome: 'W', date: '2026-11-17', start_utc: '2026-11-17T04:00:00+00:00',
               ended_utc: '2026-11-21T10:30:00+00:00', event_id: 1, match: 'ground/1' }
// Gukesh's game of yesterday evening.
const chess = { title: 'Gukesh D vs Javokhir Sindarov · Round 3', result: 'Won 1–0', outcome: 'W', date: '2026-11-21', start_utc: '2026-11-21T13:00:00+00:00', event_id: 2 }
const key = (s) => (s || '').toLowerCase()
const titles = (r) => r && { day: r.day, games: r.games.map((g) => g.players.map((p) => p.result.title)) }
const both = results([card(6, 'Jasprit Bumrah', 'Cricket', [test]), card(1, 'Gukesh D', 'Chess', [chess])], now, key)
const alone = results([card(6, 'Jasprit Bumrah', 'Cricket', [test])], now, key)
// A card from before the server said when a game was over goes by its start, as it did.
const before = results([card(6, 'Jasprit Bumrah', 'Cricket', [{ ...test, ended_utc: undefined }])], now, key)
console.log(JSON.stringify({ both: titles(both), alone: titles(alone), when: resultCard(alone.games[0], true, now, 'cricket').when, before: Boolean(before.day) }))
"""


def test_a_test_that_ended_yesterday_is_a_result_since_yesterday(tmp_path):
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "today": json.dumps(TODAY.as_uri()), "lines": json.dumps(LINES.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60,
                          env={**os.environ, "TZ": "Asia/Kolkata"})
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)
    # Newest first: Gukesh's game began after the Test ended.
    assert found["both"] == {"day": None, "games": [["Gukesh D vs Javokhir Sindarov · Round 3"], ["India v South Africa · 2nd Test"]]}
    assert found["alone"] == {"day": None, "games": [["India v South Africa · 2nd Test"]]}
    assert found["when"] == "Yesterday"
    assert found["before"] is True  # no ended_utc: its first day, the latest with a result
