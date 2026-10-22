"""Which of the site's addresses the Android app claims (mobile/app.config.ts, Android App Links): its pages, read
from its routes, and the one-word addresses of campaigns ([slug].tsx), as typed, capitals and all. Not the admin
panel's, which the app has no screen for: a fan who chose "always open with Slam" got the app's Not found page for
stann.in/admin (BUG-157). Android's patterns can't say "but not", so the campaigns' one is spelled out around it;
this checks the config Expo builds, matched as Android matches it. Skipped without Node and mobile/node_modules."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

MOBILE = Path(__file__).resolve().parent.parent / "mobile"
NPX = shutil.which("npx")
pytestmark = pytest.mark.skipif(NPX is None or not (MOBILE / "node_modules" / "expo").exists(), reason="needs Node and mobile/node_modules")


def advanced(pattern: str) -> re.Pattern[str]:
    """Android's PATTERN_ADVANCED_GLOB as a regular expression: sets with ranges, . for any one, * and + after one
    character or set; anything else is itself."""
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if c == "[":
            end = pattern.index("]", i)
            body = pattern[i + 1:end]
            out += "[" + ("^" + re.escape(body[1:]).replace("\\-", "-") if body.startswith("^") else re.escape(body).replace("\\-", "-")) + "]"
            i = end + 1
            continue
        out += c if c in ".*+" else re.escape(c)
        i += 1
    return re.compile(out)


@pytest.fixture(scope="module")
def claimed() -> list[dict[str, str]]:
    done = subprocess.run([NPX or "npx", "expo", "config", "--type", "public", "--json"], cwd=MOBILE, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    (links,) = [f for f in json.loads(done.stdout)["android"]["intentFilters"] if f.get("autoVerify")]
    return [d for d in links["data"] if "host" not in d]


def opens(claimed: list[dict[str, str]], path: str) -> bool:
    """Whether Android opens the app for https://stann.in<path>."""
    return any(path == d.get("path") or ("pathPrefix" in d and path.startswith(d["pathPrefix"]))
               or ("pathAdvancedPattern" in d and advanced(d["pathAdvancedPattern"]).fullmatch(path)) for d in claimed)


def test_the_app_opens_campaigns_and_its_pages_but_not_the_admin_panel(claimed):
    for path in ("/gukesh", "/Gukesh", "/wcc-2026", "/a", "/ad", "/adm", "/admi", "/admins", "/administer", "/adm1n", "/badmin", "/2026",
                 "/", "/search", "/player/7", "/match/espn_soccer/740912", "/team/3", "/feed"):
        assert opens(claimed, path), path
    for path in ("/admin", "/Admin", "/ADMIN", "/admin/", "/admin/feed", "/me/leagues", "/j/abcd", "/api/share.png", "/gukesh/more"):
        assert not opens(claimed, path), path
