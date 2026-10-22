"""A player's news as the apps show it (web/src/lines.ts, shared by the web and Android apps): a tile shows a headline
without its date, so only one from the last few days, and a page whose research-agent news is old shows none of it
(BUG-247: the agent's October news stayed on bound players' pages, and Carlsen's tile read "set for Barcelona launch"
as his status for weeks). The publishers' stories the curator served stay as the server sends them. Run with Node
(22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

LINES = Path(__file__).resolve().parent.parent / "web" / "src" / "lines.ts"

# lines.ts imports its neighbours as the web build does, without ".ts": Node is told where they are.
RESOLVE = """
export async function resolve(specifier, context, next) {
  try { return await next(specifier, context) } catch (err) {
    if (!specifier.startsWith('.')) throw err
    for (const tail of ['.ts', '/index.ts']) { try { return await next(specifier + tail, context) } catch {} }
    throw err
  }
}
"""

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const { newsOf, statusLine } = await import(%(module)s)
const now = Date.parse('2026-10-22T09:00:00Z')
const ago = (days) => new Date(now - days * 864e5).toISOString()
const story = (headline, days) => ({ headline, summary: '', source: 'Somewhere', url: `https://example.com/${headline.length}`, published: days === null ? null : ago(days) })
const card = (news, stories) => ({ status: 'ready', live: { is_live: false }, upcoming: [], recent_results: [], player: { name: 'Asha Rao', status: 'active' }, news, ...(stories ? { stories } : {}) })
const old = [story('Rao set for the launch of a new league', 12), story('Rao signs on', 20)]
const fresh = [story('Rao named captain', 1), ...old]
const served = [story('A publisher story of last week', 9)]
const heads = (items) => items.map((n) => n.headline)
console.log(JSON.stringify({
  oldTile: statusLine(card(old), undefined, now, 'Cricket'),
  freshTile: statusLine(card(fresh), undefined, now, 'Cricket'),
  undatedTile: statusLine(card([story('Rao in the news', null)]), undefined, now, 'Cricket'),
  oldPage: heads(newsOf(card(old), now)),
  freshPage: heads(newsOf(card(fresh), now)),
  servedPage: heads(newsOf(card(old, served), now)),
  servedTile: statusLine(card(old, served), undefined, now, 'Cricket'),
}))
"""


def node() -> str | None:
    found = shutil.which("node")
    if found is None:
        return None
    ok = subprocess.run([found, "--experimental-strip-types", "-e", "0"], capture_output=True, timeout=30)
    return found if ok.returncode == 0 else None


NODE = node()
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")


def run(tmp_path: Path) -> dict:
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(LINES.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_tile_shows_only_a_recent_headline_and_a_page_none_of_the_agents_old_news(tmp_path):
    found = run(tmp_path)
    assert found["oldTile"] == "Cricket"  # the sport, not a story of twelve days ago as if it were news
    assert found["freshTile"] == "Rao named captain"
    assert found["undatedTile"] == "Cricket"  # a story with no date can't say it's recent
    assert found["oldPage"] == [] and found["freshPage"] == ["Rao named captain"]
    # The publishers' stories are the server's to keep or take off (news.prune, rank.cards): the page shows them as
    # they come; the tile still only a recent one.
    assert found["servedPage"] == ["A publisher story of last week"] and found["servedTile"] == "Cricket"


TODAY = LINES.parent / "today.ts"

WIRE = """
import { register } from 'node:module'
register(%(hook)s)
const { wireText } = await import(%(module)s)
const row = (player, text) => ({ id: 's1', player_id: 1, player, sport: 'basketball', tag: 'News', text, source: 'Somewhere', url: 'https://example.com/1', yours: true, at: '' })
console.log(JSON.stringify([
  wireText(row('Nikola Jokić', 'Jokic drops 40 as Denver win')),
  wireText(row('Nikola Jokić', 'Jokić drops 40 as Denver win')),
  wireText(row('Nikola Jokić', 'Denver win at the buzzer')),
]))
"""


def test_a_wire_rows_line_names_the_player_first_only_when_it_doesnt_already_however_its_accents_are_written(tmp_path):
    # BUG-253: a headline writing "Jokic" got "Jokić: " put in front of it, as if it didn't name him.
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = WIRE % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(TODAY.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == ["Jokic drops 40 as Denver win", "Jokić drops 40 as Denver win", "Jokić: Denver win at the buzzer"]
