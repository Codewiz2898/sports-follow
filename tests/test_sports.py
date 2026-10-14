"""Sports are modules (sports/): shared code asks a sport instead of naming it, and SPORTS_FOLLOW_SPORTS
turns sports off (CLAUDE.md, "Code organization")."""

from __future__ import annotations

import ast
from pathlib import Path

from sports_follow import adapters, moments, registry, search, sports
from sports_follow.adapters import ALL, KNOWN, pick

from .test_search import athlete, searching

PACKAGE = Path(__file__).resolve().parent.parent / "sports_follow"


def test_every_name_finds_its_sport():
    assert [sports.family(n) for n in ("Football", "soccer", "Association football", "CS2", "BGMI", "LoL", "battle_royale")] == ["football"] * 3 + ["esports"] * 4
    assert sports.get("golf") is None and sports.family("Golf") == "golf"  # an AI-built page's sport
    assert sports.of("golf") is sports.OTHER and sports.on("golf")  # gets the shared rules, and can't be turned off
    assert sports.of_state({"kind": "battle_royale"}).key == "esports"


def test_a_sport_no_module_knows_still_gets_a_start_and_a_result():
    before, now = moments.Seen("scheduled"), moments.Seen("live", score="12 under", line={"headline": "-3 thru 6", "stats": []})
    assert [f.kind for f in moments.detect("Golf", "Rory", "1", before, now)] == ["start"]
    done = moments.Seen("final", score="Won by 2", line={"headline": "-14", "stats": []})
    assert [(f.kind, f.title) for f in moments.detect("Golf", "Rory", "1", now, done)] == [("final", "Rory: Won by 2")]


def test_a_source_serves_one_sport_and_a_game_finds_the_source_that_covers_it(monkeypatch):
    assert all(sports.get(a.sport) for a in KNOWN.values())
    monkeypatch.setattr(adapters, "ADAPTERS", dict(KNOWN))
    assert adapters.for_sport("Soccer").system == "sportmonks_football"
    assert (adapters.for_sport("Valorant").system, adapters.for_sport("BGMI").system, adapters.for_sport("PUBG").system) == ("pandascore_esports", "liquipedia_esports", "pubg_esports")
    assert adapters.for_sport("Esports").system == "pandascore_esports"
    assert adapters.for_sport("LoL") is None  # esports, but no source covers it by that name
    assert adapters.for_sport("golf") is None


def test_the_setting_picks_the_sports_that_run():
    assert [s.key for s in sports.pick(sports.ALL, {"chess", "cricket", "darts"})] == ["cricket", "chess"]  # in the app's order; unknown names ignored
    assert sports.pick(sports.ALL, set()) == sports.ALL


def test_a_sport_turned_off_runs_none_of_its_sources():
    chess_only = pick(ALL, set(), sports.pick(sports.ALL, {"chess"}))
    assert list(chess_only) == ["lichess_chess"]
    assert "espn_cricket" not in pick(ALL, set(), sports.pick(sports.ALL, {"football", "chess"}))


def test_a_sport_turned_off_isnt_imported(monkeypatch):
    monkeypatch.setattr(sports, "ENABLED", sports.pick(sports.ALL, {"football", "esports"}))
    assert registry.importable() == ["football", "esports"]


def test_a_sport_turned_off_isnt_found_or_offered(monkeypatch):
    monkeypatch.setattr(sports, "ENABLED", sports.pick(sports.ALL, {"football"}))
    found = [athlete("Q1", "Virat Kohli", "cricket", sitelinks=60), athlete("Q2", "Virat Singh", "football", sitelinks=5)]
    searching(monkeypatch, found=found)
    assert [r["name"] for r in search.run(None, "virat")["results"]] == ["Virat Singh"]


# ---------------------------------------------------------------- shared code names no sport


def _docstrings(tree: ast.AST) -> set[int]:
    nodes = [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))]
    return {id(n.body[0].value) for n in nodes if n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}


def test_no_shared_module_branches_on_a_sport():
    names = {n.lower() for s in sports.ALL for n in (s.key, s.label, *s.names, *s.kinds)}
    shared = [*PACKAGE.glob("*.py"), PACKAGE / "adapters" / "__init__.py", PACKAGE / "adapters" / "base.py"]
    found = []
    for path in shared:
        tree = ast.parse(path.read_text())
        skip = _docstrings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip and node.value.lower() in names:
                found.append(f"{path.name}:{node.lineno} names {node.value!r}")
    assert not found, "\n".join(found)
