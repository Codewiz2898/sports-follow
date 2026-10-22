"""When a part of the web app that loads as it's needed fails to load (Vite's vite:preloadError: the chess board's
Play from here, a lazy page), the tab reloads only when the build it runs was replaced (web/src/staleBuild.ts): online,
and the server answers 404 for the file, or, where the browser's error doesn't name the file, a newer build is waiting
or took over. Offline, or on a network that dropped the file, it doesn't: the page says it couldn't open, rather than
the whole app reloading onto the offline shell (BUG-308). Run with Node (22.6 or later, which reads TypeScript);
skipped without it."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

ROOT = Path(__file__).resolve().parent.parent
STALE = ROOT / "web" / "src" / "staleBuild.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const { buildReplaced, failedFile } = await import(%(module)s)

// How each browser says a file failed (Chrome, Firefox, Safari, and Vite's own for a page's styles).
const chrome = new TypeError('Failed to fetch dynamically imported module: https://stann.in/assets/chessAnalysis-3f2a9c.js')
const firefox = new TypeError('error loading dynamically imported module: https://stann.in/assets/chessLine-77b1e0.js')
const safari = new TypeError('Importing a module script failed.')
const css = new Error('Unable to preload CSS for /assets/chessAnalysis-5d0c1e.css')
const files = [chrome, firefox, safari, css, undefined].map(failedFile)

// The server and the service worker faked: what each is asked, and what it answers.
const asked = []
const looks = (online, status, newer) => ({
  online,
  status: (file) => { asked.push(['status', file]); return status instanceof Error ? Promise.reject(status) : Promise.resolve(status) },
  newer: () => { asked.push(['newer']); return newer instanceof Error ? Promise.reject(newer) : Promise.resolve(newer) },
})
const down = new TypeError('Failed to fetch')
const cases = {
  offline: [chrome, looks(false, 404, true)],
  gone: [chrome, looks(true, 404, false)],
  there: [chrome, looks(true, 200, true)],
  dropped: [firefox, looks(true, down, true)],
  restarting: [css, looks(true, 502, false)],
  safariNewer: [safari, looks(true, 200, true)],
  safariSame: [safari, looks(true, 404, false)],
  safariOffline: [safari, looks(false, 404, true)],
  safariCantTell: [safari, looks(true, 404, down)],
}
const reload = {}
const questions = {}
for (const [name, [error, look]] of Object.entries(cases)) {
  asked.length = 0
  reload[name] = await buildReplaced(error, look)
  questions[name] = [...asked]
}
console.log(JSON.stringify({ files, reload, questions }))
"""


def run(tmp_path: Path) -> dict:
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(STALE.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_file_that_failed_is_read_from_the_browsers_error(tmp_path):
    assert run(tmp_path)["files"] == ["/assets/chessAnalysis-3f2a9c.js", "/assets/chessLine-77b1e0.js", None, "/assets/chessAnalysis-5d0c1e.css", None]


def test_the_tab_reloads_only_when_its_build_was_replaced(tmp_path):
    found = run(tmp_path)
    assert found["reload"] == {
        "offline": False,  # offline: the page says it couldn't open; nothing is asked
        "gone": True,  # the server no longer has the file: a deploy replaced the build
        "there": False,  # the server has it: a failure on the way, not a new build
        "dropped": False,  # the network dropped the file and can't be asked either
        "restarting": False,  # the API restarting in a deploy: not a 404
        "safariNewer": True,  # Safari doesn't say which file: a newer build waiting or in charge says it
        "safariSame": False,
        "safariOffline": False,
        "safariCantTell": False,
    }
    assert found["questions"]["offline"] == [] and found["questions"]["safariOffline"] == []
    assert found["questions"]["gone"] == [["status", "/assets/chessAnalysis-3f2a9c.js"]]
    assert found["questions"]["safariNewer"] == [["newer"]]


def test_the_app_asks_before_it_reloads_for_a_failed_file():
    """main.tsx took every vite:preloadError for a replaced build and reloaded at once, offline too (BUG-308)."""
    source = (ROOT / "web" / "src" / "main.tsx").read_text()
    handler = re.search(r"addEventListener\('vite:preloadError',(.*?)\n\}\)", source, re.S)
    assert handler, "main.tsx has no vite:preloadError handler"
    assert "buildReplaced(" in handler[1]
    assert "preventDefault" not in handler[1]  # the page hears of the failure, and shows it, while the server is asked
