"""What fansports.py does to real rows (BHA-653): a fan's sports saved in their order, a sport turned off kept out of
sight and back in its place when it's on again. ON CONFLICT and advisory locks need Postgres itself, so these run
against a throwaway database at alembic head and are skipped without one (tests/test_accounts_db.py says how).

Every test empties the table first. The sports the app offers here are the ones whose sources run without a key:
football, cricket, basketball, tennis and chess; no esports game.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import sessionmaker

from sports_follow import controls, fansports
from sports_follow.models import FanSport

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")


@pytest.fixture
def db():
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE fan_sport RESTART IDENTITY"))
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()
    engine.dispose()


def save(db, fan, keys):
    saved = fansports.replace(db, fan, keys)
    db.commit()
    return saved


def kept(db, fan):
    return list(db.scalars(select(FanSport.sport).where(FanSport.fan_id == fan).order_by(FanSport.position)))


def test_the_fans_sports_are_kept_in_their_order_once_each(db):
    assert fansports.mine(db, "fan") == []
    assert save(db, "fan", ["chess", "cricket", "chess", "football"]) == ["chess", "cricket", "football"]
    assert fansports.mine(db, "fan") == ["chess", "cricket", "football"]
    assert save(db, "fan", ["football", "chess"]) == ["football", "chess"]  # cricket dropped, the order changed
    assert fansports.mine(db, "fan") == ["football", "chess"] and kept(db, "fan") == ["football", "chess"]
    assert fansports.mine(db, "someone-else") == []  # each fan's own
    assert save(db, "fan", []) == [] and kept(db, "fan") == []


def test_a_sport_the_app_doesnt_offer_or_knows_nothing_of_isnt_taken(db):
    """Only GET /api/sports' `on` sports: valorant runs nowhere here (no source has a key), golf isn't a sport here."""
    assert "valorant" not in fansports.offered()
    assert save(db, "fan", ["golf", "valorant", "cricket", "esports", ""]) == ["cricket"]
    assert kept(db, "fan") == ["cricket"]


def test_a_sport_turned_off_is_out_of_sight_and_back_in_its_place_when_its_on(db):
    """Bharath's choice 2 (22 Oct): hidden while it's off, as a followed player of it is, not dropped from the list."""
    save(db, "fan", ["football", "cricket", "chess"])
    controls.turn_sport("cricket", False)
    assert "cricket" not in fansports.offered()
    assert fansports.mine(db, "fan") == ["football", "chess"]
    # The fan's Edit, while it's off, saves only the sports it shows: the hidden one stays, after them.
    assert save(db, "fan", ["chess", "tennis"]) == ["chess", "tennis"]
    assert kept(db, "fan") == ["chess", "tennis", "cricket"]
    controls.turn_sport("cricket", True)
    assert fansports.mine(db, "fan") == ["chess", "tennis", "cricket"]


def test_a_sport_kept_keeps_the_date_it_was_first_picked(db):
    save(db, "fan", ["football"])
    first = datetime.now(timezone.utc) - timedelta(days=30)
    db.execute(update(FanSport).values(created_at=first))
    db.commit()
    save(db, "fan", ["chess", "football"])
    dates = {s.sport: s.created_at for s in db.scalars(select(FanSport))}
    assert dates["football"] == first and dates["chess"] > first


def test_a_sport_whose_module_is_gone_goes_at_the_next_save(db):
    """Removing a sport is its module and its line in sports.ALL (CLAUDE.md): a row of it is never shown, and goes."""
    db.add_all([FanSport(fan_id="fan", sport="quidditch", position=0), FanSport(fan_id="fan", sport="chess", position=1)])
    db.commit()
    assert fansports.mine(db, "fan") == ["chess"]
    save(db, "fan", ["chess", "football"])
    assert kept(db, "fan") == ["chess", "football"]
