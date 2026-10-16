"""Operator tooling (players.py): adding and removing players, against a throwaway database at alembic head,
since the overrides work through Postgres itself (ON CONFLICT, cascades, JSONB containment). Skipped
without one:

    docker compose exec postgres createdb -U sports sports_follow_test
    DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/alembic upgrade head
    SPORTS_FOLLOW_TEST_DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/python -m pytest tests/test_players.py

Every test empties the tables first.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import sessionmaker

from types import SimpleNamespace

from sports_follow import adapters, bus, players, registry, sports, starters
from sports_follow.adapters import Adapter, AdapterError, Fixture, PlayerRef, Roster, Rostered, pubg
from sports_follow.adapters.budget import RateLimited
from sports_follow.models import Athlete, AthleteOverride, AthleteUnmatch, Follow, Moment, Photo, Player, PlayerCard, PlayerIdentity, Starter

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

LONG_AGO = datetime.now(timezone.utc) - timedelta(days=30)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE athlete, athlete_override, athlete_unmatch, player, follow, moment, starter, photo RESTART IDENTITY CASCADE"))
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

    for module in (players, registry, starters):
        monkeypatch.setattr(module, "session", unit)
    cleared: list[int] = []
    monkeypatch.setattr(players, "forget_cards", cleared.extend)
    s = made()
    s.cleared = cleared
    yield s
    s.close()
    engine.dispose()


def athlete(qid: str, sport: str, name: str, ids: dict | None = None, **extra) -> dict:
    return {"qid": qid, "sport": sport, "name": name, "aliases": [], "search_text": registry.search_text(name, []), "birth_date": None, "birth_precision": None,
            "country": None, "teams": [], "league": None, "title": None, "ids": ids or {}, "image": None, "sitelinks": 1, "current": True, **extra}


def kohli(db) -> None:
    """Virat Kohli followed by two fans, with a moment, bound to ESPNcricinfo, and in the registry."""
    db.add(Player(id=1, slug="virat-kohli", name="Virat Kohli", sport="Cricket", wikidata_qid="Q213854"))
    db.flush()
    db.add_all([PlayerCard(player_id=1, status="ready", card={}), PlayerIdentity(player_id=1, system="wikidata", external_id="Q213854", confidence=1.0),
                PlayerIdentity(player_id=1, system="espn_cricket", external_id="253802", confidence=1.0),
                Follow(fan_id="a", player_id=1, alert_rules={}), Follow(fan_id="b", player_id=1, alert_rules={}),
                Moment(key="m1", player_id=1, kind="fifty", level="key", title="Kohli reaches 50", body="")])
    registry.upsert(db, [athlete("Q213854", "cricket", "Virat Kohli", {"espn_cricket": "253802"})])
    db.commit()


def test_removing_a_page_takes_everything_with_it_and_blocks_the_athlete(db):
    kohli(db)
    assert players.main(["remove", "1", "--yes", "--note", "test"]) == 0
    db.expire_all()
    assert db.get(Player, 1) is None and not db.scalars(select(Follow)).all() and not db.scalars(select(Moment)).all()  # cascaded
    assert not db.scalars(select(Athlete)).all() and db.cleared == [1]
    [o] = db.scalars(select(AthleteOverride)).all()
    assert (o.qid, o.sport, o.action, o.ids, o.note) == ("Q213854", "cricket", "remove", {"espn_cricket": "253802"}, "test")
    assert ("espn_cricket", "253802") in registry.blocked(db)
    # Monday's import lists him again: he isn't written back.
    registry.upsert(db, [athlete("Q213854", "cricket", "Virat Kohli", {"espn_cricket": "253802"}), athlete("Q1", "cricket", "Someone Else")])
    db.commit()
    assert [a.qid for a in db.scalars(select(Athlete))] == ["Q1"]


def test_removing_asks_first(db, capsys):
    kohli(db)
    assert players.main(["remove", "Q213854"], ask=lambda _: "n") == 1
    out = capsys.readouterr().out
    assert "page 1: Virat Kohli (Cricket), followed by 2, 0 games, 1 moments" in out and "registry Q213854 (cricket)" in out
    db.expire_all()
    assert db.get(Player, 1) is not None and not db.scalars(select(AthleteOverride)).all()


def test_a_source_id_finds_the_page_and_the_registry_row(db):
    kohli(db)
    for ref in ("1", "Q213854", "espn_cricket:253802"):
        found = players.find(db, ref)
        assert ([p.id for p in found.players], [a.qid for a in found.athletes]) == ([1], ["Q213854"]), ref
    assert players.find(db, "Q213854", "chess").athletes == []


def test_a_page_no_registry_row_has_is_blocked_by_its_source_ids(db):
    db.add(Player(id=2, slug="someone", name="Some One", sport="Basketball"))
    db.flush()
    db.add(PlayerIdentity(player_id=2, system="espn_basketball", external_id="99", confidence=1.0))
    db.commit()
    assert players.main(["remove", "2", "--yes"]) == 0
    [o] = db.scalars(select(AthleteOverride)).all()
    assert (o.qid, o.sport, o.ids) == ("player:2", "basketball", {"espn_basketball": "99"})
    assert ("espn_basketball", "99") in registry.blocked(db)


def test_an_added_athlete_is_kept_through_the_weekly_import(db, monkeypatch):
    assert players.main(["add", "--sport", "chess", "--name", "Club Player", "--alias", "C. Player", "--born", "1990-05-01", "--note", "local hero"]) == 0
    added = db.scalars(select(Athlete)).one()
    assert added.qid.startswith("op:") and added.aliases == ["C. Player"] and str(added.birth_date) == "1990-05-01"
    registry.upsert(db, [athlete("Q9", "chess", "Gone Away")])
    db.execute(update(Athlete).values(updated_at=LONG_AGO))  # both from an earlier week
    db.commit()
    # This week's run: Wikidata lists two others, not the hand-added one nor Q9.
    monkeypatch.setattr(registry, "members", lambda sport: ["Q1", "Q2"])
    monkeypatch.setattr(registry, "fetch", lambda sport, qids: ([], [], []))
    monkeypatch.setattr(registry, "build", lambda sport, *rows: [athlete("Q1", "chess", "One"), athlete("Q2", "chess", "Two")])
    monkeypatch.setattr(registry, "link_players", lambda sport: 0)
    registry.import_sport("chess")
    db.expire_all()
    assert sorted(a.qid for a in db.scalars(select(Athlete))) == sorted(["Q1", "Q2", added.qid])  # Q9 dropped, the added one kept


def test_adding_a_source_id_already_in_the_registry_pins_that_athlete(db, capsys):
    registry.upsert(db, [athlete("pandascore:34470", "cs2", "amster", {"pandascore_esports": "34470"})])
    db.commit()
    assert players.main(["add", "--sport", "cs2", "--name", "amster", "--id", "pandascore_esports=34470"]) == 0
    assert "Already in the registry, now kept: amster" in capsys.readouterr().out
    assert [a.qid for a in db.scalars(select(Athlete))] == ["pandascore:34470"]
    assert players.main(["add", "--sport", "cricket", "--name", "X", "--id", "pandascore_esports=1"]) == 2  # not a cricket source


class Lister(Adapter):
    """A source whose roster no longer lists anyone."""

    system, sports, name, covers, roster_prefix = "fake_esports", ("cs2",), "Fake", "Fake", "pandascore:"
    provider = pubg.PROVIDER

    def roster(self, keep, wanted=None):
        keep([Rostered(key="pandascore:5", ref=PlayerRef("fake_esports", "5", "Listed", [], []))])
        return Roster(complete=True)


def test_a_pinned_pro_stays_when_their_source_stops_listing_them(db):
    registry.upsert(db, [athlete("pandascore:34470", "cs2", "amster"), athlete("pandascore:7", "cs2", "Retired")])
    db.execute(update(Athlete).values(updated_at=LONG_AGO))
    db.commit()
    assert players.main(["add", "pandascore:34470"]) == 0
    registry.import_roster(Lister())
    db.expire_all()
    assert sorted(a.qid for a in db.scalars(select(Athlete))) == ["pandascore:34470", "pandascore:5"]  # 7 dropped, the pinned one kept


def test_restoring_lets_the_import_bring_them_back(db, capsys):
    kohli(db)
    players.main(["remove", "Q213854", "--yes"])
    assert players.main(["add", "Q213854"]) == 2  # removed: can't be pinned
    assert players.main(["restore", "Q213854"]) == 0 and "No longer removed: Virat Kohli" in capsys.readouterr().out
    registry.upsert(db, [athlete("Q213854", "cricket", "Virat Kohli")])
    db.commit()
    assert [a.qid for a in db.scalars(select(Athlete))] == ["Q213854"]
    assert players.main(["list"]) == 0 and "Nothing added, removed or unmatched yet." in capsys.readouterr().out


# ---------------------------------------------------------------- unmatching

PHOTO = {"url": "/api/photos/abc.jpg", "credit": "Someone, CC BY 2.0"}


def tenz(db, followed: bool = True) -> None:
    """TenZ matched to a Wikidata person, with their photo (on the Credits page), his card showing it, and
    followed unless not."""
    db.add(Player(id=3, slug="tenz", name="TenZ", sport="Valorant"))
    db.flush()
    db.add_all([PlayerIdentity(player_id=3, system="wikidata", external_id="pandascore:20401", confidence=1.0),
                PlayerCard(player_id=3, status="ready", card={"player": {"name": "TenZ", "photo": PHOTO}}, version=1),
                Photo(file="SEN TENZ 2023.jpg", name="TenZ", status="ok", digest="abc", media_type="image/jpeg", fetched_at=LONG_AGO),
                *([Follow(fan_id="a", player_id=3, alert_rules={})] if followed else [])])
    registry.upsert(db, [athlete("pandascore:20401", "valorant", "TenZ", {"pandascore_esports": "20401", "wikidata": "Q113450352"}, image="SEN TENZ 2023.jpg")])
    db.commit()


def test_unmatching_a_pro_takes_their_photo_off_at_once_and_rematching_takes_it_back(db, capsys, monkeypatch):
    tenz(db)
    published: dict[int, dict] = {}
    monkeypatch.setattr(bus, "store_card", published.__setitem__)
    assert players.main(["unmatch", "3", "--note", "someone else's face"]) == 0
    out = capsys.readouterr().out
    assert "Took back TenZ (valorant, pandascore:20401); no run matches them to anyone again." in out and "Took the photo off 1 card(s)." in out
    db.expire_all()
    [a] = db.scalars(select(Athlete)).all()
    assert a.ids == {"pandascore_esports": "20401"} and a.image is None
    card = db.get(PlayerCard, 3)
    assert card is not None and "photo" not in card.card["player"] and published == {3: card.card}  # off the fans' screens too
    assert not db.scalars(select(Photo)).all()  # and off the Credits page
    [u] = db.scalars(select(AthleteUnmatch)).all()
    assert (u.qid, u.sport, u.person, u.note) == ("pandascore:20401", "valorant", None, "someone else's face")
    assert players.main(["list"]) == 0 and "pandascore:20401      TenZ  not anyone  (someone else's face)" in capsys.readouterr().out

    assert players.main(["unmatch", "pandascore_esports:20401", "Q113450352"]) == 0  # by source id, and only that person
    assert "Nothing to take back now for TenZ (valorant, pandascore:20401) (not matched to Q113450352)" in capsys.readouterr().out
    assert sorted((u.person or "") for u in db.scalars(select(AthleteUnmatch))) == ["", "Q113450352"]
    assert players.main(["rematch", "pandascore:20401", "Q113450352"]) == 0 and "No longer unmatched from Q113450352: TenZ" in capsys.readouterr().out
    assert players.main(["rematch", "pandascore:20401"]) == 0 and "No longer unmatched from anyone: TenZ" in capsys.readouterr().out
    assert not db.scalars(select(AthleteUnmatch)).all()
    assert players.main(["rematch", "pandascore:20401"]) == 2 and "no match of pandascore:20401 was taken back" in capsys.readouterr().err


def test_unmatching_takes_the_photo_off_a_card_nobody_follows_now(db, monkeypatch):
    tenz(db, followed=False)  # anyone may still open his page
    published: dict[int, dict] = {}
    monkeypatch.setattr(bus, "store_card", published.__setitem__)
    assert players.main(["unmatch", "3"]) == 0
    db.expire_all()
    card = db.get(PlayerCard, 3)
    assert card is not None and "photo" not in card.card["player"] and published == {3: card.card}


def test_unmatching_takes_back_only_a_match_to_the_person_named(db, capsys):
    tenz(db)
    assert players.main(["unmatch", "pandascore:20401", "Q1"]) == 0  # he's matched to Q113450352
    db.expire_all()
    assert db.scalars(select(Athlete)).one().image == "SEN TENZ 2023.jpg" and db.scalars(select(AthleteUnmatch.person)).all() == ["Q1"]


def test_unmatching_refuses_what_isnt_a_matched_registry_athlete(db, capsys):
    kohli(db)
    assert players.main(["unmatch", "Q213854"]) == 2 and "came from Wikidata itself" in capsys.readouterr().err
    assert players.main(["unmatch", "pandascore:404"]) == 2 and "no registry athlete has that id" in capsys.readouterr().err
    tenz(db)
    assert players.main(["unmatch", "3", "TenZ"]) == 2 and "isn't a Wikidata id" in capsys.readouterr().err
    assert not db.scalars(select(AthleteUnmatch)).all()


def test_an_unmatch_stands_apart_from_keeping_and_removing(db, capsys, monkeypatch):
    tenz(db)
    monkeypatch.setattr(bus, "store_card", lambda *_: None)
    assert players.main(["add", "pandascore:20401"]) == 0 and players.main(["unmatch", "pandascore:20401"]) == 0
    db.expire_all()
    assert [o.action for o in db.scalars(select(AthleteOverride))] == ["keep"] and len(db.scalars(select(AthleteUnmatch)).all()) == 1
    # Removed and restored, he comes back unmatched: the wrong face doesn't.
    assert players.main(["remove", "pandascore:20401", "--yes"]) == 0 and players.main(["restore", "pandascore:20401"]) == 0
    db.expire_all()
    assert not db.scalars(select(AthleteOverride)).all() and db.scalars(select(AthleteUnmatch.qid)).all() == ["pandascore:20401"]
    # Rematching works by the registry id though the row is gone.
    assert players.main(["rematch", "pandascore:20401"]) == 0


# ---------------------------------------------------------------- starters


@pytest.fixture(autouse=True)
def live_source(monkeypatch):
    """The live source has whoever Wikidata gave an id for, and nobody else (no network in tests)."""
    asked: list[str] = []

    def resolve(person):
        asked.append(person.qid)
        return (None, person.ids) if person.ids.get(registry.live_system(person.sport) or "") else None

    monkeypatch.setattr(registry, "resolve", resolve)
    return asked


@pytest.fixture(autouse=True)
def pools(monkeypatch):
    """The top pools and live cards starters.py keeps in Redis, in memory here."""
    state = SimpleNamespace(top={}, cards={})
    monkeypatch.setattr(starters, "CACHE_SECONDS", -1)  # every offer worked out afresh
    monkeypatch.setattr(starters, "_cache", {})
    monkeypatch.setattr(starters, "top_pool", lambda sport: state.top.get(sport, []))
    monkeypatch.setattr(starters, "store_top", lambda sport, entries: state.top.__setitem__(sport, entries))
    monkeypatch.setattr(starters, "hot_cards", lambda ids: {i: state.cards[i] for i in ids if i in state.cards})
    return state


def chess_starters(db) -> str:
    """Two chess players a live source knows by id, and one it doesn't. Returns the source's system."""
    system = registry.live_system("chess")
    if system is None:
        pytest.skip("no live chess source in this build")
    registry.upsert(db, [athlete("Q10", "chess", "Gukesh D", {system: "gukesh"}), athlete("Q11", "chess", "R Praggnanandhaa", {system: "pragg"}),
                         athlete("Q12", "chess", "Nobody Online")])
    db.commit()
    return system


def test_starters_are_offered_in_the_order_they_were_added_and_pinned(db, capsys):
    system = chess_starters(db)
    for qid in ("Q11", "Q10"):
        assert players.main(["starter", "add", qid]) == 0
    assert players.main(["starter", "add", "Q12"]) == 2 and "doesn't have Nobody Online" in capsys.readouterr().err  # nobody to follow live
    assert players.main(["starter", "add", "Q10"]) == 0 and "Already a starter: Gukesh D" in capsys.readouterr().out
    db.expire_all()
    assert [(s.qid, s.position) for s in db.scalars(select(Starter).order_by(Starter.position))] == [("Q11", 1), ("Q10", 2)]
    assert {(o.qid, o.action) for o in db.scalars(select(AthleteOverride))} == {("Q10", "keep"), ("Q11", "keep")}  # no import drops them
    [offer] = starters.offer(db, "fan")
    assert offer["key"] == "chess" and [(r["name"], r["system"], r["athlete_id"], r["following"]) for r in offer["players"]] == [
        ("R Praggnanandhaa", system, "pragg", False), ("Gukesh D", system, "gukesh", False)]
    assert players.main(["starter", "list"]) == 0
    listing = capsys.readouterr().out
    assert listing.index("R Praggnanandhaa") < listing.index("Gukesh D")


def test_a_starter_whose_source_id_went_missing_isnt_offered(db, capsys):
    chess_starters(db)
    assert players.main(["starter", "add", "Q10"]) == 0
    db.execute(update(Athlete).where(Athlete.qid == "Q10").values(ids={}))  # an import that lost the id
    db.commit()
    assert starters.offer(db, "fan") == []
    assert players.main(["starter", "list"]) == 0 and "Gukesh D (not offered: no running source has their id)" in capsys.readouterr().out


def test_a_starter_already_on_the_app_says_who_follows_them(db):
    system = chess_starters(db)
    db.add(Player(id=7, slug="gukesh-d", name="Gukesh D", sport="Chess", wikidata_qid="Q10"))
    db.flush()
    db.add_all([PlayerCard(player_id=7, status="ready", card={}), PlayerIdentity(player_id=7, system=system, external_id="gukesh", confidence=1.0),
                Follow(fan_id="fan", player_id=7, alert_rules={}), Follow(fan_id="other", player_id=7, alert_rules={})])
    db.commit()
    assert players.main(["starter", "add", "Q10"]) == 0
    [row] = starters.offer(db, "fan")[0]["players"]
    assert (row["player_id"], row["following"], row["followers"]) == (7, True, 2)
    assert starters.offer(db, "someone new")[0]["players"][0]["following"] is False


def test_a_sport_thats_off_offers_no_starters(db, monkeypatch):
    chess_starters(db)
    assert players.main(["starter", "add", "Q10"]) == 0
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ENABLED if s.key != "chess"))
    assert starters.offer(db, "fan") == []


def test_removing_a_starter_or_the_athlete_takes_the_starter_out(db, capsys):
    chess_starters(db)
    for qid in ("Q10", "Q11"):
        assert players.main(["starter", "add", qid]) == 0
    assert players.main(["starter", "remove", "Q10"]) == 0
    assert players.main(["starter", "remove", "Q10"]) == 2 and "isn't a starter" in capsys.readouterr().err
    assert players.main(["remove", "Q11", "--yes"]) == 0  # the athlete goes, and their starter row with them
    db.expire_all()
    assert db.scalars(select(Starter)).all() == []
    assert players.main(["starter", "add", "Q11"]) == 2  # removed: no registry row to offer


def test_a_starter_in_two_sports_needs_the_sport(db, capsys):
    kohli(db)
    cricket = registry.live_system("cricket")
    if cricket is None:
        pytest.skip("no live cricket source in this build")
    registry.upsert(db, [athlete("Q213854", "cricket", "Virat Kohli", {cricket: "253802"}), athlete("Q213854", "chess", "Virat Kohli")])
    db.commit()
    assert players.main(["starter", "add", "Q213854"]) == 2 and "say which with --sport" in capsys.readouterr().err
    assert players.main(["starter", "add", "Q213854", "--sport", "cricket"]) == 0
    db.expire_all()
    assert [(s.qid, s.sport) for s in db.scalars(select(Starter))] == [("Q213854", "cricket")]


def test_an_esports_starter_is_checked_in_the_source_their_row_came_from(db, capsys, monkeypatch, live_source):
    """Esports sources sit side by side, each for its own games: a pro from the second is checked there,
    not looked for by name in the game's first source."""
    for system in [s for s, a in adapters.ADAPTERS.items() if "bgmi" in a.sports]:
        monkeypatch.delitem(adapters.ADAPTERS, system)
    asked: list[tuple[str, str]] = []
    for system in ("first_esports", "second_esports"):
        monkeypatch.setitem(adapters.ADAPTERS, system, SimpleNamespace(system=system, sports=("bgmi",), player=lambda xid, league=None, s=system: asked.append((s, xid)) or object()))
    registry.upsert(db, [athlete("pro:77", "bgmi", "Jonathan", {"second_esports": "77"}, league="pubgmobile")])
    db.commit()
    assert players.main(["starter", "add", "pro:77"]) == 0
    assert "followed live through second_esports" in capsys.readouterr().out
    assert asked == [("second_esports", "77")] and live_source == []  # never searched for by name in the first
    [row] = starters.offer(db, "fan")[0]["players"]
    assert (row["system"], row["athlete_id"]) == ("second_esports", "77")


NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def test_starters_mix_whos_playing_the_most_followed_and_the_best_known(db, pools):
    system = chess_starters(db)

    def page(pid: int, name: str, fans: int) -> None:
        db.add(Player(id=pid, slug=f"p{pid}", name=name, sport="Chess"))
        db.flush()
        db.add_all([PlayerCard(player_id=pid, status="ready", card={}), PlayerIdentity(player_id=pid, system=system, external_id=f"x{pid}", confidence=1.0),
                    *[Follow(fan_id=f"f{i}", player_id=pid, alert_rules={}) for i in range(fans)]])

    page(1, "Live Larry", 3)
    page(2, "Soon Sam", 2)
    page(3, "Idle Ida", 1)
    db.commit()
    # The worker's live cards: one on now, one in two hours. The morning's top pool: one tomorrow, one within the hour.
    pools.cards = {1: {"live": {"is_live": True, "headline": "Round 3"}}, 2: {"upcoming": [{"title": "Round 4", "start_utc": (NOW + timedelta(hours=2)).isoformat()}]}}
    pools.top["chess"] = [{"qid": "Q10", "start_utc": (NOW + timedelta(hours=30)).isoformat(), "title": "Tata Steel", "competition": "Tata Steel"},
                          {"qid": "Q11", "start_utc": (NOW + timedelta(hours=1)).isoformat(), "title": "Norway Chess", "competition": "Norway Chess"}]
    [offer] = starters.offer(db, "f0", NOW)
    # The pools take turns (playing, followed, top); someone in two keeps their first place, and playing now
    # outranks being followed: Soon Sam came in as followed and plays in two hours.
    assert [(r["name"], r["why"]) for r in offer["players"]] == [
        ("Live Larry", "live"), ("Gukesh D", "top"), ("R Praggnanandhaa", "soon"), ("Soon Sam", "soon"), ("Idle Ida", "followed")]
    assert [r["following"] for r in offer["players"]] == [True, False, False, True, True]  # f0 follows every page
    assert offer["players"][1]["next"]["title"] == "Tata Steel" and offer["players"][3]["next"]["title"] == "Round 4"


def test_with_nobody_followed_or_pinned_the_best_known_are_offered(db, pools):
    chess_starters(db)
    pools.top["chess"] = [{"qid": "Q11", "start_utc": (NOW + timedelta(days=3)).isoformat(), "title": "Cup", "competition": "Cup"}]
    [offer] = starters.offer(db, "fan", NOW)
    assert [(r["name"], r["why"]) for r in offer["players"]] == [("R Praggnanandhaa", "top")]


def test_the_top_pool_is_the_best_known_with_a_game_soon(db, pools, monkeypatch):
    system = registry.live_system("chess")
    if system is None:
        pytest.skip("no live chess source in this build")
    monkeypatch.setattr(starters, "STARTERS_HOME_COUNTRY", "India")
    registry.upsert(db, [athlete("Q20", "chess", "Norway Star", {system: "a"}, sitelinks=50, country="Norway"),
                         athlete("Q21", "chess", "Home Hero", {system: "b"}, sitelinks=30, country="India"),  # counts 60
                         athlete("Q22", "chess", "Played Out", {system: "c"}, sitelinks=40),
                         athlete("Q23", "chess", "Far Off", {system: "d"}, sitelinks=35),
                         athlete("Q24", "chess", "Retired", {system: "e"}, sitelinks=90, current=False),
                         athlete("Q25", "chess", "Not Found", sitelinks=80),
                         athlete("Q26", "chess", "Roster Only", {system: "f"}, sitelinks=0)])  # no Wikipedia knows them
    db.commit()

    def game(hours: float, status: str = "scheduled") -> Fixture:
        return Fixture(source_id="g", title="A game", competition="Cup", start_utc=NOW + timedelta(hours=hours), venue=None, status=status, team_ids=[], locator={})

    games = {"Q20": [game(5)], "Q21": [game(-1, "live")], "Q22": [game(-30, "final")], "Q23": [game(24 * 40)]}
    source = SimpleNamespace(fixtures=lambda ref: games[ref])
    asked: list[str] = []
    monkeypatch.setattr(registry, "find_in_source", lambda person: asked.append(person.qid) or ((source, person.qid) if person.ids else None))
    assert starters.refresh(NOW)["chess"] == 3
    # Best known first (Home Hero counts double); Played Out played yesterday, so is kept with no next game;
    # Far Off's only game is past the window, and Not Found isn't in the source.
    assert [(e["qid"], e["start_utc"]) for e in pools.top["chess"]] == [
        ("Q21", (NOW - timedelta(hours=1)).isoformat()), ("Q20", (NOW + timedelta(hours=5)).isoformat()), ("Q22", None)]
    assert "Q26" not in asked and "Q24" not in asked  # unknown to Wikipedia, or retired: never asked about

    def unreachable(ref):
        if ref == "Q21":
            raise AdapterError("source down for this one")
        return games[ref]

    source.fixtures = unreachable
    starters.refresh(NOW)
    assert [e["qid"] for e in pools.top["chess"]] == ["Q20", "Q22"]  # left out, the rest still asked

    def broken(ref):
        if ref == "Q20":
            raise KeyError("a field the source dropped")
        return games[ref]

    source.fixtures = broken
    starters.refresh(NOW)
    assert [e["qid"] for e in pools.top["chess"]] == ["Q21", "Q22"]  # any error leaves one athlete out, not the sport

    def spent(ref):
        raise RateLimited("allowance spent")

    source.fixtures = spent
    monkeypatch.setattr(starters, "LIMITED_PAUSE", 0)
    assert starters.refresh(NOW)["chess"] == 0  # waits once, then stops asking: every other call would be refused too


def test_a_top_players_game_that_is_over_isnt_shown_and_a_live_card_wins(db, pools):
    system = chess_starters(db)
    pools.top["chess"] = [{"qid": "Q10", "start_utc": (NOW - timedelta(hours=6)).isoformat(), "title": "This morning", "competition": "Cup"},
                          {"qid": "Q11", "start_utc": (NOW + timedelta(days=2)).isoformat(), "title": "Saturday", "competition": "Cup"}]
    # Praggnanandhaa has a page, nobody follows it, and its card says he's playing now.
    db.add(Player(id=9, slug="pragg", name="R Praggnanandhaa", sport="Chess", wikidata_qid="Q11"))
    db.flush()
    db.add_all([PlayerCard(player_id=9, status="ready", card={"live": {"is_live": True, "headline": "Round 5"}}),
                PlayerIdentity(player_id=9, system=system, external_id="pragg", confidence=1.0)])
    db.commit()
    players_offered = starters.offer(db, "fan", NOW)[0]["players"]
    gukesh, pragg = sorted(players_offered, key=lambda r: r["name"])
    assert (gukesh["why"], gukesh["next"]) == ("top", None)  # this morning's game is over: no "Next game" on the tile
    assert (pragg["why"], pragg["next"]["live"]) == ("live", "Round 5")  # his card knows better than Saturday's fixture
