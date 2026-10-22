"""Alerts as the apps show them (web/src/alerts.ts, shared by the web and Android apps): the activity list (Latest)
loads again when the fan follows a team, or changes a team's level, as it does for a player or a game; and a fan
who follows only teams isn't told to "follow a player" to see moments (BUG-160). Run with Node (22.6 or later,
which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

ALERTS = Path(__file__).resolve().parent.parent / "web" / "src" / "alerts.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const { followsKey, nothingYet } = await import(%(module)s)
const players = [{ player_id: 7, alerts: 'off' }, { player_id: 3 }]
const games = [{ event_id: 40, alerts: 'key' }]
const key = (teams) => followsKey(players, games, teams)
console.log(JSON.stringify({
  none: key([]),
  team: key([{ team_id: 12, alerts: 'key' }]),
  level: key([{ team_id: 12, alerts: 'results' }]),
  same: key([{ team_id: 12, alerts: 'key' }]) === key([{ team_id: 12, alerts: 'key' }]),
  empty: [nothingYet(0, 0, 0), nothingYet(0, 1, 0), nothingYet(1, 0, 0), nothingYet(0, 0, 1)],
}))
"""


def run(tmp_path: Path) -> dict:
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(ALERTS.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_following_a_team_or_changing_its_level_loads_the_activity_list_again(tmp_path):
    found = run(tmp_path)
    assert len({found["none"], found["team"], found["level"]}) == 3 and found["same"]
    assert found["none"] == "3:key,7:off,game40:key"


def test_a_fan_who_follows_only_teams_isnt_told_to_follow_a_player(tmp_path):
    nobody, teams, players, games = run(tmp_path)["empty"]
    assert nobody == "Follow a player, a team or a game to see their moments here."
    assert teams == players == games and teams.startswith("Nothing yet.") and "teams" in teams
