"""The noise rules (alerts.py) down a moment's real path (structured.apply_poll, notify.record, notify.send): what a fan
hears when quiet hours end, from two followed teammates at once, from a game long over when polling comes back to it, and
from a game called off and played again under its fixture; and how a device's health is counted. ON CONFLICT and the
push log need Postgres itself, so these run against a throwaway database at alembic head and are skipped without one
(see tests/test_corrections_db.py for setting it up). Every test empties the tables first."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text, update
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
    redis = Store()
    monkeypatch.setattr(bus, "sync_redis", lambda: redis)
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
    assert db.scalars(select(PushSubscription.failures)).one() == notify.MAX_FAILURES  # these count
    db.execute(update(PushSubscription).values(failing_since=datetime.now(timezone.utc) - notify.FAILING_FOR - timedelta(minutes=1)))
    notify._health(db, device_id, 500)
    db.commit()
    assert db.scalars(select(PushSubscription.id)).all() == []  # one that keeps failing is removed


TIMED_OUT = "HTTPSConnectionPool(host='fcm.googleapis.com', port=443): Read timed out. (read timeout=5)"


def test_a_push_service_outage_doesnt_remove_the_devices_it_failed(db, pushed, monkeypatch):
    # FCM answering 503 and a Web Push service not answering in time are the push services' trouble, which FCM says
    # to wait out: five moments of it, a few minutes on a match day, removed every fan's phone and browser (BUG-298).
    # Their failures are still counted (their pushes go by the threads for devices in doubt), not held against them.
    db.add(PushSubscription(fan_id="fan", transport="fcm", endpoint="phone-" + "x" * 150))
    device(db, "fan")
    monkeypatch.setattr(notify.fcm, "enabled", lambda: True)
    monkeypatch.setattr(notify.fcm, "push", lambda token, payload, ttl: 503)
    monkeypatch.setattr(notify, "push", lambda *a, **k: TIMED_OUT)
    for n in range(1, 2 * notify.MAX_FAILURES + 1):
        striker(db, n)
        ids = notify.record(db, n, n, [Found("goal", "key", f"Goal: Striker {n}", "1–0 · 23'", f"goal@{n}", 85)])
        db.commit()
        notify.send(ids)
    db.expire_all()
    assert sorted(db.execute(select(PushSubscription.transport, PushSubscription.failures)).all()) == [("fcm", 10), ("webpush", 10)]
    assert notify._proven(set(db.scalars(select(PushSubscription.id)))) == set()
    # Back up: a push that goes through clears the count.
    monkeypatch.setattr(notify.fcm, "push", lambda token, payload, ttl: None)
    monkeypatch.setattr(notify, "push", lambda *a, **k: None)
    ids = notify.record(db, 1, 1, [Found("goal", "key", "Goal: Striker 1 again", "2–0 · 60'", "goal@1b", 85)])
    db.commit()
    notify.send(ids)
    db.expire_all()
    assert db.scalars(select(PushSubscription.failures)).all() == [0, 0]


@pytest.mark.parametrize("error", [500, 503, 400, TIMED_OUT])
def test_a_device_is_removed_for_failing_only_once_its_failed_every_push_for_a_day(db, error):
    device(db, "fan")
    device_id = db.scalars(select(PushSubscription.id)).one()
    for _ in range(3 * notify.MAX_FAILURES):  # an outage of an afternoon
        notify._health(db, device_id, error)
    db.commit()
    started = db.scalars(select(PushSubscription.failing_since)).one()
    assert started is not None and datetime.now(timezone.utc) - started < timedelta(minutes=1)
    # Still failing past the day: it's the device, not the push service, and the next failure takes it out.
    db.execute(update(PushSubscription).values(failing_since=datetime.now(timezone.utc) - notify.FAILING_FOR - timedelta(minutes=1)))
    notify._health(db, device_id, error)
    db.commit()
    assert db.scalars(select(PushSubscription.id)).all() == []
    # A day of failures needs MAX_FAILURES of them, though: two a week apart (a player who plays weekly) aren't.
    device(db, "fan")
    device_id = db.scalars(select(PushSubscription.id)).one()
    notify._health(db, device_id, error)
    db.execute(update(PushSubscription).values(failing_since=datetime.now(timezone.utc) - timedelta(days=7)))
    notify._health(db, device_id, error)
    db.commit()
    assert db.scalars(select(PushSubscription.failures)).one() == 2
    # A push that goes through starts the clock again.
    notify._health(db, device_id, None)
    notify._health(db, device_id, error)
    db.commit()
    started = db.scalars(select(PushSubscription.failing_since)).one()
    assert started is not None and datetime.now(timezone.utc) - started < timedelta(minutes=1)


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


# ---------------------------------------------------------------- a game called off and played again (BUG-185)


def test_a_game_called_off_and_played_again_under_its_fixture_is_told_afresh(db, pushed, monkeypatch):
    # A match abandoned at 30' with Salah's goal told, set again for a fortnight on under the same fixture: its replay's
    # line-up and start went untold (their keys were the first game's), its first yellow card too, and a goal of the game
    # called off was "ruled out" as the replay began, as Salah's line had none.
    now = datetime.now(timezone.utc)
    db.add(Player(id=7, slug="mohamed-salah", name="Mohamed Salah", sport="Football"))
    db.add(Event(id=1, key="espn_soccer:1", sport="Football", title="Egypt v Ghana", status="armed", start_utc=now))
    db.flush()
    db.add_all([EventPlayer(event_id=1, player_id=7), Follow(fan_id="fan", player_id=7, alert_rules={"level": "everything"})])
    db.commit()
    device(db, "fan")
    polled = SimpleNamespace(recent=True)
    monkeypatch.setattr(structured, "_polled", lambda event_id, cadence: polled.recent)

    def poll(status: str, home: int, clock: str, goals: int = 0, yellow: int = 0, plays: list[dict] | None = None) -> list[str]:
        state = {"kind": "football", "home": {"id": "1", "name": "Egypt", "score": str(home)}, "away": {"id": "2", "name": "Ghana", "score": "0"}}
        line = Line(headline=f"{goals} goal" if goals else "Started", stats=[{"label": "Goals", "value": str(goals)}, {"label": "Yellow", "value": str(yellow)}])
        snap = Snapshot(status, f"Egypt {home} – 0 Ghana", clock, state, lines={} if status == "scheduled" else {SALAH: line}, moments=plays or [],
                        squad={SALAH: "starting"})
        structured.apply_poll(1, {"adapter": "espn_soccer", "cadence": 10}, SimpleNamespace(system="espn_soccer", credit=lambda snap: {"name": "ESPN"}), [(7, SALAH)], snap)
        titles = [p["title"] for _, p in pushed]
        pushed.clear()
        return titles

    goal = {"id": "101", "clock": "23'", "kind": "GOAL", "athlete_ids": [SALAH], "team_id": "1", "own_goal": False}
    assert poll("scheduled", 0, "") == ["Mohamed Salah starts"]
    assert poll("live", 0, "1'") == ["Mohamed Salah's game has started"]
    assert poll("live", 1, "23'", goals=1, plays=[goal]) == ["Goal: Mohamed Salah puts Egypt ahead"]
    assert poll("live", 1, "28'", goals=1, yellow=1, plays=[goal]) == ["Yellow card: Mohamed Salah"]
    assert poll("postponed", 1, "Abandoned", goals=1, yellow=1, plays=[goal]) == []
    # Set again for a fortnight on; its polls start afresh once it's armed.
    db.execute(text("UPDATE event SET start_utc = :at, status = 'armed'"), {"at": now + timedelta(days=14)})
    db.commit()
    polled.recent = False
    replay = [poll("scheduled", 0, "")]
    polled.recent = True
    replay += [poll("live", 0, "1'"), poll("live", 0, "2'"), poll("live", 0, "9'", yellow=1), poll("live", 0, "10'", yellow=1)]
    # Its line-up, its start and its first yellow card, and nothing of the game called off taken back.
    assert replay == [["Mohamed Salah starts"], ["Mohamed Salah's game has started"], [], ["Yellow card: Mohamed Salah"], []]
    # Its notification's story is the replay's alone.
    assert db.scalars(select(Notification.lines).order_by(Notification.id.desc()).limit(1)).one() == ["Yellow card: Mohamed Salah"]


def test_a_game_stopped_a_while_and_on_again_under_its_start_is_the_same_game(db, pushed, monkeypatch):
    # Suspended mid-game (floodlights) and on again: nothing told again, as it's the same game, not one played again.
    now = datetime.now(timezone.utc)
    db.add(Player(id=7, slug="mohamed-salah", name="Mohamed Salah", sport="Football"))
    db.add(Event(id=1, key="espn_soccer:1", sport="Football", title="Egypt v Ghana", status="live", start_utc=now - timedelta(minutes=40)))
    db.flush()
    db.add_all([EventPlayer(event_id=1, player_id=7), Follow(fan_id="fan", player_id=7, alert_rules={"level": "key"})])
    db.commit()
    device(db, "fan")
    monkeypatch.setattr(structured, "_polled", lambda event_id, cadence: True)
    football(1, "live", 0, 0, "1'")
    football(1, "live", 1, 0, "23'", goals=1)
    football(1, "postponed", 1, 0, "Suspended", goals=1)
    pushed.clear()
    football(1, "live", 1, 0, "31'", goals=1)
    football(1, "live", 1, 0, "32'", goals=1)
    assert pushed == [] and notify.editions(db, [1]) == {}
