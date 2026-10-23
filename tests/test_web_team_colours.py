"""How a team's colours are drawn (web/src/teamColours.ts, shared by the web and Android apps): a team's banner on its
page (9c, BHA-681), with the seam between its colours white, or ink where either is near white; and both sides of a
game's band (7a), where two sides in colours too close to tell apart see the away side change. Run with Node (22.6 or
later, which reads TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

COLOURS = Path(__file__).resolve().parent.parent / "web" / "src" / "teamColours.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const { bannerOf, sidesOf } = await import(%(module)s)
const look = (fill, accent, tones = []) => ({ logo: '/api/logos/x.png', short: 'X', colours: fill ? { fill, accent } : null, tones, set: true })
console.log(JSON.stringify({
  barca: bannerOf(look('#004d98', '#a50044')),
  madrid: bannerOf(look('#ffffff', '#e0b21b')),
  whiteSecond: bannerOf(look('#0d0d0d', '#ffffff')),
  gold: bannerOf(look('#aa8b2c', '#0d0d0d')),
  none: bannerOf(look(null)),
  unknown: bannerOf(undefined),
  clash: sidesOf(look('#d9001a', '#0d0d0d'), look('#dc052d', '#0066b2')),
  apart: sidesOf(look('#004d98', '#a50044'), look('#ffffff', '#e0b21b')),
  oneMissing: sidesOf(look('#004d98', '#a50044'), look(null)),
}))
"""


def run(tmp_path: Path) -> dict:
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(COLOURS.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_team_banner_has_its_colours_and_a_seam_that_shows_between_them(tmp_path):
    found = run(tmp_path)
    assert found["barca"] == {"fill": "#004d98", "accent": "#a50044", "ink": "#ffffff", "seam": "#ffffff"}
    # A white side, or a white second colour, takes an ink seam: a white one would vanish into it.
    assert found["madrid"] == {"fill": "#ffffff", "accent": "#e0b21b", "ink": "#0d0d0d", "seam": "#0d0d0d"}
    assert found["whiteSecond"]["seam"] == "#0d0d0d" and found["whiteSecond"]["ink"] == "#ffffff"
    assert found["gold"] == {"fill": "#aa8b2c", "accent": "#0d0d0d", "ink": "#0d0d0d", "seam": "#ffffff"}


def test_a_team_with_no_colours_has_no_banner_of_its_own(tmp_path):
    found = run(tmp_path)
    assert found["none"] is None and found["unknown"] is None  # its sport's colour, with an ink second


def test_two_sides_in_the_same_colour_see_the_away_side_change(tmp_path):
    found = run(tmp_path)
    home, away = found["clash"]
    assert home["fill"] == "#d9001a"
    assert away["fill"] == "#ffffff" and away["accent"] == "#dc052d"  # Bayern in white at Freiburg, its red the stripe
    assert [s["fill"] for s in found["apart"]] == ["#004d98", "#ffffff"]
    assert found["oneMissing"] is None  # the sport's band
