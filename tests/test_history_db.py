"""The history lane (history.py) and a player's refresh (structured.refresh) against a throwaway database at alembic
head: what they read, and whether the lane has work, is only of sports that run and sources that run. Skipped
without one; see tests/test_players.py for setting it up. Sources and Redis are made up here."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from sports_follow import bus, controls, history, structured
from sports_follow.adapters import ADAPTERS, Snapshot
from sports_follow.models import Event, EventPlayer, Follow, Player, SourceBinding

from .test_history import HashRedis

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime.now(timezone.utc)


class Arena:
    """A source of several esports games, as PandaScore is: it runs while any of them does. Every request is counted."""

    system = "arena"
    sports = ("cs2", "valorant")
    live_cadence = 10

    def __init__(self):
        self.asked: list[tuple[str, str]] = []

    def spare(self):
        return True

    def history_pages(self, ref, anchor):
        self.asked.append(("pages", ref.athlete_id))
        return [{"n": 0}]

    def history(self, ref, page):
        self.asked.append(("page", ref.athlete_id))
        return []

    def snapshot(self, locator, final=False):
        self.asked.append(("scorecard", locator["id"].split("/")[0]))  # whose game it is
        return Snapshot(status="final", score_label="2 – 1", clock_label="FT", state={})


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL or "")
    with engine.begin() as c:
        c.execute(text("TRUNCATE player, follow, event RESTART IDENTITY CASCADE"))
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

    monkeypatch.setattr(history, "session", unit)
    monkeypatch.setattr(bus, "_sync", HashRedis())
    s = made()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def arena(monkeypatch):
    source = Arena()
    monkeypatch.setitem(ADAPTERS, source.system, source)
    read: list[int] = []

    def add_scorecard(db_, event, player_id, athlete_id, snap):
        read.append(event.id)
        row = db_.get(EventPlayer, (event.id, player_id))
        row.result = {**row.result, "scorecard": True}

    monkeypatch.setattr(structured, "add_scorecard", add_scorecard)
    source.read = read  # type: ignore[attr-defined]
    return source


def player(db, pid: int, sport: str, adapter: str = "arena", history_state: dict | None = None) -> None:
    db.add(Player(id=pid, slug=f"p{pid}", name=f"P{pid}", sport=sport))
    db.flush()
    db.add_all([Follow(fan_id="fan", player_id=pid, alert_rules={}),
                SourceBinding(player_id=pid, purpose="fixtures", adapter=adapter, priority=0, history=history_state,
                              locator={"system": adapter, "athlete_id": f"A{pid}", "name": f"P{pid}", "team_ids": [], "team_names": []})])
    db.flush()


def result(db, pid: int, sport: str, days_ago: float, adapter: str = "arena") -> int:
    """A finished game of this player's, its scorecard still to read."""
    event = Event(key=f"{adapter}:{pid}-{days_ago}", sport=sport, title=f"G {pid} {days_ago}", status="final", start_utc=NOW - timedelta(days=days_ago),
                  live_binding={"adapter": adapter, "locator": {"id": f"A{pid}/{days_ago}"}})
    db.add(event)
    db.flush()
    db.add(EventPlayer(event_id=event.id, player_id=pid, result={"title": event.title, "result": "Won", "scorecard": False}))
    db.flush()
    return event.id


def turn_off(store, source: Arena, *keys: str) -> None:
    """These sports off in the admin panel (none: all on again), the made-up source still running."""
    store.rows[controls.SPORTS_OFF] = list(keys)
    controls.load()
    ADAPTERS[source.system] = source  # a change rebuilds the running sources, without the made-up one


def test_a_sport_turned_off_isnt_read_by_the_history_lane_though_its_source_runs(db, arena, admin_controls):
    # BUG-176: CS2 turned off in the admin panel, its source still running for Valorant: the lane read CS2 players'
    # older pages and scorecards from it (a paid allowance).
    player(db, 1, "CS2")
    player(db, 2, "Valorant")
    cs2_game = result(db, 1, "CS2", 1)
    valorant_game = result(db, 2, "Valorant", 2)
    db.commit()
    turn_off(admin_controls, arena, "cs2")

    history.run()
    history.run()
    assert arena.read == [valorant_game] and cs2_game not in arena.read  # type: ignore[attr-defined]
    assert {who for _, who in arena.asked} == {"A2"}  # nothing of the CS2 player's, pages or scorecards
    assert not history.has_work()  # and nothing waits that the lane may do: no idle run queued every minute

    # On again: the CS2 player's history is read as before.
    turn_off(admin_controls, arena)
    assert history.has_work()
    history.run()
    assert cs2_game in arena.read  # type: ignore[attr-defined]


def test_games_of_a_source_not_running_never_crowd_out_those_that_can_be_read(db, arena, monkeypatch):
    # BUG-179: the lane's window was the newest READS*5 scorecards waiting, whatever their source: once that many were
    # a source's that isn't running (a source switched off, or one a player moved off), no other was ever read, and the
    # lane was queued every minute to read nothing.
    monkeypatch.setattr(history, "READS", 2)  # a window of 10
    player(db, 1, "CS2", history_state={"done": True})
    gone = [result(db, 1, "CS2", 1 + i / 100, adapter="gone") for i in range(12)]  # the newest: a source that's off
    older = result(db, 1, "CS2", 30)
    db.commit()

    history.run()
    assert arena.read == [older]  # type: ignore[attr-defined]
    assert not history.has_work()
    assert all(db.get(EventPlayer, (e, 1)).result["scorecard"] is False for e in gone)  # left for when that source runs again


def test_a_refresh_of_a_player_whose_sport_is_turned_off_reads_nothing(db, arena, admin_controls, monkeypatch):
    # BUG-176: a refresh queued before the sport was turned off, or by a follow made by id since, read its source.
    monkeypatch.setattr(structured, "session", history.session)
    refreshed: list[int] = []
    monkeypatch.setattr(structured, "_refresh", refreshed.append)
    player(db, 1, "CS2")
    player(db, 2, "Valorant")
    db.commit()
    turn_off(admin_controls, arena, "cs2")
    structured.refresh(1)
    structured.refresh(2)
    assert refreshed == [2]
    assert not bus.is_locked("structured:1")
