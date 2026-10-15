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

from sports_follow import players, registry, sports, starters
from sports_follow.adapters import Adapter, PlayerRef, Roster, Rostered, pubg
from sports_follow.models import Athlete, AthleteOverride, Follow, Moment, Player, PlayerCard, PlayerIdentity, Starter

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

LONG_AGO = datetime.now(timezone.utc) - timedelta(days=30)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE athlete, athlete_override, player, follow, moment, starter RESTART IDENTITY CASCADE"))
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

    for module in (players, registry):
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
    registry.upsert(db, [athlete("pandascore:34470", "esports", "amster", {"pandascore_esports": "34470"})])
    db.commit()
    assert players.main(["add", "--sport", "esports", "--name", "amster", "--id", "pandascore_esports=34470"]) == 0
    assert "Already in the registry, now kept: amster" in capsys.readouterr().out
    assert [a.qid for a in db.scalars(select(Athlete))] == ["pandascore:34470"]
    assert players.main(["add", "--sport", "cricket", "--name", "X", "--id", "pandascore_esports=1"]) == 2  # not a cricket source


class Lister(Adapter):
    """A source whose roster no longer lists anyone."""

    system, sport, name, covers, roster_prefix = "fake_esports", "esports", "Fake", "Fake", "pandascore:"
    provider = pubg.PROVIDER

    def roster(self, keep):
        keep([Rostered(key="pandascore:5", ref=PlayerRef("fake_esports", "5", "Listed", [], []))])
        return Roster(complete=True)


def test_a_pinned_pro_stays_when_their_source_stops_listing_them(db):
    registry.upsert(db, [athlete("pandascore:34470", "esports", "amster"), athlete("pandascore:7", "esports", "Retired")])
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
    assert players.main(["list"]) == 0 and "Nothing added or removed yet." in capsys.readouterr().out


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
