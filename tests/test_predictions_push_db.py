"""Predictions' pushes against real rows (predictions_push.py, BHA-321): "Make your pick" before a campaign's game to
the fans who follow someone in it and haven't picked, as each set their alerts; how each pick went once its question
settles, with the fan's best league place; each once. Against the throwaway database tests/test_predictions_db.py
uses (skipped without one); the push services are made up."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from sports_follow import notify, predictions, predictions_push
from sports_follow.models import Campaign, EventPlayer, Follow, MatchFollow, Notification, NotificationMute, Player, PushSubscription, Question

from .test_new_adapters import CARLSEN
from .test_predictions import board
from .test_predictions_db import URL, chess_board, db, poll, redis, running  # noqa: F401  (the fixtures)

pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")


@pytest.fixture
def pushed(db, monkeypatch):  # noqa: F811
    """Every push the services were handed, as (fan's device endpoint, payload); each taken."""
    sent: list[tuple[str, dict]] = []

    def deliver(device, payload, ttl, topic=None):
        sent.append((device[2], payload))
        return None

    monkeypatch.setattr(notify, "deliver", deliver)
    monkeypatch.setattr(predictions_push, "session", predictions.session)
    return sent


def devices(db, *fans: str) -> None:  # noqa: F811
    db.add_all([PushSubscription(fan_id=f, endpoint=f"https://fcm.googleapis.com/fcm/send/{f}", p256dh="k", auth="a") for f in fans])
    db.commit()


def heard(pushed: list[tuple[str, dict]]) -> dict[str, dict]:
    out = {endpoint.rsplit("/", 1)[1]: payload for endpoint, payload in pushed}
    pushed.clear()
    return out


def test_make_your_pick_goes_once_before_a_campaigns_game_to_who_should_hear_it(db, redis, pushed):  # noqa: F811
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=90)
    chess_board(db, start)
    db.add(Player(id=1, slug="carlsen", name="Magnus Carlsen", sport="Chess", wikidata_qid="Q106807"))
    db.flush()
    db.add_all([EventPlayer(event_id=1, player_id=1), Campaign(slug="carlsen", sport="chess", title="Carlsen", players=["Q106807"], teams=[], games=[], lines=[])])
    for fan, level in (("asha", "key"), ("ravi", "results"), ("meera", "everything"), ("dev", "key"), ("kiran", "off")):
        db.add(Follow(fan_id=fan, player_id=1, alert_rules={"level": level}))
    db.add_all([MatchFollow(fan_id="sana", event_id=2, alert_rules={}, keys=[]), NotificationMute(fan_id="meera", event_id=1)])
    db.commit()
    devices(db, "asha", "ravi", "meera", "dev", "kiran", "sana")
    poll(1, board(status="*"))
    poll(2, board(status="*", fide="13515110"))
    q = db.scalars(select(Question)).one()
    predictions.pick(db, "dev", q.id, CARLSEN)
    db.commit()
    assert predictions_push.run(start - predictions_push.LEAD - timedelta(minutes=5)) == 0  # not yet
    predictions_push.run(start - timedelta(minutes=80))
    told = heard(pushed)
    # Asha (key) and Sana (follows the game itself); not Ravi (results only), Meera (muted it), Dev (picked), Kiran (off).
    assert sorted(told) == ["asha", "sana"]
    assert told["asha"]["title"] == "Make your pick: Magnus Carlsen vs Denis Lazavik" and told["asha"]["body"] == "Who wins? Picks close when it starts, in 80 minutes."
    assert (told["asha"]["kind"], told["asha"]["tag"], told["asha"]["url"]) == ("pick", "call-lichess_chess:mJnv1YBU", q.match.join(("/match/", "")))
    assert {(n.fan_id, n.kind, n.delivered) for n in db.scalars(select(Notification))} == {("asha", "pick", 1), ("sana", "pick", 1)}
    predictions_push.run(start - timedelta(minutes=70))
    assert heard(pushed) == {}  # once a game


def test_no_reminder_for_a_game_no_campaign_has(db, redis, pushed):  # noqa: F811
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=90)
    chess_board(db, start)
    db.add(Player(id=1, slug="carlsen", name="Magnus Carlsen", sport="Chess", wikidata_qid="Q106807"))
    db.flush()
    db.add_all([EventPlayer(event_id=1, player_id=1), Follow(fan_id="asha", player_id=1, alert_rules={})])
    db.commit()
    devices(db, "asha")
    poll(1, board(status="*"))
    predictions_push.run(start - timedelta(minutes=80))
    assert heard(pushed) == {} and db.scalars(select(Question)).one().reminded_at is not None


def test_how_a_pick_went_goes_once_to_who_picked_with_their_league_place(db, redis, pushed):  # noqa: F811
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    chess_board(db, start)
    poll(1, board(status="*"))
    q = db.scalars(select(Question)).one()
    for fan, option in (("acct-asha", CARLSEN), ("acct-ravi", "draw"), ("acct-dev", CARLSEN), ("quiet", CARLSEN)):
        predictions.pick(db, fan, q.id, option)
    gang = predictions.create(db, "acct-asha", "Chess gang", "Asha")
    predictions.join(db, "acct-ravi", gang.code, "Ravi")
    db.add(NotificationMute(fan_id="acct-dev", event_id=1))
    db.commit()
    devices(db, "acct-asha", "acct-ravi", "acct-dev", "quiet")
    db.execute(text("UPDATE push_subscription SET timezone = 'UTC', quiet_start = '00:00', quiet_end = '23:59' WHERE fan_id = 'quiet'"))
    db.commit()
    poll(1, board(0))  # the recorded result: Carlsen won
    predictions_push.run()
    told = heard(pushed)
    assert sorted(told) == ["acct-asha", "acct-ravi", "quiet"]  # Dev muted the game
    assert told["acct-asha"]["title"] == "You got it right: Magnus Carlsen"
    assert told["acct-asha"]["body"] == "Who wins? Magnus Carlsen vs Denis Lazavik · +25 points · 1st in Chess gang"  # 3 of 4 right
    assert told["acct-ravi"]["title"] == "Magnus Carlsen: not this time"
    assert told["acct-ravi"]["body"] == "You said Draw. Who wins? Magnus Carlsen vs Denis Lazavik · 2nd in Chess gang"
    assert told["quiet"]["quiet"] is True and told["acct-asha"]["loud"] is True and told["acct-asha"]["kind"] == "called"
    predictions_push.run()
    assert heard(pushed) == {}  # once a question


def test_a_series_maps_replace_each_other_and_only_the_first_makes_a_sound(db, redis, pushed):  # noqa: F811
    from sports_follow.adapters import pandascore_esports as ps
    from sports_follow.models import Event

    from .test_pandascore import NRG, SEN, _game, _match
    from .test_predictions_db import SERIES

    db.add(Event(id=5, key=SERIES, sport="Valorant", title="Sentinels vs NRG", start_utc=datetime.now(timezone.utc) + timedelta(hours=3), status="armed",
                 live_binding={"adapter": "pandascore_esports", "locator": {"match": "777"}}))
    db.commit()
    devices(db, "fan")
    poll(5, ps.parse_match(_match("not_started")), "pandascore_esports")
    qs = {q.ask: q for q in db.scalars(select(Question))}
    predictions.pick(db, "fan", qs["map1"].id, str(SEN))
    predictions.pick(db, "fan", qs["map2"].id, str(SEN))
    db.commit()
    poll(5, ps.parse_match(_match("running", 1, 0, [_game(1, "finished", SEN), _game(2, "running"), _game(3, "not_started")])), "pandascore_esports")
    predictions_push.run()
    first = heard(pushed)["fan"]
    poll(5, ps.parse_match(_match("running", 1, 1, [_game(1, "finished", SEN), _game(2, "finished", NRG), _game(3, "running")])), "pandascore_esports")
    predictions_push.run()
    second = heard(pushed)["fan"]
    assert (first["title"], first["loud"]) == ("You got it right: Sentinels", True)
    assert (second["title"], second["loud"], second["tag"]) == ("NRG: not this time", False, first["tag"])  # replaces it, quietly
