"""When a game was over (structured.found_over, BUG-319), against a throwaway database at alembic head: its first final
state since it was last seen on here, no later than its sport says a game can end, so a Test is a result of the day it
ended and a replay of the replay's end; a game first seen here over, or seen over long after it was played, says
nothing of when it ended (it goes by its start). A poll that finds a game over says so on its fans' cards. Skipped
without one; see tests/test_players.py for setting it up. Sources and Redis are made up here."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from sports_follow import bus, notify, structured
from sports_follow.adapters import Snapshot
from sports_follow.models import Event, EventPlayer, EventState, Player, PlayerCard

from .test_match_follow import Store

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

START = datetime(2026, 11, 17, 4, tzinfo=timezone.utc)  # a Test's first morning in India
ENDED = datetime(2026, 11, 21, 10, 30, tzinfo=timezone.utc)  # won on its fifth afternoon
TEST = {"kind": "cricket", "format": "Test"}  # no set length: over within six days of its start


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL or "postgresql+psycopg://")
    with engine.begin() as c:
        c.execute(text("TRUNCATE player, event, event_state, event_player, player_line, player_card, follow, match_follow, moment, notification RESTART IDENTITY CASCADE"))
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

    for module in (structured, notify):
        monkeypatch.setattr(module, "session", unit)
    s = made()
    yield s
    s.close()
    engine.dispose()


def event(db, event_id: int, start: datetime, *states: tuple[str, datetime]) -> None:
    db.add(Event(id=event_id, key=f"ground:{event_id}", sport="cricket", title="India v South Africa", notes="2nd Test", status="final", start_utc=start))
    db.flush()
    db.add_all([EventState(event_id=event_id, as_of=at, status=status, state=TEST) for status, at in states])
    db.commit()


def test_a_game_was_over_when_first_found_over_since_it_was_last_on(db):
    hours = lambda n: START + timedelta(hours=n)  # noqa: E731
    # A Test: on for five days, over on the fifth (its summary a poll later is the same game over).
    event(db, 1, START, ("live", START), ("live", hours(80)), ("final", ENDED), ("final", ENDED + timedelta(minutes=5)))
    # Over, on again (a super over), over.
    event(db, 2, START, ("live", START), ("final", hours(4)), ("live", hours(5)), ("final", hours(6)))
    # Called off after it seemed over, and replayed two days on under its fixture: the replay's end, not the first's.
    event(db, 3, hours(48), ("live", START), ("final", hours(2)), ("postponed", hours(3)), ("live", hours(48)), ("final", hours(56)))
    # Replayed, its end not seen yet: the game called off's says nothing of it.
    event(db, 4, hours(48), ("live", START), ("final", hours(2)), ("postponed", hours(3)))
    # First seen here over: a new follower's first read a day after the end, a history read of last season's game.
    event(db, 5, START, ("final", ENDED + timedelta(days=1)))
    event(db, 6, START - timedelta(days=200), ("final", START))
    # Seen to come, then over long after it could have ended (a worker down for days).
    event(db, 7, START - timedelta(days=10), ("scheduled", START - timedelta(days=11)), ("final", START))
    # On still.
    event(db, 8, START, ("live", START))
    assert structured.found_over(db, [1, 2, 3, 4, 5, 6, 7, 8, 9]) == {1: ENDED, 2: hours(6), 3: hours(56)}
    assert structured.found_over(db, []) == {}


class Ground:
    """A made-up cricket source: its polls are handed over as they are."""

    system = "ground"
    live_note = lag_note = None

    def credit(self, snap):
        return "Ground"


def test_a_poll_that_finds_a_test_over_dates_its_fans_result_by_its_end(db, monkeypatch):
    # BUG-319: the 2nd Test, India v South Africa, from 17 to 21 November: Bumrah's result says it was over on the 21st.
    redis = Store()
    monkeypatch.setattr(bus, "sync_redis", lambda: redis)
    monkeypatch.setattr(structured, "_polled", lambda event_id, cadence: True)
    monkeypatch.setattr(notify, "dispatch", lambda moment_ids: None)
    db.add(Player(id=1, slug="jasprit-bumrah", name="Jasprit Bumrah", sport="Cricket"))
    db.add(PlayerCard(player_id=1, status="ready", version=1, is_live=False, card={"live": {"is_live": False, "player_stats": []}, "upcoming": [], "recent_results": [], "freshness": {}}))
    event(db, 1, START)
    db.add(EventPlayer(event_id=1, player_id=1))
    db.commit()
    binding = {"adapter": "ground", "locator": {"id": 1}, "cadence": 10}
    for at, status, clock in ((START + timedelta(hours=1), "live", "Day 1 · Session 1"), (ENDED, "final", "India won by 8 wickets")):
        monkeypatch.setattr(structured, "_now", lambda at=at: at)
        structured.apply_poll(1, binding, Ground(), [(1, "93")], Snapshot(status=status, score_label="IND 410 & 120/2", clock_label=clock, state=TEST))
    db.expire_all()
    row = db.get(PlayerCard, 1)
    assert row is not None
    result = row.card["recent_results"][0]
    assert (result["start_utc"], result["ended_utc"]) == (START.isoformat(), ENDED.isoformat())
