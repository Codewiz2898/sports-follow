"""Player search against ESPN and Lichess responses recorded on 2026-09-29 (search.py)."""

from __future__ import annotations

from datetime import datetime, timezone

from sports_follow import search
from sports_follow.search import Result

from .test_new_adapters import load


def keys(results: list[Result]) -> list[str]:
    return [r.key for r in results]


# ---------------------------------------------------------------- ESPN hits


def test_espn_hits_leave_out_college_athletes():
    results = search.parse_espn(load("espn/search-caitl"), "caitl")
    names = [r.name for r in results]
    assert "Caitlin Clark" in names and "Caitlin Foord" in names
    # ESPN lists a dozen NCAA players for "caitl" (Caitlyn Clark of Bridgewater State among them).
    assert not any(n.startswith("Caitlyn") for n in names)
    clark = results[0]
    assert (clark.key, clark.system, clark.athlete_id, clark.league) == ("espn_basketball:4433403", "espn_basketball", "4433403", "wnba")
    assert (clark.sport, clark.detail, clark.live_scores) == ("Basketball", "Indiana Fever · WNBA", True)


def test_espn_hits_in_sports_without_an_adapter_are_ai_only():
    carlsen = {r.name: r for r in search.parse_espn(load("espn/search-carlsen"), "carlsen")}
    simon = carlsen["Simon Carlsen"]
    assert (simon.sport, simon.system, simon.live_scores) == ("MMA", None, False)
    assert carlsen["Morris Carlsén"].system == "espn_soccer"
    assert "Lexi Carlsen" not in carlsen  # Drake Bulldogs, NCAAW
    [max_v] = search.parse_espn(load("espn/search-max-verstappen"), "max verstappen")
    assert (max_v.sport, max_v.detail, max_v.live_scores, max_v.exact) == ("Motor racing", "F1", False, True)


def test_two_athletes_with_one_name_are_both_exact():
    results = search.parse_espn(load("espn/search-nikola-jokic"), "Nikola Jokić")
    assert [(r.sport, r.exact) for r in results] == [("Basketball", True), ("Football", True)]


# ---------------------------------------------------------------- FIDE hits


def test_fide_hits_notable_first_and_amateurs_only_when_asked():
    records = load("lichess/fide-search-carlsen")
    among_all = search.parse_fide(records, "carlsen", search.CHESS_LIMIT)
    # Titled or 2000+ only, active before inactive: Henrik (2063, inactive) after Christian (2048).
    assert [r.name for r in among_all] == ["Magnus Carlsen", "Christian Heen Carlsen", "Henrik Carlsen"]
    assert among_all[0].detail == "GM · Norway · 2823 classical"
    assert among_all[2].detail == "Norway · 2063 classical · inactive"
    chess_only = search.parse_fide(records, "carlsen", 10)
    assert len(chess_only) == 10 and chess_only[0].name == "Magnus Carlsen"
    flags = [r.inactive for r in chess_only]
    assert flags == sorted(flags)  # no inactive player above an active one


def test_fide_hits_must_match_every_word():
    records = load("lichess/fide-search-carlsen")
    assert search.parse_fide(records, "magnus carlsen", 10)[0].name == "Magnus Carlsen"
    assert search.parse_fide(records, "max carlsen", 10) == []


# ---------------------------------------------------------------- merging and ranking


def test_rank_puts_players_on_sports_follow_first_and_merges_by_athlete():
    espn_hits = search.parse_espn(load("espn/search-caitl"), "caitl")
    ours = Result(key="espn_basketball:4433403", name="Caitlin Clark", sport="Basketball", detail="Indiana Fever", system="espn_basketball", athlete_id="4433403", player_id=8, followers=2, live_scores=True, _rank=(0.4,))
    ranked = search.rank("caitl", [ours], espn_hits)
    assert ranked[0] is ours and keys(ranked).count("espn_basketball:4433403") == 1
    assert ranked[0].detail == "Indiana Fever · WNBA"  # the source's line, when it says more


def test_rank_orders_live_sources_before_ai_only_sports():
    hits = [*search.parse_espn(load("espn/search-carlsen"), "carlsen"), *search.parse_fide(load("lichess/fide-search-carlsen"), "carlsen", search.CHESS_LIMIT)]
    ranked = search.rank("carlsen", [], hits)
    assert ranked[0].name == "Magnus Carlsen"  # a GM before unpictured namesakes
    live = [r.live_scores for r in ranked]
    assert live == sorted(live, reverse=True)  # MMA and baseball Carlsens last
    assert ranked[-1].sport in ("MMA", "Baseball")


def test_a_typo_is_a_suggestion_not_a_result():
    near = Result(key="espn_basketball:4433403", name="Caitlin Clark", sport="Basketball", detail="", player_id=8, matched=False, _rank=(0.62,))
    assert search.rank("catalin clark", [near], []) == []
    assert search.closeness("catalin clark", "Caitlin Clark") >= search.SUGGEST_MIN
    assert search.closeness("catalin clark", "Catalin Carp") < search.SUGGEST_MIN


def test_prefix_match_is_per_word_and_order_free():
    assert search._prefix_match("caitl", "Caitlin Clark")
    assert search._prefix_match("clark caitlin", "Caitlin Clark")
    assert search._prefix_match("jokic", "Nikola Jokić")
    assert not search._prefix_match("catalin clark", "Caitlin Clark")


# ---------------------------------------------------------------- AI research allowance


def test_research_allowance_resets_at_utc_midnight():
    now = datetime(2026, 9, 29, 21, 30, tzinfo=timezone.utc)
    assert search.research_key("fan1", now) == "research:fan1:2026-09-29"
    assert search.research_status(1, now) == {"used": 1, "limit": 3, "left": 2, "resets_at": "2026-09-30T00:00:00+00:00"}
    assert search.research_status(5, now)["left"] == 0


def test_closeness_matches_the_words_that_were_typed():
    assert search.closeness("sabalnka", "Aryna Sabalenka") >= search.SUGGEST_MIN  # just the surname, misspelled
    assert search.closeness("kohly", "Virat Kohli") >= search.SUGGEST_MIN
    assert search.closeness("serena wiliams", "Serena Williams") >= search.SUGGEST_MIN
    assert search.closeness("kohly", "Nikola Jokić") < search.SUGGEST_MIN


def test_popularity_counts_double_for_players_of_today():
    novak = Result(key="espn_tennis:296", name="Novak Djokovic", sport="Tennis", detail="", live_scores=True, inactive=True, popularity=152)
    marko = Result(key="espn_tennis:1482", name="Marko Djokovic", sport="Tennis", detail="", live_scores=True, popularity=11)
    carlos = Result(key="espn_tennis:3782", name="Carlos Djokovic", sport="Tennis", detail="", live_scores=True, popularity=90)
    assert [r.name for r in search.rank("djok", [], [marko, novak, carlos])] == ["Carlos Djokovic", "Novak Djokovic", "Marko Djokovic"]
