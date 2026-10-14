"""The web app takes the list of sports from the API rather than keeping its own, and names no source
(CLAUDE.md, "Code organization"). tests/test_sports.py and tests/test_sources.py hold shared Python to
none; much of web/src predates the rule, so each of its files has a limit instead: how many times it
names a sport, or a source, now. A file over its limit has gained a name it should take from the API; a
file under it should have its limit lowered here, so code moved toward the rule can't drift back.

A name is a quoted string equal, in any case, to a sport's key, label, alias or scoreboard kind, or to a
source's system ("espn_cricket"). Strings inside comments don't count."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sports_follow import sports
from sports_follow.adapters import KNOWN

WEB = Path(__file__).resolve().parent.parent / "web" / "src"
# A comment, or a one-line string (group 2) in its quotes (group 1). Comments are matched so the strings
# inside them are skipped.
TOKENS = re.compile(r"""//[^\n]*|/\*.*?\*/|(["'`])((?:\\.|(?!\1)[^\\\n])*)\1""", re.S)

SPORT_LIMITS = {
    "api.ts": 7,
    "components/Blocks.tsx": 12,
    "components/Icons.tsx": 3,
    "components/Results.tsx": 4,
    "components/Search.tsx": 12,
    "format.ts": 16,
}

SOURCE_LIMITS = {
    "pages/AthletePage.tsx": 2,
}


def named(wanted: set[str]) -> dict[str, list[tuple[int, str]]]:
    """Each web/src file's quoted strings that are one of the wanted names, with their lines."""
    found = {}
    for path in sorted(p for p in WEB.rglob("*") if p.suffix in (".ts", ".tsx")):
        text = path.read_text()
        hits = [(text.count("\n", 0, m.start()) + 1, m[2]) for m in TOKENS.finditer(text) if m[1] and m[2].lower() in wanted]
        if hits:
            found[path.relative_to(WEB).as_posix()] = hits
    return found


def times(n: int) -> str:
    return f"{n} time" if n == 1 else f"{n} times"


def held(found: dict[str, list[tuple[int, str]]], limits: dict[str, int], what: str) -> None:
    problems = []
    for rel in sorted(found.keys() | limits.keys()):
        hits, limit = found.get(rel, []), limits.get(rel, 0)
        if len(hits) > limit:
            where = ", ".join(f"{line}: {s!r}" for line, s in hits)
            problems.append(f"web/src/{rel} names a {what} {times(len(hits))}, over its limit of {limit}; take it from the API instead. Lines {where}")
        elif len(hits) < limit:
            fix = f"lower its limit to {len(hits)}" if hits else "remove its limit"
            problems.append(f"web/src/{rel} names a {what} {times(len(hits))}, under its limit of {limit}: {fix} in tests/test_web_names.py")
    if problems:
        pytest.fail("\n".join(problems), pytrace=False)


def test_the_web_app_names_no_more_sports_than_before():
    held(named({n.lower() for s in sports.ALL for n in (s.key, s.label, *s.names, *s.kinds)}), SPORT_LIMITS, "sport")


def test_the_web_app_names_no_more_sources_than_before():
    held(named(set(KNOWN)), SOURCE_LIMITS, "source")
