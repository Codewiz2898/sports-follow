"""What the admin panel does to real rows (admin.py, controls.py): its controls kept in Postgres and read
by every process; accounts listed and searched, signed out everywhere, blocked (no sign-in, no session)
and let back in. These run against a throwaway database at alembic head and are skipped without one
(test_accounts_db.py says how to make it).

Every test empties the tables first.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import accounts, admin, controls, db as db_module
from sports_follow.models import Account, AccountSession, Event, Follow, League, LeagueMember, MatchFollow, Pick, Player, PushSubscription, Question, Team, TeamFollow

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE account, account_session, follow, push_subscription, player, control, team, event, prediction_question, prediction_league RESTART IDENTITY CASCADE"))
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
    monkeypatch.setattr(db_module, "session", unit)
    s = made()
    s.add_all([Player(id=i, slug=f"p{i}", name=f"Player {i}", sport="football") for i in (1, 2)])
    s.commit()
    yield s
    s.close()
    engine.dispose()


def signed_in(db, email: str) -> tuple[int, str, str]:
    """An account with one device signed in to it, following player 1, with alerts on: (id, fan, token hash)."""
    account_id = accounts.find_or_create(db, email)
    done = accounts.sign_in(db, account_id, None)
    db.add(Follow(fan_id=done.fan_id, player_id=1))
    db.add(PushSubscription(fan_id=done.fan_id, endpoint=f"https://fcm.googleapis.com/fcm/send/{email}", p256dh="k", auth="a", session_id=done.session_id))
    db.commit()
    return account_id, done.fan_id, accounts.token_hash(done.token)


def test_the_controls_are_rows_every_process_reads(db):
    controls.reset(controls.DbStore())
    controls.turn_sport("chess", False)
    controls.turn_source("lichess_chess", False)
    controls.set_budget("lichess", {"windows": [], "reserve": 0, "fan": None, "pause": 30})
    rows = dict(db.execute(text("SELECT key, value FROM control")).all())
    assert rows == {"sports_off": ["chess"], "sources_off": ["lichess_chess"], "budget:lichess": {"windows": [], "reserve": 0, "fan": None, "pause": 30}}
    # Another process (a fresh copy) reads the same.
    controls.reset(controls.DbStore())
    controls.load()
    assert controls.current().sports_off == {"chess"} and controls.current().budgets["lichess"]["pause"] == 30
    controls.turn_sport("chess", True)
    controls.set_budget("lichess", None)
    assert dict(db.execute(text("SELECT key, value FROM control")).all()) == {"sources_off": ["lichess_chess"]}
    controls.reset(controls.MemoryStore())


def test_a_change_from_an_older_view_of_an_allowance_is_refused_and_the_row_kept(db):
    """BUG-262, against the rows every process reads: a change made from a view that showed nothing saved, when
    something was saved since, raises Stale and leaves the row as it was."""
    controls.reset(controls.DbStore())
    try:
        controls.set_budget("lichess", {"windows": [], "reserve": 0, "fan": None, "pause": 30}, based_on=None)
        with pytest.raises(controls.Stale):
            controls.set_budget("lichess", {"windows": [], "reserve": 0, "fan": None, "pause": 90}, based_on=None)
        assert db.execute(text("SELECT value FROM control WHERE key = 'budget:lichess'")).scalar_one()["pause"] == 30
    finally:
        controls.reset(controls.MemoryStore())


def test_accounts_are_listed_newest_first_and_found_by_address(db):
    signed_in(db, "first@example.com")
    signed_in(db, "second@example.org")
    db.add(Follow(fan_id="anon-1", player_id=2))
    db.commit()
    page = admin.account_page(db)
    assert [a["email"] for a in page["accounts"]] == ["second@example.org", "first@example.com"]
    assert page["accounts"][0]["players"] == 1 and page["accounts"][0]["devices"] == 1 and page["accounts"][0]["last_seen_at"]
    assert [a["email"] for a in admin.account_page(db, "EXAMPLE.COM")["accounts"]] == ["first@example.com"]
    assert admin.account_page(db, "%")["accounts"] == []  # a wildcard is a character here
    assert admin.overview(db) == {"accounts": 2, "active_week": 2, "blocked": 0, "anonymous": 1, "devices": 2}


def test_one_account_shows_who_they_follow_and_their_devices(db):
    account_id, _, _ = signed_in(db, "fan@example.com")
    shown = admin.account(db, account_id)
    assert shown is not None
    assert [p["name"] for p in shown["following"]] == ["Player 1"] and shown["following"][0]["sport"] == "Football"
    assert len(shown["sessions"]) == 1 and len(shown["alert_devices"]) == 1
    assert admin.account(db, 999) is None


def test_one_account_shows_the_teams_and_games_they_follow_and_their_calls(db):
    """BUG-261: the account page listed followed players only, so an account following teams and games read
    "Following · 0, Nobody yet", though deleting it takes those too. Its picks and leagues are counted beside them."""
    account_id, fan, _ = signed_in(db, "fan@example.com")
    team = Team(name="Riverside XI", sport="football", system="espn_soccer", external_id="10")
    game = Event(key="riverside-eastern", sport="football", title="Riverside XI vs Eastern Stars", start_utc=NOW)
    asked = Question(game="espn_soccer/1", ask="result", sport="football", match="espn_soccer/1", title="Who wins?")
    office = League(code="office123", name="Office")
    db.add_all([team, game, asked, office])
    db.flush()
    db.add_all([TeamFollow(fan_id=fan, team_id=team.id), MatchFollow(fan_id=fan, event_id=game.id), Pick(question_id=asked.id, fan_id=fan, option="home"),
                LeagueMember(league_id=office.id, fan_id=fan, name="Fan")])  # fmt: skip
    db.add(TeamFollow(fan_id="anon-1", team_id=team.id))  # someone else's
    db.commit()
    shown = admin.account(db, account_id)
    assert shown is not None
    assert [(t["id"], t["name"], t["sport"]) for t in shown["teams"]] == [(team.id, "Riverside XI", "Football")]
    assert [(g["title"], g["sport"], g["over"]) for g in shown["games"]] == [("Riverside XI vs Eastern Stars", "Football", False)]
    assert (shown["picks"], shown["leagues"]) == (1, 1)


def test_signing_an_account_out_everywhere_ends_its_sessions_and_alerts(db):
    account_id, fan, token = signed_in(db, "fan@example.com")
    assert admin.sign_out(db, account_id) == [token]
    db.commit()
    assert accounts.session_for(db, token) is None
    assert db.scalar(select(PushSubscription).where(PushSubscription.fan_id == fan)) is None
    assert db.scalar(select(Follow.player_id).where(Follow.fan_id == fan)) == 1  # what they follow stays
    assert admin.sign_out(db, 999) is None


def test_a_blocked_account_is_signed_out_and_cant_sign_in_until_let_back(db):
    account_id, _, token = signed_in(db, "fan@example.com")
    assert admin.block(db, account_id, True) == [token]
    db.commit()
    assert accounts.blocked(db, "fan@example.com") and accounts.blocked(db, None, account_id)
    assert accounts.session_for(db, token) is None
    assert admin.overview(db)["blocked"] == 1
    # A session made in a race with the block is no session either.
    done = accounts.sign_in(db, account_id, None)
    db.commit()
    assert accounts.session_for(db, accounts.token_hash(done.token)) is None
    assert admin.block(db, account_id, False) == []
    db.commit()
    assert not accounts.blocked(db, "fan@example.com")
    assert accounts.session_for(db, accounts.token_hash(done.token)) is not None
    assert admin.block(db, 999, True) is None
    assert not accounts.blocked(db, "nobody@example.com")


def test_deleting_an_account_from_the_panel_takes_everything_kept_under_it(db):
    account_id, fan, token = signed_in(db, "fan@example.com")
    gone = accounts.delete_account(db, account_id)
    db.commit()
    assert gone is not None and gone.token_hashes == [token]
    assert db.get(Account, account_id) is None
    assert db.scalar(select(AccountSession)) is None
    assert db.scalar(select(Follow).where(Follow.fan_id == fan)) is None
