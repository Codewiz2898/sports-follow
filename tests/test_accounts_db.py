"""What accounts.py does to real rows: moving a device's anonymous fan into an account, signing out and
deleting. ON CONFLICT, INSERT … SELECT and cascades need Postgres itself, so these run against a
throwaway database at alembic head and are skipped without one:

    docker compose exec postgres createdb -U sports sports_follow_test
    DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/alembic upgrade head
    SPORTS_FOLLOW_TEST_DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/python -m pytest tests/test_accounts_db.py

Every test empties the tables first.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import accounts, notify
from sports_follow.models import Account, AccountSession, Event, Follow, Notification, NotificationMute, Player, PushSubscription

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE account, account_session, follow, notification_mute, notification, push_subscription, player, event RESTART IDENTITY CASCADE"))
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

    monkeypatch.setattr(accounts, "session", unit)
    s = made()
    s.add_all([Player(id=i, slug=f"p{i}", name=f"Player {i}") for i in (1, 2, 3)])
    s.add_all([Event(id=i, key=f"e{i}", sport="football", title=f"Game {i}") for i in (1, 2)])
    s.commit()
    yield s
    s.close()
    engine.dispose()


def follow(db, fan, player_id, level=None, days_ago=0):
    db.add(Follow(fan_id=fan, player_id=player_id, alert_rules={"level": level} if level else {}, created_at=NOW - timedelta(days=days_ago)))


def device(db, fan, name, session_id=None):
    db.add(PushSubscription(fan_id=fan, endpoint=f"https://fcm.googleapis.com/fcm/send/{name}", p256dh="k", auth="a", session_id=session_id))
    db.flush()


def follows(db, fan):
    db.expire_all()
    return {f.player_id: (f.alert_rules.get("level"), f.created_at) for f in db.scalars(select(Follow).where(Follow.fan_id == fan))}


def endpoints(db, fan):
    return {d.endpoint.rsplit("/", 1)[1] for d in db.scalars(select(PushSubscription).where(PushSubscription.fan_id == fan))}


def account(db, email="fan@example.com"):
    account_id = accounts.find_or_create(db, email)
    db.commit()
    return account_id, db.get(Account, account_id).fan_id


# ---------------------------------------------------------------- signing in


def test_signing_in_brings_follows_mutes_the_push_log_and_devices(db):
    account_id, fan = account(db)
    follow(db, fan, 1, level="off", days_ago=2)  # the account already follows player 1
    follow(db, "anon", 1, level="everything", days_ago=10)
    follow(db, "anon", 2)
    db.add(NotificationMute(fan_id="anon", event_id=1))
    db.add(Notification(fan_id="anon", player_id=1, kind="goal", loud=True))
    device(db, "anon", "phone")
    db.commit()

    done = accounts.sign_in(db, account_id, "anon")
    db.commit()
    assert done.players == 2 and done.fan_id == fan
    mine = follows(db, fan)
    assert set(mine) == {1, 2}
    assert mine[1][0] == "off"  # the account's level stays
    assert mine[1][1] == NOW - timedelta(days=10)  # the earlier follow: the activity list shows moments since then
    assert follows(db, "anon") == {}
    assert db.scalars(select(NotificationMute.fan_id)).all() == [fan]
    assert db.scalars(select(Notification.fan_id)).all() == [fan]
    phone = db.scalar(select(PushSubscription))
    assert (phone.fan_id, phone.session_id) == (fan, done.session_id)
    assert db.get(AccountSession, done.session_id).token_hash == accounts.token_hash(done.token)


def test_moving_again_brings_only_what_came_since_and_nothing_after_signing_out(db):
    account_id, fan = account(db)
    follow(db, "anon", 1)
    db.commit()
    done = accounts.sign_in(db, account_id, "anon")
    db.commit()
    follow(db, "anon", 2)  # a follow already under way when the device signed in
    db.commit()
    assert accounts.adopt_later("anon", account_id, done.session_id) == 1
    assert accounts.adopt_later("anon", account_id, done.session_id) == 0
    assert set(follows(db, fan)) == {1, 2}
    accounts.end_sessions(db, account_id, done.session_id)
    db.commit()
    follow(db, "anon", 3)
    db.commit()
    assert accounts.adopt_later("anon", account_id, done.session_id) == 0  # signed out since: it stays anonymous
    assert set(follows(db, "anon")) == {3}


def test_the_device_signing_in_is_never_the_one_dropped_for_room(db):
    account_id, fan = account(db)
    device(db, "anon", "this-phone")  # the oldest id of all
    for i in range(notify.MAX_DEVICES):
        device(db, fan, f"old-{i}")
    db.commit()
    accounts.sign_in(db, account_id, "anon")
    db.commit()
    kept = endpoints(db, fan)
    assert len(kept) == notify.MAX_DEVICES and "this-phone" in kept and "old-0" not in kept


# ---------------------------------------------------------------- sessions


def test_a_session_lasts_a_year_from_its_last_use_and_is_renewed_once_a_day(db):
    account_id, fan = account(db)
    done = accounts.sign_in(db, account_id, None)
    db.commit()
    assert accounts.session_for(db, accounts.token_hash(done.token)) == accounts.Signed(account_id, fan, done.session_id)
    assert accounts.bump(db, done.session_id) is False  # used today already
    db.execute(text("UPDATE account_session SET last_seen_at = now() - interval '2 days'"))
    assert accounts.bump(db, done.session_id) is True
    db.execute(text("UPDATE account_session SET expires_at = now() - interval '1 second'"))
    assert accounts.session_for(db, accounts.token_hash(done.token)) is None


@pytest.mark.parametrize("everywhere", [False, True])
def test_signing_out_turns_off_that_sessions_devices(db, everywhere):
    account_id, fan = account(db)
    here, there = accounts.sign_in(db, account_id, None), accounts.sign_in(db, account_id, None)
    device(db, fan, "here", here.session_id)
    device(db, fan, "there", there.session_id)
    device(db, fan, "loose")  # none should be without a session, but if one is
    db.commit()
    ended = accounts.end_sessions(db, account_id, None if everywhere else here.session_id)
    db.commit()
    assert set(ended) == ({accounts.token_hash(here.token), accounts.token_hash(there.token)} if everywhere else {accounts.token_hash(here.token)})
    assert endpoints(db, fan) == (set() if everywhere else {"there", "loose"})


def test_expired_sessions_are_purged_with_their_devices(db):
    account_id, fan = account(db)
    old, new = accounts.sign_in(db, account_id, None), accounts.sign_in(db, account_id, None)
    device(db, fan, "old", old.session_id)
    device(db, fan, "new", new.session_id)
    db.execute(text("UPDATE account_session SET expires_at = now() - interval '1 day' WHERE id = :id"), {"id": old.session_id})
    db.commit()
    assert accounts.purge_expired() == 1
    assert endpoints(db, fan) == {"new"}


# ---------------------------------------------------------------- deleting


def test_deleting_an_account_leaves_nothing_under_its_fan_id(db):
    account_id, fan = account(db)
    other_id, other = account(db, "other@example.com")
    done = accounts.sign_in(db, account_id, None)
    for who in (fan, other):
        follow(db, who, 1)
        db.add(NotificationMute(fan_id=who, event_id=1))
        db.add(Notification(fan_id=who, player_id=1, kind="final", loud=False))
        device(db, who, f"{who}-phone", done.session_id if who == fan else None)
    db.commit()

    gone = accounts.delete_account(db, account_id)
    db.commit()
    assert gone.fan_id == fan and gone.email == "fan@example.com" and gone.token_hashes == [accounts.token_hash(done.token)]
    for model in (Follow, NotificationMute, Notification, PushSubscription):
        assert db.scalars(select(model.fan_id)).all() == [other]  # only the other fan's
    assert db.scalars(select(Account.id)).all() == [other_id]
    assert db.scalars(select(AccountSession.id)).all() == []
    assert db.get(Player, 1) is not None and db.get(Event, 1) is not None  # everyone's
    assert accounts.delete_account(db, account_id) is None
