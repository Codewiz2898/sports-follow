"""Sharing a link from the web app (web/src/shareSheet.ts, which components/Share.tsx shares by): the share sheet, else
the link copied, else shown to copy, and how it went. A league's "Share invite" counted every tap for the launch
numbers, before the sheet even opened, so one the fan closed counted too (BUG-193): it's counted once the link went.
Run with Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from .test_web_refetch import NODE

WEB = Path(__file__).resolve().parent.parent / "web" / "src"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { sendLink } from %(module)s

const closed = () => Promise.reject(Object.assign(new Error('Share canceled'), { name: 'AbortError' }))
const refused = () => Promise.reject(Object.assign(new Error('Not allowed'), { name: 'NotAllowedError' }))
const copied = []
const clipboard = { writeText: async (text) => { copied.push(text) } }
const prompted = []
const fan = (answer) => (message, value) => { prompted.push(value); return answer }
const sent = {
  shared: await sendLink({ share: async () => {}, clipboard }, 'Join Derby', 'u1', fan(null)),
  closed: await sendLink({ share: closed, clipboard }, 'Join Derby', 'u2', fan(null)),
  refused: await sendLink({ share: refused, clipboard }, 'Join Derby', 'u3', fan(null)),
  cantShare: await sendLink({ share: async () => {}, canShare: () => false, clipboard }, 'Join Derby', 'u4', fan(null)),
  noSheet: await sendLink({ clipboard }, 'Join Derby', 'u5', fan(null)),
  shown: await sendLink({}, 'Join Derby', 'u6', fan('u6')),
  dismissed: await sendLink({ clipboard: { writeText: () => Promise.reject(new Error('denied')) } }, 'Join Derby', 'u7', fan(null)),
}
console.log(JSON.stringify({ sent, copied, prompted }))
"""


def run() -> dict:
    script = SCRIPT % {"module": json.dumps((WEB / "shareSheet.ts").as_uri()) + ";"}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_link_goes_by_the_sheet_else_copied_else_shown_and_says_how_it_went():
    found = run()
    assert found["sent"] == {"shared": "shared", "closed": "cancelled", "refused": "copied", "cantShare": "copied", "noSheet": "copied", "shown": "shown", "dismissed": "cancelled"}
    assert found["copied"] == ["u3", "u4", "u5"] and found["prompted"] == ["u6", "u7"]


def test_the_share_button_counts_a_share_only_once_it_went():
    """components/Share.tsx tells its page a share was made (onShared: a league's invite counted) after sendLink says
    the link went, never before the sheet opens nor when it was closed."""
    source = (WEB / "components" / "Share.tsx").read_text()
    told = [m.start() for m in re.finditer(r"onShared\?\.\(\)", source)]
    asked = source.find("await sendLink(")
    assert told and asked != -1 and all(at > asked for at in told)
    assert re.search(r"if \(sent !== 'cancelled'\) onShared\?\.\(\)", source)
