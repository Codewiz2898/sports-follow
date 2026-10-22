"""Predictions' pushes against real rows (predictions_push.py, BHA-321): "Make your pick" before a campaign's game to
the fans who follow someone in it and haven't picked, as each set their alerts; how each pick went once its question
settles, with the fan's best league place; each once; and moments' pushes as they were. Against the throwaway
database tests/test_predictions_db.py uses (skipped without one); the push services are made up."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from sports_follow import accounts, campaigns, notify, predictions, predictions_push
from sports_follow.adapters import pandascore_esports as ps
from sports_follow.models import (Account, Athlete, Campaign, Event, EventPlayer, EventTeam, Follow, MatchFollow, Notification, NotificationMute, Player, PlayerIdentity, PushSubscription,
                                  Question, Team, TeamFollow)
from sports_follow.moments import Found

from .test_new_adapters import CARLSEN, LAZAVIK
from .test_pandascore import NRG, SEN, _game, _match
from .test_predictions import board
from .test_predictions_db import SERIES, URL, chess_board, db, poll, redis, running  # noqa: F401  (the fixtures)

pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")
TOPIC = re.compile(r"[A-Za-z0-9_-]{1,32}")  # what a Web Push topic may be (RFC 8030, 5.4)


@pytest.fixture
def pushed(db, monkeypatch):  # noqa: F811
    """Every push the services were handed, as (device endpoint, payload, topic); each taken."""
    sent: list[tuple[str, dict, str | None]] = []

    def deliver(device, payload, ttl, topic=None):
        sent.append((device[2], payload, topic))
        return None

    monkeypatch.setattr(notify, "deliver", deliver)
    monkeypatch.setattr(predictions_push, "session", predictions.session)
    return sent


def devices(db, *fans: str) -> None:  # noqa: F811
    db.add_all([PushSubscription(fan_id=f, endpoint=f"https://fcm.googleapis.com/fcm/send/{f}", p256dh="k", auth="a") for f in fans])
    db.commit()


def heard(pushed: list) -> dict[str, dict]:
    """Who got a push, and what it said; the topics are checked as a push service would."""
    out = {}
    for endpoint, payload, topic in pushed:
        assert topic is None or TOPIC.fullmatch(topic), topic
        out[endpoint.rsplit("/", 1)[1]] = payload
    pushed.clear()
    return out


def campaign(db, start: datetime, players: list[str] | None = None, teams: list[dict] | None = None) -> None:  # noqa: F811
    db.add(Campaign(slug="final", sport="chess", title="The final", players=players or [], teams=teams or [], lines=[], games=[{"start_utc": start.isoformat(), "label": "Game 1"}]))
    db.commit()


def carlsen(db) -> None:  # noqa: F811
    """Carlsen as a campaign's players are here: made from his source's pick, his registry id his "wikidata" identity
    (registry.link), not on the player (only one the research agent built has it there)."""
    db.add(Player(id=1, slug="carlsen", name="Magnus Carlsen", sport="Chess"))
    db.flush()
    db.add_all([PlayerIdentity(player_id=1, system="wikidata", external_id="Q106807", confidence=1.0), EventPlayer(event_id=1, player_id=1)])
    db.commit()


def test_make_your_pick_goes_once_before_a_campaigns_game_to_who_should_hear_it(db, redis, pushed):  # noqa: F811
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=90)
    chess_board(db, start)
    carlsen(db)
    campaign(db, start, players=["Q106807"])
    for fan, level in (("asha", "key"), ("ravi", "results"), ("meera", "everything"), ("dev", "key"), ("kiran", "off")):
        db.add(Follow(fan_id=fan, player_id=1, alert_rules={"level": level}))
    db.add_all([MatchFollow(fan_id="sana", event_id=2, alert_rules={}, keys=[]), NotificationMute(fan_id="meera", event_id=1)])
    db.commit()
    devices(db, "asha", "ravi", "meera", "dev", "kiran", "sana")
    poll(1, board(status="*"))
    poll(2, board(status="*", fide=LAZAVIK))
    q = db.scalars(select(Question)).one()
    predictions.pick(db, "dev", q.id, CARLSEN)
    db.commit()
    assert predictions_push.run(start - predictions_push.LEAD - timedelta(minutes=5)) == 0  # not yet
    predictions_push.run(start - timedelta(minutes=80))
    told = heard(pushed)
    # Asha (key) and Sana (follows the game itself); not Ravi (results only), Meera (muted it), Dev (picked), Kiran (off).
    assert sorted(told) == ["asha", "sana"]
    assert told["asha"]["title"] == "Make your pick: Magnus Carlsen vs Denis Lazavik" and told["asha"]["body"] == "Who wins? Picks close when it starts, in 80 minutes."
    assert (told["asha"]["kind"], told["asha"]["tag"], told["asha"]["url"]) == ("pick", "call-lichess_chess:mJnv1YBU", f"/match/{q.match}")
    assert {(n.fan_id, n.kind, n.delivered) for n in db.scalars(select(Notification))} == {("asha", "pick", 1), ("sana", "pick", 1)}
    predictions_push.run(start - timedelta(minutes=70))
    assert heard(pushed) == {}  # once a game


def test_a_postponed_game_gets_no_reminder_till_its_back_on(db, redis, pushed):  # noqa: F811
    """BUG-109: "Make your pick" went for a campaign's game its source had postponed (its date kept), and its call was
    open. No reminder now while it's postponed, and none used up: back on, it's reminded, its time said truly (it said
    "in 2 hours" at 90 minutes)."""
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=115)
    chess_board(db, start)
    carlsen(db)
    campaign(db, start, players=["Q106807"])
    db.add(Follow(fan_id="asha", player_id=1, alert_rules={"level": "key"}))
    db.commit()
    devices(db, "asha")
    poll(1, board(status="*"))
    db.execute(text("UPDATE event SET status = 'postponed'"))
    db.commit()
    predictions_push.run(start - timedelta(minutes=110))
    assert heard(pushed) == {} and db.scalars(select(Question)).one().reminded_at is None
    db.execute(text("UPDATE event SET status = 'scheduled'"))  # back on, at its time
    db.commit()
    predictions_push.run(start - timedelta(minutes=100))
    assert heard(pushed)["asha"]["body"] == "Who wins? Picks close when it starts, in 1 hour 40 minutes."
    assert predictions_push._until(start, start - timedelta(minutes=90)) == "1 hour 30 minutes"  # pyright: ignore[reportPrivateUsage]
    assert predictions_push._until(start, start - timedelta(minutes=120)) == "2 hours"  # pyright: ignore[reportPrivateUsage]


def test_a_campaigns_players_are_found_as_its_page_finds_them(db):  # noqa: F811
    """A campaign's players by their registry ids (BUG-190): one made from a source's pick holds theirs as a "wikidata"
    identity (Gukesh), one bound only by the id the registry has for them in the source (Sindarov), one the research
    agent built has it on the player itself (Rapport); a two-sport athlete's player in another sport isn't this
    campaign's (Bumrah's cricket page holds his registry id: his chess page has it on the player)."""
    db.execute(text("TRUNCATE athlete RESTART IDENTITY CASCADE"))
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=90)
    db.add_all([Athlete(qid=qid, sport=sport, name=name, search_text=name.lower(), ids=ids) for qid, sport, name, ids in (
        ("Q56026072", "chess", "Gukesh D", {}), ("Q64010574", "chess", "Javokhir Sindarov", {"lichess_chess": "14205483"}),
        ("Q2104735", "chess", "Richard Rapport", {}), ("Q16227998", "cricket", "Jasprit Bumrah", {}), ("Q16227998", "chess", "Jasprit Bumrah", {}))])
    db.add_all([Player(id=1, slug="gukesh-d", name="Gukesh D", sport="Chess"), Player(id=2, slug="javokhir-sindarov", name="Javokhir Sindarov", sport="Chess"),
                Player(id=3, slug="richard-rapport", name="Richard Rapport", sport="Chess", wikidata_qid="Q2104735"),
                Player(id=4, slug="jasprit-bumrah", name="Jasprit Bumrah", sport="Cricket"), Player(id=5, slug="jasprit-bumrah-chess", name="Jasprit Bumrah", sport="Chess", wikidata_qid="Q16227998"),
                Player(id=6, slug="magnus-carlsen", name="Magnus Carlsen", sport="Chess")])
    db.flush()
    db.add_all([PlayerIdentity(player_id=1, system="wikidata", external_id="Q56026072", confidence=1.0),
                PlayerIdentity(player_id=2, system="lichess_chess", external_id="14205483", confidence=1.0),
                PlayerIdentity(player_id=4, system="wikidata", external_id="Q16227998", confidence=1.0),
                PlayerIdentity(player_id=6, system="wikidata", external_id="Q106807", confidence=1.0)])  # not in it
    db.commit()
    campaign(db, start, players=["Q56026072", "Q64010574", "Q2104735", "Q16227998"])
    assert campaigns.player_ids(db, start) == {1, 2, 3, 5}
    assert campaigns.player_ids(db, start + timedelta(days=1)) == set()  # no game of its schedule then


def test_a_campaign_players_game_off_its_schedule_gets_no_reminder(db, redis, pushed):  # noqa: F811
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=90)
    chess_board(db, start)
    carlsen(db)
    campaign(db, start + timedelta(days=2), players=["Q106807"])  # the campaign's game is another day: this is a warm-up event
    db.add(Follow(fan_id="asha", player_id=1, alert_rules={}))
    db.commit()
    devices(db, "asha")
    poll(1, board(status="*"))
    predictions_push.run(start - timedelta(minutes=80))
    assert heard(pushed) == {} and db.scalars(select(Question)).one().reminded_at is not None


def test_a_series_whole_team_campaign_reminds_its_team_fans_once_for_map_one(db, redis, pushed):  # noqa: F811
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=90)
    db.add_all([Event(id=5, key=SERIES, sport="Valorant", title="Sentinels vs NRG", start_utc=start, status="armed", live_binding={"adapter": "pandascore_esports", "locator": {"match": "777"}}),
                Player(id=2, slug="tenz", name="TenZ", sport="Valorant"),
                Team(id=1, name="Sentinels", sport="valorant", system="pandascore_esports", external_id=str(SEN))])
    db.flush()
    db.add_all([PlayerIdentity(player_id=2, system="pandascore_esports", external_id="31001", confidence=1.0), EventPlayer(event_id=5, player_id=2),
                EventTeam(event_id=5, team_id=1), TeamFollow(fan_id="sen-fan", team_id=1, alert_rules={})])
    db.commit()
    campaign(db, start, teams=[{"system": "pandascore_esports", "team_id": str(SEN), "name": "Sentinels", "players": [{"athlete_id": "31001", "name": "TenZ"}]}])
    devices(db, "sen-fan")
    poll(5, ps.parse_match(_match("not_started")), "pandascore_esports")
    predictions_push.run(start - timedelta(minutes=80))
    told = heard(pushed)
    assert list(told) == ["sen-fan"] and told["sen-fan"]["body"].startswith("Who takes map 1?")  # one a series: its first map's
    predictions_push.run(start - timedelta(minutes=60))
    assert heard(pushed) == {}


def test_how_a_pick_went_goes_once_to_who_picked_with_their_league_place(db, redis, pushed):  # noqa: F811
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    chess_board(db, start)
    carlsen(db)
    poll(1, board(status="*"))
    q = db.scalars(select(Question)).one()
    for fan, option in (("acct-asha", CARLSEN), ("acct-ravi", "draw"), ("acct-dev", CARLSEN), ("quiet", CARLSEN), ("acct-kiran", "draw")):
        predictions.pick(db, fan, q.id, option)
    gang = predictions.create(db, "acct-asha", "Chess gang", "Asha")
    predictions.join(db, "acct-ravi", gang.code, "Ravi")
    # Ravi hears results only (he still hears his), Kiran set Carlsen to off, Dev muted the game.
    db.add_all([Follow(fan_id="acct-ravi", player_id=1, alert_rules={"level": "results"}), Follow(fan_id="acct-kiran", player_id=1, alert_rules={"level": "off"}),
                NotificationMute(fan_id="acct-dev", event_id=1)])
    db.commit()
    devices(db, "acct-asha", "acct-ravi", "acct-dev", "quiet", "acct-kiran")
    db.execute(text("UPDATE push_subscription SET timezone = 'UTC', quiet_start = '00:00', quiet_end = '23:59' WHERE fan_id = 'quiet'"))
    db.commit()
    poll(1, board(0))  # the recorded result: Carlsen won
    predictions_push.run()
    told = heard(pushed)
    assert sorted(told) == ["acct-asha", "acct-ravi", "quiet"]
    assert told["acct-asha"]["title"] == "You got it right: Magnus Carlsen"
    assert told["acct-asha"]["body"] == "Who wins? Magnus Carlsen vs Denis Lazavik · +40 points · 1st in Chess gang"  # 3 of 5 right
    assert told["acct-ravi"]["title"] == "Magnus Carlsen: not this time"
    assert told["acct-ravi"]["body"] == "You said Draw. Who wins? Magnus Carlsen vs Denis Lazavik"  # no points yet: no place (BUG-113)
    assert told["quiet"]["quiet"] is True and told["acct-asha"]["loud"] is True and told["acct-asha"]["kind"] == "called"
    predictions_push.run()
    assert heard(pushed) == {}  # once a question
    # Corrected at the source (an arbiter's ruling): settled again, and told again.
    db.expire_all()
    predictions.settle(db, [db.get_one(Question, q.id)], predictions.sports.of("Chess"), "final", board(1).state, datetime.now(timezone.utc))
    db.commit()
    predictions_push.run()
    assert heard(pushed)["acct-ravi"]["title"] == "You got it right: Draw"


def test_how_a_pick_went_says_no_place_with_no_points_or_everyone_level(db, redis, pushed):  # noqa: F811
    """BUG-113: in a league where everyone had 0, a wrong call was told "· 1st in <league>" (members level on points
    share a place). A place is said only once the fan has points, and isn't level with everyone in it."""
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    chess_board(db, start)
    carlsen(db)
    poll(1, board(status="*"))
    q = db.scalars(select(Question)).one()
    for fan, option in (("acct-asha", "draw"), ("acct-ravi", "draw"), ("acct-dev", CARLSEN), ("acct-sita", CARLSEN)):
        predictions.pick(db, fan, q.id, option)
    for maker, member, name in (("acct-asha", "acct-ravi", "New gang"), ("acct-dev", "acct-sita", "Pair")):
        made = predictions.create(db, maker, name, maker[5:].title())
        predictions.join(db, member, made.code, member[5:].title())
    db.commit()
    devices(db, "acct-asha", "acct-dev")
    poll(1, board(0))  # the recorded result: Carlsen won
    predictions_push.run()
    told = heard(pushed)
    assert told["acct-asha"]["body"] == "You said Draw. Who wins? Magnus Carlsen vs Denis Lazavik"  # 0 points: not "1st"
    assert told["acct-dev"]["body"] == "Who wins? Magnus Carlsen vs Denis Lazavik · +50 points"  # level with Sita, the only other


def test_a_call_made_on_a_phone_before_it_signed_in_mid_game_is_told_there(db, redis, pushed):  # noqa: F811
    """BUG-091 (BHA-413): a fan calls the game on their phone, it starts, and they sign in (to join a friend's league).
    How it went reaches that phone, now the account's. The pick used to stay with the phone's anonymous id, whose
    devices had gone to the account, so no one was told."""
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)
    chess_board(db, start)
    carlsen(db)
    poll(1, board(status="*"))
    q = db.scalars(select(Question)).one()
    predictions.pick(db, "phone", q.id, CARLSEN)
    db.add(Account(id=1, email="sana@example.com", fan_id="acct-sana"))
    db.commit()
    devices(db, "phone")
    poll(1, board(status="*", ongoing=True))  # the first move: it closes
    accounts.sign_in(db, 1, "phone")
    db.commit()
    poll(1, board(0))  # the recorded result: Carlsen won
    predictions_push.run()
    told = heard(pushed)
    assert list(told) == ["phone"] and told["phone"]["title"] == "You got it right: Magnus Carlsen"


def test_a_map_voided_tells_no_one_till_a_winner_comes(db, redis, pushed):  # noqa: F811
    db.add(Event(id=5, key=SERIES, sport="Valorant", title="Sentinels vs NRG", start_utc=datetime.now(timezone.utc) + timedelta(hours=3), status="armed",
                 live_binding={"adapter": "pandascore_esports", "locator": {"match": "777"}}))
    db.commit()
    devices(db, "fan")
    poll(5, ps.parse_match(_match("not_started")), "pandascore_esports")
    qs = {q.ask: q for q in db.scalars(select(Question))}
    predictions.pick(db, "fan", qs["map3"].id, str(SEN))
    db.commit()
    # The source called it over with map 3 unplayed: voided, and nobody's told.
    poll(5, ps.parse_match(_match("finished", 2, 0, [_game(1, "finished", SEN), _game(2, "finished", SEN)], winner_id=SEN)), "pandascore_esports")
    predictions_push.run()
    db.expire_all()
    assert heard(pushed) == {} and db.get_one(Question, qs["map3"].id).status == "void"
    # Then it lists map 3's winner after all: settled, and told.
    poll(5, ps.parse_match(_match("finished", 2, 1, [_game(1, "finished", SEN), _game(2, "finished", NRG), _game(3, "finished", SEN)], winner_id=SEN)), "pandascore_esports")
    predictions_push.run()
    assert heard(pushed)["fan"]["title"] == "You got it right: Sentinels"


def test_a_series_maps_replace_each_other_and_only_the_first_makes_a_sound(db, redis, pushed):  # noqa: F811
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


def test_a_reminder_leaves_the_games_own_alerts_as_they_were(db, redis, pushed):  # noqa: F811
    start = datetime.now(timezone.utc).replace(microsecond=0)
    chess_board(db, start)
    carlsen(db)
    db.add(Follow(fan_id="asha", player_id=1, alert_rules={}))
    db.add(Notification(fan_id="asha", event_id=1, player_id=None, moment_ids=[], kind="pick", lines=["Make your pick: Magnus Carlsen vs Denis Lazavik"], loud=True,
                        devices=1, delivered=1, created_at=start - timedelta(minutes=110)))
    db.commit()
    devices(db, "asha")
    [moment] = notify.record(db, 1, 1, [Found("start", "key", "Magnus Carlsen is playing", "Round 1 · vs Denis Lazavik")])
    db.commit()
    notify.send([moment])
    push = heard(pushed)["asha"]
    assert push["loud"] is True and "Make your pick" not in push["body"]  # its game's first moment, as without the reminder
