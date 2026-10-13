"""Player search against ESPN and Lichess responses recorded on 2026-09-29 (search.py)."""

from __future__ import annotations

from datetime import datetime, timezone

from sports_follow import search
from sports_follow.models import Athlete
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


# ---------------------------------------------------------------- names as the registry folds them


def test_apostrophes_are_typed_with_or_without_them():
    # The registry's search text drops apostrophes; matching kept them, so A'ja Wilson was found and then
    # left out (BUG-070).
    assert search._prefix_match("aja wilson", "A'ja Wilson")
    assert search._prefix_match("shaquille oneal", "Shaquille O'Neal")
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
    cricket = Athlete(qid="Q8062264", sport="cricket", name="Yuzvendra Chahal", aliases=[], teams=["India"], ids={"espn_cricket": "430246"}, sitelinks=16, current=True)
    chess = Athlete(qid="Q8062264", sport="chess", name="Yuzvendra Chahal", aliases=[], teams=[], ids={"lichess_chess": "5022215"}, sitelinks=16, current=True)
    ranked = search.rank("yuzvendra chahal", [], [search.from_athlete(chess, "yuzvendra chahal"), search.from_athlete(cricket, "yuzvendra chahal")])
    assert sorted((r.key, r.sport) for r in ranked) == [("espn_cricket:430246", "Cricket"), ("lichess_chess:5022215", "Chess")]
    # Neither with a live id: the key carries the sport, and following sends it.
    football = Athlete(qid="Q483027", sport="football", name="Paolo Maldini", aliases=[], teams=[], ids={"transfermarkt": "5803"}, sitelinks=81, current=False)
    tennis = Athlete(qid="Q483027", sport="tennis", name="Paolo Maldini", aliases=[], teams=[], ids={"atp": "M0DX"}, sitelinks=81, current=False, league="atp")
    ranked = search.rank("maldini", [], [search.from_athlete(tennis, "maldini"), search.from_athlete(football, "maldini")])
    assert sorted((r.key, r.sport) for r in ranked) == [("wikidata:Q483027:football", "Football"), ("wikidata:Q483027:tennis", "Tennis")]
    # A player on Sports Follow is the row of their own sport only.
    ours = Result(key="player:3", name="Yuzvendra Chahal", sport="Cricket", detail="India", player_id=3, qid="Q8062264", live_scores=True, _rank=(1.0,))
    ranked = search.rank("chahal", [ours], [search.from_athlete(cricket, "chahal"), search.from_athlete(chess, "chahal")])
    assert [(r.player_id, r.sport) for r in ranked] == [(3, "Cricket"), (None, "Chess")]


# ---------------------------------------------------------------- exact names


def test_an_exact_handle_without_popularity_beats_partial_matches():
    # Esports rows have no popularity, and exactness only multiplied it: "tenz" listed four footballers
    # above TenZ (BUG-073).
    def row(key, name, popularity, exact=False, inactive=False, sport="Football"):
        return Result(key=key, name=name, sport=sport, detail="", live_scores=True, exact=exact, inactive=inactive, popularity=popularity)

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


def searching(monkeypatch, found=(), near=(), espn=()):
    """search.run against a registry that finds `found` and spells `near`, and an ESPN that answers
    `espn`; returns the response and the queries ESPN was asked."""
    asked = []
    monkeypatch.setattr(search, "local", lambda db, query, fan, sport: [])
    monkeypatch.setattr(search.registry, "find", lambda db, query, sport, limit: list(found))
    monkeypatch.setattr(search.registry, "near", lambda db, query, sport=None: list(near))
    monkeypatch.setattr(search, "_espn", lambda query, limit=20: asked.append(query) or list(espn))
    return asked


def test_a_loose_did_you_mean_still_asks_the_live_sources(monkeypatch):
    # "shane warne" suggested Shane Watson and never asked ESPN (BUG-072).
    watson = athlete("Q311", "Shane Watson", "cricket", sitelinks=30, ids={"espn_cricket": "8180"})
    warne = Result(key="espn_cricket:8166", name="Shane Warne", sport="Cricket", detail="Australia", system="espn_cricket", athlete_id="8166", live_scores=True, exact=True)
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
    assert len(out["results"]) <= search.ESPN_LIMIT + search.LOCAL_LIMIT + 4


def test_one_person_in_two_sports_is_not_two_namesakes(monkeypatch):
    cricket = athlete("Q8062264", "Yuzvendra Chahal", "cricket", sitelinks=16, ids={"espn_cricket": "430246"})
    chess = athlete("Q8062264", "Yuzvendra Chahal", "chess", sitelinks=16, ids={"lichess_chess": "5022215"})
    searching(monkeypatch, found=[chess, cricket])
    out = search.run(None, "yuzvendra chahal")
    assert sorted(r["sport"] for r in out["results"]) == ["Chess", "Cricket"]
    assert out["namesakes"] == 0  # one athlete, listed once per sport
