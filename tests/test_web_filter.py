"""Today's game filter (web/src/filter.ts, BHA-305): a chip of each of the fan's own games on Today, the other games
there behind a search, and Today narrowed to the game picked, each block by the sport its items say. Run with Node (22.6 or later, which
reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

FILTER = Path(__file__).resolve().parent.parent / "web" / "src" / "filter.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const f = await import(%(module)s)
// A sport's key from the name an item says, as sportKey does: any spelling of a sport's name; else "other".
const keyOf = (name) => ({ cricket: 'cricket', football: 'football', chess: 'chess', tennis: 'tennis', cs2: 'cs2' })[(name ?? '').toLowerCase()] ?? 'other'
const sport = (key, on = true) => ({ key, label: key, on })
const catalog = [sport('football'), sport('cricket'), sport('tennis'), sport('chess'), sport('cs2'), sport('basketball', false)]
const card = (id, sport) => ({ player_id: id, player: { name: `p${id}`, sport } })
const blocks = {
  ...f.NO_BLOCKS,
  cards: [card(1, 'Cricket'), card(2, 'FOOTBALL')],
  live: [['g', [card(2, 'Football')]]],
  results: [{ key: 'r1', players: [{ sport: 'Cricket' }] }, { key: 'r2', players: [{ sport: 'Football' }] }],
  bigResults: [{ key: 'b1', sport: 'cs2' }, { key: 'b2', sport: 'golf' }],
  // A team's game says its own sport; a game of the fan's players, its first player's.
  coming: [{ key: 'c1', sport: 'chess', players: [] }, { key: 'c2', players: [{ player: { sport: 'Cricket' } }] }],
  moreLive: [{ game: { key: 'm1', sport: 'basketball' }, label: 'Basketball' }],
  wire: [{ id: 'w1', sport: 'Tennis' }, { id: 'w2', sport: 'cricket' }],
}
const keys = (b) => Object.fromEntries(Object.entries(b).map(([k, v]) => [k, v.map((x) => x.key ?? x.id ?? x.player_id ?? x[0])]))
const here = f.gamesOnToday(blocks, keyOf)
const theirs = f.theirGames(blocks, keyOf)
const row = f.gameRow(catalog, here, theirs)
console.log(JSON.stringify({
  here: [...here].sort(),
  theirs: [...theirs].sort(),
  chips: row.chips.map((s) => s.key),
  more: row.more.map((s) => s.key),
  found: [f.findGames(row.more, ' CS').map((s) => s.key), f.findGames(row.more, '').map((s) => s.key), f.findGames(row.more, 'golf').length],
  cricket: keys(f.narrow(blocks, 'cricket', keyOf)),
  all: f.narrow(blocks, null, keyOf) === blocks,
  oneGame: f.gameRow(catalog, new Set(['cricket']), new Set(['cricket'])),
  offOnly: f.gameRow(catalog, new Set(['cricket', 'basketball', 'other']), new Set()),
  nobodys: f.gameRow(catalog, here, new Set()).chips.length,
  picked: [f.pickedGame('chess', row)?.key ?? null, f.pickedGame('cs2', row)?.key ?? null, f.pickedGame('basketball', row), f.pickedGame('golf', row), f.pickedGame(null, row)],
  nothing: [f.nothingOn({ ...f.NO_BLOCKS, cards: blocks.cards }), f.nothingOn({ ...f.NO_BLOCKS, cards: blocks.cards, wire: blocks.wire })],
}))
"""


@pytest.fixture(scope="module")
def found(tmp_path_factory: pytest.TempPathFactory) -> dict:
    hook = tmp_path_factory.mktemp("node") / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(FILTER.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_chip_of_each_of_the_fans_own_games_and_the_rest_behind_a_search(found):
    # The other games on now are a link to the Live page, naming none: neither a chip nor in the search.
    assert found["here"] == ["chess", "cricket", "cs2", "football", "other", "tennis"]
    assert found["theirs"] == ["chess", "cricket", "football"]  # a team's game to come is theirs too
    assert found["chips"] == ["football", "cricket", "chess"]  # in the catalog's order
    assert found["more"] == ["tennis", "cs2"]  # the big games' and the Wire's; "other" (golf) is neither
    assert found["nobodys"] == 0  # a fan who follows nobody: every game behind the search


def test_the_search_finds_a_game_by_any_part_of_its_name(found):
    assert found["found"] == [["cs2"], ["tennis", "cs2"], 0]


def test_no_row_for_one_game_and_none_for_a_sport_turned_off(found):
    assert found["oneGame"] == {"chips": [], "more": []}
    assert found["offOnly"] == {"chips": [], "more": []}  # cricket alone once basketball (off) and other are left out


def test_today_narrowed_to_a_game_keeps_each_blocks_items_of_it(found):
    cricket = found["cricket"]
    assert cricket["cards"] == [1]
    assert cricket["live"] == [] and cricket["moreLive"] == []
    assert cricket["results"] == ["r1"] and cricket["bigResults"] == []
    assert cricket["coming"] == ["c2"]  # by its first player, as it names no sport of its own
    assert cricket["wire"] == ["w2"]
    assert found["all"] is True  # no game picked: Today as it is


def test_a_game_picked_is_one_of_the_rows_else_all(found):
    assert found["picked"] == ["chess", "cs2", None, None, None]  # a chip, or one behind the search


def test_the_strip_alone_is_nothing_on(found):
    assert found["nothing"] == [True, False]
