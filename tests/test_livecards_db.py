"""A followed game's live card on Android phones (livecards.py), sent to the fans of a game in a real database
through the fake FCM (tests/test_fcm.py): who gets it, what each fan's says of their own players, and its end;
and the poller sending both (structured.apply_poll). These run against a throwaway database at alembic head and
are skipped without one:

    docker compose exec postgres createdb -U sports sports_follow_test
    DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/alembic upgrade head
    SPORTS_FOLLOW_TEST_DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/python -m pytest tests/test_livecards_db.py

Every test empties the tables first.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import Future
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import bus, livecards, notify, server, structured
from sports_follow.adapters.base import Line, Snapshot
from sports_follow.models import (Event, EventPlayer, EventState, EventTeam, Follow, MatchFollow, Notification, NotificationMute, Player, PlayerLine,
                                  PushSubscription, Team, TeamFollow)

from .test_fcm import TOKEN, fake_fcm  # noqa: F401  (the fixture)
from .test_livecards import Redis

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime.now(timezone.utc)
BOARD = {"kind": "football", "home": {"id": "359", "name": "Arsenal", "abbr": "ARS", "score": "1"}, "away": {"id": "363", "name": "Chelsea", "abbr": "CHE", "score": "0"}}


def phone(fan: str, n: int = 0) -> str:
    return f"{fan}-{n}:APA91b" + "x" * 40


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE player, event, event_state, event_player, player_line, follow, match_follow, team, team_follow, notification, notification_mute, "
                       "push_subscription RESTART IDENTITY CASCADE"))
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

    for module in (livecards, notify, structured):
        monkeypatch.setattr(module, "session", unit)
    s = made()
    s.add_all([Player(id=7, slug="bukayo-saka", name="Bukayo Saka", sport="Football"), Player(id=8, slug="declan-rice", name="Declan Rice", sport="Football"),
               Player(id=9, slug="cole-palmer", name="Cole Palmer", sport="Football")])
    s.add(Event(id=1, key="espn_soccer:740001", sport="Football", title="Arsenal vs Chelsea", competition="Premier League", status="live", start_utc=NOW - timedelta(minutes=30)))
    s.add(Team(id=1, name="Arsenal", sport="football", system="espn_soccer", external_id="359"))
    s.flush()
    s.add_all([EventPlayer(event_id=1, player_id=p) for p in (7, 8, 9)] + [EventTeam(event_id=1, team_id=1)])
    s.add(EventState(event_id=1, as_of=NOW, status="live", score_label="Arsenal 1 – 0 Chelsea", clock_label="31'", state=BOARD))
    s.add_all([PlayerLine(event_id=1, player_id=7, as_of=NOW - timedelta(minutes=10), stats={"headline": "Started", "stats": []}),
               PlayerLine(event_id=1, player_id=7, as_of=NOW, stats={"headline": "1 goal", "stats": []})])
    s.commit()
    yield s
    s.close()
    engine.dispose()


def fans(db) -> None:
    """Every way of following the game, each fan with an Android phone (and one a browser too)."""
    follows = {"keen": [(7, "key"), (8, "everything"), (9, "off")], "results": [(7, "results")], "off": [(7, "off")], "muted": [(7, "key")]}
    for fan, players in follows.items():
        db.add_all([Follow(fan_id=fan, player_id=p, alert_rules={"level": level}) for p, level in players])
    db.add_all([MatchFollow(fan_id="off", event_id=1, alert_rules={"level": "key"}), MatchFollow(fan_id="lapsed", event_id=1, alert_rules={}, until=NOW),
                TeamFollow(fan_id="gooner", team_id=1, alert_rules={}), NotificationMute(fan_id="muted", event_id=1)])
    for fan in ("keen", "results", "off", "muted", "lapsed", "gooner"):
        db.add(PushSubscription(fan_id=fan, transport="fcm", endpoint=phone(fan)))
    db.add(PushSubscription(fan_id="keen", endpoint="https://fcm.googleapis.com/fcm/send/keen-browser", p256dh="k", auth="a"))
    db.commit()


def by_fan(sent: list[dict]) -> dict[str, dict]:
    return {m["token"].split("-")[0]: m for m in sent}


def test_a_live_card_reaches_the_fans_who_hear_the_games_key_moments(db, fake_fcm):  # noqa: F811
    fans(db)
    card = {**livecards.keys(livecards.sport_of("Football", BOARD), "Premier League", "espn_soccer:740001", "live"), "type": "live", "tag": "live-e1", "event_id": 1,
            "title": "Arsenal 1 – 0 Chelsea", "body": "31'", "progress": "31/90", "marks": [[12, 0]]}
    assert livecards.send(1, "live", card) == 3
    # Not a fan of results only or off, nor one who muted the game or whose follow of it lapsed; on the phone only.
    sent = by_fan(fake_fcm.sent)
    assert set(sent) == {"keen", "off", "gooner"}
    # Each card names the fan's own players in the game (not one set to off), with their line so far.
    assert {fan: json.loads(m["data"]["players"] or "[]") for fan, m in sent.items()} == {"keen": ["Bukayo Saka · 1 goal", "Declan Rice"], "off": [], "gooner": []}
    keen = sent["keen"]
    # Not collapsible: FCM throttles a device's collapsible messages, and a game's moments are (by its tag).
    assert keen["android"] == {"priority": "normal", "ttl": "60s"} and "notification" not in keen
    assert {k: keen["data"][k] for k in ("v", "type", "tag", "event_id", "sport", "colour", "league", "match", "title", "progress", "marks", "live")} == {
        "v": "2", "type": "live", "tag": "live-e1", "event_id": "1", "sport": "football", "colour": "#12c46c", "league": "Premier League",
        "match": "/match/espn_soccer/740001", "title": "Arsenal 1 – 0 Chelsea", "progress": "31/90", "marks": "[[12,0]]", "live": "1"}

    # The end goes to everyone who follows the game any way, whatever their level, to take a card down.
    fake_fcm.sent.clear()
    assert livecards.send(1, "end") == 6
    sent = by_fan(fake_fcm.sent)
    assert set(sent) == {"keen", "results", "off", "muted", "lapsed", "gooner"}
    end = sent["results"]
    assert end["android"] == {"priority": "normal", "ttl": "3600s", "collapse_key": "live-e1"}
    assert {k: end["data"].get(k) for k in ("type", "tag", "event_id", "sides", "status", "live", "players")} == {
        "type": "end", "tag": "live-e1", "event_id": "1", "sides": '[["ARS","1"],["CHE","0"]]', "status": "31'", "live": "0", "players": None}


def test_a_fans_newest_phones_get_the_card_and_one_uninstalled_goes(db, fake_fcm):  # noqa: F811
    db.add(MatchFollow(fan_id="many", event_id=1, alert_rules={}))
    db.add_all([PushSubscription(fan_id="many", transport="fcm", endpoint=phone("many", n)) for n in range(notify.MAX_DEVICES + 2)])
    db.commit()
    fake_fcm.gone.add(phone("many", notify.MAX_DEVICES + 1))
    assert livecards.send(1, "live", {"type": "live", "tag": "live-e1", "event_id": 1}) == notify.MAX_DEVICES - 1
    assert len(fake_fcm.sent) == notify.MAX_DEVICES - 1 and phone("many", 0) not in {m["token"] for m in fake_fcm.sent}
    db.expire_all()
    assert phone("many", notify.MAX_DEVICES + 1) not in set(db.scalars(select(PushSubscription.endpoint)))  # uninstalled: removed
    assert db.scalar(select(func.count()).select_from(Notification)) == 0  # a card is no alert


def test_a_phone_with_live_scores_off_or_that_stopped_the_game_gets_none_of_its_cards(db, fake_fcm):  # noqa: F811
    """Alerts' Live scores and a card's Stop button reach the server (server.register_fcm, stop_live_card): the
    phone would throw the game's cards away, so none goes to it, nor its end. The fan's other phones keep them."""
    fans(db)
    db.add_all([PushSubscription(fan_id="keen", transport="fcm", endpoint=phone("keen", 1)), PushSubscription(fan_id="gooner", transport="fcm", endpoint=phone("gooner", 1))])
    db.commit()
    card = {"type": "live", "tag": "live-e1", "event_id": 1}

    def to() -> set[str]:
        tokens = {m["token"].split(":")[0] for m in fake_fcm.sent}
        fake_fcm.sent.clear()
        return tokens

    # The app sends the switch with every registration: off, the phone gets no card; the fan's other phone does.
    server.register_fcm(body={"token": phone("keen"), "live_cards": False}, fan="keen", signed=None, db=db)
    # Stop live card on a game's card: that phone gets no more of it.
    server.stop_live_card(1, body={"token": phone("gooner", 1)}, fan="gooner", db=db)
    assert livecards.send(1, "live", card) == 3
    assert to() == {"keen-1", "off-0", "gooner-0"}
    assert livecards.send(1, "end") == 6
    assert to() == {"keen-1", "results-0", "off-0", "muted-0", "lapsed-0", "gooner-0"}

    # Registering again without it (a build from before) changes nothing; turned back on, the cards come again.
    server.register_fcm(body={"token": phone("keen")}, fan="keen", signed=None, db=db)
    assert livecards.send(1, "live", card) == 3
    assert to() == {"keen-1", "off-0", "gooner-0"}
    server.register_fcm(body={"token": phone("keen"), "live_cards": True}, fan="keen", signed=None, db=db)
    assert livecards.send(1, "live", card) == 4
    assert to() == {"keen-0", "keen-1", "off-0", "gooner-0"}


def test_a_phone_that_stopped_another_game_still_gets_this_ones_card(db, fake_fcm):  # noqa: F811
    db.add(Event(id=2, key="espn_soccer:740002", sport="Football", title="Spurs vs Fulham", status="live"))
    db.add(MatchFollow(fan_id="fan", event_id=1, alert_rules={}))
    db.add(PushSubscription(fan_id="fan", transport="fcm", endpoint=phone("fan")))
    db.commit()
    server.stop_live_card(2, body={"token": phone("fan")}, fan="fan", db=db)
    assert livecards.send(1, "live", {"type": "live", "tag": "live-e1", "event_id": 1}) == 1


def test_a_card_fcm_fails_isnt_counted_against_the_phone(db, fake_fcm):  # noqa: F811
    """Cards come every few seconds: a minute of trouble at FCM mustn't use up a phone's failures and remove it."""
    db.add(MatchFollow(fan_id="fan", event_id=1, alert_rules={}))
    db.add(PushSubscription(fan_id="fan", transport="fcm", endpoint=phone("fan"), failures=notify.MAX_FAILURES - 1))
    db.commit()
    fake_fcm.failing = 503
    for _ in range(3):
        assert livecards.send(1, "live", {"type": "live", "tag": "live-e1", "event_id": 1}) == 0
    db.expire_all()
    assert db.scalar(select(PushSubscription.failures).where(PushSubscription.endpoint == phone("fan"))) == notify.MAX_FAILURES - 1


def test_the_poller_sends_a_live_card_and_its_end(db, fake_fcm, monkeypatch):  # noqa: F811
    fans(db)
    monkeypatch.setattr(bus, "_sync", Redis())
    monkeypatch.setattr(structured, "_polled", lambda event_id, cadence: True)
    monkeypatch.setattr(notify, "dispatch", lambda ids: None)  # moments aren't this test's

    def at_once(fn, *args):
        done: Future = Future()
        done.set_result(fn(*args))
        return done

    monkeypatch.setattr(livecards, "_pool", SimpleNamespace(submit=at_once))
    source = SimpleNamespace(system="espn_soccer", credit=lambda snap: "ESPN", live_note=None, lag_note=None)
    goal = {"id": "g1", "clock": "12'", "kind": "GOAL", "team_id": "359", "own_goal": False, "athlete_ids": ["SAKA"], "text": "Bukayo Saka (Arsenal)"}

    def poll(status: str, clock: str) -> None:
        snap = Snapshot(status, "Arsenal 1 – 0 Chelsea", clock, json.loads(json.dumps(BOARD)), lines={"SAKA": Line("1 goal", [])}, moments=[goal])
        structured.apply_poll(1, {"adapter": "espn_soccer", "cadence": 10}, source, [(7, "SAKA")], snap)

    poll("live", "33'")
    assert {m["token"].split("-")[0]: (m["data"]["type"], m["data"]["body"], m["data"]["progress"], m["data"]["marks"]) for m in fake_fcm.sent} == \
        {fan: ("live", "33'", "33/90", "[[12,0]]") for fan in ("keen", "off", "gooner")}
    fake_fcm.sent.clear()
    poll("live", "34'")  # a minute on, seconds later: it waits
    assert fake_fcm.sent == []
    poll("final", "FT")
    assert sorted((m["token"].split("-")[0], m["data"]["type"], m["data"]["status"]) for m in fake_fcm.sent) == \
        [(fan, "end", "Full time") for fan in sorted(("keen", "results", "off", "muted", "lapsed", "gooner"))]
