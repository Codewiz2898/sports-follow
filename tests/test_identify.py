"""Recognising a typed name from the structured sources alone (structured.identify), end to end on what
ESPN and Lichess answered on 2026-10-15 (tests/fixtures/espn, tests/fixtures/lichess), trimmed to what
the code reads; the Nikola Jokić and Max Verstappen searches are from 2026-09-29.

identify asks ESPN's search first and reads the athlete from the adapter of their sport; when ESPN knows
nobody by the name, Lichess looks in FIDE's list. Each request is answered from a recording, and one
nothing was recorded for fails the test, so no case passes on an answer the sources never gave. The
build is a development one without licensed tokens: ESPN's four adapters and Lichess.
"""

from __future__ import annotations

from collections.abc import Iterator

from datetime import date

import httpx
import pytest

from sports_follow import adapters, registry, structured
from sports_follow.adapters import KNOWN, Adapter, Provider, espn, lichess_chess

from .test_new_adapters import load

SEARCH = "https://site.web.api.espn.com/apis/search/v2?query={}&limit=10"
ATHLETE = "https://site.web.api.espn.com/apis/common/v3/sports/{}"
FIDE = "https://lichess.org/api/fide/player"

# The URL identify asks, and the recording of the answer.
RECORDED = {
    SEARCH.format("Caitlin%20Clark"): "espn/search-caitlin-clark",
    ATHLETE.format("basketball/wnba/athletes/4433403"): "espn/basketball-athlete-caitlin-clark",
    SEARCH.format("Caitlyn%20Clark"): "espn/search-caitlyn-clark",
    ATHLETE.format("basketball/womens-college-basketball/athletes/5324154"): "espn/basketball-athlete-caitlyn-clark",
    SEARCH.format("A%27ja%20Wilson"): "espn/search-aja-wilson",
    ATHLETE.format("basketball/wnba/athletes/3149391"): "espn/basketball-athlete-aja-wilson",
    SEARCH.format("Pau%20Sans"): "espn/search-pau-sans",
    ATHLETE.format("soccer/athletes/359149"): "espn/soccer-athlete-pau-sans",
    SEARCH.format("Spain"): "espn/search-spain",  # Pau Sans's national team, by his citizenship
    SEARCH.format("Ahmed%20Salah"): "espn/search-ahmed-salah",
    ATHLETE.format("soccer/athletes/135729"): "espn/soccer-athlete-ahmed-salah-hosni",
    SEARCH.format("Nikola%20Joki%C4%87"): "espn/search-nikola-jokic",
    SEARCH.format("Max%20Verstappen"): "espn/search-max-verstappen",
    SEARCH.format("Jos%20Butler"): "espn/search-jos-butler",
    f"{FIDE}?q=Jos%20Butler": "lichess/fide-search-jos-butler",
    f"{FIDE}?q=butler": "lichess/fide-search-butler",
    SEARCH.format("Gukesh%20Dommaraju"): "espn/search-gukesh-dommaraju",
    f"{FIDE}?q=Gukesh%20Dommaraju": "lichess/fide-search-gukesh-dommaraju",
    f"{FIDE}/46616543": "lichess/fide-player-gukesh",
}

class Recorded:
    """ESPN and Lichess as recorded. A URL with no recording fails the test."""

    def __init__(self):
        self.asked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.asked.append(url)
        if url in RECORDED:
            return httpx.Response(200, json=load(RECORDED[url]))
        raise AssertionError(f"identify asked for {url}, which wasn't recorded")


def lineup(*providers: Provider) -> dict[str, Adapter]:
    """These providers' adapters, as adapters.ADAPTERS holds the running ones."""
    return {a.system: a for a in KNOWN.values() if a.provider in providers}


@pytest.fixture
def web(monkeypatch) -> Iterator[Recorded]:
    recorded = Recorded()
    for module in (espn, lichess_chess):
        monkeypatch.setattr(module, "_client", httpx.Client(transport=httpx.MockTransport(recorded)))
        monkeypatch.setattr(module, "_cache", {})
    monkeypatch.setattr(lichess_chess, "SPACING", 0)
    monkeypatch.setattr(adapters, "ADAPTERS", lineup(espn.PROVIDER, lichess_chess.PROVIDER))
    yield recorded
    # A source may swallow the failure of an unrecorded request: every request must have had an answer.
    assert [url for url in recorded.asked if url not in RECORDED] == []


# ---------------------------------------------------------------- full names


def test_a_full_name_one_athlete_has_is_theirs(web):
    # One ESPN athlete is called Caitlin Clark, and the basketball adapter reads her: her page comes from
    # the source in seconds, for one search and one athlete read.
    found = structured.identify("Caitlin Clark")
    assert found is not None
    adapter, ref = found
    assert (adapter.system, ref.athlete_id, ref.name, ref.league, ref.team_names) == ("espn_basketball", "4433403", "Caitlin Clark", "wnba", ["Indiana Fever"])
    assert web.asked == [SEARCH.format("Caitlin%20Clark"), ATHLETE.format("basketball/wnba/athletes/4433403")]


def test_a_footballer_comes_with_their_national_team_and_birth_date(web):
    found = structured.identify("Pau Sans")
    assert found is not None
    adapter, ref = found
    assert (adapter.system, ref.athlete_id, ref.team_names) == ("espn_soccer", "359149", ["Real Zaragoza", "Spain"])
    assert ref.born == "2004-11-24"  # ESPN writes 24/11/2004, day first


def test_a_registry_athlete_known_to_the_year_is_found_by_name_and_year(web, monkeypatch):
    # BUG-071: Wikidata knows only that Pau Sans was born in 2004, stored as 1 January; ESPN says 24
    # November 2004. Compared as days they disagreed, so following him from search found no live source
    # and spent an AI research. The year and the name are enough.
    learned = []
    monkeypatch.setattr(registry, "learn", lambda qid, sport, system, athlete_id: learned.append((qid, system, athlete_id)))
    sans = registry.Person("Q11940570", "football", "Pau Sans", [], date(2004, 1, 1), ["Real Zaragoza"], None, {"transfermarkt": "1061123"}, registry.YEAR)
    found = registry.resolve(sans)
    assert found is not None
    adapter, ref = found
    assert (adapter.system, ref.athlete_id) == ("espn_soccer", "359149")
    assert learned == [("Q11940570", "espn_soccer", "359149")]


# ---------------------------------------------------------------- surnames


def test_a_single_name_goes_to_the_agent_without_asking_anyone(web):
    # "Clark" is hundreds of athletes: only a full name is looked up, and a lone surname costs no request.
    for typed in ("Kohli", "Clark", "Jokić", "Salah", "M.", ""):
        assert structured.identify(typed) is None
    assert web.asked == []


def test_a_longer_name_or_the_same_words_turned_round_isnt_the_one_typed(web, monkeypatch):
    # BUG-032 bound "Ahmed Salah" to Liverpool's Mohamed Salah on the surname alone. ESPN's search for
    # "Ahmed Salah" finds Tunisia's Ahmed Salah Hosni and Pakistan's Salahuddin Ahmed: the football
    # adapter reads Ahmed Salah Hosni (every typed word is in his name), but nobody on ESPN has exactly
    # the typed name, so the agent decides. Lichess isn't running here: FIDE's answer wasn't recorded.
    monkeypatch.setattr(adapters, "ADAPTERS", lineup(espn.PROVIDER))
    assert structured.identify("Ahmed Salah") is None
    assert web.asked == [SEARCH.format("Ahmed%20Salah"), ATHLETE.format("soccer/athletes/135729")]


# ---------------------------------------------------------------- typos


def test_a_misspelt_name_goes_to_the_agent(web):
    # "Jos Butler" for Jos Buttler: ESPN lists no player by it, and FIDE's list has Joses and Butlers
    # (Jos Woolley, Kevin Butler, "Butler, Jp") but no Jos Butler, so neither source claims the name.
    assert structured.identify("Jos Butler") is None
    assert web.asked[0] == SEARCH.format("Jos%20Butler")


@pytest.mark.xfail(strict=True, reason="identify takes a name only a college record has: 'Caitlyn Clark' is Bridgewater State's NCAAW guard (espn_basketball 5324154, league womens-college-basketball), though search and the registry leave college athletes out")
def test_a_misspelling_that_is_a_college_players_name_goes_to_the_agent(web):
    # A common misspelling of Caitlin Clark. ESPN has one athlete by it, a Division III college player,
    # and the basketball adapter reads college leagues too. The fan almost surely means the Fever star;
    # ESPN knows the name, so it ends the lookup, as for an athlete in a sport we don't cover.
    assert structured.identify("Caitlyn Clark") is None
    assert web.asked == [SEARCH.format("Caitlyn%20Clark")]


# ---------------------------------------------------------------- namesakes


def test_a_name_two_athletes_share_goes_to_the_agent(web):
    # ESPN has the NBA's Nikola Jokić and a Malaysian Super League footballer of the same name: the agent
    # (and the fan's pick) decide, never the first one found. ESPN's answer ends the lookup.
    assert structured.identify("Nikola Jokić") is None
    assert web.asked == [SEARCH.format("Nikola%20Joki%C4%87")]


def test_an_athlete_in_a_sport_we_dont_cover_isnt_handed_to_another_source(web):
    # ESPN knows Max Verstappen, in F1: the agent covers him, and Lichess isn't asked for a chess namesake.
    assert structured.identify("Max Verstappen") is None
    assert web.asked == [SEARCH.format("Max%20Verstappen")]


# ---------------------------------------------------------------- college records


def test_the_professional_is_meant_over_their_own_college_record(web):
    # ESPN keeps A'Ja Wilson's South Carolina record (NCAAW, 4412077) beside the Aces star's: two athletes
    # by the name, but one professional, and she is the one read. Her id, not her name, is what the build
    # lands on (BUG-031), so the college record can't take her place.
    found = structured.identify("A'ja Wilson")
    assert found is not None
    adapter, ref = found
    assert (adapter.system, ref.athlete_id, ref.league, ref.team_names) == ("espn_basketball", "3149391", "wnba", ["Las Vegas Aces"])
    assert web.asked == [SEARCH.format("A%27ja%20Wilson"), ATHLETE.format("basketball/wnba/athletes/3149391")]


# ---------------------------------------------------------------- legal names


def test_a_chess_player_listed_by_an_initial_is_found_by_their_full_name(web):
    # FIDE lists Gukesh Dommaraju as "Gukesh D". ESPN knows no player by the name, so Lichess looks in
    # FIDE's list: the initial stands for "Dommaraju", and a titled, active player is taken. The other
    # Dommaraju on the list (Chaithanya Ramkumar, unrated) isn't.
    found = structured.identify("Gukesh Dommaraju")
    assert found and (found[0].system, found[1].athlete_id, found[1].name) == ("lichess_chess", "46616543", "Gukesh D")
    assert web.asked == [SEARCH.format("Gukesh%20Dommaraju"), f"{FIDE}?q=Gukesh%20Dommaraju", f"{FIDE}/46616543"]
