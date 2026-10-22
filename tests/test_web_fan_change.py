"""What the apps say as a device becomes another fan (web/src/fanChange.ts, shared by the web and Android apps): a
sign-in says how many of the device's teams didn't fit in the account (BUG-228). Run with Node (22.6 or later, which
reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_refetch import NODE

ROOT = Path(__file__).resolve().parent.parent
FAN_CHANGE = ROOT / "web" / "src" / "fanChange.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { welcome } from %(module)s

const said = [[0, 0], [1, 0], [3, 0], [3, 1], [0, 15], [2, 15]].map(([players, teams]) => welcome(players, teams))
console.log(JSON.stringify({ said }))
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

