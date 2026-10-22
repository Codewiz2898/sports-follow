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
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from sports_follow import accounts, controls, fansports, matches, notify, teams
from sports_follow.models import (Account, AccountSession, Clip, ClipReport, Event, FanSource, FanSport, Follow, League, LeagueMember, MatchFollow, Notification, NotificationMute, Pick,
                                  Player, PushSubscription, Question, Team, TeamFollow)

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE account, account_session, follow, notification_mute, notification, push_subscription, player, event, fan_source, clip, clip_report, "
                       "team, team_follow, match_follow, fan_sport, prediction_league RESTART IDENTITY CASCADE"))
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
    s.add_all([Team(id=i, name=f"Team {i}", sport="basketball", system="espn_basketball", external_id=str(i)) for i in range(1, 46)])
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

    db.add(FanSource(fan_id="anon", utm_source="whatsapp", landing="/gukesh"))
    db.commit()

    done = accounts.sign_in(db, account_id, "anon")
    db.commit()
    assert done.players == 2 and done.fan_id == fan
    assert [(s.fan_id, s.utm_source, s.landing) for s in db.scalars(select(FanSource))] == [(fan, "whatsapp", "/gukesh")]  # where they came from
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


def test_moving_again_brings_only_what_came_since_even_after_signing_out(db):
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
    device(db, "anon", "this-phone")
    db.commit()
    # BUG-099: signed out within the minute, what was under way stayed on the anonymous fan, which no device holds:
    # nobody could unfollow it. It comes into the account; its device doesn't, as its alerts stopped with the session.
    assert accounts.adopt_later("anon", account_id, done.session_id) == 1
    assert set(follows(db, fan)) == {1, 2, 3} and follows(db, "anon") == {}
    assert endpoints(db, "anon") == set() and endpoints(db, fan) == set()


def test_signing_in_brings_the_devices_sports_after_the_accounts_own(db):
    """BHA-653: the account's sports first, in its order, then the device's it didn't have, in the device's; a sport turned
    off comes too, out of sight, and the device keeps none."""
    account_id, fan = account(db)
    fansports.replace(db, fan, ["chess", "football"])
    fansports.replace(db, "anon", ["cricket", "football", "tennis", "basketball"])
    db.commit()
    controls.turn_sport("tennis", False)
    done = accounts.sign_in(db, account_id, "anon")
    db.commit()
    assert fansports.mine(db, fan) == ["chess", "football", "cricket", "basketball"] and fansports.mine(db, "anon") == []
    controls.turn_sport("tennis", True)
    assert fansports.mine(db, fan) == ["chess", "football", "cricket", "tennis", "basketball"]
    assert db.scalars(select(FanSport.fan_id).distinct()).all() == [fan]
    # Changed on the device after, under way as it signed in: the minute after brings it, after the rest.
    db.add(FanSport(fan_id="anon", sport="tennis", position=0))
    db.add(FanSport(fan_id="anon", sport="chess", position=1))
    db.commit()
    accounts.adopt_later("anon", account_id, done.session_id)
    db.expire_all()
    assert fansports.mine(db, fan) == ["chess", "football", "cricket", "tennis", "basketball"] and fansports.mine(db, "anon") == []


def test_what_was_under_way_goes_with_an_account_deleted_since_signing_in(db):
    """BUG-099: the account deleted within the minute, the stragglers stayed on the anonymous fan for good."""
    account_id, fan = account(db)
    done = accounts.sign_in(db, account_id, "anon")
    db.commit()
    follow(db, "anon", 2)
    device(db, "anon", "this-phone")
    db.add(TeamFollow(fan_id="anon", team_id=1))
    db.commit()
    accounts.delete_account(db, account_id)
    db.commit()
    assert accounts.adopt_later("anon", account_id, done.session_id) == 0
    db.expire_all()
    assert db.scalars(select(Follow.fan_id)).all() == [] and endpoints(db, "anon") == set() and db.scalars(select(TeamFollow.fan_id)).all() == []


def test_a_game_the_account_let_lapse_comes_back_on_only_if_it_fits(db):
    """BUG-099: a game the account followed once (lapsed) but the device still follows came over without counting
    toward matches.MAX, and on again: the account then followed more games than it may."""
    account_id, fan = account(db)
    db.add_all([Event(id=i, key=f"e{i}", sport="football", title=f"Game {i}") for i in range(3, matches.MAX + 3)])
    db.flush()
    db.add_all([MatchFollow(fan_id=fan, event_id=i) for i in range(3, matches.MAX + 3)])  # a full account
    db.add(MatchFollow(fan_id=fan, event_id=1, until=NOW - timedelta(days=1)))
    db.add(MatchFollow(fan_id="anon", event_id=1))
    db.commit()
    accounts.sign_in(db, account_id, "anon")
    db.commit()
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(MatchFollow).where(MatchFollow.fan_id == fan, MatchFollow.until.is_(None))) == matches.MAX
    assert db.scalar(select(MatchFollow.until).where(MatchFollow.fan_id == fan, MatchFollow.event_id == 1)) is not None


def test_bringing_a_devices_teams_waits_for_a_team_follow_under_way_on_the_account(db):
    """BUG-099: teams.merge counted the account's teams without the lock teams.follow takes, so a follow on another
    device at that moment could take the account past teams.MAX."""
    account_id, fan = account(db)
    db.add(TeamFollow(fan_id="anon", team_id=1))
    db.commit()
    other = db.get_bind().connect()
    under_way = other.begin()
    other.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"team-follow:{fan}", 0))))  # as teams.follow does
    try:
        db.execute(text("SET lock_timeout = '300ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            accounts.sign_in(db, account_id, "anon")
        db.rollback()
    finally:
        under_way.rollback()
        other.close()
    done = accounts.sign_in(db, account_id, "anon")  # once it's done
    db.commit()
    assert db.scalars(select(TeamFollow.fan_id)).all() == [fan] and done.teams_dropped == 0


def test_the_devices_teams_that_dont_fit_in_the_account_are_counted_for_the_fan(db):
    """BUG-228: the account follows at most teams.MAX teams, and the device's that didn't fit went without a word."""
    account_id, fan = account(db)
    db.add_all([TeamFollow(fan_id=fan, team_id=i) for i in range(1, teams.MAX + 1)])
    db.add_all([TeamFollow(fan_id="anon", team_id=i) for i in range(16, 46)])  # 15 the account has, 15 it hasn't
    db.commit()
    done = accounts.sign_in(db, account_id, "anon")
    db.commit()
    assert done.teams_dropped == 15
    assert sorted(db.scalars(select(TeamFollow.team_id).where(TeamFollow.fan_id == fan))) == list(range(1, teams.MAX + 1))
    other_id, other = account(db, "other@example.com")
    db.add_all([TeamFollow(fan_id="anon-2", team_id=i) for i in (1, 2)])
    db.commit()
    assert accounts.sign_in(db, other_id, "anon-2").teams_dropped == 0


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


def test_google_finds_by_its_id_then_links_the_address_and_never_takes_over_another_link(db):
    by_email, _ = account(db, "fan@gmail.com")
    assert accounts.find_or_create_google(db, "g-1", "fan@gmail.com") == by_email  # linked
    assert accounts.find_or_create_google(db, "g-1", "renamed@gmail.com") == by_email  # by its id from now on
    assert accounts.find_or_create_google(db, "g-2", "fan@gmail.com") is None  # the address has another Google account
    new = accounts.find_or_create_google(db, "g-3", "new@gmail.com")
    db.commit()
    assert db.get(Account, new).email == "new@gmail.com" and db.get(Account, new).google_sub == "g-3"
    assert db.get(Account, by_email).google_sub == "g-1"


def test_an_account_stands_once_its_a_day_old_and_the_codes_kept_for_accounts_are_for_those(db):
    """BUG-093, reopened: an account made minutes before counted as one with standing, so accounts made for it could
    spend the sign-in codes kept for fans with accounts."""
    account(db, "new@example.com")
    db.add(Account(email="old@example.com", fan_id="acct-old", created_at=NOW - timedelta(days=1, minutes=1), last_sign_in_at=NOW - timedelta(days=1)))
    db.commit()
    assert [accounts.standing(db, e) for e in ("new@example.com", "old@example.com", "nobody@example.com")] == [False, True, False]


def test_counting_inboxes_in_sql_agrees_with_python_and_an_account_is_still_its_whole_address(db):
    """BUG-296: queries that count fans count inboxes (accounts.inbox_of, the SQL twin of accounts.inbox); an account
    made with a +tag, or a dot, is its own account still, and signs in as itself."""
    emails = ["me+1@gmail.com", "m.e@googlemail.com", "me@gmail.com", "first.last+news@example.com", "first.last@example.com", "+only@example.com", "plain@example.org"]
    ids = [account(db, e)[0] for e in emails]
    assert len(set(ids)) == len(emails) and account(db, "me+1@gmail.com")[0] == ids[0]
    assert {e: i for e, i in db.execute(select(Account.email, accounts.inbox_of(Account.email)))} == {e: accounts.inbox(e) for e in emails}


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
    # Whose channel the account's open streams hear it on (server._tell_signed_out, BUG-162).
    assert accounts.fan_of(db, account_id) == fan and accounts.fan_of(db, account_id + 1) is None


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
    db.add(Clip(slug="c1", source="game_client", seconds=30, width=1386, height=900, status="live"))
    db.flush()
    for who in (fan, other):
        db.add(ClipReport(clip_id=1, fan_id=who))
        follow(db, who, 1)
        db.add(NotificationMute(fan_id=who, event_id=1))
        db.add(Notification(fan_id=who, player_id=1, kind="final", loud=False))
        device(db, who, f"{who}-phone", done.session_id if who == fan else None)
        db.add(FanSource(fan_id=who, tag="share", landing="/"))
        db.add(FanSport(fan_id=who, sport="chess", position=0))
    db.commit()

    gone = accounts.delete_account(db, account_id)
    db.commit()
    assert gone.fan_id == fan and gone.email == "fan@example.com" and gone.token_hashes == [accounts.token_hash(done.token)]
    for model in (Follow, NotificationMute, Notification, PushSubscription, FanSource, FanSport, ClipReport):
        assert db.scalars(select(model.fan_id)).all() == [other]  # only the other fan's
    assert db.scalars(select(Account.id)).all() == [other_id]
    assert db.scalars(select(AccountSession.id)).all() == []
    assert db.get(Player, 1) is not None and db.get(Event, 1) is not None  # everyone's
    assert accounts.delete_account(db, account_id) is None


def test_what_a_request_under_way_wrote_for_a_deleted_account_goes_at_the_next_sweep(db):
    """BUG-223: a follow, a pick or a place in a league written by another of the account's devices as it was deleted
    (its fan id read just before) outlived it: personal data kept, and its players polled, for good."""
    account_id, fan = account(db)
    other_id, other = account(db, "other@example.com")
    accounts.delete_account(db, account_id)
    db.commit()
    db.add(Question(id=1, game="g1", ask="result", sport="football", match="pitch/1", title="Who wins?"))
    db.add(League(id=1, code="ABC123", name="Friends", owner=fan))
    db.flush()
    for who in (fan, other, "anon"):
        follow(db, who, 1)
        db.add(TeamFollow(fan_id=who, team_id=1))
        db.add(Pick(question_id=1, fan_id=who, option="home"))
    db.add_all([LeagueMember(league_id=1, fan_id=fan, name="Asha"), LeagueMember(league_id=1, fan_id=other, name="Ravi")])
    db.commit()
    assert accounts.purge_expired() == 0
    db.expire_all()
    for model in (Follow, TeamFollow, Pick):
        assert sorted(db.scalars(select(model.fan_id))) == sorted([other, "anon"])  # an account's and an anonymous fan's stay
    assert db.scalars(select(LeagueMember.fan_id)).all() == [other] and db.get(League, 1).owner is None
