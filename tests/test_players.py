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
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import sessionmaker

from types import SimpleNamespace

from sports_follow import adapters, bus, photos, players, registry, sports, starters
from sports_follow.adapters import Adapter, AdapterError, Fixture, PlayerRef, Roster, Rostered, pubg
from sports_follow.adapters.budget import RateLimited
from sports_follow.models import (Account, Athlete, AthleteOverride, AthleteUnmatch, Clip, Event, EventPlayer, EventState, Follow, MatchFollow, Moment, Photo, Player,
                                  PlayerAlias, PlayerCard, PlayerIdentity, PlayerLine, Question, Starter)

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

LONG_AGO = datetime.now(timezone.utc) - timedelta(days=30)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE athlete, athlete_override, athlete_unmatch, player, follow, moment, starter, photo, account, event, clip RESTART IDENTITY CASCADE"))
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


def games(db, **players_in: list[int]) -> dict[str, int]:
    """Finished games, each named for what it is, with the players in it; each with a score and a line of the first's."""
    now = datetime.now(timezone.utc)
    made = {name: Event(key=f"espn_cricket:{name}", sport="Cricket", title=name, status="final") for name in players_in}
    db.add_all(made.values())
    db.flush()
    for name, ids in players_in.items():
        db.add_all([*(EventPlayer(event_id=made[name].id, player_id=pid) for pid in ids), EventState(event_id=made[name].id, as_of=now, status="final", state={}),
                    PlayerLine(event_id=made[name].id, player_id=ids[0], as_of=now, stats={})])
    db.flush()
    return {name: e.id for name, e in made.items()}


def test_removing_a_player_takes_the_games_only_they_were_in_with_them(db):
    # BUG-226: removing a player left the games only they were in, with every score of them, though nothing polls or
    # shows them any more: they piled up for good. One another page, a followed game or a question still holds stays.
    kohli(db)
    db.add(Player(id=2, slug="rohit-sharma", name="Rohit Sharma", sport="Cricket"))
    made = games(db, alone=[1], shared=[1, 2], followed=[1], asked=[1], **{"clipped:2": [1]})
    db.add_all([MatchFollow(fan_id="c", event_id=made["followed"], keys=["espn_cricket:followed"]),
                Question(game="espn_cricket:asked", ask="result", keys=["espn_cricket:asked"], sport="cricket", match="espn_cricket/asked", title="Who wins?"),
                Clip(slug="c1", match="espn_cricket/clipped:2", source="file", seconds=5.0, width=1, height=1, players=[1])])
    db.commit()
    assert players.main(["remove", "1", "--yes"]) == 0
    db.expire_all()
    assert sorted(e.title for e in db.scalars(select(Event))) == ["asked", "clipped:2", "followed", "shared"]
    assert made["alone"] not in {s.event_id for s in db.scalars(select(EventState))} and len(db.scalars(select(EventState)).all()) == 4
    assert [(e.event_id, e.player_id) for e in db.scalars(select(EventPlayer))] == [(made["shared"], 2)]


def test_a_page_folded_into_another_takes_the_games_only_it_was_in_with_it(db):
    # BUG-226: a typed name's page folded into the player who holds their athlete left the games its research found
    # for it, which no page is in any more; one the player is in too stays theirs.
    from sports_follow import pipeline

    db.add_all([Player(id=7, slug="virat-kohli", name="Virat Kohli", sport="Cricket"), Player(id=8, slug="kohli", name="Kohli", sport="Cricket")])
    db.flush()
    made = games(db, researched=[8], both=[8, 7])
    folded, into = db.get(Player, 8), db.get(Player, 7)
    assert folded is not None and into is not None
    assert pipeline._fold(db, folded, into) == 8  # pyright: ignore[reportPrivateUsage]
    db.commit()
    db.expire_all()
    assert [e.title for e in db.scalars(select(Event))] == ["both"] and {s.event_id for s in db.scalars(select(EventState))} == {made["both"]}
    assert [(e.event_id, e.player_id) for e in db.scalars(select(EventPlayer))] == [(made["both"], 7)]


def test_removing_a_player_takes_their_photo_off_the_credits_page_at_once(db):
    # BUG-220: removing a player left their photo on the Credits page, and its file served, until the photos job
    # forgot it, up to 30 days on. One another athlete's row still shows stays.
    kohli(db)
    registry.upsert(db, [athlete("Q213854", "chess", "Virat Kohli", {"lichess_chess": "vk"}, image="Kohli 2019.jpg")])  # the same person in a sport he isn't removed from
    db.execute(update(Athlete).where(Athlete.sport == "cricket").values(image="Virat Kohli 2019.jpg"))
    db.add(Player(id=2, slug="virat-kohli-chess", name="Virat Kohli", sport="Chess", wikidata_qid="Q213854"))
    db.flush()
    db.add(Follow(fan_id="c", player_id=2, alert_rules={}))
    now = datetime.now(timezone.utc)
    db.add_all([Photo(file="Virat Kohli 2019.jpg", name="Virat Kohli", status="ok", digest="a" * 32, media_type="image/jpeg", data=b"jpeg", fetched_at=now),
                Photo(file="Kohli 2019.jpg", name="Virat Kohli", status="ok", digest="b" * 32, media_type="image/jpeg", data=b"jpeg", fetched_at=now)])
    db.commit()
    assert len(photos.used(db)) == 2 and photos.serve(db, f"{'a' * 32}.jpg") is not None
    assert players.main(["remove", "1", "--yes"]) == 0
    db.expire_all()
    assert [p["name"] for p in photos.used(db)] == ["Virat Kohli"] and photos.serve(db, f"{'a' * 32}.jpg") is None
    assert photos.serve(db, f"{'b' * 32}.jpg") is not None  # his chess page's


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


def shutte(db) -> None:
    """Ryan Shutte, a Zimbabwe cricketer Wikidata knows by his ESPNcricinfo id alone, followed through it."""
    db.add(Player(id=5, slug="ryan-shutte", name="Ryan Shutte", sport="Cricket", wikidata_qid="Q101095505"))
    db.flush()
    db.add_all([PlayerCard(player_id=5, status="ready", card={}), PlayerIdentity(player_id=5, system="wikidata", external_id="Q101095505", confidence=1.0),
                PlayerIdentity(player_id=5, system="espn_cricket", external_id="38606", confidence=1.0), Follow(fan_id="a", player_id=5, alert_rules={})])
    registry.upsert(db, [athlete("Q101095505", "cricket", "Ryan Shutte", {"espn_cricket": "38606"}, birth_date=date(1996, 2, 13), birth_precision=registry.DAY, teams=["Zimbabwe"])])
    db.commit()


def shutte_elsewhere(xid: str, name: str = "Ryan Shutte", born: str | None = "1996-02-13") -> PlayerRef:
    return PlayerRef("sportmonks_cricket", xid, name, ["1"], ["Zimbabwe"], born=born)


def cricket_sources(monkeypatch, search) -> None:
    """The cricket sources this build can run: ESPNcricinfo, which has Shutte's id (so is never searched), and a
    second whose own search answers as `search` does."""
    first = SimpleNamespace(system="espn_cricket", sports=("cricket",), search=lambda name, league=None: pytest.fail("searched a source for an id it was given"))
    second = SimpleNamespace(system="sportmonks_cricket", sports=("cricket",), search=search)
    monkeypatch.setattr(registry, "ALL", (first, second))


def test_removing_a_player_blocks_them_in_their_sports_other_sources(db, monkeypatch):
    """Removing Ryan Shutte blocked only his ESPNcricinfo id. The other cricket source lists him under its own, so
    Live now, a match page or a switch of cricket's source offered him again, and a tap made a new page (BUG-098)."""
    shutte(db)
    asked: list[str] = []
    cricket_sources(monkeypatch, lambda name, league=None: asked.append(name) or [shutte_elsewhere("9990002", born="1980-05-05"), shutte_elsewhere("9990001")])
    assert players.main(["remove", "5", "--yes"]) == 0
    blocked = registry.blocked(db)
    assert ("espn_cricket", "38606") in blocked and ("sportmonks_cricket", "9990001") in blocked
    assert ("sportmonks_cricket", "9990002") not in blocked  # a namesake, born another day
    assert asked == ["Ryan Shutte"]
    [o] = db.scalars(select(AthleteOverride)).all()
    assert (o.ids, o.birth_date) == ({"espn_cricket": "38606", "sportmonks_cricket": "9990001"}, date(1996, 2, 13))


def test_a_pick_with_a_removed_players_name_and_birth_date_is_refused(db, monkeypatch):
    """A source the removal couldn't ask (down then, or added since) has him under an id nobody blocked: a pick with
    his name and birth date is him all the same (BUG-098)."""
    shutte(db)

    def down(name, league=None):
        raise AdapterError("the second source didn't answer")

    cricket_sources(monkeypatch, down)
    assert players.main(["remove", "5", "--yes"]) == 0  # removed all the same
    assert ("sportmonks_cricket", "9990001") not in registry.blocked(db)
    assert registry.removed(db, "Cricket", shutte_elsewhere("9990001"))
    assert registry.removed(db, "Cricket", PlayerRef("espn_cricket", "38606", "R Shutte", [], []))  # by the id blocked, as before
    assert not registry.removed(db, "Cricket", shutte_elsewhere("9990002", born="1980-05-05"))  # a namesake
    assert not registry.removed(db, "Cricket", shutte_elsewhere("9990003", born=None))  # nothing to tell them apart by
    assert not registry.removed(db, "Cricket", shutte_elsewhere("9990004", name="Ryan Burl"))  # born the same day
    assert not registry.removed(db, "Chess", shutte_elsewhere("9990001"))
    # Restored, he can be followed again.
    assert players.main(["restore", "Q101095505"]) == 0
    assert not registry.removed(db, "Cricket", shutte_elsewhere("9990001"))


def test_a_removed_athletes_new_years_birthday_blocks_only_them(db, monkeypatch):
    """BUG-292: a removed athlete's 1 January birth date counted as the year alone, and a name inside theirs as
    theirs, so removing an Ahmed Hassan born on New Year's Day blocked every namesake born that year, and a
    Mohammad Ali the one-name Ali."""
    monkeypatch.setattr(registry, "ALL", ())  # no other source to ask
    registry.upsert(db, [athlete("Q1", "football", "Ahmed Hassan", birth_date=date(1993, 1, 1), birth_precision=registry.DAY),  # a real New Year's Day
                         athlete("Q2", "cricket", "Mohammad Ali", birth_date=date(1990, 1, 1), birth_precision=registry.YEAR)])  # the year alone
    db.commit()
    assert players.main(["remove", "Q1", "--yes"]) == 0 and players.main(["remove", "Q2", "--yes"]) == 0

    def pick(sport: str, name: str, born: str) -> bool:
        return registry.removed(db, sport, PlayerRef("sportmonks", f"x{name}{born}", name, [], [], born=born))

    assert pick("Football", "Ahmed Hassan", "1993-01-01") and pick("Football", "A. Hassan", "1993-01-01")  # him, as before
    assert not pick("Football", "Ahmed Hassan", "1993-11-02") and not pick("Football", "Ahmed Hassan Mahgoub", "1993-03-05")
    assert pick("Cricket", "Mohammad Ali", "1990-08-30") and pick("Cricket", "Ali Mohammad", "1990-08-30")  # known to the year: maybe him
    assert not pick("Cricket", "Ali", "1990-05-05") and not pick("Cricket", "Moeen Munir Ali", "1990-05-05")  # but only by his whole name


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


def test_unmatching_takes_the_persons_profile_off_the_card_at_once(db, monkeypatch):
    # BUG-255: unmatching took back the photo but left that person's About, birth date and honours on the card until
    # the next monthly profile read, though the command says it's off every card at once.
    from sports_follow import pipeline

    tenz(db)
    wrong = {"born": "1975-01-01", "nationality": "Someland", "honours": ["Someone's award (2001)"], "summary": "Someone else, a cyclist.",
             "about": {"source": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Someone"},
             "profile_from": {"born": "wikidata", "nationality": "wikidata", "honours": "wikidata", "summary": "wikipedia"}}
    card = db.get(PlayerCard, 3)
    assert card is not None
    card.card = {**card.card, "player": {**card.card["player"], **wrong}}
    db.commit()
    monkeypatch.setattr(pipeline, "session", players.session)
    published: dict[int, dict] = {}
    monkeypatch.setattr(bus, "store_card", published.__setitem__)
    assert players.main(["unmatch", "3"]) == 0
    db.expire_all()
    shown = db.get(PlayerCard, 3).card["player"]  # pyright: ignore[reportOptionalMemberAccess]
    assert not {"born", "nationality", "honours", "summary", "about", "photo"} & set(shown) and shown["profile_from"] == {}
    assert published[3]["player"] == shown and shown["name"] == "TenZ"


def test_unmatching_takes_the_persons_country_and_role_off_the_page_itself_too(db, monkeypatch):
    # BUG-297, BUG-255: the card lost the wrong person's country and role, but the page kept them: search showed the
    # role under the pro's name, and stories were matched to them by that country. The agent's own word stays.
    from sports_follow import pipeline

    tenz(db)
    page, card = db.get(Player, 3), db.get(PlayerCard, 3)
    assert page is not None and card is not None
    page.nationality, page.role = "Someland", "Duelist"
    card.card = {**card.card, "player": {**card.card["player"], "nationality": "Someland", "role": "Duelist", "profile_from": {"nationality": "wikidata"}}}
    db.commit()
    monkeypatch.setattr(pipeline, "session", players.session)
    monkeypatch.setattr(bus, "store_card", lambda *_: None)
    assert players.main(["unmatch", "3"]) == 0
    db.expire_all()
    page = db.get(Player, 3)
    assert page is not None and (page.nationality, page.role) == (None, "Duelist")


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
    fans = signed_in(db, 2)
    db.add(Player(id=7, slug="gukesh-d", name="Gukesh D", sport="Chess", wikidata_qid="Q10"))
    db.flush()
    db.add_all([PlayerCard(player_id=7, status="ready", card={}), PlayerIdentity(player_id=7, system=system, external_id="gukesh", confidence=1.0),
                *[Follow(fan_id=f, player_id=7, alert_rules={}) for f in (*fans, "cookie")]])
    db.commit()
    assert players.main(["starter", "add", "Q10"]) == 0
    [row] = starters.offer(db, fans[0])[0]["players"]
    assert (row["player_id"], row["following"], row["followers"]) == (7, True, 2)  # its signed-in fans, as a starter's counted (BUG-293)
    assert starters.offer(db, "someone new")[0]["players"][0]["following"] is False


def test_a_sport_thats_off_offers_no_starters(db, monkeypatch):
    chess_starters(db)
    assert players.main(["starter", "add", "Q10"]) == 0
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ENABLED if s.key != "chess"))
    assert starters.offer(db, "fan") == []


def test_a_turned_off_sports_registry_row_isnt_found_by_its_id_alone(db, monkeypatch):
    # BUG-097: a registry id with no sport took any sport's row, so a turned-off sport's athlete was previewed and
    # followed by it; and one in two sports was the better-known row's even with that sport off.
    from sports_follow import server

    registry.upsert(db, [athlete("Q5812", "tennis", "Novak Djokovic", {"espn_tennis": "296"}),
                         athlete("Q2", "cricket", "Yuzvendra Chahal", {"espn_cricket": "430246"}, sitelinks=5),
                         athlete("Q2", "chess", "Yuzvendra Chahal", {"lichess_chess": "yuzi"}, sitelinks=9)])
    db.commit()
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key not in ("tennis", "chess")))
    assert server._registry_athlete(db, {"qid": "Q5812"}) is None  # pyright: ignore[reportPrivateUsage]
    assert server._registry_athlete(db, {"qid": "Q5812", "sport": "tennis"}) is None  # pyright: ignore[reportPrivateUsage]
    chahal = server._registry_athlete(db, {"qid": "Q2"})  # pyright: ignore[reportPrivateUsage]
    assert chahal is not None and chahal.sport == "cricket"
    monkeypatch.setattr(sports, "ENABLED", sports.ALL)
    djokovic, chahal = server._registry_athlete(db, {"qid": "Q5812"}), server._registry_athlete(db, {"qid": "Q2"})  # pyright: ignore[reportPrivateUsage]
    assert djokovic is not None and djokovic.name == "Novak Djokovic" and chahal is not None and chahal.sport == "chess"


def test_a_turned_off_sports_page_isnt_followed_or_previewed_by_registry_id_or_by_name(db, monkeypatch):
    # BUG-097 (reopened): with no sport said, whoever held a registry id was followed and previewed, a turned-off sport's
    # page too; and a typed name found that page by its alias.
    import asyncio

    from sports_follow import pipeline, server

    class Cards(dict):
        async def get(self, key: str):
            return super().get(key)

        async def set(self, key: str, value: str, **kwargs) -> bool:
            self[key] = value
            return True

    @contextmanager
    def unit():
        s = sessionmaker(bind=db.get_bind(), expire_on_commit=False)()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    async def no_source(*args):
        return None

    for module in (server, pipeline):
        monkeypatch.setattr(module, "session", unit)
    monkeypatch.setitem(server.state, "redis", Cards())
    monkeypatch.setattr(server, "_source_lookup", no_source)
    monkeypatch.setattr(server, "_resolve", no_source)  # chess Chahal is in no live source here
    registry.upsert(db, [athlete("Q2", "cricket", "Yuzvendra Chahal", {"espn_cricket": "430246"}, sitelinks=5),
                         athlete("Q2", "chess", "Yuzvendra Chahal", {"lichess_chess": "yuzi"}, sitelinks=9)])
    db.add(Player(id=1, slug="yuzvendra-chahal", name="Yuzvendra Chahal", sport="Cricket", wikidata_qid="Q2"))
    db.flush()
    db.add_all([PlayerCard(player_id=1, status="ready", card={"player_id": 1, "player": {"name": "Yuzvendra Chahal", "sport": "Cricket"}}),
                PlayerIdentity(player_id=1, system="wikidata", external_id="Q2", confidence=1.0), PlayerAlias(alias="yuzvendra-chahal", player_id=1)])
    db.commit()
    request = SimpleNamespace(client=None)
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key != "cricket"))
    for body in ({"qid": "Q2"}, {"query": "Yuzvendra Chahal"}):
        with pytest.raises(server.HTTPException) as off:
            asyncio.run(server.follow(request, body, "fan"))  # pyright: ignore[reportArgumentType]
        assert off.value.status_code == 404
    assert "player_id" not in asyncio.run(server.registry_preview(request, "Q2", None, "fan"))  # pyright: ignore[reportArgumentType]: chess Chahal, as the registry has him
    db.expire_all()
    assert db.scalars(select(Follow.player_id)).all() == []
    # Cricket on: the page is theirs to follow either way, as before.
    monkeypatch.setattr(sports, "ENABLED", sports.ALL)
    assert asyncio.run(server.registry_preview(request, "Q2", None, "fan")) == {"player_id": 1}  # pyright: ignore[reportArgumentType]
    assert [asyncio.run(server.follow(request, body, "fan"))["player_id"] for body in ({"qid": "Q2"}, {"query": "Yuzvendra Chahal"})] == [1, 1]  # pyright: ignore[reportArgumentType]
    # BUG-328: a fan with all the players they may follow was refused even one they follow already (a page's Retry).
    monkeypatch.setattr("sports_follow.follows.MAX", 1)
    assert asyncio.run(server.follow(request, {"qid": "Q2", "sport": "cricket"}, "fan"))["player_id"] == 1  # pyright: ignore[reportArgumentType]


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


def signed_in(db, n: int, blocked: int = 0) -> list[str]:
    """n fans with accounts (the last `blocked` of them blocked by an admin): their fan ids."""
    ids = [f"acct-{i}" for i in range(n)]
    db.add_all([Account(email=f"{f}@example.com", fan_id=f, blocked_at=LONG_AGO if i >= n - blocked else None) for i, f in enumerate(ids)])
    db.flush()
    return ids


def test_starters_mix_whos_playing_the_most_followed_and_the_best_known(db, pools):
    system = chess_starters(db)
    fans = signed_in(db, 4)

    def page(pid: int, name: str, followers: int) -> None:
        db.add(Player(id=pid, slug=f"p{pid}", name=name, sport="Chess"))
        db.flush()
        db.add_all([PlayerCard(player_id=pid, status="ready", card={}), PlayerIdentity(player_id=pid, system=system, external_id=f"x{pid}", confidence=1.0),
                    *[Follow(fan_id=f, player_id=pid, alert_rules={}) for f in fans[:followers]]])

    page(1, "Live Larry", 4)
    page(2, "Soon Sam", 3)
    page(3, "Idle Ida", 2)
    db.commit()
    # The worker's live cards: one on now, one in two hours. The morning's top pool: one tomorrow, one within the hour.
    pools.cards = {1: {"live": {"is_live": True, "headline": "Round 3"}}, 2: {"upcoming": [{"title": "Round 4", "start_utc": (NOW + timedelta(hours=2)).isoformat()}]}}
    pools.top["chess"] = [{"qid": "Q10", "start_utc": (NOW + timedelta(hours=30)).isoformat(), "title": "Tata Steel", "competition": "Tata Steel"},
                          {"qid": "Q11", "start_utc": (NOW + timedelta(hours=1)).isoformat(), "title": "Norway Chess", "competition": "Norway Chess"}]
    [offer] = starters.offer(db, fans[0], NOW)
    # The pools take turns (playing, followed, top); someone in two keeps their first place, and playing now
    # outranks being followed: Soon Sam came in as followed and plays in two hours.
    assert [(r["name"], r["why"]) for r in offer["players"]] == [
        ("Live Larry", "live"), ("Gukesh D", "top"), ("R Praggnanandhaa", "soon"), ("Soon Sam", "soon"), ("Idle Ida", "followed")]
    assert [r["following"] for r in offer["players"]] == [True, False, False, True, True]  # the first fan follows every page
    assert offer["players"][1]["next"]["title"] == "Tata Steel" and offer["players"][3]["next"]["title"] == "Round 4"


def test_only_signed_in_fans_make_a_player_a_starter_and_only_a_page_a_live_source_has_is_offered(db, pools, monkeypatch):
    """Four cookies following a fourth-tier player put him first for every new fan, ahead of Messi and Salah,
    and a page no live source has (a namesake the agent wrote, or Messi before his source id was known) was
    offered though it shows no scores or alerts (BUG-085)."""
    from sports_follow import config

    system = registry.live_system("football")
    if system is None:
        pytest.skip("no live football source in this build")
    fans = signed_in(db, 4, blocked=1)

    def page(pid: int, name: str, followers: list[str], live: bool = True) -> None:
        db.add(Player(id=pid, slug=f"p{pid}", name=name, sport="Football"))
        db.flush()
        ident = PlayerIdentity(player_id=pid, system=system, external_id=f"x{pid}", confidence=1.0) if live else PlayerIdentity(player_id=pid, system="wikidata", external_id=f"Q{pid}", confidence=1.0)
        db.add_all([PlayerCard(player_id=pid, status="ready", card={}), ident, *[Follow(fan_id=f, player_id=pid, alert_rules={}) for f in followers]])

    page(29, "Tenzin Samdup", ["cookie-1", "cookie-2", "cookie-3", "cookie-4"])  # four anonymous cookies, one script
    page(28, "Lionel Messi", fans[:3], live=False)  # followed, but no live source has his page
    page(27, "Mohamed Salah", fans[:2])
    page(26, "Mohammed Sallah", [fans[0], fans[3]])  # one account, and one an admin blocked
    page(25, "Someone Real", [fans[1], "cookie-1"])  # one account: not yet enough
    db.commit()
    [offer] = starters.offer(db, "someone new", NOW)
    assert offer["key"] == "football"
    assert [(r["name"], r["why"], r["live_scores"]) for r in offer["players"]] == [("Mohamed Salah", "followed", True)]
    # With research on, a page with no live source is one the agent keeps up, and may be offered.
    monkeypatch.setattr(config, "RESEARCH", True)
    assert [r["name"] for r in starters.offer(db, "someone new", NOW)[0]["players"]] == ["Lionel Messi", "Mohamed Salah"]


def test_search_ranks_players_by_their_signed_in_fans_and_anyones_follows_only_break_a_tie(db):
    """Search put players on Sports Follow in order of every follow, a cookie's too, ahead of whether their name starts
    with what was typed, and said "N following" of them: three cookieless follows put Mohammed Sallah over Mohamed
    Salah for "moha" (BUG-293). Now signed-in fans an admin hasn't blocked count, as starters count them; anyone's
    follows only break a tie the rest leaves, so search on a young app, with few accounts, still ranks."""
    from sports_follow import search

    fans = signed_in(db, 2, blocked=1)

    def page(pid: int, name: str, followers: list[str], alias: str = "") -> None:
        db.add(Player(id=pid, slug=name.lower().replace(" ", "-"), name=name, sport="Football"))
        db.flush()
        db.add_all([PlayerCard(player_id=pid, status="ready", card={}), PlayerAlias(alias=alias or name.lower().replace(" ", "-"), player_id=pid),
                    *[Follow(fan_id=f, player_id=pid, alert_rules={}) for f in followers]])

    page(27, "Mohamed Salah", [fans[0], "cookie-1"])
    page(26, "Mohammed Sallah", [fans[1], *[f"cookie-{n}" for n in range(1, 7)]])  # an account an admin blocked, and a script's cookies
    page(25, "Mohan Kumar", ["cookie-1"])
    page(24, "The Egyptian King", [f"cookie-{n}" for n in range(1, 10)], alias="mohamed-the-king")  # found by another name
    db.commit()
    ranked = search.rank("moha", search.local(db, "moha", None, None), [])
    assert [(r.name, r.followers) for r in ranked] == [("Mohamed Salah", 1), ("Mohammed Sallah", 0), ("Mohan Kumar", 0), ("The Egyptian King", 0)]
    assert "follows" not in search.public(ranked[0])  # what breaks a tie isn't said


def test_search_counts_an_inbox_as_one_fan_as_starters_do(db):
    """BUG-296 with BUG-293: "N following", and the order search, For you and the Feed's sweeps rank by, counted
    accounts, and me+1@ and me+2@ are one inbox's two accounts, while starters counted inboxes. Followers are counted
    by inbox everywhere now (models.followers)."""
    from sports_follow import models, search

    emails = ["me+1@gmail.com", "m.e@googlemail.com", "me@gmail.com", "you@example.com", "them@example.com"]
    db.add_all([Account(email=e, fan_id=f"acct-{i}") for i, e in enumerate(emails)])
    db.flush()

    def page(pid: int, name: str, followers: list[str]) -> None:
        db.add(Player(id=pid, slug=name.lower().replace(" ", "-"), name=name, sport="Football"))
        db.flush()
        db.add_all([PlayerCard(player_id=pid, status="ready", card={}), PlayerAlias(alias=name.lower().replace(" ", "-"), player_id=pid),
                    *[Follow(fan_id=f, player_id=pid, alert_rules={}) for f in followers]])

    page(27, "Mohamed Salah", ["acct-0", "acct-1", "acct-2", "cookie-1"])  # one inbox's three accounts, and a cookie
    page(26, "Mohammed Sallah", ["acct-3", "acct-4"])  # two people
    db.commit()
    ranked = search.rank("moha", search.local(db, "moha", None, None), [])
    assert [(r.name, r.followers) for r in ranked] == [("Mohammed Sallah", 2), ("Mohamed Salah", 1)]
    assert {pid: (inboxes, follows) for pid, inboxes, follows in db.execute(models.followers(Follow.player_id, Follow.fan_id))} == {27: (1, 4), 26: (2, 2)}


def test_one_inbox_is_one_fan_however_many_accounts_its_plus_addresses_made(db, pools):
    """BUG-296: two signed-in fans following a page made it a starter for every new fan, and me+1@ and me+2@ reach one
    inbox: one person's two accounts were two fans. Followers are counted by inbox now (accounts.inbox_of)."""
    system = chess_starters(db)
    emails = ["me+1@gmail.com", "m.e@googlemail.com", "me+2@gmail.com", "you@example.com"]
    db.add_all([Account(email=e, fan_id=f"acct-{i}") for i, e in enumerate(emails)])
    db.flush()

    def page(pid: int, name: str, followers: list[int]) -> None:
        db.add(Player(id=pid, slug=f"p{pid}", name=name, sport="Chess"))
        db.flush()
        db.add_all([PlayerCard(player_id=pid, status="ready", card={}), PlayerIdentity(player_id=pid, system=system, external_id=f"x{pid}", confidence=1.0),
                    *[Follow(fan_id=f"acct-{i}", player_id=pid, alert_rules={}) for i in followers]])

    page(1, "One Inbox", [0, 1, 2])  # three accounts, one inbox
    page(2, "Two Fans", [2, 3])
    db.commit()
    [offer] = starters.offer(db, "someone new", NOW)
    assert [(r["name"], r["why"]) for r in offer["players"] if r["why"] == "followed"] == [("Two Fans", "followed")]


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
    asked.clear()
    starters.refresh(NOW)
    # Kept as the last refresh found them, and the rest still asked (BUG-172).
    assert [e["qid"] for e in pools.top["chess"]] == ["Q21", "Q20", "Q22"] and {"Q20", "Q22"} <= set(asked)

    def broken(ref):
        if ref == "Q20":
            raise KeyError("a field the source dropped")
        return games[ref]

    source.fixtures = broken
    starters.refresh(NOW)
    assert [e["qid"] for e in pools.top["chess"]] == ["Q21", "Q20", "Q22"]  # any error keeps one athlete as they were, not the sport

    refused: list[str] = []

    def spent(ref):
        refused.append(ref)
        raise RateLimited("allowance spent")

    source.fixtures = spent
    monkeypatch.setattr(starters, "LIMITED_PAUSE", 0)
    assert starters.refresh(NOW)["chess"] == 3  # those it didn't get to ask about stay as they were
    assert refused == ["Q21", "Q21"]  # waits once, then stops asking: every other call would be refused too


def three_playing(db, monkeypatch) -> tuple[SimpleNamespace, list[str]]:
    """Three chess players, best known first, whom the live source has with a game this afternoon, as it answers
    (`.fixtures`, which a test may change), and the athletes it was asked about, in order."""
    system = registry.live_system("chess")
    if system is None:
        pytest.skip("no live chess source in this build")
    registry.upsert(db, [athlete(f"Q3{i}", "chess", name, {system: name}, sitelinks=50 - i) for i, name in enumerate(("First", "Second", "Third"))])
    db.commit()
    game = Fixture(source_id="g", title="A game", competition="Cup", start_utc=NOW + timedelta(hours=5), venue=None, status="scheduled", team_ids=[], locator={})
    source = SimpleNamespace(fixtures=lambda ref: [game])
    asked: list[str] = []
    monkeypatch.setattr(registry, "find_in_source", lambda person: asked.append(person.qid) or (source, person.qid))
    return source, asked


def test_a_source_outage_leaves_the_top_pool_as_the_last_refresh_found_it(db, pools, monkeypatch):
    """The morning's refresh during a source outage stored what it found, nothing, so new fans were offered none of
    the sport's best known for a day (BUG-172). Whoever it can't look up stays as the last refresh found them, and
    whoever the source does answer for is as it says."""
    source, _ = three_playing(db, monkeypatch)
    assert starters.refresh(NOW)["chess"] == 3
    before = pools.top["chess"]

    def down(ref):
        raise AdapterError("source unreachable: ConnectTimeout")

    source.fixtures = down
    assert starters.refresh(NOW)["chess"] == 3
    assert pools.top["chess"] == before
    source.fixtures = lambda ref: [] if ref == "Q31" else down(ref)  # back for one, who has no game near any more
    starters.refresh(NOW)
    assert [e["qid"] for e in pools.top["chess"]] == ["Q30", "Q32"]


def test_a_refresh_the_worker_stops_during_ends_there_and_stores_nothing(db, pools, monkeypatch, stopping):
    """A deploy stopped the worker during the refresh: its thread asked on about each athlete while the interpreter
    shut down, every lookup failing, then stored the few it had found (BUG-172). It ends at its next step instead,
    and leaves the pool as it was."""
    source, asked = three_playing(db, monkeypatch)
    starters.refresh(NOW)
    before = pools.top["chess"]

    def stopped(ref):
        stopping.set()  # the worker's shutdown hook (worker.wind_down), while this athlete was looked up
        raise RuntimeError("cannot schedule new futures after interpreter shutdown")

    source.fixtures = stopped
    asked.clear()
    starters.refresh(NOW)
    assert asked == ["Q30"] and pools.top["chess"] is before

    def spent(ref):
        stopping.set()
        raise RateLimited("allowance spent")

    source.fixtures = spent
    stopping.clear()
    monkeypatch.setattr(starters, "LIMITED_PAUSE", 30)
    started = time.monotonic()
    starters.refresh(NOW)
    assert time.monotonic() - started < 5 and pools.top["chess"] is before  # no waiting out the minute's allowance


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
