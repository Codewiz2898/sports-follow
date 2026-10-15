"""A goal VAR rules out, down a live poll's real path (structured.apply_poll, notify.record, notify.send):
the game's notification is corrected for the fans who were sent the goal, once, and the next real goal
is still told. ON CONFLICT and the push log need Postgres itself, so these run against a throwaway
database at alembic head and are skipped without one:

    docker compose exec postgres createdb -U sports sports_follow_test
    DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/alembic upgrade head
    SPORTS_FOLLOW_TEST_DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/python -m pytest tests/test_corrections_db.py

Every test empties the tables first.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import moments, notify, server, structured
from sports_follow.adapters.base import Line, Snapshot
from sports_follow.models import Event, Follow, Moment, NotificationMute, Player, PushSubscription

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

SALAH = "173896"


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE player, event, event_state, player_line, follow, moment, notification, notification_mute, push_subscription RESTART IDENTITY CASCADE"))
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

    monkeypatch.setattr(structured, "session", unit)
    monkeypatch.setattr(notify, "session", unit)
    s = made()
    s.add(Player(id=7, slug="mohamed-salah", name="Mohamed Salah", sport="Football"))
    s.add(Event(id=1, key="e1", sport="football", title="Egypt v South Africa", status="live"))
    s.commit()
    yield s
    s.close()
    engine.dispose()


def poll(home: int, clock: str, goals: int, plays: list[dict]) -> None:
    """One poll of the game, as the source has it: the score, Salah's line and his goals in its events."""
    state = {"kind": "football", "home": {"id": "1", "name": "Egypt", "score": str(home)}, "away": {"id": "2", "name": "South Africa", "score": "0"}}
    line = Line(headline=f"{goals} goal" if goals else "Started", stats=[{"label": "Goals", "value": str(goals)}])
    snap = Snapshot("live", f"Egypt {home} – 0 South Africa", clock, state, lines={SALAH: line}, moments=plays)
    structured.apply_poll(1, {"adapter": "espn_soccer", "cadence": 10}, SimpleNamespace(system="espn_soccer"), [(7, SALAH)], snap)


def goal(play_id: str, clock: str) -> dict:
    return {"id": play_id, "clock": clock, "kind": "GOAL", "athlete_ids": [SALAH], "team_id": "1", "own_goal": False}


def fan(db, name: str, level: str = "key") -> None:
    db.add(Follow(fan_id=name, player_id=7, alert_rules={"level": level}))
    db.add(PushSubscription(fan_id=name, endpoint=f"https://fcm.googleapis.com/fcm/send/{name}", p256dh="k", auth="a"))
    db.commit()


def test_a_goal_var_rules_out_corrects_the_games_notification(db, monkeypatch):
    pushed: list[tuple[str, str, str, str, bool]] = []
    monkeypatch.setattr(structured, "_polled", lambda event_id, cadence: True)  # polled all along
    monkeypatch.setattr(notify, "dispatch", notify.send)  # at once, not in the background
    monkeypatch.setattr(notify, "push", lambda endpoint, p256dh, auth, payload, ttl, topic=None: pushed.append((endpoint.rsplit("/", 1)[1], payload["tag"], payload["title"], payload["body"], payload["loud"])))
    monkeypatch.setattr(notify.bus, "publish", lambda *args: None)

    def heard() -> set[tuple[str, str, str, str, bool]]:
        out = set(pushed)
        pushed.clear()
        return out

    fan(db, "sent")
    fan(db, "muted")
    fan(db, "results", level="results")
    poll(0, "20'", 0, [])
    poll(1, "23'", 1, [goal("101", "23'")])
    goal_push = ("e1", "Goal: Mohamed Salah puts Egypt ahead", "Egypt 1 – 0 South Africa · 23'", True)
    assert heard() == {("sent", *goal_push), ("muted", *goal_push)}

    # Since the goal: one fan muted the game, another followed Salah. Then VAR rules it out.
    db.add(NotificationMute(fan_id="muted", event_id=1))
    db.commit()
    fan(db, "later")
    poll(0, "25'", 0, [])
    assert heard() == set()  # one poll could be an older copy of the game
    poll(0, "26'", 0, [])
    # The goal's notification (same tag) becomes the correction, without the goal under it; silent, as
    # the game made a sound a moment ago. Only the fan who was sent the goal and still wants the game hears.
    assert heard() == {("sent", "e1", "Goal ruled out: Mohamed Salah", "Egypt 0 – 0 South Africa · 26'", False)}
    [correction] = db.scalars(select(Moment).where(Moment.kind == "correction")).all()
    assert correction.corrects == db.scalar(select(Moment.id).where(Moment.kind == "goal"))
    # The activity list has it with the goal, and not for a fan who followed since.
    assert [m["title"] for m in server.activity(None, 50, "sent", db)["items"]] == ["Goal ruled out: Mohamed Salah", "Goal: Mohamed Salah puts Egypt ahead"]
    assert server.activity(None, 50, "later", db)["items"] == []

    poll(0, "27'", 0, [])
    poll(0, "30'", 0, [])
    assert heard() == set()  # corrected once

    poll(1, "70'", 1, [goal("102", "70'")])
    later_goal = ("e1", "Goal: Mohamed Salah puts Egypt ahead", "Egypt 1 – 0 South Africa · 70'\nGoal ruled out: Mohamed Salah", True)
    assert heard() == {("sent", *later_goal), ("later", "e1", "Goal: Mohamed Salah puts Egypt ahead", "Egypt 1 – 0 South Africa · 70'", True)}


def test_a_goal_taken_back_before_it_went_isnt_sent(db, monkeypatch):
    # A restart dropped the goal's batch; its correction went to nobody, as nobody had the goal. The
    # tick's sweep then finds the goal, still young enough to send: no push, and no toast of the goal.
    pushed, toasts = [], []
    monkeypatch.setattr(notify, "push", lambda *args, **kwargs: pushed.append(args))
    monkeypatch.setattr(notify.bus, "publish", lambda player_id, event: toasts.append(event["moment"]["title"]))
    fan(db, "sent")
    goal_id = notify.record(db, 7, 1, [moments.Found("goal", "key", "Goal: Mohamed Salah", "Egypt 1 – 0 South Africa · 23'", "goal@101")])[0]
    correction_id = notify.record(db, 7, 1, [moments.Found("correction", "key", "Goal ruled out: Mohamed Salah", "Egypt 0 – 0 South Africa · 26'", "goal:goal@101")])[0]
    db.commit()
    assert notify.send([correction_id]) == 0
    assert notify.send([goal_id]) == 0
    assert pushed == [] and "Goal: Mohamed Salah" not in toasts
