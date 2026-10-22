"""How search compares what was typed with names (web/src/search.ts, shared by the web and Android apps): as the
server does, a letter Unicode doesn't take apart (ø, đ, ł, æ) is its plain one, so "ode" is the start of Ødegaard and
bold in it, and Enter on "bodo/glimt" opens Bodø/Glimt (BUG-167). Run with Node (22.6 or later, which reads
TypeScript); skipped without it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE, RESOLVE

SEARCH = Path(__file__).resolve().parent.parent / "web" / "src" / "search.ts"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+ (TypeScript without a build)")

SCRIPT = """
import { register } from 'node:module'
register(%(hook)s)
const { enterTeam, highlightParts, teamsFirst } = await import(%(module)s)
const bold = (name, typed) => highlightParts(name, typed).filter((p) => p.bold).map((p) => p.text)
const team = (name) => ({ team_id: name.length, name, sport: 'football', following: false, next: null, last: null })
const asked = (query, teams) => ({ query, results: [], namesakes: 0, suggestions: [], teams })
console.log(JSON.stringify({
  bold: [['Martin Ødegaard', 'ode'], ['Novak Đoković', 'dok'], ['Alexander Sørloth', 'sorl'], ['Łukasz Fabiański', 'lukasz'],
    ['Kylian Mbappé', 'mbappe'], ["A'ja Wilson", 'aja'], ['Æsir Ødegaard', 'aes']].map(([name, typed]) => bold(name, typed)),
  enter: enterTeam(asked('bodo/glimt', [team('Bodø/Glimt'), team('Bodo Town')]))?.name ?? null,
  first: teamsFirst({ ...asked('bodo/glimt', [team('Bodø/Glimt')]), results: [{ name: 'Someone' }] }),
}))
"""


def run(tmp_path: Path) -> dict:
    hook = tmp_path / "resolve.mjs"
    hook.write_text(RESOLVE)
    script = SCRIPT % {"hook": json.dumps(hook.as_uri()), "module": json.dumps(SEARCH.as_uri())}
    done = subprocess.run([NODE or "node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_typed_start_is_bold_in_names_with_letters_unicode_doesnt_take_apart(tmp_path):
    assert run(tmp_path)["bold"] == [["Øde"], ["Đok"], ["Sørl"], ["Łukasz"], ["Mbappé"], ["A'ja"], ["Æs"]]


def test_enter_opens_the_team_called_just_what_was_typed_without_its_special_letters(tmp_path):
    found = run(tmp_path)
    assert found["enter"] == "Bodø/Glimt"
    assert found["first"] is True  # and its teams come first, as for "Arsenal"
