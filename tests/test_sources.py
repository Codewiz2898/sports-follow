"""Sources describe themselves (adapters.base.Adapter), and nothing outside a source's own modules names it,
so adding a source is its module plus one entry in adapters.KNOWN (CLAUDE.md, "Code organization")."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from sports_follow import search
from sports_follow.adapters import KNOWN, Provider, providers, source_name

PACKAGE = Path(__file__).resolve().parent.parent / "sports_follow"
SOURCE_MODULES = {p.stem for p in (PACKAGE / "adapters").glob("*.py")} - {"__init__", "base"}


def test_every_source_describes_itself():
    for system, adapter in KNOWN.items():
        assert adapter.system == system and adapter.sports and adapter.live_cadence > 0, system
        assert adapter.name and adapter.covers and isinstance(adapter.provider, Provider), system
        assert not adapter.id_pattern.fullmatch(""), system
    assert {p.name for p, _ in providers(KNOWN)} == {"Sportmonks", "ESPN", "Lichess", "PandaScore", "Liquipedia", "PUBG"}


def test_providers_keep_the_order_of_preference():
    order = [(p.name, [a.system for a in adapters]) for p, adapters in providers(KNOWN)]
    assert order[:3] == [
        ("Sportmonks", ["sportmonks_football", "sportmonks_cricket"]),
        ("ESPN", ["espn_soccer", "espn_cricket", "espn_basketball", "espn_tennis"]),
        ("Lichess", ["lichess_chess"]),
    ]


def test_a_source_no_longer_known_is_named_by_its_system():
    # A stored binding or event may name a source since removed.
    assert source_name("espn_cricket") == "ESPNcricinfo" and source_name("sportmonks_cricket") == "Sportmonks"
    assert source_name("gone_source") == "gone_source"


def test_live_searches_are_asked_only_under_the_sports_their_sources_serve(monkeypatch):
    monkeypatch.setattr(search, "providers", lambda: providers(KNOWN))

    def asked(query: str, sport: str | None = None, by_word: bool = False) -> list[str]:
        return [p.name for p, _ in search._live(query, sport, by_word)]

    assert asked("kohli") == asked("kohli", "cricket") == ["ESPN"]
    assert asked("carlsen", "chess") == ["Lichess"]  # FIDE's list only for a chess search
    assert asked("ca", "chess") == []  # three letters before Lichess is asked
    assert asked("tenz", "esports") == []
    assert asked("clark", by_word=True) == ["ESPN"]  # did-you-mean, word by word: ESPN has no typo tolerance


def _docstrings(tree: ast.AST) -> set[int]:
    nodes = [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))]
    return {id(n.body[0].value) for n in nodes if n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}


def test_no_shared_module_names_a_source():
    named = re.compile(rf"\b({'|'.join(KNOWN)})\b")
    found = []
    for path in [*PACKAGE.glob("*.py"), *(PACKAGE / "sports").glob("*.py")]:
        tree = ast.parse(path.read_text())
        skip = _docstrings(tree)
        if path.name == "registry.py":
            # The Wikidata ids that mark a sport's athletes, keyed by the id they hold: they move to the
            # sports themselves (BHA-178).
            sports = next(n for n in tree.body if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", None) == "SPORTS")
            skip |= {id(n) for n in ast.walk(sports)}
        for node in ast.walk(tree):
            module = (getattr(node, "module", None) or "").split(".")
            if isinstance(node, ast.ImportFrom) and module[-2:-1] == ["adapters"] and module[-1] in SOURCE_MODULES:
                found.append(f"{path.name}:{node.lineno} imports adapters.{module[-1]}")  # from .adapters.espn import …
            elif isinstance(node, ast.ImportFrom) and module[-1] == "adapters":
                found += [f"{path.name}:{node.lineno} imports {a.name}" for a in node.names if a.name in SOURCE_MODULES]  # from .adapters import espn
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip and named.search(node.value):
                found.append(f"{path.name}:{node.lineno} names {named.search(node.value)[0]}")
    assert not found, "\n".join(found)


def test_no_source_borrows_from_another():
    # What two sources of a sport share lives in sports/ (Sportmonks cricket once read ESPN's scorecard
    # parser), so removing one never breaks another. A provider's client module (espn, sportmonks) is
    # shared by its own adapters.
    adapter_modules = {type(a).__module__.rsplit(".", 1)[-1] for a in KNOWN.values()}
    found = []
    for name in adapter_modules:
        tree = ast.parse((PACKAGE / "adapters" / f"{name}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                imported = [node.module] if node.module else [a.name for a in node.names]
                found += [f"{name} imports {m}" for m in imported if m in adapter_modules - {name}]
    assert not found, "\n".join(found)
