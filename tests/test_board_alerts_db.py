"""A chess board two of a fan's players are on (BHA-378): a board is a fixture of each of its players, so an event of
each, polled apart, and each poll finds the board's moments from its own side. Alerts, live cards and mutes take the
two for one game: a fan who follows both players hears each moment once, from the side it went toward, on one
notification and one live card, and muting either event mutes the game; a fan who follows one of them hears it from
their player's side, as before. A side tells a moment for the other only where its own fixture is here and being
polled; else both do, twice beating never. The recorded Esports World Cup grand final (Carlsen v Lazavik,
tests/test_predictions.py's board) through the poller, against a throwaway database at alembic head (skipped without
one; see tests/test_players.py for setting it up)."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import bus, engine, livecards, notify, server, structured
from sports_follow.adapters import KNOWN, Snapshot, lichess_chess
from sports_follow.models import Event, EventPlayer, Follow, NotificationMute, Player, PlayerIdentity, PushSubscription

from .test_fcm import fake_fcm  # noqa: F401  (the fixture)
from .test_match_follow import Store
from .test_new_adapters import CARLSEN, LAZAVIK, load
from .test_predictions import NOW, board

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

ROUND = load("lichess/round-final")["round"]["id"]
SOURCE = KNOWN["lichess_chess"]
# Each player's fixture of the board, its event and the player in it: Carlsen (player 5) has White in game 1.
SIDES = {1: (5, CARLSEN), 2: (6, LAZAVIK)}
FANS = {"both": (5, 6), "carlsen": (5,), "lazavik": (6,)}


@pytest.fixture
def db(monkeypatch):
    engine_ = create_engine(URL or "postgresql+psycopg://")
    with engine_.begin() as c:
        c.execute(text("TRUNCATE player, event, event_state, event_player, player_line, follow, match_follow, moment, notification, notification_mute, "
                       "push_subscription, player_identity, player_card, prediction_question, prediction_pick RESTART IDENTITY CASCADE"))
    made = sessionmaker(bind=engine_, expire_on_commit=False)

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

    for module in (structured, notify, livecards):
        monkeypatch.setattr(module, "session", unit)
    s = made()
    start = datetime.now(timezone.utc)
    s.add_all([Player(id=5, slug="magnus-carlsen", name="Magnus Carlsen", sport="Chess"), Player(id=6, slug="denis-lazavik", name="Denis Lazavik", sport="Chess")])
    for event_id, (player_id, fide) in SIDES.items():
        s.add(Event(id=event_id, key=f"lichess_chess:{ROUND}-{fide}", sport="Chess", title="Magnus Carlsen vs Denis Lazavik", competition="Esports World Cup 2026",
                    start_utc=start, status="scheduled", live_binding=binding(fide)))
        s.add(PlayerIdentity(player_id=player_id, system="lichess_chess", external_id=fide, confidence=1.0))
    s.flush()
    s.add_all([EventPlayer(event_id=e, player_id=p) for e, (p, _) in SIDES.items()])
    s.add_all([Follow(fan_id=fan, player_id=p, alert_rules={}) for fan, players in FANS.items() for p in players])
    s.commit()
    yield s
    s.close()
    engine_.dispose()


def binding(fide: str) -> dict:
    return {"adapter": "lichess_chess", "locator": {"round": ROUND, "fide": fide}, "cadence": 15}


@pytest.fixture
def polls(monkeypatch):
    """Polls applied at once, each noted when it ran as the poller notes it (polled all along), their moments sent in
    the foreground, the engine's evaluations as each snapshot carries them; every push as (fan, title, tag, body),
    every toast's moment, and Redis."""
    pushed: list[tuple[str, str, str, str]] = []
    toasts: list[dict] = []
    redis = Store()

    def polled(event_id: int, cadence: int) -> bool:
        redis.set(f"polled_at:{event_id}", str(int(time.time())))
        return True

    monkeypatch.setattr(structured, "_polled", polled)
    monkeypatch.setattr(engine, "enabled", lambda: False)
    monkeypatch.setattr(notify, "dispatch", notify.send)
    monkeypatch.setattr(notify, "push", lambda endpoint, p256dh, auth, payload, ttl, topic=None: pushed.append((endpoint.rsplit("/", 1)[1], payload["title"], payload["tag"], payload["body"])))
    monkeypatch.setattr(notify.bus, "publish", lambda player_id, event: toasts.append(event["moment"]))
    monkeypatch.setattr(bus, "sync_redis", lambda: redis)
    return SimpleNamespace(pushed=pushed, toasts=toasts, redis=redis)


def quiet(redis: Store, event_id: int) -> None:
    """A side's fixture not polled for longer than a poll gap (a restart, its source failing a while)."""
    redis.set(f"polled_at:{event_id}", str(int(time.time()) - structured.POLL_GAP - 60))


def poll(event_id: int, snap: Snapshot, band: int | None = None, move: int = 40) -> None:
    """One poll of a side's fixture: its own player's board, with the engine's settled band from White's side."""
    player_id, fide = SIDES[event_id]
    if band is not None:
        snap.state["eval"] = {"cp": band * 200, "band": band, "raw": band, "told": band if abs(band) == 2 else 0, "move": move, "at": move}
    structured.apply_poll(event_id, binding(fide), SOURCE, [(player_id, fide)], snap)


def side(event_id: int, **kwargs) -> Snapshot:
    return board(fide=SIDES[event_id][1], **kwargs)


def phones(db, transport: str = "webpush") -> None:
    for fan in FANS:
        endpoint = f"https://fcm.googleapis.com/fcm/send/{fan}" if transport == "webpush" else f"{fan}:APA91b" + "x" * 40
        db.add(PushSubscription(fan_id=fan, transport=transport, endpoint=endpoint, p256dh="k", auth="a"))
    db.commit()


def heard(pushed: list[tuple[str, str, str, str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for fan, title, *_ in pushed:
        out.setdefault(fan, []).append(title)
    pushed.clear()
    return out


def test_a_fan_of_both_players_hears_each_moment_of_their_board_once_from_the_side_it_went_toward(db, polls):
    pushed, toasts = polls.pushed, polls.toasts
    phones(db)
    # The pairing's out; each side's fixture is polled before the round starts.
    for event_id in (2, 1):
        poll(event_id, side(event_id, status="*"))
    assert pushed == []
    # The start: Lazavik's fixture sees it first. A fan of both hears it once, from White's side, never waiting for it.
    poll(2, side(2, status="*", ongoing=True), band=0)
    assert heard(pushed) == {"lazavik": ["Denis Lazavik is playing"]}
    poll(1, side(1, status="*", ongoing=True), band=0)
    assert heard(pushed) == {"carlsen": ["Magnus Carlsen is playing"], "both": ["Magnus Carlsen is playing"]}
    # Carlsen pulls ahead: Lazavik's side finds it first, from his side. Carlsen's words tell it to a fan of both.
    poll(2, side(2, status="*", ongoing=True), band=2, move=41)
    assert heard(pushed) == {"lazavik": ["Denis Lazavik is in trouble"]}
    poll(1, side(1, status="*", ongoing=True), band=2, move=41)
    winning = [p for p in pushed if p[0] == "both"]
    assert heard(pushed) == {"carlsen": ["Magnus Carlsen is winning"], "both": ["Magnus Carlsen is winning"]}
    # Turned around, Carlsen's side first: now it's Lazavik's to tell, on the same notification, with the story so far.
    poll(1, side(1, status="*", ongoing=True), band=-2, move=50)
    assert heard(pushed) == {"carlsen": ["Turnaround: Magnus Carlsen is in trouble"]}
    poll(2, side(2, status="*", ongoing=True), band=-2, move=50)
    turned = [p for p in pushed if p[0] == "both"]
    assert heard(pushed) == {"lazavik": ["Turnaround: Denis Lazavik is winning"], "both": ["Turnaround: Denis Lazavik is winning"]}
    [(_, _, tag, _)], [(_, _, again, body)] = winning, turned
    assert again == tag and body.split("\n")[1:] == ["Magnus Carlsen is winning", "Magnus Carlsen is playing"]
    # The result (Carlsen won game 1), hours of a classical game later: once, the winner's, its story both sides' pushes.
    db.execute(text("UPDATE notification SET created_at = created_at - interval '3 hours'"))
    db.commit()
    poll(2, side(2, game=0))
    assert heard(pushed) == {"lazavik": ["Denis Lazavik: Lost with Black vs Magnus Carlsen"]}
    poll(1, side(1, game=0))
    [(_, _, last, body)] = [p for p in pushed if p[0] == "both"]
    assert last == tag and body.split("\n")[1:] == ["Turnaround: Denis Lazavik is winning", "Magnus Carlsen is winning", "Magnus Carlsen is playing"]
    assert heard(pushed) == {"carlsen": ["Magnus Carlsen: Won with White vs Denis Lazavik"], "both": ["Magnus Carlsen: Won with White vs Denis Lazavik"]}

    # The Alerts list says each once too, as the push did; a fan of one of them still sees all of their player's.
    def listed(fan: str) -> list[str]:
        return [i["title"] for i in server.activity(None, 50, fan, db)["items"]]

    assert listed("both") == ["Magnus Carlsen: Won with White vs Denis Lazavik", "Turnaround: Denis Lazavik is winning", "Magnus Carlsen is winning", "Magnus Carlsen is playing"]
    assert listed("lazavik") == ["Denis Lazavik: Lost with Black vs Magnus Carlsen", "Turnaround: Denis Lazavik is winning", "Denis Lazavik is in trouble", "Denis Lazavik is playing"]
    # And the app's toasts say whose side tells each (its voice), so one open on a fan of both shows it once too.
    assert [(m["title"], m.get("voice")) for m in toasts if m["player_id"] == 6] == [
        ("Denis Lazavik is playing", 5), ("Denis Lazavik is in trouble", 5), ("Turnaround: Denis Lazavik is winning", None), ("Denis Lazavik: Lost with Black vs Magnus Carlsen", 5)]


def test_muting_either_fixture_of_a_board_mutes_the_game(db, polls):
    pushed = polls.pushed
    phones(db)
    for event_id in (1, 2):
        poll(event_id, side(event_id, status="*"))
        poll(event_id, side(event_id, status="*", ongoing=True), band=0)
    pushed.clear()
    # The start reached the fan of both from Carlsen's fixture; they mute the game from Lazavik's.
    server.mute(2, "both", db)
    assert sorted(db.scalars(select(NotificationMute.event_id).where(NotificationMute.fan_id == "both"))) == [1, 2]
    for event_id in (1, 2):
        poll(event_id, side(event_id, status="*", ongoing=True), band=2, move=41)
    assert heard(pushed) == {"carlsen": ["Magnus Carlsen is winning"], "lazavik": ["Denis Lazavik is in trouble"]}
    # Unmuted from Carlsen's: back on both.
    server.unmute(1, "both", db)
    assert db.scalars(select(NotificationMute.event_id).where(NotificationMute.fan_id == "both")).all() == []
    poll(1, side(1, game=0))
    assert heard(pushed)["both"] == ["Magnus Carlsen: Won with White vs Denis Lazavik"]


def test_a_fan_of_both_players_has_one_live_card_for_their_board(db, polls, fake_fcm, monkeypatch):  # noqa: F811
    phones(db, "fcm")
    monkeypatch.setattr(livecards, "_queue", lambda event_id, kind, made, board=None: livecards.send(event_id, kind, made, board))  # at once

    def cards() -> dict[str, list[tuple[str, list[str]]]]:
        out: dict[str, list[tuple[str, list[str]]]] = {}
        for m in fake_fcm.sent:
            if m["data"].get("type") == "live":
                out.setdefault(m["token"].split(":")[0], []).append((m["data"]["event_id"], json.loads(m["data"]["players"])))
        fake_fcm.sent.clear()
        return out

    for event_id in (2, 1):
        poll(event_id, side(event_id, status="*"))
    fake_fcm.sent.clear()
    poll(2, side(2, status="*", ongoing=True), band=0)
    poll(1, side(1, status="*", ongoing=True), band=0)
    # Each side's fans have their fixture's card; a fan of both has one, its first player's (Carlsen's), naming both,
    # whichever side polled first.
    assert cards() == {"lazavik": [("2", ["Denis Lazavik · Black vs Magnus Carlsen"])], "carlsen": [("1", ["Magnus Carlsen · White vs Denis Lazavik"])],
                       "both": [("1", ["Denis Lazavik · Black vs Magnus Carlsen", "Magnus Carlsen · White vs Denis Lazavik"])]}
    # Where Carlsen's fixture has stopped being polled, theirs comes from Lazavik's: never none.
    quiet(polls.redis, 1)
    again(polls.redis)
    poll(2, side(2, status="*", ongoing=True), band=0, move=41)
    assert cards() == {"lazavik": [("2", ["Denis Lazavik · Black vs Magnus Carlsen"])], "both": [("2", ["Denis Lazavik · Black vs Magnus Carlsen"])]}
    # Muted from the other side's push, the game sends them no card at all.
    server.mute(2, "both", db)
    again(polls.redis)
    poll(2, side(2, status="*", ongoing=True), band=2, move=42)
    poll(1, side(1, status="*", ongoing=True), band=2, move=42)
    assert set(cards()) == {"lazavik", "carlsen"}


def again(redis: Store) -> None:
    """Forget what each game's live card last showed, so the next poll sends it again (as REFRESH would, later)."""
    for key in [k for k in redis if k.startswith("livecard:")]:
        del redis[key]


def test_a_fan_of_both_hears_every_moment_where_only_one_sides_fixture_is_here(db, polls):
    # Lazavik's fixture of the board isn't here (his tournaments weren't all found, or haven't been read yet): the
    # moments his side would tell come from Carlsen's. Game 2 of the grand final: Lazavik had White, and it was drawn.
    db.execute(text("DELETE FROM event WHERE id = 2"))
    db.commit()
    phones(db)
    poll(1, side(1, game=1, status="*"))
    poll(1, side(1, game=1, status="*", ongoing=True), band=0)  # White's to tell: Lazavik's
    poll(1, side(1, game=1, status="*", ongoing=True), band=2, move=41)  # White winning: Lazavik's too
    poll(1, side(1, game=1))  # a draw: White's
    assert heard(polls.pushed)["both"] == ["Magnus Carlsen is playing", "Magnus Carlsen is in trouble", "Magnus Carlsen: Drew with Black vs Denis Lazavik"]


def test_a_moment_whose_tellers_fixture_has_stopped_polling_still_arrives(db, polls):
    phones(db)
    for event_id in (1, 2):
        poll(event_id, side(event_id, status="*"))
        poll(event_id, side(event_id, status="*", ongoing=True), band=0)
    polls.pushed.clear()
    # Carlsen's fixture hasn't been polled for a while: it may never find his side of what comes, so Lazavik's says it.
    quiet(polls.redis, 1)
    poll(2, side(2, status="*", ongoing=True), band=2, move=41)
    assert heard(polls.pushed) == {"lazavik": ["Denis Lazavik is in trouble"], "both": ["Denis Lazavik is in trouble"]}
    assert polls.toasts[-1]["title"] == "Denis Lazavik is in trouble" and "voice" not in polls.toasts[-1]  # an open app shows it too


def test_a_mini_match_is_one_notification_through_its_games(db, polls):
    # A knockout's mini-match is several games on one board, each with an id of its own; its fixtures, one a player,
    # last the whole match, and so does its notification.
    def match(event_id: int, played: int, status: str = "live") -> Snapshot:
        data = load("lichess/round-final")
        games = [*data["games"][:played], {**data["games"][played], "status": "*"}]
        to_come = status == "scheduled"
        rnd = {**data["round"], "finished": False, "ongoing": not to_come, "startsAt": int((NOW + timedelta(days=1 if to_come else -1)).timestamp() * 1000)}
        return lichess_chess.parse_board({**data, "round": rnd, "games": games}, SIDES[event_id][1], NOW)

    phones(db)
    for event_id in (1, 2):
        poll(event_id, match(event_id, 0, "scheduled"))
        poll(event_id, match(event_id, 0), band=0)
    poll(1, match(1, 0), band=2, move=41)  # game 1: Carlsen, with White, pulls ahead
    poll(2, match(2, 0), band=2, move=41)
    for event_id in (1, 2):
        poll(event_id, match(event_id, 1), band=0)  # game 2 starts: a new board id
    poll(2, match(2, 1), band=-2, move=30)  # game 2: Carlsen, with Black, pulls ahead
    poll(1, match(1, 1), band=-2, move=30)
    tags = {(fan, title): tag for fan, title, tag, _ in polls.pushed}
    assert {tags[("carlsen", "Magnus Carlsen is winning")], tags[("lazavik", "Denis Lazavik is in trouble")], tags[("both", "Magnus Carlsen is winning")]} == {"e1"}
    assert [title for fan, title, *_ in polls.pushed if fan == "both"] == ["Magnus Carlsen is playing", "Magnus Carlsen is winning", "Magnus Carlsen is winning"]
    assert len({tag for *_, tag, _ in polls.pushed}) == 1  # every push of the match, either side's, on one notification
