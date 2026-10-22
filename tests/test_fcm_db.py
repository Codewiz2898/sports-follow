"""The Android app's devices in a real database: registered by POST /api/push/fcm, sent planned moments
through the fake FCM (tests/test_fcm.py) beside a browser of the same fan, and moved or removed as FCM
and the app say. ON CONFLICT needs Postgres itself, so these run against a throwaway database at alembic
head and are skipped without one:

    docker compose exec postgres createdb -U sports sports_follow_test
    DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/alembic upgrade head
    SPORTS_FOLLOW_TEST_DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/python -m pytest tests/test_fcm_db.py

Every test empties the tables first.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import accounts, moments, notify, server
from sports_follow.models import Account, AccountSession, Event, EventState, Follow, Notification, Player, PlayerCard, PushSubscription

from .test_fcm import TOKEN, fake_fcm  # noqa: F401  (the fixture)

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

PHONE = TOKEN
OTHER_PHONE = "b3RoZXItaW5zdGFuY2U:APA91bF" + "y" * 140
BROWSER = "https://fcm.googleapis.com/fcm/send/browser"


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE player, event, follow, moment, notification, notification_mute, push_subscription, account, account_session RESTART IDENTITY CASCADE"))
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

    monkeypatch.setattr(notify, "session", unit)
    monkeypatch.setattr(notify.bus, "publish", lambda player_id, event: None)
    s = made()
    s.add(Player(id=7, slug="mohamed-salah", name="Mohamed Salah", sport="Football"))
    s.add(Event(id=1, key="e1", sport="football", title="Egypt v South Africa", status="live"))
    s.flush()
    s.add(Follow(fan_id="fan-a", player_id=7, alert_rules={"level": "key"}))
    s.commit()
    yield s
    s.close()
    engine.dispose()


def register(db, fan: str, **body):
    """POST /api/push/fcm as the app sends it, for `fan`, with the database underneath."""
    return server.register_fcm(body={"token": PHONE, "timezone": "Asia/Kolkata", **body}, fan=fan, signed=None, db=db)


def devices(db) -> dict[str, tuple]:
    db.expire_all()
    return {d.endpoint: (d.fan_id, d.transport, d.quiet_start, d.quiet_end) for d in db.scalars(select(PushSubscription))}


def goal(db, detail: str, body: str) -> list[int]:
    ids = notify.record(db, 7, 1, [moments.Found("goal", "key", "Goal: Mohamed Salah puts Egypt ahead", body, detail)])
    db.commit()
    return ids


def test_a_planned_moment_reaches_the_android_app_and_the_browser(db, fake_fcm, monkeypatch):  # noqa: F811
    browser = []
    monkeypatch.setattr(notify, "push", lambda endpoint, p256dh, auth, payload, ttl, topic=None: browser.append((payload["tag"], payload["title"], topic, set(payload))))
    monkeypatch.setattr(notify, "SITE_URL", "https://sports-follow.example")
    assert register(db, "fan-a") == {"ok": True, "timezone": "Asia/Kolkata", "quiet_start": None, "quiet_end": None}
    db.add(PushSubscription(fan_id="fan-a", endpoint=BROWSER, p256dh="k", auth="a"))
    # The game as its last poll had it, and Salah's card with his photo.
    db.get(Event, 1).competition = "Africa Cup of Nations"
    board = {"kind": "football", "home": {"id": "1", "name": "Egypt", "abbr": "EGY", "score": "1"}, "away": {"id": "2", "name": "South Africa", "abbr": "RSA", "score": "0"}}
    db.add(EventState(event_id=1, as_of=datetime.now(timezone.utc), status="live", score_label="Egypt 1 – 0 South Africa", clock_label="23'", state=board))
    db.add(PlayerCard(player_id=7, status="ready", card={"player": {"name": "Mohamed Salah", "photo": {"url": "/api/photos/0123.jpg", "credit": "…"}}}))
    db.commit()

    assert notify.send(goal(db, "goal@101", "Egypt 1 – 0 South Africa · 23'")) == 2
    [row] = db.scalars(select(Notification)).all()
    assert (row.devices, row.delivered, row.loud) == (2, 2, True)
    [sent] = fake_fcm.sent
    assert sent["token"] == PHONE and "notification" not in sent  # the app draws it
    assert sent["data"] == {
        "v": "2", "type": "moment", "tag": "e1", "loud": "1", "id": str(row.id), "url": "/player/7", "event_id": "1", "player_id": "7", "kind": "goal",
        "title": "Goal: Mohamed Salah puts Egypt ahead", "body": "Egypt 1 – 0 South Africa · 23'",
        # Its scorecard: the game as its last poll had it, in football's colours, and the moment's word over its line.
        "sport": "football", "colour": "#12c46c", "ink": "#0b1a10", "league": "Africa Cup of Nations", "match": "",
        "sides": '[["EGY","1"],["RSA","0"]]', "status": "23'", "live": "1",
        "word": "Goal", "line": "Mohamed Salah puts Egypt ahead", "story": "", "photo": "https://sports-follow.example/api/photos/0123.jpg",
    }
    assert sent["android"] == {"priority": "high", "ttl": "900s", "collapse_key": "e1"}
    # The same push by Web Push, no bigger than it was.
    assert browser == [("e1", "Goal: Mohamed Salah puts Egypt ahead", "e1", {"id", "title", "body", "url", "tag", "event_id", "player_id", "kind", "loud", "quiet"})]
    assert db.scalar(select(PushSubscription.last_ok_at).where(PushSubscription.endpoint == PHONE)) is not None

    # The next goal of the game replaces that notification (its tag); during the phone's quiet hours (from
    # half an hour ago to half an hour on, in India) it arrives silently.
    india = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    register(db, "fan-a", quiet_start=f"{india - timedelta(minutes=30):%H:%M}", quiet_end=f"{india + timedelta(minutes=30):%H:%M}")
    assert notify.send(goal(db, "goal@140", "Egypt 2 – 0 South Africa · 40'")) == 2
    second = fake_fcm.sent[-1]
    assert (second["android"]["priority"], second["data"]["loud"], second["data"]["tag"]) == ("normal", "0", "e1")
    assert second["data"]["body"].startswith("Egypt 2 – 0 South Africa · 40'")

    # Uninstalled: FCM no longer knows the token, and the device goes. The browser stays.
    fake_fcm.gone.add(PHONE)
    assert notify.send(goal(db, "goal@160", "Egypt 3 – 0 South Africa · 60'")) == 1
    assert set(devices(db)) == {BROWSER}


def test_without_a_key_android_devices_are_left_alone(db, monkeypatch, tmp_path):
    db.add(PushSubscription(fan_id="fan-a", transport="fcm", endpoint=PHONE))
    db.commit()
    monkeypatch.setattr(notify.fcm, "KEY_FILE", tmp_path / "missing.json")
    notify.fcm.account.cache_clear()
    try:
        assert notify.send(goal(db, "goal@101", "Egypt 1 – 0 South Africa · 23'")) == 0
        assert db.scalars(select(Notification)).all() == []  # no device to tell, so nothing logged as sent
        assert db.scalar(select(PushSubscription.failures)) == 0  # not counted against it either
        with pytest.raises(HTTPException) as refused:
            register(db, "fan-a")
        assert refused.value.status_code == 503
    finally:
        notify.fcm.account.cache_clear()


def test_a_token_moves_with_whoever_presents_it_and_a_new_one_replaces_the_old(db, fake_fcm):  # noqa: F811
    register(db, "fan-a", quiet_start="22:00", quiet_end="07:00")
    # The app's cookie changed (a sign-in, or data cleared): the token it presents moves the device.
    register(db, "fan-b", quiet_start="22:00", quiet_end="07:00")
    assert devices(db) == {PHONE: ("fan-b", "fcm", "22:00", "07:00")}
    # FCM gave the app a new token: the old one goes, and its quiet hours come along.
    server.register_fcm(body={"token": OTHER_PHONE, "previous_token": PHONE}, fan="fan-b", signed=None, db=db)
    assert devices(db) == {OTHER_PHONE: ("fan-b", "fcm", "22:00", "07:00")}
    # Turning notifications off in the app removes just that device.
    db.add(PushSubscription(fan_id="fan-b", endpoint=BROWSER, p256dh="k", auth="a"))
    db.commit()
    assert server.unregister_fcm(body={"token": OTHER_PHONE}, fan="fan-a", db=db) == {"ok": True}
    assert OTHER_PHONE in devices(db)  # not another fan's to remove
    server.unregister_fcm(body={"token": OTHER_PHONE}, fan="fan-b", db=db)
    assert set(devices(db)) == {BROWSER}


def test_a_phone_signed_in_to_an_account_isnt_another_fans_to_take(db, fake_fcm):  # noqa: F811
    """BUG-103: whoever presented a phone's token moved the phone to their own fan, so it got their alerts and stopped
    getting its own (a browser moves only with its own keys). A phone signed in to an account moves into it as it signs
    in and goes with its session, so no other fan takes it; nor does an account take an anonymous fan's phone, which it
    then couldn't take back. Anonymous fans' phones still move by their token: a cookie lost (or the server's cookie key
    changed) leaves the phone a new fan, who must have it back."""
    db.add(Account(id=1, email="fan@example.com", fan_id="acct-1"))
    db.flush()
    db.add(AccountSession(id=1, account_id=1, token_hash="h", expires_at=datetime.now(timezone.utc) + timedelta(days=365)))
    db.commit()
    signed = accounts.Signed(account_id=1, fan_id="acct-1", session_id=1)
    server.register_fcm(body={"token": PHONE, "timezone": "Asia/Kolkata"}, fan="acct-1", signed=signed, db=db)
    with pytest.raises(HTTPException) as refused:
        register(db, "fan-b", quiet_start="00:00", quiet_end="23:59")  # someone else, with the phone's token
    assert refused.value.status_code == 409
    server.register_fcm(body={"token": PHONE, "quiet_start": "22:00", "quiet_end": "07:00"}, fan="acct-1", signed=signed, db=db)  # the phone itself
    assert devices(db)[PHONE] == ("acct-1", "fcm", "22:00", "07:00")
    server.register_fcm(body={"token": OTHER_PHONE}, fan="fan-a", signed=None, db=db)
    with pytest.raises(HTTPException) as refused:
        server.register_fcm(body={"token": OTHER_PHONE}, fan="acct-1", signed=signed, db=db)
    assert refused.value.status_code == 409 and devices(db)[OTHER_PHONE][0] == "fan-a"
    server.register_fcm(body={"token": OTHER_PHONE}, fan="fan-c", signed=None, db=db)  # anonymous to anonymous: as before
    assert devices(db)[OTHER_PHONE][0] == "fan-c"


def test_a_fans_android_devices_and_browsers_share_the_cap(db, fake_fcm):  # noqa: F811
    db.add_all([PushSubscription(fan_id="fan-a", endpoint=f"{BROWSER}-{i}", p256dh="k", auth="a") for i in range(notify.MAX_DEVICES)])
    db.commit()
    register(db, "fan-a")
    kept = devices(db)
    assert len(kept) == notify.MAX_DEVICES and PHONE in kept and f"{BROWSER}-0" not in kept  # the oldest browser made room


def live(db) -> dict[str, tuple]:
    db.expire_all()
    return {d.endpoint: (d.live_cards, d.live_stopped) for d in db.scalars(select(PushSubscription))}


def test_the_apps_live_scores_switch_comes_with_its_registration(db, fake_fcm):  # noqa: F811
    register(db, "fan-a")
    assert live(db) == {PHONE: (True, [])}  # on unless the app says
    register(db, "fan-a", live_cards=False)
    assert live(db) == {PHONE: (False, [])}
    # A build from before the switch reached the server sends none, or something that isn't one: it stays as it was.
    register(db, "fan-a")
    register(db, "fan-a", live_cards="no")
    assert live(db) == {PHONE: (False, [])}
    # FCM gave the app a new token: the switch and the stopped games come along, as its quiet hours do.
    server.stop_live_card(1, body={"token": PHONE}, fan="fan-a", db=db)
    server.register_fcm(body={"token": OTHER_PHONE, "previous_token": PHONE}, fan="fan-a", signed=None, db=db)
    assert live(db) == {OTHER_PHONE: (False, [1])}
    server.register_fcm(body={"token": OTHER_PHONE, "live_cards": True}, fan="fan-a", signed=None, db=db)
    assert live(db) == {OTHER_PHONE: (True, [1])}


def test_stop_live_card_reaches_the_server_for_that_phone_and_game(db, fake_fcm):  # noqa: F811
    register(db, "fan-a")
    server.register_fcm(body={"token": OTHER_PHONE}, fan="fan-b", signed=None, db=db)
    db.add_all([Event(id=n, key=f"e{n}", sport="football", title=f"Game {n}", status="live") for n in range(2, server.LIVE_STOPS_KEPT + 3)])
    db.commit()
    assert server.stop_live_card(1, body={"token": PHONE}, fan="fan-a", db=db) == {"event_id": 1, "stopped": True}
    assert live(db) == {PHONE: (True, [1]), OTHER_PHONE: (True, [])}  # the fan's other phones keep the card
    for refused, (event_id, body, fan) in ((400, (1, {"token": "nope"}, "fan-a")), (404, (99, {"token": PHONE}, "fan-a")), (404, (1, {"token": OTHER_PHONE}, "fan-a"))):
        with pytest.raises(HTTPException) as answer:
            server.stop_live_card(event_id, body=body, fan=fan, db=db)  # not a token, no such game, another fan's phone
        assert answer.value.status_code == refused
    # The newest LIVE_STOPS_KEPT games, as the app keeps them: older ones are long over.
    for event_id in [*range(2, server.LIVE_STOPS_KEPT + 3), 5]:
        server.stop_live_card(event_id, body={"token": PHONE}, fan="fan-a", db=db)
    stopped = live(db)[PHONE][1]
    assert len(stopped) == server.LIVE_STOPS_KEPT and stopped[-2:] == [server.LIVE_STOPS_KEPT + 2, 5] and 1 not in stopped and stopped.count(5) == 1
