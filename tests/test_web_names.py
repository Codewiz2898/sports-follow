"""The web app takes the list of sports from the API rather than keeping its own, and names no source
(CLAUDE.md, "Code organization"). tests/test_sports.py and tests/test_sources.py hold shared Python to
none. In web/src only a sport's view (web/src/sports/<sport>.tsx: its live board and mark) names a sport,
and only its own. Where a file still names something it should take from the API (a source, today), it
has a limit instead: how many times it names it now. A file over its limit has gained a name; a file
under it should have its limit lowered here, so code moved toward the rule can't drift back.

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

SPORT_LIMITS: dict[str, int] = {}
VIEWS = "sports/"  # web/src/sports/<sport>.tsx: each sport's own view

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


def names_of(sport: sports.Sport) -> set[str]:
    return {n.lower() for n in (sport.key, sport.label, *sport.names, *sport.kinds)}


def test_the_web_app_names_no_more_sports_than_before():
    found = named(set().union(*(names_of(s) for s in sports.ALL)))
    held({rel: hits for rel, hits in found.items() if not rel.startswith(VIEWS)}, SPORT_LIMITS, "sport")


def test_a_sports_view_names_only_its_own_sport():
    found = named(set().union(*(names_of(s) for s in sports.ALL)))
    wrong = []
    for rel, hits in found.items():
        if rel.startswith(VIEWS):
            own = sports.BY_KEY.get(Path(rel).stem)
            wrong += [f"web/src/{rel}:{line} names {s!r}" for line, s in hits if own is None or s.lower() not in names_of(own)]
    assert not wrong, "\n".join(wrong)


def test_the_web_app_names_no_more_sources_than_before():
    held(named(set(KNOWN)), SOURCE_LIMITS, "source")
