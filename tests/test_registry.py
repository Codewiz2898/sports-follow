"""The player registry: Wikidata query rows to athlete rows, and matching a registry person to a
live-source athlete (registry.py). Rows are shaped like the query service's JSON bindings."""

from __future__ import annotations

import importlib.util
import re
from datetime import date
from pathlib import Path

import pytest

from sports_follow import bus, registry, search, sports
from sports_follow.adapters import KNOWN, PlayerRef, SearchResult
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


# ---------------------------------------------------------------- names in every language's "mul" (BUG-145)


# Messi and MS Dhoni as the query service answers for them: their aliases, and Dhoni's label, are kept under
# "mul" (a name every language shares), none under "en" (Dhoni's 18 as Wikidata lists them).
MESSI, DHONI = WD + "Q615", WD + "Q470774"
STARS_CORE = [
    {"p": MESSI, "sitelinks": "200", "birth": "1987-06-24T00:00:00Z", "birth_precision": "11", "id_espn_soccer": "45843"},
    {"p": DHONI, "sitelinks": "90", "birth": "1981-07-07T00:00:00Z", "birth_precision": "11", "id_espn_cricket": "28081"},
]
DHONI_ALIASES = [
    "Mahi", "Mahendra Singh Dhoni", "Mahendra Singh Dhauni", "MS Dhauni", "Dhauni", "D. Dhenier", "Captain Cool", "Mr Cool", "Finisher",
    "MSD", "M. S. Dhoni", "M.S. Dhoni", "M. S. Dhauni", "M.S. Dhauni", "Dhoni", "Mr. Cool", "Thala", "D Dhenier",
]
STARS_NAMES = [
    {"p": MESSI, "n": "Lionel Messi", "kind": "label", "lang": "en"},
    {"p": MESSI, "n": "Lionel Messi", "kind": "label", "lang": "mul"},
    {"p": MESSI, "n": "Leo Messi", "kind": "alias", "lang": "mul"},
    {"p": MESSI, "n": "La Pulga", "kind": "alias", "lang": "mul"},
    {"p": DHONI, "n": "MS Dhoni", "kind": "label", "lang": "mul"},
    *({"p": DHONI, "n": n, "kind": "alias", "lang": "mul"} for n in DHONI_ALIASES),
]
# A club whose name Wikidata keeps under "mul" alone, and one named in English too.
STARS_TEAMS = [
    {"p": DHONI, "team": WD + "Q1", "teamLabel": "Chennai Super Kings", "lang": "mul", "start": "2018-01-01T00:00:00Z"},
    {"p": MESSI, "team": WD + "Q2", "teamLabel": "Inter Miami CF", "lang": "en", "start": "2023-07-15T00:00:00Z"},
    {"p": MESSI, "team": WD + "Q2", "teamLabel": "Inter Miami", "lang": "mul", "start": "2023-07-15T00:00:00Z"},
]


def _langs(query: str, clause: str) -> set[str]:
    """The languages a query reads a clause's names in: `<clause> FILTER(LANG(?x) = "en")` or `... IN ("en", "mul")`."""
    found = re.search(re.escape(clause) + r' FILTER\(LANG\(\?\w+\) (?:= "([\w-]+)"|IN \(([^)]*)\))', query)
    assert found, f"no language asked for {clause}"
    return {found.group(1)} if found.group(1) else set(re.findall(r'"([\w-]+)"', found.group(2)))


def query_service(core: list[dict], names: list[dict], teams: list[dict]):
    """registry.sparql answering fetch's three queries from these rows, each name in the languages the query asks."""

    def sparql(query: str) -> list[dict[str, str]]:
        if "skos:altLabel" in query:
            asked = {"label": _langs(query, "rdfs:label ?n"), "alias": _langs(query, "skos:altLabel ?n")}
            return [r for r in names if r["lang"] in asked[r["kind"]]]
        if "p:P54" in query:
            return [r for r in teams if r["lang"] in _langs(query, "rdfs:label ?teamLabel")]
        return core

    return sparql


def stars(monkeypatch, sport: str, qid: str, names: list[dict] = STARS_NAMES) -> dict:
    monkeypatch.setattr(registry, "sparql", query_service([r for r in STARS_CORE if r["p"] == qid], [r for r in names if r["p"] == qid], [r for r in STARS_TEAMS if r["p"] == qid]))
    [row] = registry.build(sport, *registry.fetch(sport, [qid.rsplit("/", 1)[-1]]), TODAY)
    return row


def test_names_wikidata_keeps_for_every_language_are_read(monkeypatch):
    # Messi had no aliases and "leo messi" found nothing: Wikidata moved them from "en" to "mul" (BUG-145).
    messi = stars(monkeypatch, "football", MESSI)
    assert messi["aliases"] == ["Leo Messi", "La Pulga"]
    assert messi["search_text"] == " lionel messi | leo messi | la pulga |"
    assert messi["teams"] == ["Inter Miami CF"]  # named in English and in mul: one club, by its English name
    dhoni = stars(monkeypatch, "cricket", DHONI)
    assert dhoni["name"] == "MS Dhoni" and dhoni["teams"] == ["Chennai Super Kings"]
    # Still eight at most, the first the service lists: MSD is Wikidata's tenth of Dhoni's (see MAX_ALIASES).
    assert dhoni["aliases"] == DHONI_ALIASES[:8] and len(dhoni["aliases"]) == registry.MAX_ALIASES


def test_english_aliases_come_first_and_a_spelling_search_cant_tell_apart_takes_no_place():
    names = [
        {"p": WD + "Q9", "n": "Asha Rao", "kind": "label", "lang": "en"},
        *({"p": WD + "Q9", "n": f"Nickname {i}", "kind": "alias", "lang": "mul"} for i in range(8)),
        {"p": WD + "Q9", "n": "A. Rao", "kind": "alias", "lang": "mul"},
        {"p": WD + "Q9", "n": "A.Rao", "kind": "alias", "lang": "en"},
    ]
    [rao] = registry.build("football", [{"p": WD + "Q9", "id_transfermarkt": "9"}], names, [], TODAY)
    # "A. Rao" is "A.Rao" to search (words "a rao"): one place, the English spelling's.
    assert rao["aliases"] == ["A.Rao", *(f"Nickname {i}" for i in range(7))]


def test_words_and_search_text_fold_names():
    assert registry.words("A'ja Wilson") == ["aja", "wilson"]
    assert registry.words("Magnus Øen Carlsen") == ["magnus", "oen", "carlsen"]
    assert registry.search_text("Nikola Jokić", ["Joker"]) == " nikola jokic | joker |"
    assert registry.team_name("India national cricket team") == "India"


def test_birth_dates_agree_only_as_the_source_gives_them():
    # Every source's date is ISO, its own order read by its adapter (espn.birth_date), so a day and month the
    # other way round is another day: 11 May isn't 5 November (BUG-146).
    born = date(1988, 11, 5)
    assert registry.same_day(born, "1988-11-05") is True
    assert registry.same_day(born, "1988-05-11") is False
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


def test_a_stranger_born_the_same_day_isnt_the_person():
    # Giacomo De Pieri and ESPN's Ethan Mbappé were both born on 29 December 2006: the date alone made them one
    # person, and De Pieri's Wikidata profile Ethan's page (BUG-146). The names must match too.
    de_pieri = registry.Person("Q118643915", "football", "Giacomo De Pieri", [], date(2006, 12, 29), [], None, {}, registry.DAY)
    assert not registry.same_person(de_pieri, PlayerRef("espn_soccer", "355971", "Ethan Mbappé", ["1"], ["Lille"], born="2006-12-29"))
    assert registry.same_person(de_pieri, PlayerRef("espn_soccer", "1", "Giacomo De Pieri", ["2"], ["Inter"], born="2006-12-29"))
    # Their name, but with the day and month the other way round: someone else.
    rao = registry.Person("Q2", "football", "Asha Rao", [], date(2001, 3, 4), [], None, {}, registry.DAY)
    assert not registry.same_person(rao, PlayerRef("espn_soccer", "2", "Asha Rao", ["1"], [], born="2001-04-03"))


def test_registry_rows_as_search_results():
    live = registry.live_system("cricket")  # Sportmonks with a token, else ESPN
    kohli = Athlete(qid="Q213854", sport="cricket", name="Virat Kohli", aliases=[], teams=["India", "Royal Challengers Bengaluru"], ids={live: "253802"}, sitelinks=67, current=True, birth_date=date(1988, 11, 5))
    r = search.from_athlete(kohli, "kohli")
    assert (r.key, r.system, r.athlete_id, r.qid, r.detail) == (f"{live}:253802", live, "253802", "Q213854", "India · Royal Challengers Bengaluru")
    foord = Athlete(qid="Q5", sport="football", name="Caitlin Foord", aliases=[], teams=["Arsenal"], country="Australia", ids={"transfermarkt": "1"}, sitelinks=20, current=True)
    r = search.from_athlete(foord, "caitl")
    # No ESPN id yet: followed by registry id, and found in ESPN by name and birth date then.
    assert (r.key, r.system, r.qid, r.detail, r.live_scores) == ("wikidata:Q5:football", None, "Q5", "Arsenal · Australia", True)


def test_a_followed_player_and_their_registry_row_are_one_result():
    ours = SearchResult(key="espn_basketball:4433403", name="Caitlin Clark", sport="Basketball", detail="Indiana Fever", system="espn_basketball", athlete_id="4433403", player_id=8, followers=1, live_scores=True, qid="Q9", _rank=(1.0,))
    theirs = SearchResult(key="wikidata:Q9", name="Caitlin Clark", sport="Basketball", detail="Indiana Fever · WNBA", qid="Q9", live_scores=True, popularity=40)
    other = SearchResult(key="wikidata:Q10", name="Caitlin Foord", sport="Football", detail="Arsenal", qid="Q10", live_scores=True, popularity=20)
    ranked = search.rank("caitl", [ours], [theirs, other])
    assert [r.key for r in ranked] == ["espn_basketball:4433403", "wikidata:Q10"]
    assert ranked[0].detail == "Indiana Fever · WNBA"


def test_a_birth_date_known_to_the_year_compares_by_the_year():
    # Wikidata has "2004" for Pau Sans, which reads as 1 January 2004; ESPN has 24 November 2004, so
    # he was never found there and following him spent an AI research (BUG-071).
    core = [{"p": WD + "Q11940570", "birth": "2004-01-01T00:00:00Z", "birth_precision": "9", "id_transfermarkt": "1061123"}]
    names = [{"p": WD + "Q11940570", "n": "Pau Sans", "kind": "label", "lang": "en"}]
    [sans] = registry.build("football", core, names, [], TODAY)
    assert (sans["birth_date"], sans["birth_precision"]) == (date(2004, 1, 1), registry.YEAR)
    # Two statements, the year and the day: the day is kept.
    both = [*core, {**core[0], "birth": "2004-11-24T00:00:00Z", "birth_precision": "11"}]
    [sans_day] = registry.build("football", both, names, [], TODAY)
    assert (sans_day["birth_date"], sans_day["birth_precision"]) == (date(2004, 11, 24), registry.DAY)
    # A century says nothing.
    [vague] = registry.build("football", [{**core[0], "birth_precision": "7"}], names, [], TODAY)
    assert vague["birth_date"] is None

    year = date(2004, 1, 1)
    assert registry.same_day(year, "2004-11-24", registry.YEAR) is True
    assert registry.same_day(year, "2005-11-24", registry.YEAR) is False
    assert registry.same_day(year, "2004-11-24", registry.DAY) is False  # a real New Year's birthday
    assert registry.same_day(year, "2004-11-24") is True  # imported before the precision: 1 January is the year
    assert registry.same_day(date(2004, 11, 1), "2004-11-24", registry.MONTH) is True

    person = registry.Person("Q11940570", "football", "Pau Sans", [], year, ["Real Zaragoza"], None, {"transfermarkt": "1061123"}, registry.YEAR)
    assert registry.same_person(person, PlayerRef("espn_soccer", "1", "Pau Sans", ["1"], ["Real Zaragoza"], born="2004-11-24"))
    # The year alone isn't enough: the name must match too.
    assert not registry.same_person(person, PlayerRef("espn_soccer", "2", "Pau Torres", ["1"], ["Aston Villa"], born="2004-03-16"))
    assert not registry.same_person(person, PlayerRef("espn_soccer", "3", "Pau Sans", ["1"], ["Real Zaragoza"], born="2003-11-24"))


def test_a_whole_name_is_a_name_or_an_alias_not_the_end_of_one():
    # "% ronaldo |%" matched " cristiano ronaldo |" too, filling the namesakes with Cristiano, and "silva"
    # with every da Silva (BUG-074).
    sql = str(registry.named("ronaldo").compile(compile_kwargs={"literal_binds": True}))
    assert "' ronaldo |%'" in sql and "'%| ronaldo |%'" in sql
    assert "'% ronaldo |%'" not in sql


def test_commons_file_names_are_decoded_in_full():
    # Only "%20" was decoded: 29,253 of 65,597 names kept "%C4%87" and "%28cropped%29" (BUG-077).
    path = "http://commons.wikimedia.org/wiki/Special:FilePath/"
    for encoded, name in [
        ("Zlatan%20Ibrahimovi%C4%87%20nyc.jpg", "Zlatan Ibrahimović nyc.jpg"),
        ("Jan-Krzysztof%20Duda%202021%20%28cropped%29.jpg", "Jan-Krzysztof Duda 2021 (cropped).jpg"),
        ("100%25%20Pure.jpg", "100% Pure.jpg"),  # a "%" in the name itself
        ("C++%20Cup.png", "C++ Cup.png"),  # "+" is a plus in a path, not a space
    ]:
        [row] = registry.build("chess", [{"p": WD + "Q1", "id_lichess_chess": "1", "image": path + encoded}], [{"p": WD + "Q1", "n": "Somebody", "kind": "label", "lang": "en"}], [], TODAY)
        assert row["image"] == name


def test_the_stored_names_are_decoded_once_and_only_once():
    """The migration that decodes names already stored: a decoded name is never decoded again, since
    MediaWiki allows no "%" and two hex digits in a title."""
    spec = importlib.util.spec_from_file_location("decode_images", Path(__file__).parent.parent / "migrations/versions/f4a7c2e9b1d6_decode_athlete_image_names.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.decoded("Zlatan Ibrahimovi%C4%87 nyc.jpg") == "Zlatan Ibrahimović nyc.jpg"
    assert migration.decoded("100%25 Pure %28cropped%29.jpg") == "100% Pure (cropped).jpg"
    for decoded in ["Zlatan Ibrahimović nyc.jpg", "100% Pure (cropped).jpg", "50 % off.jpg"]:
        assert migration.decoded(decoded) == decoded
    assert migration.decoded("Cut at 300 characters Ibrahimovi%C4") == "Cut at 300 characters Ibrahimovi%C4"  # left for the import


def test_national_cricket_sides_without_national_in_the_name():
    # "Ireland cricket team" (123 athletes), "West Indies cricket team" (86) and "England cricket team" (66)
    # were kept as clubs (BUG-082); domestic sides named the same way are clubs.
    assert registry.team_name("Ireland cricket team") == "Ireland"
    assert registry.team_name("West Indies women's cricket team") == "West Indies"
    assert registry.team_name("South Africa national women's cricket team") == "South Africa"
    assert registry.team_name("Guernsey cricket team") == "Guernsey"
    for club in ["Tasmania cricket team", "Mumbai cricket team", "United States Virgin Islands cricket team", "Pakistan International Airlines cricket team", "National Bank of Pakistan cricket team"]:
        assert not registry.is_national(club) and registry.team_name(club) == club
    core = [{"p": WD + "Q1", "birth": "1988-04-29T00:00:00Z", "id_espn_cricket": "276298", "citizenship": "Jamaica"}]
    teams = [
        {"p": WD + "Q1", "teamLabel": "West Indies cricket team", "start": "2010-01-01T00:00:00Z"},
        {"p": WD + "Q1", "teamLabel": "Kolkata Knight Riders", "start": "2014-01-01T00:00:00Z"},
        {"p": WD + "Q1", "teamLabel": "Jamaica Tallawahs", "start": "2013-01-01T00:00:00Z"},
    ]
    [russell] = registry.build("cricket", core, [{"p": WD + "Q1", "n": "Andre Russell", "kind": "label", "lang": "en"}], teams, TODAY)
    assert russell["teams"] == ["West Indies", "Kolkata Knight Riders"]  # cricket: the country first, then the latest club


# ---------------------------------------------------------------- one run at a time, one sport at a time (BUG-023)


def test_one_failed_sport_doesnt_stop_the_others(monkeypatch, caplog):
    asked = []

    def import_sport(sport, new_only=False):
        asked.append(sport)
        if sport == "basketball":
            raise RuntimeError("wikidata query service kept failing")
        return 10

    games = [s.key for s in sports.ALL if s.group == "esports"]
    monkeypatch.setattr(registry, "import_sport", import_sport)
    monkeypatch.setattr(registry, "import_rosters", lambda keys: asked.append(keys) or dict.fromkeys(keys, 3))
    monkeypatch.setattr(registry, "find_people", lambda keys: None)
    done = registry.import_all()
    # Smallest first, football last of Wikidata's; then every esports game at once (each source read once).
    assert asked == ["basketball", "tennis", "cricket", "chess", "football", games]
    assert done == {"basketball": None, "tennis": 10, "cricket": 10, "chess": 10, "football": 10, **dict.fromkeys(games, 3)}
    assert "basketball failed" in caplog.text


def test_a_roster_sports_people_are_found_after_its_import_even_a_failed_one(monkeypatch, caplog):
    from sports_follow import roster_wikidata

    found = []

    def import_rosters(keys):
        raise RuntimeError("PandaScore failed")

    def run(kind, within):
        found.append((kind.key, within))
        raise RuntimeError("wikidata query service kept failing")

    monkeypatch.setattr(registry, "import_rosters", import_rosters)
    monkeypatch.setattr(roster_wikidata, "run", run)
    done = registry.import_all(["valorant", "bgmi"])
    # Last week's rows are matched again, every game's at once (and the esports rows of no game); matching failing changes nothing.
    assert found == [("esports", ["esports", "valorant", "bgmi"])] and done == {"valorant": None, "bgmi": None}
    assert "matching athletes to Wikidata failed" in caplog.text
    monkeypatch.setattr(registry, "import_rosters", lambda keys: dict.fromkeys(keys, 3))
    assert registry.import_all(["valorant", "bgmi"]) == {"valorant": 3, "bgmi": 3} and len(found) == 2


def test_one_failed_esports_source_doesnt_stop_the_others(monkeypatch):
    ran = []

    def pandascore():
        ran.append("pandascore")
        raise KeyError("id")

    imports = {"pandascore_esports": pandascore, "liquipedia_esports": lambda: ran.append("liquipedia") or {"bgmi": 4, "freefire": 1}, "pubg_esports": lambda: ran.append("pubg") or {"pubg": 5}}
    monkeypatch.setattr(registry, "ADAPTERS", {s: KNOWN[s] for s in imports})
    asked = []
    monkeypatch.setattr(registry, "import_roster", lambda adapter, keys: asked.append((adapter.system, keys)) or imports[adapter.system]())
    done = registry.import_rosters(["valorant", "cs2", "bgmi", "freefire", "pubg"])
    assert ran == ["pandascore", "liquipedia", "pubg"]  # each source once, for all of its games asked for
    assert asked == [("pandascore_esports", ["valorant", "cs2"]), ("liquipedia_esports", ["bgmi", "freefire"]), ("pubg_esports", ["pubg"])]
    assert done == {"valorant": None, "cs2": None, "bgmi": 4, "freefire": 1, "pubg": 5}  # PandaScore's games failed, the others kept


def test_an_empty_answer_from_wikidata_deletes_nothing(monkeypatch):
    # BUG-083: the weekly run deletes what it didn't refresh, so an empty member list would wipe the sport.
    monkeypatch.setattr(registry, "members", lambda sport: [])
    monkeypatch.setattr(registry, "session", lambda: pytest.fail("the database was touched"))
    with pytest.raises(RuntimeError, match="no one in chess"):
        registry.import_sport("chess")


def test_a_run_that_would_remove_more_than_it_kept_removes_nothing():
    assert registry.too_many_gone(leaving=24_319, kept=0)
    assert registry.too_many_gone(leaving=600, kept=500)
    assert not registry.too_many_gone(leaving=40, kept=24_279)


def test_a_run_by_hand_takes_the_weekly_runs_lock(monkeypatch, capsys):
    locks: dict[str, str] = {}

    def try_lock(name, seconds):
        if name in locks:
            return None
        locks[name] = "token"
        return "token"

    monkeypatch.setattr(bus, "try_lock", try_lock)
    monkeypatch.setattr(bus, "release", lambda name, token=None: locks.pop(name, None))
    held = []
    monkeypatch.setattr(registry, "import_sport", lambda sport, new_only=False: held.append(dict(locks)) or 7)
    registry.main(["chess"])
    assert held == [{"registry-import": "token"}] and locks == {}  # held while importing, then let go
    assert "{'chess': 7}" in capsys.readouterr().out

    locks["registry-import"] = "the weekly run's"
    with pytest.raises(SystemExit, match="another registry import is running"):
        registry.main(["chess"])
    assert len(held) == 1  # nothing imported
    # And the weekly job skips while a run by hand holds it.
    from sports_follow import worker
    worker._import_registry()
    assert len(held) == 1


def test_the_commands_take_esports_as_its_games_that_run(monkeypatch, capsys):
    """`registry esports` and `roster_wikidata esports` with a game turned off: the games that run."""
    from sports_follow import roster_wikidata

    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key != "cod"))
    games = [s.key for s in sports.ALL if s.group and s.key != "cod"]
    asked = []
    monkeypatch.setattr(registry, "import_locked", lambda keys, new_only=False: asked.append(list(keys)) or dict.fromkeys(keys, 1))
    registry.main(["esports"])
    registry.main(["valorant", "chess"])
    assert asked == [games, ["valorant", "chess"]]
    with pytest.raises(SystemExit, match="unknown sport"):
        registry.main(["cod"])  # turned off: not one that runs
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if not s.group))
    with pytest.raises(SystemExit, match="none of esports runs"):
        registry.main(["esports"])

    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key != "cod"))
    matched = []
    monkeypatch.setattr(roster_wikidata, "match_sport", lambda kind, within, write=True: matched.append((kind.key, within)) or ([], {}))
    roster_wikidata.main(["esports", "--dry-run"])
    assert matched == [("esports", ["esports", *games])]
