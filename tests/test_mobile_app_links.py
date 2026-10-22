"""Which of the site's addresses the Android app claims (mobile/links.js, for app.config.ts: Android App Links): its
pages, read from its routes, and the one-word addresses of campaigns ([slug].tsx), as typed, capitals and all. Not
the admin panel's, which the app has no screen for: a fan who chose "always open with Slam" got the app's Not found
page for stann.in/admin (BUG-157). Android's patterns can't say "but not", so the campaigns' are spelled out around
it; they're matched here as Android matches them. Skipped without Node."""

from __future__ import annotations

import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from .test_web_refetch import NODE

MOBILE = Path(__file__).resolve().parent.parent / "mobile"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node")


@lru_cache(maxsize=1)
def claimed() -> tuple[dict[str, str], ...]:
    """What the app claims, as app.config.ts gives Android (its intent filter's data, but the scheme and host)."""
    script = f"const {{ pages }} = require({json.dumps(str(MOBILE / 'links.js'))}); console.log(JSON.stringify(pages({json.dumps(str(MOBILE / 'src' / 'app'))})))"
    done = subprocess.run([NODE or "node", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return tuple(json.loads(done.stdout))


def advanced(pattern: str) -> re.Pattern[str]:
    """Android's PATTERN_ADVANCED_GLOB as a regular expression: sets with ranges (a "-" before "]" is itself), "."
    for any one, and * and + after one character or set; anything else is itself."""
    out, i = "", 0
    while i < len(pattern):
        if pattern[i] == "[":
            end = pattern.index("]", i)
            body = pattern[i + 1:end]
            negated, body = body.startswith("^"), body.removeprefix("^")
            out += "[" + ("^" if negated else "") + re.escape(body).replace("\\-", "-").removesuffix("-") + ("\\-" if body.endswith("-") else "") + "]"
            i = end + 1
            continue
        out += pattern[i] if pattern[i] in ".*+" else re.escape(pattern[i])
        i += 1
    return re.compile(out)


def campaigns_take(path: str) -> bool:
    """Whether the campaigns' patterns ([slug].tsx) take this path."""
    return any(advanced(d["pathAdvancedPattern"]).fullmatch(path) for d in claimed() if "pathAdvancedPattern" in d)


def opens(path: str) -> bool:
    """Whether Android opens the app for https://stann.in<path>."""
    return campaigns_take(path) or any(path == d.get("path") or ("pathPrefix" in d and path.startswith(d["pathPrefix"])) for d in claimed())


def test_the_app_opens_campaigns_and_its_pages_but_not_the_admin_panel():
    for path in ("/gukesh", "/Gukesh", "/wcc-2026", "/a", "/ad", "/adm", "/admi", "/admins", "/administer", "/adm1n", "/badmin", "/2026",
                 "/", "/search", "/player/7", "/match/espn_soccer/740912", "/team/3", "/feed"):
        assert opens(path), path
    for path in ("/admin", "/Admin", "/ADMIN", "/admin/", "/admin/feed", "/me/leagues", "/j/abcd", "/api/share.png", "/gukesh/more"):
        assert not opens(path), path
