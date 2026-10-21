"""The Credits page: it credits what the build runs, with the licences the sources and code carry."""

from __future__ import annotations

import re
from importlib.metadata import metadata
from pathlib import Path

from sports_follow import credits
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


def test_the_engines_source_is_the_one_the_boards_link():
    # The boards' "source" link (web/src/sports/facts/chessEngineCore.ts) and the Credits page name one commit.
    core = (Path(__file__).resolve().parent.parent / "web" / "src" / "sports" / "facts" / "chessEngineCore.ts").read_text()
    built = re.search(r"const BUILT_FROM = '([0-9a-f]{40})'", core)
    assert built and built[1] == chess.ENGINE_BUILT_FROM


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


def test_listed_python_licences_match_the_installed_packages():
    for item in credits.SOFTWARE:
        if "package" not in item:
            continue
        meta = metadata(item["package"])
        assert item["licence"] == (meta.get("License-Expression") or meta.get("License")), item["name"]
