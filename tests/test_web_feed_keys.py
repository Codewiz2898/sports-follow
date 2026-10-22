"""The Feed's keys on a computer (web/src/feedKeys.ts, BHA-660), as YouTube's own: Space pauses the video on screen or
plays it on, F takes it full screen and back, M turns its sound off or on. Run with Node (22.6 or later, which reads
TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

KEYS = Path(__file__).resolve().parent.parent / "web" / "src" / "feedKeys.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const { feedKey } = await import(%(module)s)
const press = (key, more = {}) => ({ key, code: more.code, ctrlKey: false, metaKey: false, altKey: false, ...more })
const does = (key, more, focus = null) => feedKey(press(key, more), focus)
console.log(JSON.stringify({
  keys: [does(' '), does('f'), does('F'), does('m'), does('M'), does('k'), does('Enter'), does('ArrowDown'), does('Escape')],
  held: [does('f', { ctrlKey: true }), does('m', { metaKey: true }), does(' ', { altKey: true })],
  shift: [does('F', { shiftKey: true }), does(' ', { shiftKey: true })],
  typing: [does(' ', {}, 'typing'), does('f', {}, 'typing'), does('m', {}, 'typing')],
  control: [does(' ', {}, 'control'), does('f', {}, 'control'), does('m', {}, 'control')],
  hindi: [does('ि', { code: 'KeyF' }), does('म', { code: 'KeyM' }), does('क', { code: 'KeyK' })],
  dvorak: [does('u', { code: 'KeyF' }), does('f', { code: 'KeyY' })],
}))
"""


@pytest.fixture(scope="module")
def found(tmp_path_factory: pytest.TempPathFactory) -> dict:
    hook = tmp_path_factory.mktemp("node") / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(KEYS.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_space_f_and_m_and_nothing_else(found):
    assert found["keys"] == ["pause", "full", "full", "sound", "sound", None, None, None, None]
    assert found["shift"] == ["full", "pause"]


def test_not_the_browsers_or_the_systems_own(found):
    assert found["held"] == [None, None, None]


def test_nothing_while_the_fan_types(found):
    assert found["typing"] == [None, None, None]


def test_space_on_a_button_reached_with_tab_presses_that(found):
    assert found["control"] == [None, "full", "sound"]


def test_a_letter_is_the_keys_own_else_the_one_in_its_place(found):
    assert found["hindi"] == ["full", "sound", None]
    assert found["dvorak"] == [None, "full"]  # a Latin letter is the letter, wherever its key is
