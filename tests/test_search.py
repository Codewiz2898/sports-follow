"""Player search against ESPN and Lichess responses recorded on 2026-09-29 (search.py)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from sports_follow import naming, pipeline, registry, search, sports
from sports_follow.models import Athlete, PlayerCard, PlayerIdentity
from sports_follow.adapters import KNOWN, LiveSearch, Provider, SearchResult, cache, espn, lichess_chess

from .test_new_adapters import load


@pytest.fixture(autouse=True)
def research_on(monkeypatch):
    """These tests cover pages for players no live source has, which the agent writes with research on
    (config.RESEARCH, off by default): on here, and off with the agent in the tests that turn it off."""
    from sports_follow import config

    monkeypatch.setattr(config, "RESEARCH", True)


ESPN = [a for a in KNOWN.values() if a.provider is espn.PROVIDER]


def keys(results: list[SearchResult]) -> list[str]:
    return [r.key for r in results]


# ---------------------------------------------------------------- ESPN hits


def test_espn_hits_leave_out_college_athletes():
    results = espn.parse_search(load("espn/search-caitl"), "caitl", ESPN)
    names = [r.name for r in results]
    assert "Caitlin Clark" in names and "Caitlin Foord" in names
    # ESPN lists a dozen NCAA players for "caitl" (Caitlyn Clark of Bridgewater State among them).
    assert not any(n.startswith("Caitlyn") for n in names)
    clark = results[0]
    assert (clark.key, clark.system, clark.athlete_id, clark.league) == ("espn_basketball:4433403", "espn_basketball", "4433403", "wnba")
    assert (clark.sport, clark.detail, clark.live_scores) == ("Basketball", "Indiana Fever · WNBA", True)


def test_espn_hits_in_sports_without_an_adapter_are_ai_only():
    carlsen = {r.name: r for r in espn.parse_search(load("espn/search-carlsen"), "carlsen", ESPN)}
    simon = carlsen["Simon Carlsen"]
    assert (simon.sport, simon.system, simon.live_scores) == ("MMA", None, False)
    assert carlsen["Morris Carlsén"].system == "espn_soccer"
    assert "Lexi Carlsen" not in carlsen  # Drake Bulldogs, NCAAW
    [max_v] = espn.parse_search(load("espn/search-max-verstappen"), "max verstappen", ESPN)
    assert (max_v.sport, max_v.detail, max_v.live_scores, max_v.exact) == ("Motor racing", "F1", False, True)


def test_two_athletes_with_one_name_are_both_exact():
    results = espn.parse_search(load("espn/search-nikola-jokic"), "Nikola Jokić", ESPN)
    assert [(r.sport, r.exact) for r in results] == [("Basketball", True), ("Football", True)]


# ---------------------------------------------------------------- FIDE hits


def test_fide_hits_notable_first_and_amateurs_only_when_asked():
    records = load("lichess/fide-search-carlsen")
    among_all = lichess_chess.parse_fide(records, "carlsen", lichess_chess.CHESS_LIMIT)
    # Titled or 2000+ only, active before inactive: Henrik (2063, inactive) after Christian (2048).
    assert [r.name for r in among_all] == ["Magnus Carlsen", "Christian Heen Carlsen", "Henrik Carlsen"]
    assert among_all[0].detail == "GM · Norway · 2823 classical"
    assert among_all[2].detail == "Norway · 2063 classical · inactive"
    chess_only = lichess_chess.parse_fide(records, "carlsen", 10)
    assert len(chess_only) == 10 and chess_only[0].name == "Magnus Carlsen"
    flags = [r.inactive for r in chess_only]
    assert flags == sorted(flags)  # no inactive player above an active one


def test_fide_hits_must_match_every_word():
    records = load("lichess/fide-search-carlsen")
    assert lichess_chess.parse_fide(records, "magnus carlsen", 10)[0].name == "Magnus Carlsen"
    assert lichess_chess.parse_fide(records, "max carlsen", 10) == []


# ---------------------------------------------------------------- merging and ranking


def test_rank_puts_players_on_sports_follow_first_and_merges_by_athlete():
    espn_hits = espn.parse_search(load("espn/search-caitl"), "caitl", ESPN)
    ours = SearchResult(key="espn_basketball:4433403", name="Caitlin Clark", sport="Basketball", detail="Indiana Fever", system="espn_basketball", athlete_id="4433403", player_id=8, followers=2, live_scores=True, _rank=(0.4,))
    ranked = search.rank("caitl", [ours], espn_hits)
    assert ranked[0] is ours and keys(ranked).count("espn_basketball:4433403") == 1
    assert ranked[0].detail == "Indiana Fever · WNBA"  # the source's line, when it says more


def test_rank_orders_live_sources_before_ai_only_sports():
    hits = [*espn.parse_search(load("espn/search-carlsen"), "carlsen", ESPN), *lichess_chess.parse_fide(load("lichess/fide-search-carlsen"), "carlsen", lichess_chess.CHESS_LIMIT)]
    ranked = search.rank("carlsen", [], hits)
    assert ranked[0].name == "Magnus Carlsen"  # a GM before unpictured namesakes
    live = [r.live_scores for r in ranked]
    assert live == sorted(live, reverse=True)  # MMA and baseball Carlsens last
    assert ranked[-1].sport in ("MMA", "Baseball")


def test_a_typo_is_a_suggestion_not_a_result():
    near = SearchResult(key="espn_basketball:4433403", name="Caitlin Clark", sport="Basketball", detail="", player_id=8, matched=False, _rank=(0.62,))
    assert search.rank("catalin clark", [near], []) == []
    assert search.closeness("catalin clark", "Caitlin Clark") >= search.SUGGEST_MIN
    assert search.closeness("catalin clark", "Catalin Carp") < search.SUGGEST_MIN


def test_prefix_match_is_per_word_and_order_free():
    assert naming.prefix_match("caitl", "Caitlin Clark")
    assert naming.prefix_match("clark caitlin", "Caitlin Clark")
    assert naming.prefix_match("jokic", "Nikola Jokić")
    assert not naming.prefix_match("catalin clark", "Caitlin Clark")


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
    novak = SearchResult(key="espn_tennis:296", name="Novak Djokovic", sport="Tennis", detail="", live_scores=True, inactive=True, popularity=152)
    marko = SearchResult(key="espn_tennis:1482", name="Marko Djokovic", sport="Tennis", detail="", live_scores=True, popularity=11)
    carlos = SearchResult(key="espn_tennis:3782", name="Carlos Djokovic", sport="Tennis", detail="", live_scores=True, popularity=90)
    assert [r.name for r in search.rank("djok", [], [marko, novak, carlos])] == ["Carlos Djokovic", "Novak Djokovic", "Marko Djokovic"]


# ---------------------------------------------------------------- names as the registry folds them


def test_apostrophes_are_typed_with_or_without_them():
    # The registry's search text drops apostrophes; matching kept them, so A'ja Wilson was found and then
    # left out (BUG-070).
    assert naming.prefix_match("aja wilson", "A'ja Wilson")
    assert naming.prefix_match("shaquille oneal", "Shaquille O'Neal")
    kante = Athlete(qid="Q7", sport="football", name="N'Golo Kanté", aliases=[], teams=["Al-Ittihad"], ids={}, sitelinks=60, current=True)
    for typed in ("ngolo kante", "N’Golo Kanté", "n'golo"):  # the second as an iPhone types it
        r = search.from_athlete(kante, typed)
        assert r.matched, typed
    assert search.from_athlete(kante, "N’Golo Kanté").exact
    assert search.closeness("ngolo kante", "N'Golo Kanté") == 1.0


# ---------------------------------------------------------------- one person, two sports


def test_an_athlete_in_two_sports_is_two_results():
    # Wikidata has one id for Yuzvendra Chahal; the registry a row for his cricket and one for his chess
    # (BUG-069). Merged by id alone, the chess row stood for both and following it bound his FIDE id.
    live = registry.live_system("cricket")  # Sportmonks with a token, else ESPN
    cricket = Athlete(qid="Q8062264", sport="cricket", name="Yuzvendra Chahal", aliases=[], teams=["India"], ids={live: "430246"}, sitelinks=16, current=True)
    chess = Athlete(qid="Q8062264", sport="chess", name="Yuzvendra Chahal", aliases=[], teams=[], ids={"lichess_chess": "5022215"}, sitelinks=16, current=True)
    ranked = search.rank("yuzvendra chahal", [], [search.from_athlete(chess, "yuzvendra chahal"), search.from_athlete(cricket, "yuzvendra chahal")])
    assert sorted((r.key, r.sport) for r in ranked) == sorted([(f"{live}:430246", "Cricket"), ("lichess_chess:5022215", "Chess")])
    # Neither with a live id: the key carries the sport, and following sends it.
    football = Athlete(qid="Q483027", sport="football", name="Paolo Maldini", aliases=[], teams=[], ids={"transfermarkt": "5803"}, sitelinks=81, current=False)
    tennis = Athlete(qid="Q483027", sport="tennis", name="Paolo Maldini", aliases=[], teams=[], ids={"atp": "M0DX"}, sitelinks=81, current=False, league="atp")
    ranked = search.rank("maldini", [], [search.from_athlete(tennis, "maldini"), search.from_athlete(football, "maldini")])
    assert sorted((r.key, r.sport) for r in ranked) == [("wikidata:Q483027:football", "Football"), ("wikidata:Q483027:tennis", "Tennis")]
    # A player on Sports Follow is the row of their own sport only.
    ours = SearchResult(key="player:3", name="Yuzvendra Chahal", sport="Cricket", detail="India", player_id=3, qid="Q8062264", live_scores=True, _rank=(1.0,))
    ranked = search.rank("chahal", [ours], [search.from_athlete(cricket, "chahal"), search.from_athlete(chess, "chahal")])
    assert [(r.player_id, r.sport) for r in ranked] == [(3, "Cricket"), (None, "Chess")]


# ---------------------------------------------------------------- exact names


def test_an_exact_handle_without_popularity_beats_partial_matches():
    # Esports rows have no popularity, and exactness only multiplied it: "tenz" listed four footballers
    # above TenZ (BUG-073).
    def row(key, name, popularity, exact=False, inactive=False, sport="Football"):
        return SearchResult(key=key, name=name, sport=sport, detail="", live_scores=True, exact=exact, inactive=inactive, popularity=popularity)

    tenzin = [row("wikidata:Q1753405:football", "Sonam Tenzing", 9, inactive=True), row("wikidata:Q27662793:football", "Tenzin Dorji", 4)]
    tenz = row("pandascore_esports:20401", "TenZ", 0, exact=True, sport="Valorant")
    assert [r.name for r in search.rank("tenz", [], [*tenzin, tenz])] == ["TenZ", "Sonam Tenzing", "Tenzin Dorji"]
    # Known popularity still counts first: Cristiano before the Brazilian Ronaldo, and the LoL pro after
    # both but before the partial matches below them.
    cristiano = row("espn_soccer:22774", "Cristiano Ronaldo", 214)
    r9 = row("wikidata:Q529207:football", "Ronaldo", 124, exact=True, inactive=True)
    lol = row("pandascore_esports:23057", "Ronaldo", 0, exact=True, sport="League of Legends")
    tiago = row("wikidata:Q949262:football", "Tiago Ronaldo", 7, inactive=True)
    assert [r.key for r in search.rank("ronaldo", [], [tiago, lol, r9, cristiano])] == [cristiano.key, r9.key, lol.key, tiago.key]


# ---------------------------------------------------------------- search end to end, without a database


def athlete(qid, name, sport="football", aliases=(), sitelinks=0, **extra) -> Athlete:
    return Athlete(qid=qid, sport=sport, name=name, aliases=list(aliases), teams=extra.pop("teams", []), ids=extra.pop("ids", {}), sitelinks=sitelinks, current=extra.pop("current", True), **extra)


def searching(monkeypatch, found=(), near=(), espn=(), ours=False):
    """search.run against a registry that finds `found` and spells `near`, and an ESPN that answers
    `espn`; returns the queries ESPN was asked. Nobody is on Sports Follow yet, unless `ours`: then
    run's session says who is (Players)."""
    asked = []
    monkeypatch.setattr(search, "local", lambda db, query, fan, sport: [])
    if not ours:
        monkeypatch.setattr(search, "_ours", lambda db, rows, fan: None, raising=False)
    monkeypatch.setattr(search.registry, "find", lambda db, query, sport, limit: list(found))
    monkeypatch.setattr(search.registry, "blocked", lambda db: set())
    monkeypatch.setattr(search.registry, "near", lambda db, query, sport=None: list(near))
    kept = cache.Cache("espn-test")

    def find(query, sport, adapters):  # as ESPN's: read through its source's cache
        return kept.read(f"search?query={query}", 3600, lambda: asked.append(query) or list(espn))

    live = Provider(name="ESPN", url="", what="", search=LiveSearch(find=find, by_word=True))
    monkeypatch.setattr(search, "providers", lambda: [(live, ESPN)])
    return asked


def test_a_loose_did_you_mean_still_asks_the_live_sources(monkeypatch):
    # "shane warne" suggested Shane Watson and never asked ESPN (BUG-072).
    watson = athlete("Q311", "Shane Watson", "cricket", sitelinks=30, ids={"espn_cricket": "8180"})
    warne = SearchResult(key="espn_cricket:8166", name="Shane Warne", sport="Cricket", detail="Australia", system="espn_cricket", athlete_id="8166", live_scores=True, exact=True)
    asked = searching(monkeypatch, near=[watson], espn=[warne])
    out = search.run(None, "shane warne")
    assert asked == ["shane warne"]
    assert [r["name"] for r in out["results"]] == ["Shane Warne"]
    assert [s["name"] for s in out["suggestions"]] == ["Shane Watson"]  # both are shown
    # A spelling that is surely the name needs no live source.
    sabalenka = athlete("Q2", "Aryna Sabalenka", "tennis", sitelinks=60, ids={"espn_tennis": "2"}, league="wta")
    asked = searching(monkeypatch, near=[sabalenka])
    out = search.run(None, "sabalnka")
    assert asked == [] and [s["name"] for s in out["suggestions"]] == ["Aryna Sabalenka"]


def test_a_removed_athletes_source_ids_arent_found(monkeypatch):
    # players.py removed Shane Warne: ESPN still lists him, but search leaves him out.
    warne = SearchResult(key="espn_cricket:8166", name="Shane Warne", sport="Cricket", detail="Australia", system="espn_cricket", athlete_id="8166", live_scores=True, exact=True)
    searching(monkeypatch, espn=[warne])
    monkeypatch.setattr(search.registry, "blocked", lambda db: {("espn_cricket", "8166")})
    assert search.run(None, "shane warne")["results"] == []


def test_a_client_over_its_search_limit_is_answered_without_the_live_sources(monkeypatch):
    warne = SearchResult(key="espn_cricket:8166", name="Shane Warne", sport="Cricket", detail="Australia", system="espn_cricket", athlete_id="8166", live_scores=True, exact=True)
    asked = searching(monkeypatch, espn=[warne])
    out = search.run(None, "shane warne", may_ask=lambda: False)
    assert asked == [] and out["results"] == []
    assert out["partial"] == ["ESPN"]  # the app says some athletes may be missing


def test_a_search_is_counted_once_and_only_when_it_asks_the_live_sources(monkeypatch):
    counted = []

    def may_ask():
        counted.append("search")
        return True

    # Nothing in the registry: ESPN is asked for the name, then word by word for "did you mean".
    asked = searching(monkeypatch)
    search.run(None, "catalin clark", may_ask=may_ask)
    assert sorted(asked) == ["catalin", "catalin clark", "clark"] and counted == ["search"]
    # A name the registry knows well never reaches the sources, so it isn't counted.
    counted.clear()
    asked = searching(monkeypatch, found=[athlete("Q6167581", "Jasprit Bumrah", "cricket", sitelinks=40, ids={"espn_cricket": "625383"})])
    search.run(None, "bumrah", may_ask=may_ask)
    assert asked == [] and counted == []


def test_a_search_the_source_cache_answers_isnt_counted_and_isnt_held_back(monkeypatch):
    # BUG-148: the per-minute limit was counted before the source cache was looked at, so a fan typing the same name
    # again over it was told ESPN didn't answer, though nothing would have been sent.
    warne = SearchResult(key="espn_cricket:8166", name="Shane Warne", sport="Cricket", detail="Australia", system="espn_cricket", athlete_id="8166", live_scores=True, exact=True)
    kept, sent = cache.Cache("search-test"), []

    def find(query, sport, adapters):  # as ESPN's: one document, read through its source's cache
        names = kept.read(f"search?query={query}", 3600, lambda: sent.append(query) or (["Shane Warne"] if query == "shane warne" else []))
        return [warne] if "Shane Warne" in names else []

    searching(monkeypatch)
    live = Provider(name="ESPN", url="", what="", search=LiveSearch(find=find))
    monkeypatch.setattr(search, "providers", lambda: [(live, ESPN)])
    counted = []

    def may_ask(allowed: bool):
        def ask() -> bool:
            counted.append(allowed)
            return allowed
        return ask

    first = search.run(None, "shane warne", may_ask=may_ask(True))
    assert sent == ["shane warne"] and counted == [True] and [r["name"] for r in first["results"]] == ["Shane Warne"]
    # Over the limit now: what the cache has is answered, uncounted; a new name still waits.
    again = search.run(None, "shane warne", may_ask=may_ask(False))
    assert (again["partial"], [r["name"] for r in again["results"]], sent, counted) == ([], ["Shane Warne"], ["shane warne"], [True])
    new = search.run(None, "glenn mcgrath", may_ask=may_ask(False))
    assert (new["partial"], new["results"], sent, counted) == (["ESPN"], [], ["shane warne"], [True, False])


def test_surely_the_name_meant():
    assert search.surely("sabalnka", "Aryna Sabalenka")
    assert search.surely("mohammad salah", "Mohamed Salah")  # the surname, and the first name spelled another way
    assert not search.surely("shane warne", "Shane Watson")
    assert not search.surely("kobe bryant", "Em Bryant")
    assert not search.surely("bharath kumar", "S R. Bharathi")


def test_the_picker_lists_and_counts_every_namesake(monkeypatch):
    # "ronaldo" said four athletes and listed three: the count came before the cut to fourteen (BUG-074).
    popular = [athlete(f"Q{i}", f"Ronaldo Player{i}", sitelinks=100 - i) for i in range(20)]
    exact = [athlete("Q529207", "Ronaldo", sitelinks=124, current=False), athlete("Q21707180", "Ronaldo da Silva Souza", aliases=["Ronaldo"], sitelinks=12),
             athlete("Q56065881", "Ronaldo de Oliveira Strada", aliases=["Ronaldo"], sitelinks=4),
             athlete("pandascore:23057", "Ronaldo", "esports", aliases=["Ronaldo Betea"], league="league-of-legends")]
    searching(monkeypatch, found=[*exact, *popular])
    out = search.run(None, "ronaldo")
    listed = sorted(r["name"] for r in out["results"] if r["exact"])
    assert out["namesakes"] == len(listed) == 4
    assert listed == ["Ronaldo", "Ronaldo", "Ronaldo da Silva Souza", "Ronaldo de Oliveira Strada"]  # the LoL pro too
    assert len(out["results"]) <= search.LIVE_LIMIT + search.LOCAL_LIMIT + 4


def test_one_person_in_two_sports_is_not_two_namesakes(monkeypatch):
    cricket = athlete("Q8062264", "Yuzvendra Chahal", "cricket", sitelinks=16, ids={"espn_cricket": "430246"})
    chess = athlete("Q8062264", "Yuzvendra Chahal", "chess", sitelinks=16, ids={"lichess_chess": "5022215"})
    searching(monkeypatch, found=[chess, cricket])
    out = search.run(None, "yuzvendra chahal")
    assert sorted(r["sport"] for r in out["results"]) == ["Chess", "Cricket"]
    assert out["namesakes"] == 0  # one athlete, listed once per sport


# ---------------------------------------------------------------- found by another name


def test_a_result_found_by_an_alias_says_which(monkeypatch):
    # "jonathan" listed Cije, Zame and EliGE by their real names and showed only handle, team and
    # country, so the list looked random (BUG-007).
    cije = athlete("pandascore:14258", "Cije", "esports", aliases=["Christian Jonathan"], teams=["Veroja"], ids={"pandascore_esports": "14258"}, league="cs-go", country="Indonesia")
    r = search.from_athlete(cije, "jonathan")
    assert r.matched and r.aka == "Christian Jonathan"
    assert search.from_athlete(cije, "cije").aka is None  # the name was typed: nothing to explain
    r9 = athlete("Q529207", "Ronaldo Nazário", aliases=["R9 Fenômeno", "R9"], sitelinks=124)
    assert search.from_athlete(r9, "r9").aka == "R9"  # an alias typed whole before one it starts
    searching(monkeypatch, found=[cije])
    assert [(x["name"], x["aka"]) for x in search.run(None, "jonathan")["results"]] == [("Cije", "Christian Jonathan")]
    # Any sport, and a player on Sports Follow found by a fan's alias shows it as the registry writes it.
    ours = SearchResult(key="lichess_chess:46616543", name="Gukesh D", sport="Chess", detail="", player_id=1, live_scores=True, aka="dommaraju gukesh", _rank=(1.0,))
    gukesh = athlete("Q56026072", "Gukesh D", "chess", aliases=["Dommaraju Gukesh"], ids={"lichess_chess": "46616543"})
    [row] = search.rank("dommaraju gukesh", [ours], [search.from_athlete(gukesh, "dommaraju gukesh")])
    assert (row.player_id, row.aka) == (1, "Dommaraju Gukesh")


# ---------------------------------------------------------------- nothing to search by


def test_a_query_with_nothing_to_search_by_lists_no_one_and_offers_no_research(monkeypatch):
    # "--" listed ESPN's default stars, and "विराट कोहली" found no one but offered an AI research that
    # always failed: a researched player is named by the query's Latin letters (BUG-075).
    lebron = SearchResult(key="espn_basketball:1966", name="LeBron James", sport="Basketball", detail="", system="espn_basketball", athlete_id="1966", live_scores=True)
    assert not naming.prefix_match("--", "LeBron James")
    for query in ["--", "..", "%%", "विराट कोहली", "விராட் கோலி", "梅西", "Лионель Месси"]:
        asked = searching(monkeypatch, espn=[lebron])
        out = search.run(None, query)
        assert (out["results"], out["suggestions"], out["researchable"], asked) == ([], [], False, []), query
    asked = searching(monkeypatch, espn=[lebron])
    out = search.run(None, "lebron")
    assert out["researchable"] and asked == ["lebron"]


# ---------------------------------------------------------------- without the research agent


def test_without_the_agent_search_lists_only_athletes_a_source_has(monkeypatch):
    # SPORTS_FOLLOW_AGENT=0 (no OpenRouter key): a page comes from a live source or not at all, so an
    # athlete only the agent could build isn't listed, and no research is offered.
    from sports_follow import config

    warne = SearchResult(key="espn_cricket:8166", name="Shane Warne", sport="Cricket", detail="Australia", system="espn_cricket", athlete_id="8166", live_scores=True)
    carwin = SearchResult(key="espn:mma:2335", name="Shane Carwin", sport="MMA", detail="", live_scores=False)
    searching(monkeypatch, espn=[warne, carwin])
    out = search.run(None, "shane")
    assert sorted(r["name"] for r in out["results"]) == ["Shane Carwin", "Shane Warne"] and out["researchable"]
    monkeypatch.setattr(config, "RESEARCH", False)  # the default: the agent on, for backgrounds only
    out = search.run(None, "shane")
    assert [r["name"] for r in out["results"]] == ["Shane Warne"] and not out["researchable"]
    monkeypatch.setattr(config, "AGENT", False)
    out = search.run(None, "shane")
    assert [r["name"] for r in out["results"]] == ["Shane Warne"] and not out["researchable"]
    # A registry athlete in a sport with a running source stays, even without its id there yet (it's
    # looked up by name and birth date when followed); one in a sport without one is left out before
    # anything is decided on it, so the live sources are still asked.
    monkeypatch.setattr(search.registry, "follow_system", lambda sport, ids: "espn_soccer" if sport == "football" else None)
    bond = athlete("Q1", "Shane Bond", "cricket", sitelinks=40)  # cricket has no running source, here
    long = athlete("Q2", "Shane Long", "football", sitelinks=40)  # no source id yet
    searching(monkeypatch, found=[bond, long])
    assert [r["name"] for r in search.run(None, "shane")["results"]] == ["Shane Long"]
    asked = searching(monkeypatch, found=[bond], espn=[warne])  # Bond alone would have spared the live sources
    assert [r["name"] for r in search.run(None, "shane")["results"]] == ["Shane Warne"] and asked == ["shane"]


# ---------------------------------------------------------------- native spellings


def test_native_spellings_find_the_star(monkeypatch):
    # "Novak Đoković" found only a did-you-mean and "thomas mueller" no Thomas Müller: Wikidata writes
    # Djokovic, and the registry folds Müller to "muller" (BUG-076).
    everyone = [
        athlete("Q5812", "Novak Djokovic", "tennis", sitelinks=153, ids={"espn_tennis": "296"}, league="atp"),
        athlete("Q3701304", "Damjan Đoković", sitelinks=3),
        athlete("Q43666", "Thomas Müller", sitelinks=94),
        athlete("Q7690078", "Taylor Mueller", aliases=["Taylor Thomas Mueller"], sitelinks=5),
        athlete("Q1", "Michal Kadlec", sitelinks=30),
    ]
    searching(monkeypatch)

    def find(db, query, sport, limit):  # as the registry looks names up: a word of the search text starts with each typed word
        return [a for a in everyone if all(f" {w}" in search.registry.search_text(a.name, a.aliases) for w in search.registry.words(query))]

    monkeypatch.setattr(search.registry, "find", find)
    for query in ["Novak Đoković", "Novak Ðoković", "Đoković"]:  # the second with Ð, U+00D0
        assert [r["name"] for r in search.run(None, query)["results"]][:1] == ["Novak Djokovic"], query
    out = search.run(None, "thomas mueller")
    assert [(r["name"], r["exact"]) for r in out["results"]] == [("Thomas Müller", True), ("Taylor Mueller", False)]
    assert [r["name"] for r in search.run(None, "mueller")["results"]][:1] == ["Thomas Müller"]
    assert search.run(None, "michael")["results"] == []  # looked up as "michal" too, but Michal isn't Michael
    assert search.registry.words("Ðoković") == ["dokovic"]
    assert naming.same("Novak Đoković", "Novak Djokovic") and naming.same("thomas mueller", "Thomas Müller")


# ---------------------------------------------------------------- players on Sports Follow


class Players:
    """A session that knows the players on Sports Follow by source id (and qid), their followers, and
    `mine`, the ones this fan follows. Every player's page is ready."""

    def __init__(self, ids, followers=None, mine=()):
        self.ids, self.followers, self.mine = ids, followers or {}, set(mine)

    def execute(self, stmt):
        column = stmt.column_descriptions[0]["expr"]
        if column is PlayerIdentity.system:
            rows = [(system, ext, pid) for (system, ext), pid in self.ids.items()]
        elif column is PlayerCard.player_id:
            rows = [(pid, "ready", {}) for pid in set(self.ids.values())]
        else:  # followers per player: signed-in fans, and every follow (models.followers)
            rows = [(pid, n, n) for pid, n in self.followers.items()]
        return SimpleNamespace(all=lambda: rows)

    def scalars(self, stmt):
        return list(self.mine)


def test_a_followed_player_found_by_another_name_is_theirs(monkeypatch):
    # "dommaraju gukesh" listed Gukesh D as a new athlete with a Follow button, though the fan follows
    # him: it isn't an alias fans typed, so only the registry found him (BUG-080).
    gukesh = athlete("Q56026072", "Gukesh D", "chess", aliases=["Dommaraju Gukesh"], sitelinks=40, ids={"lichess_chess": "46616543"}, title="GM", country="India")
    db = Players({("lichess_chess", "46616543"): 1, ("wikidata", "Q56026072"): 1, ("wikidata", "Q8062264"): 3, ("espn_soccer", "173896"): 14}, followers={1: 2, 3: 1}, mine={1})
    monkeypatch.setattr(pipeline, "registry_holder", lambda db, qid, sport: {("Q8062264", "cricket"): 3}.get((qid, sport)))
    searching(monkeypatch, found=[gukesh], ours=True)
    [r] = search.run(db, "dommaraju gukesh", fan="fan")["results"]
    assert (r["key"], r["player_id"], r["following"], r["followers"], r["status"]) == ("lichess_chess:46616543", 1, True, 2, "ready")
    # By qid only in the athlete's sport: Chahal the cricketer is player 3, Chahal the chess player isn't.
    rows = [search.from_athlete(athlete("Q8062264", "Yuzvendra Chahal", sport), "chahal") for sport in ("cricket", "chess")]
    search._ours(db, rows, "fan")
    assert [(r.sport, r.player_id, r.following, r.followers) for r in rows] == [("Cricket", 3, False, 1), ("Chess", None, False, 0)]
    # One player keyed two ways (ours by Sportmonks id, ESPN's hit by its own) is one row.
    ours = SearchResult(key="sportmonks_football:4125", name="Mohamed Salah", sport="Football", detail="Liverpool", player_id=14, live_scores=True, _rank=(1.0,))
    hit = SearchResult(key="espn_soccer:173896", name="Mohamed Salah", sport="Football", detail="Liverpool · Premier League", system="espn_soccer", athlete_id="173896", live_scores=True)
    search._ours(db, [hit], "fan")
    assert [(r.key, r.player_id) for r in search.rank("salah", [ours], [hit])] == [("sportmonks_football:4125", 14)]


@pytest.mark.parametrize("research", [True, False])
def test_a_live_hit_lists_the_player_here_it_names_though_the_query_is_only_like_them(monkeypatch, research):
    # "leo messi": ESPN finds Lionel Messi, and Messi's page here is only alike to what was typed (no alias of it
    # starts so). The hit was dropped as the page's, and the page wasn't listed: the answer was empty (BUG-147).
    from sports_follow import config

    monkeypatch.setattr(config, "RESEARCH", research)

    def page() -> SearchResult:
        return SearchResult(key="wikidata:Q615:football", name="Lionel Messi", sport="Football", detail="Inter Miami", player_id=28, followers=3, matched=False, _rank=(0.45,))

    hit = SearchResult(key="espn_soccer:45843", name="Lionel Messi", sport="Football", detail="Inter Miami · MLS", system="espn_soccer", athlete_id="45843", live_scores=True)
    asked = searching(monkeypatch, espn=[hit])
    monkeypatch.setattr(search, "local", lambda db, query, fan, sport: [page()])
    out = search.run(None, "leo messi")
    assert "leo messi" in asked
    # The page stands for both, as a registry row the source names does: one Messi, the one fans follow here.
    assert [(r["name"], r["player_id"]) for r in out["results"]] == [("Lionel Messi", 28)]
    # Someone only alike, whom the source didn't name, is still a "did you mean" at most, not a result.
    other = SearchResult(key="espn_soccer:1", name="Leon Messing", sport="Football", detail="", system="espn_soccer", athlete_id="1", live_scores=True)
    searching(monkeypatch, espn=[other])
    monkeypatch.setattr(search, "local", lambda db, query, fan, sport: [page()])
    assert 28 not in [r["player_id"] for r in search.run(None, "leo messi")["results"]]


# ---------------------------------------------------------------- the trigram index


def test_local_search_filters_with_what_the_trigram_index_answers():
    # Likeness was a function of the alias in WHERE, which no index answers: a sequential scan of every
    # alias, 311 ms a keystroke at 100k (BUG-081). It filters with % and <% at LIKENESS instead.
    statements = []
    db = SimpleNamespace(execute=lambda stmt: statements.append(stmt) or SimpleNamespace(all=lambda: []))
    assert search.local(db, "kohly", None, None) == []
    sql = [str(s.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})) for s in statements]
    [players] = [s for s in statements if "player_alias" in str(s)]
    where = str(players.whereclause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "similarity" not in where
    assert "player_alias.alias %% 'kohly'" in where and "'kohly' <%% player_alias.alias" in where
    assert any(f"'pg_trgm.similarity_threshold', '{search.LIKENESS}'" in s and f"'pg_trgm.word_similarity_threshold', '{search.LIKENESS}'" in s for s in sql)


class _NoFixturesInPreview:
    """A source whose games take many reads (as chess broadcasts do): its preview shows stats only."""

    sports = ("chess",)
    preview_fixtures = False

    def sport_of(self, ref):
        return "chess"

    def player(self, athlete_id, league):
        from sports_follow.adapters.base import PlayerRef
        return PlayerRef(system="test_source", athlete_id=athlete_id, name="Test Player", team_ids=[], team_names=[])

    def fixtures(self, ref):
        raise AssertionError("a preview of this source mustn't read its games")

    def stats(self, ref, recent):
        return [], ""


def test_a_preview_says_when_it_doesnt_read_the_athletes_games(monkeypatch):
    """Both apps hide Next and Last game on `fixtures: false` (AthletePage.tsx, mobile's athlete page)."""
    monkeypatch.setitem(search.ADAPTERS, "test_source", _NoFixturesInPreview())
    search._previews.clear()
    out = search.preview("test_source", "p1", None)
    assert out is not None and out["fixtures"] is False and out["next"] is None and out["last"] is None


def test_esports_as_a_filter_from_an_older_app_is_its_games_that_run(monkeypatch):
    """An app from before each game was a sport (an old web bundle, the installed Android app) asks for
    sport=esports: its games, not an unfiltered search (registry.find takes a group's sports: test_esports_games_db)."""
    asked = []
    searching(monkeypatch)
    monkeypatch.setattr(search.registry, "find", lambda db, query, sport, limit: asked.append(sport) or [])
    assert search.run(None, "tenz", "esports")["sport"] == "esports" and asked and set(asked) == {"esports"}
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if not s.group))
    assert search.run(None, "tenz", "esports")["sport"] is None  # no game runs: no such filter
