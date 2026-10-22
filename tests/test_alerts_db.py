"""The noise rules (alerts.py) down a moment's real path (notify.record, notify.send): what a fan hears when quiet hours
end and from two followed teammates at once, and how a device's health is counted. ON CONFLICT and the push log need Postgres itself, so these run against a throwaway database at alembic head and are
skipped without one (see tests/test_corrections_db.py for setting it up). Every test empties the tables first."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import bus, notify, sports, structured, teams
from sports_follow.adapters.base import Line, Snapshot
from sports_follow.models import Event, EventPlayer, Follow, MatchFollow, Notification, Player, PushSubscription
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


# ---------------------------------------------------------------- a device's health (BUG-187)


def test_two_batches_failing_on_one_device_at_once_both_count(db, monkeypatch):
    # Each batch writes back what its pushes' services said: two at once, each read the device's failures as 0 and wrote
    # 1, so a device that kept failing was never removed. A thread stands for each batch, the second writing back while
    # the first's isn't committed.
    device(db, "fan")
    device_id = db.scalars(select(PushSubscription.id)).one()
    engine = db.get_bind()
    made = sessionmaker(bind=engine)
    first_wrote, go_on = threading.Event(), threading.Event()

    def first() -> None:
        with made() as s:
            notify._health(s, device_id, 500)
            first_wrote.set()
            go_on.wait(5)
            s.commit()

    def second() -> None:
        first_wrote.wait(5)
        with made() as s:
            notify._health(s, device_id, 500)  # waits for the first's commit, where the count is added to
            s.commit()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    time.sleep(0.5)
    go_on.set()
    for t in threads:
        t.join(10)
    db.expire_all()
    assert db.scalars(select(PushSubscription.failures)).one() == 2


def test_a_device_the_push_service_is_throttling_isnt_counted_as_failing(db):
    # FCM answers 429 for a device sent more than its quota (a busy fan's goals on a Saturday afternoon), as a push
    # service may: not the device's fault, so five of them don't remove it, as a failing device's do.
    device(db, "fan")
    device_id = db.scalars(select(PushSubscription.id)).one()
    for _ in range(notify.MAX_FAILURES):
        notify._health(db, device_id, 429)
    db.commit()
    assert db.scalars(select(PushSubscription.failures)).one() == 0
    for _ in range(notify.MAX_FAILURES):
        notify._health(db, device_id, 500)
    db.commit()
    assert db.scalars(select(PushSubscription.id)).all() == []  # one that keeps failing is removed


# ---------------------------------------------------------------- teammates (BUG-206)


def test_a_fan_of_two_teammates_hears_their_teams_result_once_naming_both(db, pushed):
    # Their team's result is found for each of them (Sport.team_moments): one push, but its lines were the same fact
    # twice, "zekken: Sentinels win 2–0 | TenZ: Sentinels win 2–0".
    db.add_all([Player(id=3, slug="tenz", name="TenZ", sport="Valorant"), Player(id=4, slug="zekken", name="zekken", sport="Valorant"),
                Event(id=1, key="pandascore_esports:777", sport="Valorant", title="Sentinels vs NRG", status="final")])
    db.flush()
    db.add_all([Follow(fan_id="fan", player_id=3, alert_rules={"level": "key"}), Follow(fan_id="fan", player_id=4, alert_rules={"level": "key"}),
                Follow(fan_id="tenz-fan", player_id=3, alert_rules={"level": "key"})])
    db.commit()
    device(db, "fan")
    device(db, "tenz-fan")
    ids = [m for pid, who in ((3, "TenZ"), (4, "zekken")) for m in notify.record(db, pid, 1, [Found("final", "key", f"{who}: Sentinels win 2–0", "v NRG · Upper bracket final")])]
    db.commit()
    notify.send(ids)
    heard = {fan: (p["title"], p["body"]) for fan, p in pushed}
    assert heard == {"fan": ("TenZ and zekken: Sentinels win 2–0", "v NRG · Upper bracket final"), "tenz-fan": ("TenZ: Sentinels win 2–0", "v NRG · Upper bracket final")}
    lines = {fan: lines for fan, lines in db.execute(select(Notification.fan_id, Notification.lines))}
    assert lines == {"fan": ["TenZ and zekken: Sentinels win 2–0"], "tenz-fan": ["TenZ: Sentinels win 2–0"]}


# ---------------------------------------------------------------- a game long over when polling comes back (BUG-259)

SALAH = "173896"


def football(event_id: int, status: str, home: int, away: int, clock: str, goals: int = 0) -> None:
    """One poll of an Egypt game with Salah in it, as the source has it, through the poller's real path."""
    state = {"kind": "football", "home": {"id": "1", "name": "Egypt", "score": str(home)}, "away": {"id": "2", "name": "Ghana", "score": str(away)}}
    line = Line(headline=f"{goals} goal" if goals else "Started", stats=[{"label": "Goals", "value": str(goals)}])
    snap = Snapshot(status, f"Egypt {home} – {away} Ghana", clock, state, lines={SALAH: line})
    structured.apply_poll(event_id, {"adapter": "espn_soccer", "cadence": 10}, SimpleNamespace(system="espn_soccer", credit=lambda snap: {"name": "ESPN"}), [(7, SALAH)], snap)


def test_a_game_found_over_days_after_it_started_isnt_told_as_news(db, pushed, monkeypatch):
    # Polling comes back to a game two days after it started (its sport turned back on, a worker down that long): the
    # first look finds it over, and told its result as new. One that started two hours ago is still news.
    now = datetime.now(timezone.utc)
    db.add(Player(id=7, slug="mohamed-salah", name="Mohamed Salah", sport="Football"))
    db.add_all([Event(id=1, key="espn_soccer:1", sport="Football", title="Egypt v Ghana", status="live", start_utc=now - timedelta(days=2)),
                Event(id=2, key="espn_soccer:2", sport="Football", title="Egypt v Ghana", status="live", start_utc=now - timedelta(hours=2))])
    db.flush()
    db.add_all([EventPlayer(event_id=1, player_id=7), EventPlayer(event_id=2, player_id=7), Follow(fan_id="fan", player_id=7, alert_rules={"level": "key"}),
                MatchFollow(fan_id="game-fan", event_id=1, alert_rules={"level": "key"}, keys=[]), MatchFollow(fan_id="game-fan", event_id=2, alert_rules={"level": "key"}, keys=[])])
    db.commit()
    device(db, "fan")
    device(db, "game-fan")
    polled = SimpleNamespace(recent=True)
    monkeypatch.setattr(structured, "_polled", lambda event_id, cadence: polled.recent)
    for event_id in (1, 2):
        football(event_id, "live", 0, 0, "10'")
    assert pushed == []
    polled.recent = False  # no polls since: the sport was off, the worker down
    football(1, "final", 1, 2, "FT", goals=1)
    assert pushed == []  # two days old
    football(2, "final", 1, 2, "FT", goals=1)
    assert sorted((fan, p["title"]) for fan, p in pushed) == [("fan", "Mohamed Salah: Egypt 1 – 2 Ghana"), ("game-fan", "Egypt v Ghana: Ghana won")]


@pytest.mark.parametrize("sport, board, days, old", [
    ("football", {"kind": "football"}, 1.5, True),
    ("football", {"kind": "football"}, 0.9, False),  # it may have run late, and a result keeps half a day
    ("cricket", {"kind": "cricket", "format": "T20I"}, 3, True),  # a reserve day, and a day more
    ("cricket", {"kind": "cricket", "format": "T20I"}, 2, False),
    ("cricket", {"kind": "cricket", "format": "Test"}, 5, False),  # a Test runs five days
    ("cricket", {"kind": "cricket", "format": "Test"}, 7, True),
    ("cricket", {"kind": "cricket"}, 5, False),  # no word for its length: as long as a Test
])
def test_how_long_after_its_start_a_game_found_over_is_old_news(sport, board, days, old):
    now = datetime.now(timezone.utc)
    game = SimpleNamespace(start_utc=now - timedelta(days=days))
    over = Snapshot("final", "", "", board)
    assert structured.long_over(sports.of(sport), game, over, now) is old  # type: ignore[arg-type]
    assert structured.long_over(sports.of(sport), game, Snapshot("live", "", "", board), now) is False  # one still on is news
    assert structured.long_over(sports.of(sport), SimpleNamespace(start_utc=None), over, now) is False  # type: ignore[arg-type]  # no start: can't say
