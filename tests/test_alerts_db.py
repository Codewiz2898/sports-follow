"""The noise rules (alerts.py) down a moment's real path (notify.record, notify.send): what a fan hears when quiet hours
end. ON CONFLICT and the push log need Postgres itself, so these run against a throwaway database at alembic head and are
skipped without one (see tests/test_corrections_db.py for setting it up). Every test empties the tables first."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import bus, notify, structured, teams
from sports_follow.models import Event, Follow, Notification, Player, PushSubscription
from sports_follow.moments import Found

from .test_match_follow import Store

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL or "postgresql+psycopg://")
    with engine.begin() as c:
        c.execute(text("TRUNCATE player, event, event_state, event_player, player_line, follow, match_follow, moment, notification, notification_mute, push_subscription,"
                       " player_identity, player_card, team, team_follow, event_team RESTART IDENTITY CASCADE"))
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

    for module in (structured, notify, teams):
        monkeypatch.setattr(module, "session", unit)
    s = made()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def pushed(monkeypatch):
    """Polls applied at once, with Redis made up; every push's payload, by the fan it went to."""
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(bus, "sync_redis", lambda: Store())
    monkeypatch.setattr(notify, "dispatch", notify.send)  # at once, not in the background
    monkeypatch.setattr(notify, "push", lambda endpoint, p256dh, auth, payload, ttl, topic=None: sent.append((endpoint.rsplit("/", 1)[1], payload)))
    return sent


def device(db, fan: str, quiet: tuple[str, str] | None = None) -> None:
    db.add(PushSubscription(fan_id=fan, endpoint=f"https://fcm.googleapis.com/fcm/send/{fan}", p256dh="k", auth="a", timezone="UTC",
                            quiet_start=quiet[0] if quiet else None, quiet_end=quiet[1] if quiet else None))
    db.commit()


# ---------------------------------------------------------------- quiet hours and the hour's sound (BUG-242)


def always(now: datetime) -> tuple[str, str]:
    """Quiet hours that take in `now` (UTC): all day but its last minute, or but the one before it."""
    return ("00:00", "23:59") if now.strftime("%H:%M") < "23:59" else ("23:59", "23:58")


def striker(db, n: int) -> None:
    """Striker n, followed by "fan", in game n."""
    db.add(Player(id=n, slug=f"striker-{n}", name=f"Striker {n}", sport="Football"))
    db.add(Event(id=n, key=f"g{n}", sport="football", title=f"Game {n}", status="live"))
    db.flush()
    db.add(Follow(fan_id="fan", player_id=n, alert_rules={"level": "key"}))
    db.commit()


def test_pushes_that_arrived_silent_in_quiet_hours_leave_the_hours_sound_to_the_next(db, pushed):
    # Six goals in six games during quiet hours each arrive silent; the seventh, the minute quiet hours end, makes a
    # sound: the six never used the hour's LOUD_PER_HOUR, as none made one. They counted as loud, so the first hour after
    # quiet hours was silent except breakthroughs.
    now = datetime.now(timezone.utc)
    for n in range(1, 8):
        striker(db, n)
    device(db, "fan", quiet=always(now))

    def goal(n: int) -> list[int]:
        ids = notify.record(db, n, n, [Found("goal", "key", f"Goal: Striker {n}", "1–0 · 23'", "goal@1", 85)])
        db.commit()
        return ids

    for n in range(1, 7):
        notify.send(goal(n), now)
    assert [(p["loud"], p["quiet"]) for _, p in pushed] == [(True, True)] * 6  # loud moments, silent on a phone in quiet hours

    pushed.clear()
    db.execute(text("UPDATE push_subscription SET quiet_start = NULL, quiet_end = NULL"))  # quiet hours are over
    db.commit()
    notify.send(goal(7), now)
    [(_, payload)] = pushed
    assert (payload["title"], payload["loud"], payload["quiet"]) == ("Goal: Striker 7", True, False)
    assert db.scalars(select(Notification.loud).order_by(Notification.id)).all() == [False] * 6 + [True]  # the six made no sound


def test_a_push_counts_as_loud_where_one_of_the_fans_devices_made_a_sound(db, pushed):
    striker(db, 1)
    device(db, "fan", quiet=always(datetime.now(timezone.utc)))
    db.add(PushSubscription(fan_id="fan", endpoint="https://fcm.googleapis.com/fcm/send/fan-laptop", p256dh="k", auth="a"))  # no quiet hours
    db.commit()
    ids = notify.record(db, 1, 1, [Found("goal", "key", "Goal: Striker 1", "1–0 · 23'", "goal@1", 85)])
    db.commit()
    notify.send(ids)
    assert sorted(p["quiet"] for _, p in pushed) == [False, True]
    assert db.scalars(select(Notification.loud)).one() is True
