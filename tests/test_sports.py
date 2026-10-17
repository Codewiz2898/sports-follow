"""Sports are modules (sports/): shared code asks a sport instead of naming it, and SPORTS_FOLLOW_SPORTS
turns sports off (CLAUDE.md, "Code organization")."""

from __future__ import annotations

import ast
import itertools
import math
import re
from pathlib import Path

import pytest

from sports_follow import adapters, admin, controls, moments, registry, search, sports
from sports_follow.adapters import ALL, KNOWN, pick

from .test_api import api  # noqa: F401  (the fixture)
from .test_search import athlete, searching

PACKAGE = Path(__file__).resolve().parent.parent / "sports_follow"


def test_every_name_finds_its_sport():
    assert [sports.family(n) for n in ("Football", "soccer", "Association football")] == ["football"] * 3
    # Each esports game is a sport of its own, by any name a source or a page gives it.
    assert [sports.family(n) for n in ("CS2", "Counter-Strike", "BGMI", "LoL", "League of Legends", "PUBG", "PUBG Mobile", "King of Glory")] == ["cs2", "cs2", "bgmi", "lol", "lol", "pubg", "pubgmobile", "hok"]
    # A game no source covers, or "Esports" itself, is the esports group: esports' rules, no game's.
    assert [sports.family(n) for n in ("Esports", "e-sports", "Call of Duty: Mobile")] == ["esports"] * 3
    assert sports.get("golf") is None and sports.family("Golf") == "golf"  # an AI-built page's sport
    assert sports.of("golf") is sports.OTHER and sports.on("golf")  # gets the shared rules, and can't be turned off
    assert sports.of_state({"kind": "battle_royale"}).key == "esports"  # every game's scoreboards are read by esports' rules


def test_every_game_is_a_sport_of_its_own_sharing_esports_rules():
    games = [s for s in sports.ALL if s.group == "esports"]
    assert [g.key for g in games][:7] == ["valorant", "cs2", "lol", "dota2", "bgmi", "freefire", "pubgmobile"]
    assert sports.members(sports.BY_KEY["esports"]) == tuple(games) and sports.BY_KEY["esports"] not in sports.ALL
    assert len({g.label for g in games}) == len(games) and all(g.colour and g.pattern for g in games)
    # One place for their rules: each game is an esports sport, overriding nothing but its name and colour.
    esports = type(sports.BY_KEY["esports"])
    assert all(isinstance(g, esports) for g in games)
    assert all(type(g).__dict__.keys() <= {"__module__", "__doc__", "__init__", "__repr__", "group", "kinds", "__firstlineno__", "__static_attributes__", "__qualname__"} for g in games)
    assert [sports.label(n) for n in ("cs2", "valorant", "PUBG Mobile", "Esports")] == ["CS2", "Valorant", "PUBG Mobile", "Esports"]
    # Every source covers the games it says, each one game's sport.
    covered = {k for a in KNOWN.values() for k in a.sports}
    assert {g.key for g in games} <= covered and all(sports.BY_KEY[k] in sports.ALL for k in covered)


def test_esports_in_a_setting_stands_for_every_game():
    games = [s.key for s in sports.ALL if s.group]
    assert [s.key for s in sports.pick(sports.ALL, {"esports", "chess"})] == ["chess", *games]
    assert [s.key for s in sports.pick(sports.ALL, {"valorant", "cricket"})] == ["cricket", "valorant"]
    assert sports.expand(["football", "esports"]) == ["football", *games] and sports.expand(["cs2", "golf"]) == ["cs2", "golf"]


def test_a_game_turned_off_leaves_the_others_running(monkeypatch):
    controls.reset(controls.MemoryStore())
    try:
        admin.set_sport("valorant", False)
        assert "valorant" not in [s.key for s in sports.ENABLED] and "cs2" in [s.key for s in sports.ENABLED]
        assert sports.on("CS2") and not sports.on("Valorant") and sports.on("Esports")  # the group, while any game runs
        # A source of several games runs while one does, and reads only those that run.
        assert sports.BY_KEY["valorant"] not in sports.ENABLED and "valorant" not in KNOWN["pandascore_esports"].running()
        assert "cs2" in KNOWN["pandascore_esports"].running()
        # Turned off as "esports" (a row from before each game was a sport): every game is off, and turning
        # one back on leaves the others off.
        controls.STORE.rows[controls.SPORTS_OFF] = ["esports"]
        controls.load()
        assert not any(s.group for s in sports.ENABLED) and not sports.on("Esports")
        admin.set_sport("bgmi", True)
        assert [s.key for s in sports.ENABLED if s.group] == ["bgmi"] and "esports" not in controls.STORE.rows[controls.SPORTS_OFF]
    finally:
        controls.reset(controls.MemoryStore())


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


def test_a_source_serves_its_sports_and_a_game_finds_the_source_that_covers_it(monkeypatch):
    assert all(a.sports and all(sports.BY_KEY[k] in sports.ALL for k in a.sports) for a in KNOWN.values())
    monkeypatch.setattr(adapters, "ADAPTERS", dict(KNOWN))
    assert adapters.for_sport("Soccer").system == "sportmonks_football"
    assert (adapters.for_sport("Valorant").system, adapters.for_sport("BGMI").system, adapters.for_sport("PUBG").system) == ("pandascore_esports", "liquipedia_esports", "pubg_esports")
    assert adapters.for_sport("LoL").system == "pandascore_esports"  # a game's other name
    assert adapters.for_sport("Esports").system == "pandascore_esports"  # a pro whose game isn't said: looked up across them
    assert adapters.for_sport("Call of Duty: Mobile").system == "pandascore_esports" and adapters.for_sport("golf") is None


def test_the_setting_picks_the_sports_that_run():
    assert [s.key for s in sports.pick(sports.ALL, {"chess", "cricket", "darts"})] == ["cricket", "chess"]  # in the app's order; unknown names ignored
    assert sports.pick(sports.ALL, set()) == sports.ALL


def test_a_sport_turned_off_runs_none_of_its_sources():
    chess_only = pick(ALL, set(), sports.pick(sports.ALL, {"chess"}))
    assert list(chess_only) == ["lichess_chess"]
    assert "espn_cricket" not in pick(ALL, set(), sports.pick(sports.ALL, {"football", "chess"}))


def test_a_sport_turned_off_isnt_imported(monkeypatch):
    monkeypatch.setattr(sports, "ENABLED", sports.pick(sports.ALL, {"football", "valorant", "bgmi"}))
    assert registry.importable() == ["football", "valorant", "bgmi"]


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
    # Each esports game is a sport of its own, drawn as esports (its group); the group itself isn't listed,
    # but the app knows it, for a game no source covers.
    cs2 = next(s for s in body["sports"] if s["key"] == "cs2")
    assert cs2["label"] == "CS2" and cs2["group"] == "esports" and football["group"] == ""
    assert cs2["team_results"] and cs2["keeps_names"] and cs2["stats_title"] == "Overview"
    assert "esports" not in [s["key"] for s in body["sports"]] and [g["key"] for g in body["groups"]] == ["esports"]
    assert body["groups"][0]["on"] is False and body["groups"][0]["pattern"]  # no game runs here
    # The Android app can't read /api/sports.css: each sport brings its colours here too, "other" none.
    cricket = next(s for s in body["sports"] if s["key"] == "cricket")
    assert cricket["colour"] == {"sport": "#1f5bef", "on": "#ffffff", "on_soft": "#ffffff", "ink": "#7aa2ff"}
    assert all(s["colour"] for s in body["sports"]) and body["other"]["colour"] is None


def test_each_sport_brings_its_colour(api):
    res = api.call("GET", "/api/sports.css")
    assert res.headers["content-type"].startswith("text/css")
    assert "[data-sport='cricket'] { --sport: #1f5bef; --on-sport: #ffffff; --on-sport-soft: #ffffff; --sport-on-ink: #7aa2ff; }" in res.text
    assert all(f"[data-sport='{s.key}']" in res.text for s in sports.ALL)


WEB = PACKAGE.parent / "web" / "src"


def test_every_apps_sport_facts_are_of_a_sport_the_server_knows():
    """web/src/sports/facts/ holds what both apps draw a sport by (its scoreboard kinds, its line art): a sport's,
    or a group's, which draws each of its sports (esports, every game)."""
    facts = list((WEB / "sports" / "facts").glob("*.ts"))
    views = {m.group(1): p.name for p in facts for m in re.finditer(r"^  key: '(\w+)',$", p.read_text(), re.M)}
    assert views and set(views) <= set(sports.BY_KEY), views
    kinds = {k for p in facts for m in re.finditer(r"^  kinds: \[(.*)\],$", p.read_text(), re.M) for k in re.findall(r"'(\w+)'", m.group(1))}
    assert kinds <= {k for s in (*sports.ALL, *sports.GROUPS) for k in s.kinds}


def _rgb(colour: str) -> tuple[float, ...]:
    return tuple(int(colour.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))


def _luminance(colour: str) -> float:
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in _rgb(colour)]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _over(colour: str, block: str) -> str:
    """ "rgba(26, 0, 16, 0.85)" laid over a block, as a colour."""
    if not colour.startswith("rgba"):
        return colour
    *rgb, alpha = (float(x) for x in colour[5:-1].split(","))
    return "#" + "".join(f"{round((c / 255 * alpha + b * (1 - alpha)) * 255):02x}" for c, b in zip(rgb, _rgb(block)))


def _lab(colour: str) -> tuple[float, float, float]:
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in _rgb(colour)]
    xyz = [sum(m * c for m, c in zip(row, linear)) / n for row, n in zip(((0.4124, 0.3576, 0.1805), (0.2126, 0.7152, 0.0722), (0.0193, 0.1192, 0.9505)), (0.95047, 1.0, 1.08883))]
    f = [t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116 for t in xyz]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


@pytest.mark.parametrize("sport", [s for s in (*sports.ALL, *sports.GROUPS) if s.colour], ids=lambda s: s.key)
def test_a_sports_text_reads_on_its_colour(sport):
    """styles.css: the ink on a block, and that ink for secondary text, read at 4.5:1 or better; so does the
    sport's colour as text on the dark ink."""
    c = sport.colour
    assert _contrast(c["on"], c["sport"]) >= 4.5 and _contrast(_over(c["on_soft"], c["sport"]), c["sport"]) >= 4.5
    assert _contrast(c["ink"], "#0d0d0d") >= 4.5


def test_every_sport_wears_a_colour_of_its_own():
    """No two sports' blocks alike (CIE76 distance in Lab), nor one like the red that means "live" (styles.css)."""
    blocks = {s.key: s.colour["sport"] for s in sports.ALL} | {"live": "#dc2016"}
    close = [(a, b, round(math.dist(_lab(blocks[a]), _lab(blocks[b])))) for a, b in itertools.combinations(blocks, 2) if math.dist(_lab(blocks[a]), _lab(blocks[b])) < 20]
    assert not close, close
