"""What the apps say and forget as a device becomes another fan (web/src/fanChange.ts, shared by the web and Android
apps): a sign-in says how many of the device's teams didn't fit in the account (BUG-228), and signing in, out or
deleting the account forgets what the device kept of the fan it was, so the next person on it isn't shown it (BUG-169).
Run with Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from .test_web_refetch import NODE

ROOT = Path(__file__).resolve().parent.parent
FAN_CHANGE = ROOT / "web" / "src" / "fanChange.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { PHONE_KEPT, WEB_KEPT, welcome } from %(module)s

const said = [[0, 0], [1, 0], [3, 0], [3, 1], [0, 15], [2, 15]].map(([players, teams]) => welcome(players, teams))
const dropped = [[2, 0, 1, 0], [2, 0, 4, 0], [0, 0, 0, 1], [2, 15, 4, 0], [2, 1, 1, 3]].map(([added, teams, players, games]) => welcome(added, teams, players, games))
console.log(JSON.stringify({ said, dropped, web: WEB_KEPT, phone: PHONE_KEPT }))
"""


def run() -> dict:
    script = SCRIPT % {"module": json.dumps(FAN_CHANGE.as_uri()) + ";"}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_sign_in_says_what_came_over_and_how_many_teams_didnt_fit():
    assert run()["said"] == [
        "Signed in.",
        "Signed in. The player you followed on this device is in your account now.",
        "Signed in. The 3 players you followed on this device are in your account now.",
        "Signed in. The 3 players you followed on this device are in your account now. "
        "One team you followed on this device didn't fit: your account follows as many teams as it can.",
        "Signed in. 15 teams you followed on this device didn't fit: your account follows as many teams as it can.",
        "Signed in. The 2 players you followed on this device are in your account now. "
        "15 teams you followed on this device didn't fit: your account follows as many teams as it can.",
    ]


def test_a_sign_in_says_how_many_players_and_games_didnt_fit():
    # BUG-328: an account follows at most so many players and games too, and the device's that didn't fit went unsaid.
    assert run()["dropped"] == [
        "Signed in. The 2 players you followed on this device are in your account now. "
        "One player you followed on this device didn't fit: your account follows as many players as it can.",
        "Signed in. The 2 players you followed on this device are in your account now. "
        "4 players you followed on this device didn't fit: your account follows as many players as it can.",
        "Signed in. One game you followed on this device didn't fit: your account follows as many games as it can.",
        "Signed in. The 2 players you followed on this device are in your account now. "
        "4 players and 15 teams you followed on this device didn't fit: your account follows as many as it can.",
        "Signed in. The 2 players you followed on this device are in your account now. "
        "One player, one team and 3 games you followed on this device didn't fit: your account follows as many as it can.",
    ]


def kept(path: str, name: str) -> str:
    """The storage key a module of the app keeps something of the fan's under (its `const NAME = '…'`)."""
    found = re.search(rf"const {name} = '([^']+)'", (ROOT / path).read_text())
    assert found, f"{path} keeps no {name}"
    return found[1]


def test_every_key_a_device_keeps_of_its_fan_is_forgotten_as_it_becomes_another():
    found = run()
    # The fan's last look at Alerts, at the Feed tab and at each player's circle, and the address a code went to.
    assert set(found["web"]) == {kept("web/src/activity.tsx", "SEEN"), kept("web/src/feedSeen.ts", "OPENED"), kept("web/src/feedSeen.ts", "SEEN"),
                                 kept("web/src/pages/AccountPage.tsx", "PENDING")}
    assert set(found["phone"]) == {kept("mobile/src/activity.tsx", "SEEN"), kept("mobile/src/feedSeen.ts", "OPENED"), kept("mobile/src/feedSeen.ts", "SEEN"),
                                   kept("mobile/src/app/account.tsx", "PENDING")}
    # Both apps forget them all as they start over, not one of them.
    assert "WEB_KEPT" in (ROOT / "web/src/account.tsx").read_text() and "PHONE_KEPT" in (ROOT / "mobile/src/account.tsx").read_text()
