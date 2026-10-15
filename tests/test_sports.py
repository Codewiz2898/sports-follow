"""Sports are modules (sports/): shared code asks a sport instead of naming it, and SPORTS_FOLLOW_SPORTS
turns sports off (CLAUDE.md, "Code organization")."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from sports_follow import adapters, moments, registry, search, sports
from sports_follow.adapters import ALL, KNOWN, pick

from .test_api import api  # noqa: F401  (the fixture)
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


def test_a_sport_that_says_how_a_score_is_taken_back_gets_corrections(monkeypatch):
    # Nothing shared names football: a sport's own taken_back is all a correction needs (alerts.py and
    # notify.py route it by its kind, "correction").
    class Darts(sports.Sport):
        key = "darts"

        def taken_back(self, who, athlete_id, before, now, told):
            legs = moments.number(moments.stat(now.line, "Legs"))
            return [moments.Found("correction", "key", f"Leg taken back: {who}", now.score, f"leg:{d}") for k, d in told if k == "leg" and int(d) > legs]

    monkeypatch.setattr(sports, "of", lambda name: Darts())
    legs = lambda n: {"headline": f"{n} legs", "stats": [{"label": "Legs", "value": str(n)}]}  # noqa: E731
    before, now = moments.Seen("live", line=legs(3)), moments.Seen("live", line=legs(2), score="2–2")
    [f] = moments.detect("Darts", "Luke Littler", "1", before, now, [("leg", "2"), ("leg", "3")])
    assert (f.kind, f.level, f.title, f.detail, f.priority) == ("correction", "key", "Leg taken back: Luke Littler", "leg:3", 85)
    assert moments.detect("Darts", "Luke Littler", "1", before, now) == []  # nothing told, nothing to take back


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


# ---------------------------------------------------------------- the app takes the sports from the server


def test_the_app_is_sent_every_sport_in_order_with_what_it_says(api, monkeypatch):
    monkeypatch.setattr(sports, "ENABLED", sports.pick(sports.ALL, {"football", "chess"}))
    body = api.call("GET", "/api/sports").json()
    assert [s["key"] for s in body["sports"]] == [s.key for s in sports.ALL]  # the off ones too: their players' pages keep their colour
    assert [s["key"] for s in body["sports"] if s["on"]] == ["football", "chess"]
    football = body["sports"][0]
    assert football["label"] == "Football" and football["pattern"] and football["alerts"]["key"][0] == "Line-ups, goals and results"
    assert body["other"]["key"] == "other" and body["other"]["alerts"]["key"][0] == "Big news"
    esports = next(s for s in body["sports"] if s["key"] == "esports")
    assert esports["team_results"] and esports["keeps_names"] and esports["stats_title"] == "Overview"
    # The Android app can't read /api/sports.css: each sport brings its colours here too, "other" none.
    cricket = next(s for s in body["sports"] if s["key"] == "cricket")
    assert cricket["colour"] == {"sport": "#1f5bef", "on": "#ffffff", "on_soft": "#ffffff", "ink": "#7aa2ff"}
    assert all(s["colour"] for s in body["sports"]) and body["other"]["colour"] is None


def test_each_sport_brings_its_colour(api):
    res = api.call("GET", "/api/sports.css")
    assert res.headers["content-type"].startswith("text/css")
    assert "[data-sport='cricket'] { --sport: #1f5bef; --on-sport: #ffffff; --on-sport-soft: #ffffff; --sport-on-ink: #7aa2ff; --photo-dark: #0d2a7a; --photo-light: #133be0; }" in res.text
    assert all(f"[data-sport='{s.key}']" in res.text for s in sports.ALL)


def _rgb(colour: str) -> tuple[float, ...]:
    return tuple(int(colour.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))


@pytest.mark.parametrize("sport", [s for s in sports.ALL if s.colour], ids=lambda s: s.key)
def test_a_photo_is_recoloured_from_the_sports_deep_colour_to_its_block_colour(sport):
    """The page multiplies the grey photo by --photo-light and screens --photo-dark over it (styles.css,
    .duo): black must come out as the deep colour, white as the block colour, and grey in between."""
    dark, light = sports.photo_tones(sport)
    d, pre, block = _rgb(dark), _rgb(light), _rgb(sport.colour["sport"])
    assert all(dc < bc for dc, bc in zip(d, block)) or sum(d) < sum(block), "the photo's darks must be the darker"

    def shown(grey: float) -> tuple[float, ...]:
        return tuple(1 - (1 - grey * p) * (1 - dc) for p, dc in zip(pre, d))

    assert shown(0) == pytest.approx(d)
    assert shown(1) == pytest.approx(block, abs=1 / 255)
    assert shown(0.5) == pytest.approx(tuple((dc + bc) / 2 for dc, bc in zip(d, block)), abs=1 / 255)


WEB = PACKAGE.parent / "web" / "src"


def test_every_web_view_is_of_a_sport_the_server_knows():
    views = {m.group(1): p.name for p in (WEB / "sports").glob("*.tsx") for m in re.finditer(r"^  key: '(\w+)',$", p.read_text(), re.M)}
    assert views and set(views) <= set(sports.BY_KEY), views
    kinds = {k for p in (WEB / "sports").glob("*.tsx") for m in re.finditer(r"^  kinds: \[(.*)\],$", p.read_text(), re.M) for k in re.findall(r"'(\w+)'", m.group(1))}
    assert kinds <= {k for s in sports.ALL for k in s.kinds}
