"""Your sports in the apps (web/src/yourSports.ts, BHA-653): the welcome's and the Edit sheet's tiles, what the sheet
ticks as it opens and what Save sends, the starters Today keeps offering, and the fan's sports first in the rails and
lists that hold several. And a picked sport's chip on Today (filter.ts theirGames). Run with Node (22.6 or later, which
reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

WEB = Path(__file__).resolve().parent.parent / "web" / "src"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const y = await import(%(yours)s)
const f = await import(%(filter)s)
const keyOf = (name) => ({ cricket: 'cricket', football: 'football', chess: 'chess', valorant: 'valorant', cs2: 'cs2' })[(name ?? '').toLowerCase()] ?? 'other'
const sport = (key, on = true, group = '') => ({ key, label: key[0].toUpperCase() + key.slice(1), on, group })
// As GET /api/sports lists them: a group's games among the rest, one turned off.
const catalog = [sport('football'), sport('cricket'), sport('valorant', true, 'esports'), sport('basketball', false), sport('chess'), sport('cs2', true, 'esports')]
const card = (sport) => ({ player: { sport } })
const starters = [{ key: 'football', label: 'Football', players: [] }, { key: 'cricket', label: 'Cricket', players: [] }, { key: 'chess', label: 'Chess', players: [] }]
const followed = y.followedSports([card('Cricket'), card('CRICKET')], [{ sport: 'chess' }], keyOf)
const offered = (yours, few, game = null, stays) => y.startersOffered(starters, yours, followed, few, game, stays).map((s) => s.key)
const games = [{ k: 1, sport: 'chess' }, { k: 2, sport: 'football' }, { k: 3, sport: 'Cricket' }, { k: 4, sport: 'football' }, { k: 5, sport: 'golf' }]
const order = (yours, arrange) => y.yoursFirst(games, (g) => g.sport, yours, keyOf, arrange).map((g) => g.k)
const label = (k) => k[0].toUpperCase() + k.slice(1)
const blocks = { ...f.NO_BLOCKS, cards: [card('Cricket')], bigResults: [{ key: 'b1', sport: 'cs2' }, { key: 'b2', sport: 'valorant' }], wire: [{ id: 'w', sport: 'football' }] }
const here = f.gamesOnToday(blocks, keyOf)
console.log(JSON.stringify({
  groups: y.tileGroups(catalog).map((g) => [g.group, g.sports.map((s) => s.key)]),
  followed: [...followed].sort(),
  firstTicks: [y.tickedAtFirst([], followed, catalog), y.tickedAtFirst(['valorant', 'football'], followed, catalog), y.tickedAtFirst(['apex', 'chess'], followed, catalog), y.tickedAtFirst(['apex'], followed, catalog)],
  shown: y.yoursShown(['chess', 'apex', 'basketball', 'cs2'], (k) => catalog.find((s) => s.key === k) ?? { key: 'other', on: true }).map((s) => s.key),
  toggled: [y.toggled(['chess'], 'football'), y.toggled(['chess', 'football'], 'chess')],
  toSave: [y.toSave(['football'], ['valorant', 'cricket', 'cs2'], catalog.filter((s) => s.key !== 'cs2')), y.toSave(['cs2'], ['cs2'], catalog.filter((s) => s.key !== 'cs2'))],
  summary: [y.sheetSummary(['cricket', 'chess', 'valorant', 'football'], ['cricket', 'chess', 'valorant'], label), y.sheetSummary(['chess'], ['cricket', 'chess', 'valorant'], label), y.sheetSummary([], ['chess'], label), y.sheetSummary(['chess'], ['chess'], label)],
  starters: [offered(['football', 'cricket'], true), offered(['football', 'cricket'], false), offered([], false), offered(['football'], true, 'chess'), offered(['football'], false, 'cricket'),
             offered(['football', 'cricket'], false, null, new Set(['cricket']))],
  first: [order(['football']), order([]), order(['cricket', 'chess']), order(['chess'], (lot) => [...lot].reverse())],
  inOrder: y.inYourOrder(['football', 'cricket', 'chess', 'cs2'], (k) => k, ['cs2', 'chess']),
  moved: [y.yoursMovedUp(games, (g) => g.sport, ['chess'], keyOf), y.yoursMovedUp(games.slice(0, 1), (g) => g.sport, ['chess'], keyOf), y.yoursMovedUp(games, (g) => g.sport, ['golf2'], keyOf)],
  chips: [f.gameRow(catalog, here, f.theirGames(blocks, keyOf)).chips.map((s) => s.key), f.gameRow(catalog, here, f.theirGames(blocks, keyOf, ['valorant', 'chess'])).chips.map((s) => s.key)],
}))
"""


@pytest.fixture(scope="module")
def found(tmp_path_factory: pytest.TempPathFactory) -> dict:
    hook = tmp_path_factory.mktemp("node") / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "yours": json.dumps((WEB / "yourSports.ts").as_uri()), "filter": json.dumps((WEB / "filter.ts").as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_tiles_are_every_sport_on_a_groups_games_after_under_its_name(found):
    """Not only the sports with starters (BHA-653: on stann.in that was football, cricket and chess, no esports game)."""
    assert found["groups"] == [["", ["football", "cricket", "chess"]], ["esports", ["valorant", "cs2"]]]


def test_edit_ticks_the_fans_sports_else_those_of_who_they_follow(found):
    assert found["followed"] == ["chess", "cricket"]
    assert found["firstTicks"][:2] == [["cricket", "chess"], ["valorant", "football"]]  # in the tiles' order; theirs as saved
    # One this app has no tile for isn't ticked (Save sends it back anyway); with only that, still nothing else ticked.
    assert found["firstTicks"][2:] == [["chess"], []]
    # Following's row, and its count: a sport this app's copy of the list doesn't know, or has off, isn't shown.
    assert found["shown"] == ["chess", "cs2"]
    assert found["toggled"] == [["chess", "football"], ["football"]]  # a tick goes at the end of their order


def test_save_keeps_a_sport_of_theirs_the_app_shows_no_tile_for(found):
    """PR #225's review: a sport off in this app's copy of /api/sports but on again at the server, which the fan never
    saw to untick, isn't dropped by their Save."""
    assert found["toSave"] == [["football", "cs2"], ["cs2"]]


def test_the_sheet_says_what_changed_and_how_many(found):
    assert found["summary"] == ["Football added · 4 sports", "2 dropped · 1 sport", "Chess dropped · No sports picked", "1 sport"]


def test_starters_past_the_first_few_are_of_their_sports_they_follow_nobody_in(found):
    """A fan who picked football and followed three cricketers is still offered footballers; none of cricket, whose
    players they follow; none of a sport they didn't pick. With a game picked on Today, its own alone: chess while they
    follow few, but cricket's none once they follow some cricketers."""
    assert found["starters"] == [["football", "cricket", "chess"], ["football"], [], ["chess"], [], ["football", "cricket"]]
    # The last: cricket was offered on this visit, so following a cricketer from it doesn't take it away (the review's).


def test_the_fans_sports_come_first_each_lot_as_it_was(found):
    assert found["first"] == [[2, 4, 1, 3, 5], [1, 2, 3, 4, 5], [1, 3, 2, 4, 5], [1, 5, 4, 3, 2]]
    assert found["inOrder"] == ["cs2", "chess", "football", "cricket"]
    assert found["moved"] == [True, False, False]  # "yours first" only where theirs went ahead of others


def test_a_picked_sport_gets_a_chip_only_while_something_of_it_is_on_today(found):
    """Bharath's default: a picked sport with nothing on Today gets no chip (chess here); one with a big result does."""
    assert found["chips"] == [["cricket"], ["cricket", "valorant"]]
