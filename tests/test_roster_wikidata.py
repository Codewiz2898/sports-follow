"""Esports pros matched to their Wikidata person, for their photos (roster_wikidata.py): query rows shaped
like the query service's JSON bindings (Wikidata is CC0) to people, and registry rows matched to them,
without the network; and, against a throwaway database at alembic head, what a match writes, which a
source's weekly roster keeps and a later run takes back. The database tests are skipped without one:

    SPORTS_FOLLOW_TEST_DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/python -m pytest tests/test_roster_wikidata.py
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.orm import sessionmaker

from sports_follow import photos, players, registry, roster_wikidata as rw, sports
from sports_follow.adapters import KNOWN, PlayerRef, Rostered
from sports_follow.models import Athlete, AthleteUnmatch, Follow, Player, PlayerCard, PlayerIdentity

WD = "http://www.wikidata.org/entity/"
FILE = "http://commons.wikimedia.org/wiki/Special:FilePath/"
ESPORTS = sports.BY_KEY["esports"]
GAMES = ["esports", *(s.key for s in sports.members(ESPORTS))]  # every game's rows, matched together, and those of no game


def query(qid: str, label: str, *, born: str | None = None, precision: str = "11", image: str | None = None, sitelinks: int = 0, dead: bool = False,
          aliases: tuple[str, ...] = (), ids: dict[str, list[str]] | None = None, teams: tuple[str, ...] = (), disciplines: tuple[str, ...] = (),
          given: tuple[str, ...] = (), family: tuple[str, ...] = ()) -> tuple[list[dict], list[dict], list[dict]]:
    """One person's rows, as the query service gives them."""
    core = {"p": WD + qid, "sitelinks": str(sitelinks)}
    if born:
        core |= {"birth": f"{born}T00:00:00Z", "birth_precision": precision}
    if image:
        core["image"] = FILE + image.replace(" ", "%20")
    if dead:
        core["death"] = "2020-01-01T00:00:00Z"
    facts = [{"p": WD + qid, "kind": prop, "v": v} for prop, values in (ids or {}).items() for v in values]
    for kind, values in (("team", teams), ("discipline", disciplines), ("given", given), ("family", family)):
        facts += [{"p": WD + qid, "kind": kind, "v": v} for v in values]
    names = [{"p": WD + qid, "n": label, "kind": "label", "lang": "en"}] + [{"p": WD + qid, "n": a, "kind": "alias", "lang": "en"} for a in aliases]
    return [core], facts, names


def person(qid: str, label: str, **facts) -> rw.Person:
    [found] = rw.people(ESPORTS, *query(qid, label, **facts))
    return found


def row(qid: str, handle: str, *, real: str = "", born: date | None = None, game: str = "", teams: tuple[str, ...] = (), ids: tuple[tuple[str, str], ...] = ()) -> rw.Row:
    return rw.Row(qid, handle, [real] if real else [], born, ESPORTS.game_key(game), list(teams), list(ids))


def wikidata_tenz() -> rw.Person:
    return person("Q113450352", "TenZ", born="2001-05-05", image="SEN TENZ 2023.jpg", sitelinks=4, aliases=('Tyson "TenZ" Ngo', "SEN TenZ"),
                  ids={"P10918": ["valorant/TenZ"], "P8878": ["13254/tenz"]}, teams=("Sentinels",), disciplines=("Valorant",), given=("Tyson",), family=("Ngo",))


def matched(rows: list[rw.Row], everyone: list[rw.Person]) -> dict[str, tuple[str, str]]:
    return {m.row.qid: (m.person.qid, m.how) for m in rw.match(rows, everyone)}


# ---------------------------------------------------------------- query rows to people


def test_people_from_query_rows():
    tenz = wikidata_tenz()
    assert {"tenz", "sen tenz", "tyson ngo"} <= tenz.handles  # the label, aliases, the nickname in quotes, Liquipedia's page and HLTV's name
    assert ["tyson", "ngo"] in tenz.names and ["ngo", "tyson"] in tenz.names
    assert tenz.born == date(2001, 5, 5) and tenz.image == "SEN TENZ 2023.jpg" and tenz.plays == {"valorant", "cs2"}  # HLTV is Counter-Strike's
    assert rw.people(ESPORTS, *query("Q1", "Old Pro", dead=True)) == []
    assert person("Q2", "Someone", born="1999-01-01", precision="9").born is None  # a year says nothing about which person


def test_a_liquipedia_page_gives_a_handle_without_its_disambiguation():
    collapse = person("Q108916234", "Magomed Khalilov", ids={"P10918": ["dota2/Collapse_(Russian_player)"]})
    assert "collapse" in collapse.handles and collapse.plays == {"dota2"}


def test_a_hyphenated_real_name_is_read_apart_and_run_together():
    faker = person("Q15618298", "Faker", aliases=("Lee Sang-hyeok",))
    assert ["lee", "sang", "hyeok"] in faker.names and ["lee", "sanghyeok"] in faker.names


def test_games_are_one_key_however_written():
    keys = {ESPORTS.game_key(g) for g in ("CS2", "Counter-Strike 2", "Counter-Strike: Global Offensive", "counterstrike")}
    assert keys == {"cs2"}
    assert ESPORTS.game_key("Dota 2") == ESPORTS.game_key("dota2") and ESPORTS.game_key("BGMI") == ESPORTS.game_key("PUBG Mobile")
    assert ESPORTS.game_key("") == ""


def test_liquipedia_says_where_wikidata_keeps_its_ids():
    lp = KNOWN["liquipedia_esports"]
    assert lp.wikidata_property == "P10918" and lp.wikidata_id("pubgmobile:Jonathan") == "pubgmobile/Jonathan"
    assert KNOWN["pandascore_esports"].wikidata_property is None  # Wikidata has no PandaScore ids


# ---------------------------------------------------------------- matching


def test_a_pro_is_matched_by_handle_real_name_and_game():
    tenz = wikidata_tenz()
    assert matched([row("pandascore:20401", "TenZ", real="Tyson Ngo", game="Valorant")], [tenz]) == {"pandascore:20401": ("Q113450352", "handle")}
    # A team for the game, and the birth date for the real name, do as well.
    assert matched([row("pandascore:1", "TenZ", born=date(2001, 5, 5), teams=("Sentinels",))], [tenz]) == {"pandascore:1": ("Q113450352", "handle")}


def test_a_handle_alone_is_never_enough():
    band = person("Q3307572", "Faker", image="Faker at Sydney Big Day Out 2007.jpg")
    pro = person("Q15618298", "Faker", ids={"P10918": ["leagueoflegends/Faker"]})
    faker = row("pandascore:585", "Faker", game="League of Legends", teams=("T1",))
    assert matched([faker], [band, pro]) == {}  # no real name or birth date to tell them apart
    assert matched([row("pandascore:585", "Faker", real="Lee Sang-hyeok", game="League of Legends")], [band]) == {}  # the band's no player


def test_the_real_name_without_the_game_or_a_team_isnt_enough():
    s1mple = person("Q48428626", "S1mple", aliases=("Oleksandr Kostyliev",), ids={"P10918": ["counterstrike/S1mple"]})
    assert matched([row("pandascore:59479", "s1mple", real="Oleksandr Kostyliev", game="Valorant")], [s1mple]) == {}
    # The game decides; and a name spelled alike is the name ("Aleksandr" for "Oleksandr").
    assert matched([row("pandascore:17541", "s1mple", real="Aleksandr Kostyliev", game="CS2")], [s1mple]) == {"pandascore:17541": ("Q48428626", "handle")}


def test_two_pros_with_one_handle_each_get_their_own():
    lol = person("Q105488493", "Choi Woo-je", ids={"P10918": ["leagueoflegends/Zeus"]})
    cs = person("Q18634253", "Danylo Teslenko", aliases=('Danylo "Zeus" Teslenko',), disciplines=("Counter-Strike",))
    god = person("Q34201", "Zeus")
    rows = [row("pandascore:31391", "Zeus", real="Choi Woo-je", game="League of Legends"), row("pandascore:17570", "Zeus", real="Danylo Teslenko", game="CS2"),
            row("pandascore:33200", "Zeus", real="Kim Hyun-woo", game="Valorant")]
    assert matched(rows, [lol, cs, god]) == {"pandascore:31391": ("Q105488493", "handle"), "pandascore:17570": ("Q18634253", "handle")}


def test_birth_dates_that_differ_rule_a_person_out():
    tenz = wikidata_tenz()
    assert matched([row("pandascore:1", "TenZ", real="Tyson Ngo", born=date(2003, 1, 1), game="Valorant")], [tenz]) == {}


@pytest.mark.parametrize("a, b", [
    ("Jeong Ji-hoon", "Jung Ji-hun"), ("Lee Seong-hyuk", "Lee Seong-hyeok"), ("Cho Geon-hee", "Jo Geon-hee"), ("Gwon Sang-yun", "Kwon Sang-yun"),
    ("Byeong-jun Yu", "Yu Byeong-jun"), ("Ilya Mulyarchuk", "Illya Mulyarchuk"), ("Erick Santos", "Erick Andrade Santos"),
    ("Aleksandr Kostyliev", "Oleksandr Kostyliev"), ("Alexandr Kostylev", "Oleksandr Kostyliev"), ("Ilya Zalutskiy", "Ilya Zalutsky"),
    ("Eduard Abgaryan", "Edward Abgaryan"), ("Andrei Petrov", "Andrey Petrov"), ("Aleksei Petrov", "Alexey Petrov"),
    ("Dmitri Petrov", "Dmitriy Petrov"), ("Chung Junho", "Jeong Junho"),
    ("Miroslaw Kolpakov", "Miroslav Kolpakow"), ("Vladimir Nikogosyan", "Vladimir Nikogosian"),
])
def test_one_persons_names_agree(a, b):
    assert any(rw.names_agree(x, y) for x in rw.plain(a) for y in rw.plain(b))


@pytest.mark.parametrize("a, b", [
    ("Kim Junho", "Kim Jinho"), ("Park Jihoon", "Park Jaehoon"), ("Lee Sanghyeok", "Lee Seunghyuk"), ("Kim Minsu", "Kim Minseo"),
    ("Choi Hyunwoo", "Choi Hyunwook"), ("Martin Hansen", "Morten Hansen"), ("Ivan Petrov", "Ivana Petrov"), ("Kim Min-ho", "Kim Min-su"),
    ("Ahmed Salah", "Mohamed Salah"), ("Salah", "Mohamed Salah"), ("Byeong-jun Yu", "Byeong-chun Yu"),
    ("Chung Junho", "Chung Jinho"), ("Junho Chung", "Jinho Chung"), ("Bang Junho", "Bang Jinho"), ("Jiang Zihao", "Jiang Zihan"),
    ("Feng Yuhan", "Feng Yuhao"), ("Cheung Kaiho", "Cheung Kaiyo"), ("Christian Jensen", "Christina Jensen"),
    ("Wang Junlei", "Wang Junli"), ("Zhang Hao-lei", "Zhang Hao-li"), ("Chen Zhilei", "Chen Zhili"), ("Li Xiaomei", "Li Xiaomi"),
])
def test_other_peoples_names_dont(a, b):
    assert not any(rw.names_agree(x, y) for x in rw.plain(a) for y in rw.plain(b))


def test_a_digit_for_a_letter_is_the_same_handle():
    suma1l = person("Q21033312", "Suma1L", aliases=("Syed Sumail Hassan",), given=("Syed",), family=("Hassan",), disciplines=("Dota 2",))
    assert matched([row("pandascore:9368", "SumaiL", real="Syed Sumail Hassan", game="Dota 2")], [suma1l]) == {"pandascore:9368": ("Q21033312", "handle")}


def test_teams_are_one_by_their_telling_words():
    filler = ESPORTS.team_filler
    assert rw.same_team(["Vitality"], ["Team Vitality"], filler) and rw.same_team(["Gen.G Esports"], ["Gen.G"], filler)
    assert not rw.same_team(["Team Liquid"], ["Team Vitality"], filler) and not rw.same_team(["T1"], ["T10"], filler)
    assert not rw.same_team(["Team Esports"], ["Esports Team"], filler)  # nothing left that tells them apart


def test_a_pro_whose_source_id_wikidata_keeps_is_matched_by_it():
    jonathan = person("Q116030750", "Jonathan Gaming", born="2002-09-21", ids={"P10918": ["pubgmobile/Jonathan"]}, image="Jonathan Gaming in 2023 (cropped).jpg")
    lp = row("lp:pubgm:4567", "Jonathan", ids=(("P10918", "pubgmobile/Jonathan"),))
    assert matched([lp], [jonathan]) == {"lp:pubgm:4567": ("Q116030750", "id")}
    # Unless their birth dates differ: the page has passed to a namesake since Wikidata noted it.
    lp.born = date(2004, 2, 2)
    assert matched([lp], [jonathan]) == {}


def test_a_handle_with_a_teams_tag_is_no_real_name():
    tgltn = person("Q1", "TGLTN", aliases=("FaZe TGLTN",), ids={"P10918": ["pubg/TGLTN"]})
    assert matched([row("pubg:tgltn", "TGLTN", real="FaZe_TGLTN", game="PUBG")], [tgltn]) == {}
    assert all(rw.tagged(name, {"tenz"}) for name in ("SEN TenZ", "C9 TenZ", "Cloud9 Tenz", "TenZ LEV"))
    assert rw.tagged("Liquid TenZ", {"tenz"}, ["Team Liquid"]) and not rw.tagged("Liquid TenZ", {"tenz"}, ["Sentinels"])
    # A real name may hold the handle.
    yawar = person("Q128403794", "YawaR", aliases=("Yawar Hassan",), disciplines=("Dota 2",))
    assert matched([row("pandascore:9492", "YawaR", real="Yawar Hassan", game="Dota 2")], [yawar]) == {"pandascore:9492": ("Q128403794", "handle")}


def test_a_real_name_and_birth_date_match_without_the_handle():
    jonathan = person("Q116030750", "Jonathan Gaming", born="2002-09-21", given=("Jonathan",), family=("Amaral",))
    pro = row("lp:pubgm:4567", "Jonathan", real="Jonathan Amaral", born=date(2002, 9, 21), game="BGMI")
    assert matched([pro], [jonathan]) == {"lp:pubgm:4567": ("Q116030750", "name and birth date")}
    pro.born = date(2002, 9, 22)
    assert matched([pro], [jonathan]) == {}


def test_a_pro_two_people_could_be_or_a_person_two_pros_could_be_is_matched_to_no_one():
    tenz, double = wikidata_tenz(), person("Q9", "TenZ", aliases=("Tyson Ngo",), ids={"P10918": ["valorant/TenZ"]})
    assert matched([row("pandascore:1", "TenZ", real="Tyson Ngo", game="Valorant")], [tenz, double]) == {}
    twice = [row("pandascore:1", "TenZ", real="Tyson Ngo", game="Valorant"), row("pandascore:2", "TenZ", real="Tyson Ngo", game="Valorant")]
    assert matched(twice, [tenz]) == {}
    # Unless one is theirs by id.
    by_id = row("lp:val:1", "TenZ", ids=(("P10918", "valorant/TenZ"),))
    assert matched([*twice, by_id], [tenz]) == {"lp:val:1": ("Q113450352", "id")}
    # A pro two people fit is no one's, and still keeps either from going to another pro.
    one = row("pandascore:1", "TenZ", real="Tyson Ngo", game="Valorant")
    other = row("pandascore:3", "TenZ", real="Tyson Ngo", teams=("Sentinels",))
    assert matched([one, other], [tenz, double]) == {}


def test_a_pro_an_operator_unmatched_is_never_matched_to_that_person_or_to_anyone():
    tenz, double = wikidata_tenz(), person("Q9", "TenZ", aliases=("Tyson Ngo",), teams=("Sentinels",))
    one = row("pandascore:1", "TenZ", real="Tyson Ngo", game="Valorant", teams=("Sentinels",))

    def vetoed(rows: list[rw.Row], everyone: list[rw.Person], vetoes: dict[str, set[str | None]]) -> dict[str, str]:
        return {m.row.qid: m.person.qid for m in rw.match(rows, everyone, vetoes=vetoes)}

    assert vetoed([one], [tenz], {}) == {"pandascore:1": "Q113450352"}
    assert vetoed([one], [tenz], {"pandascore:1": {"Q113450352"}}) == {}
    assert vetoed([one], [tenz], {"pandascore:1": {None}}) == {}
    assert vetoed([one], [tenz], {"pandascore:2": {None}}) == {"pandascore:1": "Q113450352"}  # another pro's
    # Not that person: someone else who fits can be them; not anyone: no one.
    assert vetoed([one], [tenz, double], {}) == {}
    assert vetoed([one], [tenz, double], {"pandascore:1": {"Q113450352"}}) == {"pandascore:1": "Q9"}
    assert vetoed([one], [tenz, double], {"pandascore:1": {None, "Q113450352"}}) == {}
    # By id too: the page Wikidata keeps for them isn't theirs.
    by_id = row("lp:val:1", "TenZ", ids=(("P10918", "valorant/TenZ"),))
    assert vetoed([by_id], [tenz], {"lp:val:1": {"Q113450352"}}) == {}
    # Not that person leaves them to another pro who fits; not anyone still keeps them from the other pro.
    other = row("pandascore:3", "TenZ", real="Tyson Ngo", teams=("Sentinels",))
    assert vetoed([one, other], [tenz], {}) == {}
    assert vetoed([one, other], [tenz], {"pandascore:1": {"Q113450352"}}) == {"pandascore:3": "Q113450352"}
    assert vetoed([one, other], [tenz], {"pandascore:1": {None}}) == {}


# ---------------------------------------------------------------- the registry

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL or "")
    with engine.begin() as c:
        c.execute(text("TRUNCATE athlete, athlete_override, athlete_unmatch, player, follow RESTART IDENTITY CASCADE"))
    made = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def unit():
        s = made()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(registry, "session", unit)
    monkeypatch.setattr(rw, "session", unit)
    s = made()
    yield s
    s.close()
    engine.dispose()


def pro(athlete_id: str, name: str, real: str) -> Rostered:
    return Rostered(key=f"pandascore:{athlete_id}", ref=PlayerRef("pandascore_esports", athlete_id, name, ["1"], ["Sentinels"], league="valorant"), aliases=[real])


def _row(db, qid: str) -> Athlete:
    db.expire_all()
    return db.scalars(select(Athlete).where(Athlete.qid == qid)).one()


@needs_db
def test_a_match_is_written_kept_by_the_weekly_roster_and_taken_back_when_it_no_longer_holds(db, monkeypatch):
    registry.upsert(db, registry.roster_rows(KNOWN["pandascore_esports"], [pro("20401", "TenZ", "Tyson Ngo"), pro("7", "Nobody", "No One")]), photos=False)
    db.commit()
    tenz = wikidata_tenz()
    read = {"people": ([tenz], True)}
    monkeypatch.setattr(rw, "read_people", lambda kind: read["people"])

    found, done = rw.match_sport(ESPORTS, GAMES)
    assert [m.row.qid for m in found] == ["pandascore:20401"] and (done["changed"], done["with_photo"]) == (1, 1)
    row = _row(db, "pandascore:20401")
    assert row.ids == {"pandascore_esports": "20401", "wikidata": "Q113450352"} and row.image == "SEN TENZ 2023.jpg"
    assert rw.match_sport(ESPORTS, GAMES)[1]["changed"] == 0  # nothing new

    # The weekly roster refreshes the row and keeps what was found.
    registry.upsert(db, registry.roster_rows(KNOWN["pandascore_esports"], [pro("20401", "TenZ", "Tyson Ngo")]), photos=False)
    db.commit()
    row = _row(db, "pandascore:20401")
    assert row.ids["wikidata"] == "Q113450352" and row.image == "SEN TENZ 2023.jpg"

    # A run that couldn't read everyone takes nothing back; a complete one does, photo and all.
    read["people"] = ([], False)
    assert rw.match_sport(ESPORTS, GAMES)[1]["taken_back"] == 0 and _row(db, "pandascore:20401").image
    read["people"] = ([], True)
    assert rw.match_sport(ESPORTS, GAMES)[1]["taken_back"] == 1
    row = _row(db, "pandascore:20401")
    assert row.ids == {"pandascore_esports": "20401"} and row.image is None


@needs_db
def test_a_match_a_weekly_run_takes_back_takes_that_persons_profile_off_the_page(db, monkeypatch):
    # BUG-297: a complete run that no longer matched a pro to their person took back the Wikidata id and photo, but
    # the birth date, country and About that person's Wikidata and Wikipedia gave the page stayed until its monthly
    # profile read; only an operator's unmatch took them off.
    from sports_follow import bus, pipeline

    registry.upsert(db, registry.roster_rows(KNOWN["pandascore_esports"], [pro("20401", "TenZ", "Tyson Ngo")]), photos=False)
    db.add(Player(id=3, slug="tenz", name="TenZ", sport="Valorant", nationality="Someland"))
    db.flush()
    theirs = {"born": "2001-05-05", "nationality": "Someland", "summary": "Someone.", "about": {"source": "Wikipedia"},
              "profile_from": {"born": "wikidata", "nationality": "wikidata", "summary": "wikipedia"}}
    db.add_all([PlayerIdentity(player_id=3, system="wikidata", external_id="pandascore:20401", confidence=1.0),
                PlayerCard(player_id=3, status="ready", card={"player": {"name": "TenZ", **theirs}}, version=1)])
    db.commit()
    read = {"people": ([wikidata_tenz()], True)}
    monkeypatch.setattr(rw, "read_people", lambda kind: read["people"])
    for module in (players, pipeline):
        monkeypatch.setattr(module, "session", rw.session)
    published: dict[int, dict] = {}
    monkeypatch.setattr(bus, "store_card", published.__setitem__)
    assert rw.match_sport(ESPORTS, GAMES)[1]["changed"] == 1 and published == {}  # a match made takes nothing off

    read["people"] = ([], True)  # Wikidata no longer says he's them
    assert rw.match_sport(ESPORTS, GAMES)[1]["taken_back"] == 1
    db.expire_all()
    card, page = db.get(PlayerCard, 3), db.get(Player, 3)
    assert card is not None and page is not None
    assert not {"born", "nationality", "summary", "about"} & set(card.card["player"]) and page.nationality is None
    assert published[3]["player"] == card.card["player"]


@needs_db
def test_a_dry_run_writes_nothing(db, monkeypatch):
    registry.upsert(db, registry.roster_rows(KNOWN["pandascore_esports"], [pro("20401", "TenZ", "Tyson Ngo")]), photos=False)
    db.commit()
    monkeypatch.setattr(rw, "read_people", lambda kind: ([wikidata_tenz()], True))
    found, done = rw.match_sport(ESPORTS, GAMES, write=False)
    assert len(found) == 1 and "changed" not in done and _row(db, "pandascore:20401").image is None


@needs_db
def test_a_followed_pro_matched_has_their_photo_fetched(db, monkeypatch):
    registry.upsert(db, registry.roster_rows(KNOWN["pandascore_esports"], [pro("20401", "TenZ", "Tyson Ngo")]), photos=False)
    db.add(Player(id=3, slug="tenz", name="TenZ", sport="Valorant"))
    db.flush()
    db.add_all([PlayerIdentity(player_id=3, system="wikidata", external_id="pandascore:20401", confidence=1.0), Follow(fan_id="fan", player_id=3, alert_rules={})])
    db.commit()
    assert photos.wanted(db) == {}
    monkeypatch.setattr(rw, "read_people", lambda kind: ([wikidata_tenz()], True))
    rw.match_sport(ESPORTS, GAMES)
    db.expire_all()
    assert photos.wanted(db) == {"SEN TENZ 2023.jpg": "TenZ"}


@needs_db
def test_a_pro_an_operator_unmatched_stays_unmatched_through_the_weekly_runs_until_rematched(db, monkeypatch):
    registry.upsert(db, registry.roster_rows(KNOWN["pandascore_esports"], [pro("20401", "TenZ", "Tyson Ngo")]), photos=False)
    db.commit()
    read = {"people": ([wikidata_tenz()], True)}
    monkeypatch.setattr(rw, "read_people", lambda kind: read["people"])
    rw.match_sport(ESPORTS, GAMES)
    assert _row(db, "pandascore:20401").image == "SEN TENZ 2023.jpg"

    [(_, now, image)] = players.unmatch(db, players.find(db, "pandascore:20401"), note="someone else's face")
    db.commit()
    row = _row(db, "pandascore:20401")
    assert now and image == "SEN TENZ 2023.jpg" and row.ids == {"pandascore_esports": "20401"} and row.image is None
    [u] = db.scalars(select(AthleteUnmatch)).all()
    assert (u.qid, u.sport, u.person, u.name, u.note) == ("pandascore:20401", "valorant", None, "TenZ", "someone else's face")

    # Neither a run that read everyone nor one that didn't matches him again, nor does the weekly roster.
    for complete in (True, False):
        read["people"] = ([wikidata_tenz()], complete)
        found, done = rw.match_sport(ESPORTS, GAMES)
        assert found == [] and (done["changed"], done["taken_back"]) == (0, 0)
    registry.upsert(db, registry.roster_rows(KNOWN["pandascore_esports"], [pro("20401", "TenZ", "Tyson Ngo")]), photos=False)
    db.commit()
    assert _row(db, "pandascore:20401").image is None

    # Rematched, the next run matches him again.
    assert [u.person for u in players.rematch(db, "pandascore:20401")] == [None]
    db.commit()
    assert rw.match_sport(ESPORTS, GAMES)[1]["changed"] == 1 and _row(db, "pandascore:20401").image == "SEN TENZ 2023.jpg"


@needs_db
def test_a_match_found_before_the_operator_unmatched_isnt_written_and_one_held_is_taken_back_by_any_run(db):
    registry.upsert(db, registry.roster_rows(KNOWN["pandascore_esports"], [pro("20401", "TenZ", "Tyson Ngo")]), photos=False)
    db.commit()
    found = rw.match(rw.rows(db, ESPORTS, GAMES), [wikidata_tenz()])  # the run read the registry and Wikidata, then:
    [(_, now, _)] = players.unmatch(db, players.find(db, "pandascore:20401"), "Q113450352")
    db.commit()
    assert not now  # he wasn't matched to them yet
    assert rw.record(db, GAMES, found, complete=False) == ({"changed": 0, "taken_back": 0}, [])
    db.commit()
    assert _row(db, "pandascore:20401").image is None
    # A match to them the row holds anyway (written by hand) is taken back, even by a run that didn't read everyone.
    db.execute(update(Athlete).where(Athlete.qid == "pandascore:20401").values(ids=Athlete.ids.op("||")(func.jsonb_build_object("wikidata", "Q113450352")), image="x.jpg"))
    db.commit()
    assert rw.record(db, GAMES, [], complete=False) == ({"changed": 0, "taken_back": 1}, ["pandascore:20401"])
    db.commit()
    assert _row(db, "pandascore:20401").image is None
