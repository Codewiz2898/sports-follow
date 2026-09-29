"""The player registry: Wikidata query rows to athlete rows, and matching a registry person to a
live-source athlete (registry.py). Rows are shaped like the query service's JSON bindings."""

from __future__ import annotations

from datetime import date

from sports_follow import registry, search
from sports_follow.adapters import PlayerRef
from sports_follow.models import Athlete

WD = "http://www.wikidata.org/entity/"
TODAY = date(2026, 9, 29)


def test_rows_from_query_results():
    core = [
        {"p": WD + "Q1354960", "sitelinks": "104", "birth": "1992-06-15T00:00:00Z", "citizenship": "Egypt", "id_espn_soccer": "173896", "id_transfermarkt": "148455",
         "image": "http://commons.wikimedia.org/wiki/Special:FilePath/Mohamed%20Salah%202018.jpg"},
        {"p": WD + "Q1", "sitelinks": "3", "birth": "1930-01-01T00:00:00Z", "death": "1990-01-01T00:00:00Z", "id_transfermarkt": "1"},
        {"p": WD + "Q2", "sitelinks": "1", "id_transfermarkt": "2"},  # no name in a language we read
    ]
    names = [
        {"p": WD + "Q1354960", "n": "Mohamed Salah", "kind": "label", "lang": "en"},
        {"p": WD + "Q1354960", "n": "Mo Salah", "kind": "alias", "lang": "en"},
        {"p": WD + "Q1354960", "n": "mohamed salah", "kind": "alias", "lang": "en"},
        {"p": WD + "Q1", "n": "Old Timer", "kind": "label", "lang": "en"},
    ]
    teams = [
        {"p": WD + "Q1354960", "teamLabel": "Egypt national football team", "start": "2011-09-03T00:00:00Z"},
        {"p": WD + "Q1354960", "teamLabel": "Trabzonspor", "start": "2026-07-01T00:00:00Z"},
        {"p": WD + "Q1354960", "teamLabel": "Egypt national under-23 football team"},
    ]
    [salah] = registry.build("football", core, names, teams, TODAY)
    assert salah["name"] == "Mohamed Salah" and salah["aliases"] == ["Mo Salah"]
    assert salah["search_text"] == " mohamed salah | mo salah |"
    assert salah["teams"] == ["Trabzonspor", "Egypt"]  # club first, the national side as its country
    assert salah["ids"] == {"espn_soccer": "173896", "transfermarkt": "148455"}
    assert (salah["birth_date"], salah["country"], salah["sitelinks"], salah["current"]) == (date(1992, 6, 15), "Egypt", 104, True)
    assert salah["image"] == "Mohamed Salah 2018.jpg"


def test_chess_titles_tours_and_leagues():
    polgar = [{"p": WD + "Q231556", "title": t, "citizenship": "Hungary", "id_lichess_chess": "700070"} for t in ("International Master", "Grandmaster", "Woman Grandmaster")]
    [judit] = registry.build("chess", polgar, [{"p": WD + "Q231556", "n": "Judit Polgár", "kind": "label", "lang": "en"}], [], TODAY)
    assert (judit["title"], judit["country"], judit["teams"]) == ("GM", "Hungary", [])  # the highest title held
    [ding] = registry.build("chess", [{"p": WD + "Q1", "citizenship": "People's Republic of China", "id_lichess_chess": "8603677"}], [{"p": WD + "Q1", "n": "Ding Liren", "kind": "label", "lang": "en"}], [], TODAY)
    assert ding["country"] == "China"
    [clark] = registry.build("basketball", [{"p": WD + "Q9", "id_wnba": "1642286", "birth": "2002-01-22T00:00:00Z"}], [{"p": WD + "Q9", "n": "Caitlin Clark", "kind": "label", "lang": "mul"}], [], TODAY)
    assert clark["league"] == "wnba" and clark["name"] == "Caitlin Clark"
    draftee = registry.build("basketball", [{"p": WD + "Q8", "id_wnba": "1", "birth": "2000-05-05T00:00:00Z"}], [{"p": WD + "Q8", "n": "Caitlin Bickle", "kind": "label", "lang": "en"}], [{"p": WD + "Q8", "teamLabel": "Baylor Bears women's basketball"}], TODAY)
    assert draftee[0]["teams"] == []  # a college side isn't a pro's team


def test_words_and_search_text_fold_names():
    assert registry.words("A'ja Wilson") == ["aja", "wilson"]
    assert registry.words("Magnus Øen Carlsen") == ["magnus", "oen", "carlsen"]
    assert registry.search_text("Nikola Jokić", ["Joker"]) == " nikola jokic | joker |"
    assert registry.team_name("India national cricket team") == "India"


def test_birth_dates_agree_either_way_round():
    born = date(1988, 11, 5)
    assert registry.same_day(born, "1988-11-05") is True
    assert registry.same_day(born, "1988-05-11") is True  # day and month read the other way
    assert registry.same_day(born, "1988-11-06") is False
    assert registry.same_day(None, "1988-11-05") is None


def test_same_person_trusts_the_birth_date_over_the_name():
    foord = registry.Person("Q1", "football", "Caitlin Foord", [], date(1994, 11, 11), ["Arsenal"], None, {"transfermarkt": "1"})
    assert registry.same_person(foord, PlayerRef("espn_soccer", "158678", "Caitlin Foord", ["359"], ["Arsenal"], born="1994-11-11"))
    # Same name, different person: the birth dates decide.
    assert not registry.same_person(foord, PlayerRef("espn_soccer", "9", "Caitlin Foord", ["1"], ["Arsenal"], born="2001-03-02"))
    # No birth date in the source: the whole name and a team must match.
    assert registry.same_person(foord, PlayerRef("espn_soccer", "158678", "Caitlin Foord", ["359"], ["Arsenal Women"]))
    assert not registry.same_person(foord, PlayerRef("espn_soccer", "158678", "Caitlin Foord", ["2"], ["Chelsea"]))


def test_registry_rows_as_search_results():
    kohli = Athlete(qid="Q213854", sport="cricket", name="Virat Kohli", aliases=[], teams=["India", "Royal Challengers Bengaluru"], ids={"espn_cricket": "253802"}, sitelinks=67, current=True, birth_date=date(1988, 11, 5))
    r = search.from_athlete(kohli, "kohli")
    assert (r.key, r.system, r.athlete_id, r.qid, r.detail) == ("espn_cricket:253802", "espn_cricket", "253802", "Q213854", "India · Royal Challengers Bengaluru")
    foord = Athlete(qid="Q5", sport="football", name="Caitlin Foord", aliases=[], teams=["Arsenal"], country="Australia", ids={"transfermarkt": "1"}, sitelinks=20, current=True)
    r = search.from_athlete(foord, "caitl")
    # No ESPN id yet: followed by registry id, and found in ESPN by name and birth date then.
    assert (r.key, r.system, r.qid, r.detail, r.live_scores) == ("wikidata:Q5", None, "Q5", "Arsenal · Australia", True)


def test_a_followed_player_and_their_registry_row_are_one_result():
    ours = search.Result(key="espn_basketball:4433403", name="Caitlin Clark", sport="Basketball", detail="Indiana Fever", system="espn_basketball", athlete_id="4433403", player_id=8, followers=1, live_scores=True, qid="Q9", _rank=(1.0,))
    theirs = search.Result(key="wikidata:Q9", name="Caitlin Clark", sport="Basketball", detail="Indiana Fever · WNBA", qid="Q9", live_scores=True, popularity=40)
    other = search.Result(key="wikidata:Q10", name="Caitlin Foord", sport="Football", detail="Arsenal", qid="Q10", live_scores=True, popularity=20)
    ranked = search.rank("caitl", [ours], [theirs, other])
    assert [r.key for r in ranked] == ["espn_basketball:4433403", "wikidata:Q10"]
    assert ranked[0].detail == "Indiana Fever · WNBA"
