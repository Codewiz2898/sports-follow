"""The Credits page: it credits what the build runs, with the licences the sources and code carry."""

from __future__ import annotations

import json
import re
from importlib.metadata import metadata
from pathlib import Path

from sports_follow import controls, credits
from sports_follow.adapters import ALL, KNOWN, NEWS, VIDEOS
from sports_follow.sports import chess, football


def _names(items: list[dict]) -> list[str]:
    return [c["name"] for c in items]


def test_a_public_build_credits_its_licensed_sources_only():
    page = credits.credits({"sportmonks_football": 1, "sportmonks_cricket": 1, "lichess_chess": 1}, stockfish=True, on=(football.SPORT, chess.SPORT))
    assert _names(page["data"]) == ["Sportmonks", "Lichess"]
    assert page["data"][0]["what"].startswith("Football and cricket: ")
    lichess = page["data"][1]
    assert (lichess["licence"], lichess["licence_url"]) == ("Broadcast games: CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/")
    assert _names(page["also"]) == ["Wikidata", "Wikipedia", "FIDE", "Stockfish", "Stockfish.js", "AI", "News publishers"]
    wikipedia = page["also"][1]  # each player's About paragraph is CC BY-SA, which asks for the credit and the licence
    assert (wikipedia["licence"], wikipedia["licence_url"]) == ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/")


def test_the_engine_on_the_fans_device_is_credited_while_chess_is_on():
    # Play from here runs Stockfish.js on the fan's phone or browser (GPL-3.0): its credit links its source, the
    # commit that built it, and the licence as that commit has it; with chess off, there's no board to run it.
    js = next(c for c in credits.credits({}, stockfish=False, on=(chess.SPORT,))["also"] if c["name"] == "Stockfish.js")
    assert (js["licence"], js["url"], js["licence_url"]) == ("GPL-3.0", chess.ENGINE_SOURCE, chess.ENGINE_LICENCE)
    assert "Stockfish.js" not in _names(credits.credits({}, stockfish=False, on=(football.SPORT,))["also"])


ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "web" / "src" / "sports" / "engine"


def test_the_engines_source_is_the_one_the_boards_link():
    # The boards' "source" link (web/src/sports/facts/chessEngineCore.ts) and the Credits page name one commit.
    core = (ROOT / "web" / "src" / "sports" / "facts" / "chessEngineCore.ts").read_text()
    built = re.search(r"const BUILT_FROM = '([0-9a-f]{40})'", core)
    assert built and built[1] == chess.ENGINE_BUILT_FROM


def test_every_engine_file_named_is_there():
    # Each place that names the engine's files by name: the web's two ?url imports, the Android module's copy (whose
    # build fails on a missing one, but CI doesn't build the app), and the Android page's worker. A renamed or
    # missing file would ship a board whose engine never starts.
    web = re.findall(r"from '\./engine/([^'?]+)\?url'", (ROOT / "web" / "src" / "sports" / "chessEngine.ts").read_text())
    android = ROOT / "mobile" / "modules" / "chess-engine" / "android"
    gradle = re.search(r"def engineFiles = \[([^\]]*)\]", (android / "build.gradle").read_text())
    copied = re.findall(r'"([^"]+)"', gradle[1]) if gradle else []
    page = re.findall(r"new Worker\('([^'#]+)#' \+ encodeURIComponent\('([^']+)'\)\)", (android / "src" / "main" / "assets" / "engine" / "page.js").read_text())
    ran = [name for pair in page for name in pair]
    assert len(web) == 2 and len(copied) == 3 and len(ran) == 2
    assert set(ran) <= set(copied)  # what the page runs is what the build copies
    missing = [name for name in (*web, *copied, *ran) if not (ENGINE / name).is_file()]
    assert not missing, f"named but not in web/src/sports/engine: {missing}"


def test_a_development_build_also_names_espn():
    page = credits.credits({"espn_soccer": 1, "espn_cricket": 1, "espn_tennis": 1}, stockfish=False, on=(football.SPORT,))
    assert _names(page["data"]) == ["ESPN"]
    assert page["data"][0]["what"].startswith("Football, cricket (ESPNcricinfo) and tennis: ")
    assert "FIDE" not in _names(page["also"]) and "Stockfish" not in _names(page["also"])  # no chess, no engine


def test_the_ai_credit_names_the_model():
    ai = next(c for c in credits.credits({}, stockfish=False)["also"] if c["name"] == "AI")
    assert credits._model() in ai["what"] and "can get things wrong" in ai["note"]


def test_a_build_without_the_agent_credits_no_model_and_no_news():
    # SPORTS_FOLLOW_AGENT=0 (no OpenRouter key): pages come from the sources alone.
    page = credits.credits({"sportmonks_football": 1, "pandascore_esports": 1, "pubg_esports": 1}, stockfish=False, agent=False)
    assert "PandaScore" in _names(page["data"]) and "AI" not in _names(page["also"]) and "News publishers" not in _names(page["also"])


def test_a_sport_or_source_turned_off_keeps_its_credit():
    # Turning chess, or Lichess, off in the admin panel stops it being read, but the players' pages, results and boards
    # built from Lichess's broadcasts stay up: Lichess's CC BY-SA credit and its licence link stay with them, and so do
    # FIDE's ratings and the engine under the boards. A source this build can't run (no token) is still never credited.
    lichess = KNOWN["lichess_chess"].provider
    for turn in (lambda on: controls.turn_sport("chess", on), lambda on: controls.turn_source("lichess_chess", on)):
        turn(False)
        page = credits.credits(stockfish=False, agent=False)
        entry = next((c for c in page["data"] if c["name"] == lichess.name), None)
        assert entry and (entry["licence"], entry["licence_url"]) == (lichess.licence, lichess.licence_url)
        assert {"FIDE", "Stockfish.js"} <= set(_names(page["also"]))
        turn(True)
    can = {a.provider.name for a in ALL}
    assert set(_names(credits.credits(stockfish=False, agent=False)["data"])) <= can


def test_the_feeds_sources_are_credited():
    # The Feed shows videos and publishers' headlines with or without the research agent: each of its sources is
    # credited for what it gives, a news source with the publishers whose feeds the sports read.
    feeds = [*VIDEOS.values(), *NEWS.values()]
    page = credits.credits({s.system: s for s in feeds}, stockfish=False, agent=False, on=(football.SPORT, chess.SPORT))
    also = {c["name"]: c for c in page["also"]}
    for source in feeds:
        entry = also.get(source.provider.name)
        assert entry and entry["url"] == source.provider.url and source.provider.what in entry["what"], source.system
    for source in NEWS.values():
        read = [outlet for s in (football.SPORT, chess.SPORT) for feed, outlet in s.news_feeds.items() if source.feed_pattern.fullmatch(feed)]
        assert read and all(outlet in also[source.provider.name]["what"] for outlet in read)
    # By default, those this build can run (a video source with no key isn't), whether the admin panel has them on now.
    for source in feeds:
        controls.turn_source(source.system, False)
    named = _names(credits.credits(stockfish=False, agent=False)["also"])
    assert [s.provider.name in named for s in feeds] == [s.available() for s in feeds]


ROOT_WEB = ROOT / "web"
# A package the web app ships that is part of another's project, credited as that one.
PART_OF = {"react-dom": "react"}


def test_every_package_the_web_app_ships_is_credited():
    # What web/package.json puts in the bundle (its dependencies, not the tools that build it) is under Built with,
    # with the licence its package gives where it's installed; the page itself shows only the name, link and licence.
    shipped = json.loads((ROOT_WEB / "package.json").read_text())["dependencies"]
    listed = {item["npm"]: item for item in credits.SOFTWARE if "npm" in item}
    missing = [name for name in shipped if PART_OF.get(name, name) not in listed]
    assert not missing, f"shipped but not credited: {missing}"
    for name, item in listed.items():
        installed = ROOT_WEB / "node_modules" / name / "package.json"
        if installed.is_file():
            assert item["licence"] == json.loads(installed.read_text())["license"], name
    assert all(set(s) <= {"name", "url", "licence"} for s in credits.credits({}, stockfish=False)["software"])


def test_listed_python_licences_match_the_installed_packages():
    for item in credits.SOFTWARE:
        if "package" not in item:
            continue
        meta = metadata(item["package"])
        assert item["licence"] == (meta.get("License-Expression") or meta.get("License")), item["name"]
