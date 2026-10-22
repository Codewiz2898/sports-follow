"""Where a fan came from (web/src/visitTags.ts, shared by the web and Android apps): the tags a link carries (s=share,
utm_source=whatsapp…) and its page, which each app tells the server once (server.visit_source). The Android app never
told it, so the launch numbers missed every fan a link brought to the app rather than the site (BUG-195). Run with
Node (22.6 or later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_refetch import NODE

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { linkTags, linkVisit } from %(module)s

console.log(JSON.stringify({
  web: linkTags('?utm_source=instagram&utm_campaign=Gukesh+v+Sindarov&x=1&s=share'),
  piped: linkTags('?utm_campaign=wcc%%7Cday1&utm_medium=%%20post%%20&utm_medium=again'),
  long: linkTags('?s=' + 'x'.repeat(80)).s.length,
  garbled: linkTags('?utm_source=%%E0%%A4&s=share'),
  opened: linkVisit('https://stann.in/gukesh?utm_source=whatsapp&utm_campaign=launch#top'),
  home: linkVisit('https://stann.in?s=share'),
  untagged: linkVisit('https://stann.in/player/7'),
  own: linkVisit('slam://player/7?n=31&utm_source=x'),
}))
"""


def run() -> dict:
    script = SCRIPT % {"module": json.dumps((ROOT / "web" / "src" / "visitTags.ts").as_uri()) + ";"}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_links_tags_are_read_as_typed_and_its_page_with_them():
    found = run()
    assert found["web"] == {"utm_source": "instagram", "utm_campaign": "Gukesh v Sindarov", "s": "share"}
    assert found["piped"] == {"utm_campaign": "wcc|day1", "utm_medium": "post"} and found["long"] == 60
    assert found["garbled"] == {"s": "share"}  # one not encoded right is left out
    assert found["opened"] == {"path": "/gukesh", "tags": {"utm_source": "whatsapp", "utm_campaign": "launch"}}
    assert found["home"] == {"path": "/", "tags": {"s": "share"}}
    assert found["untagged"] is None and found["own"] is None  # the app's own links (a notification's) aren't visits


def test_the_android_app_tells_where_the_link_that_opened_it_came_from():
    """mobile/src/visit.ts reads the link that opened the app (and one opened while it runs) and tells its tags, once
    the phone's fan is made: the root layout's fan screens (inside AccountProvider, which waits for it) run it."""
    visit = (ROOT / "mobile" / "src" / "visit.ts").read_text()
    assert "Linking.getInitialURL()" in visit and "addEventListener('url'" in visit and "linkVisit(" in visit and "api.visitSource(" in visit
    layout = (ROOT / "mobile" / "src" / "app" / "_layout.tsx").read_text()
    fan = layout[layout.index("function Fan()"):]
    assert "useVisitNoted()" in fan[: fan.index("return")]
