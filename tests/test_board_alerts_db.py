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
from sports_follow.models import Event, EventPlayer, Follow, MatchFollow, Moment, NotificationMute, Player, PlayerIdentity, PushSubscription

from .test_fcm import fake_fcm  # noqa: F401  (the fixture)
from .test_match_follow import Store
from .test_new_adapters import CARLSEN, LAZAVIK, load, tiebreak
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


class Redis(Store):
    """Store, with the hash notify keeps the moments left to a board's other side in (notify.HELD)."""

    def __init__(self):
        super().__init__()
        self.hashes: dict[str, dict[str, str]] = {}

    def hset(self, name: str, key: str, value: str) -> int:
        self.hashes.setdefault(name, {})[str(key)] = value
        return 1

    def hget(self, name: str, key: str) -> str | None:
        return self.hashes.get(name, {}).get(str(key))

    def hmget(self, name: str, keys: list[str]) -> list:
        return [self.hget(name, k) for k in keys]

    def hgetall(self, name: str) -> dict[str, str]:
        return dict(self.hashes.get(name, {}))

    def hdel(self, name: str, *keys: str) -> int:
        return sum(self.hashes.get(name, {}).pop(str(k), None) is not None for k in keys)

    def expire(self, name: str, seconds: int) -> int:
        return 1

    def eval(self, script: str, numkeys: int, key: str, was: str, new: str, seconds: int) -> str | None:
        assert script == engine.SETTLE  # the board's settle: kept only if it's still what it was settled from
        if (self.get(key) or "") == was:
            self.set(key, new, ex=seconds)
            return new
        return self.get(key)


@pytest.fixture
def polls(monkeypatch):
    """Polls applied at once, each noted when it ran as the poller notes it (polled all along), their moments sent in
    the foreground, the engine's evaluations as each snapshot carries them; every push as (fan, title, tag, body),
    every toast's moment, and Redis."""
    pushed: list[tuple[str, str, str, str]] = []
    toasts: list[dict] = []
    redis = Redis()

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


def quiet(redis: Redis, event_id: int) -> None:
    """A side's fixture whose last poll to commit was three minutes ago: inside a poll gap, but stalled."""
    redis.set(f"told_at:{event_id}", str(int(time.time()) - 180))


def test_a_side_speaks_for_the_other_only_while_its_next_poll_still_finds_the_moment():
    # A fixture that told its side TELLS_WITHIN ago may stall up to three of its source's rounds more and still find the
    # moment against the poll before (a gap past POLL_GAP starts a new baseline, which finds none).
    assert structured.TELLS_WITHIN <= structured.POLL_GAP - 3 * SOURCE.live_cadence


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
        """Each fan's live cards, by event and whom they name, and ends ("end", the card it takes down)."""
        out: dict[str, list[tuple[str, list[str]]]] = {}
        for m in fake_fcm.sent:
            if m["data"].get("type") in ("live", "end"):
                said = json.loads(m["data"]["players"]) if m["data"]["type"] == "live" else [m["data"]["tag"]]
                out.setdefault(m["token"].split(":")[0], []).append((m["data"]["event_id"] if m["data"]["type"] == "live" else "end", said))
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
    # Polled again, Carlsen's sends theirs once more, and Lazavik's takes its own down from their phone: one card.
    again(polls.redis)
    poll(1, side(1, status="*", ongoing=True), band=0, move=42)
    poll(2, side(2, status="*", ongoing=True), band=0, move=42)
    sent = cards()
    assert sent["both"] == [("1", ["Denis Lazavik · Black vs Magnus Carlsen", "Magnus Carlsen · White vs Denis Lazavik"]), ("end", ["live-e2"])]
    assert sent["lazavik"] == [("2", ["Denis Lazavik · Black vs Magnus Carlsen"])]
    # Muted from the other side's push, the game sends them no card: the one they had is taken down (BUG-154).
    server.mute(2, "both", db)
    again(polls.redis)
    poll(2, side(2, status="*", ongoing=True), band=2, move=43)
    poll(1, side(1, status="*", ongoing=True), band=2, move=43)
    sent = cards()
    assert set(sent) == {"lazavik", "carlsen", "both"} and sent["both"] == [("end", ["live-e1"])]
    again(polls.redis)
    poll(2, side(2, status="*", ongoing=True), band=2, move=44)
    poll(1, side(1, status="*", ongoing=True), band=2, move=44)
    assert set(cards()) == {"lazavik", "carlsen"}


def test_with_redis_unreadable_each_side_tells_its_own(db, polls, fake_fcm, monkeypatch):  # noqa: F811
    # Nothing says whether the other side's fixture is polling: each side sends its own words and its own card; and the
    # other side's word for a moment a fan of both was told already isn't told again.
    phones(db)
    phones(db, "fcm")
    monkeypatch.setattr(livecards, "_queue", lambda event_id, kind, made, board=None: livecards.send(event_id, kind, made, board))  # at once
    for event_id in (1, 2):
        poll(event_id, side(event_id, status="*"))
    polls.pushed.clear()

    def unreadable(keys: list[str]) -> list:
        raise ConnectionError("Redis went away")

    monkeypatch.setattr(polls.redis, "mget", unreadable)
    fake_fcm.sent.clear()
    for event_id in (2, 1):
        poll(event_id, side(event_id, status="*", ongoing=True), band=0)
    assert {m["data"]["event_id"] for m in fake_fcm.sent if m["token"].startswith("both:") and m["data"].get("type") == "live"} == {"1", "2"}
    polls.pushed.clear()
    poll(2, side(2, status="*", ongoing=True), band=2, move=41)
    poll(1, side(1, status="*", ongoing=True), band=2, move=41)
    assert heard(polls.pushed) == {"lazavik": ["Denis Lazavik is in trouble"], "both": ["Denis Lazavik is in trouble"], "carlsen": ["Magnus Carlsen is winning"]}


def test_a_crashing_fixture_doesnt_speak_for_its_side(db, polls, monkeypatch):
    # Carlsen's fixture keeps failing before its poll commits (it's still polled, so it looks polled): it finds
    # nothing, so Lazavik's side says what Carlsen's would have.
    phones(db)
    for event_id in (1, 2):
        poll(event_id, side(event_id, status="*"))
        poll(event_id, side(event_id, status="*", ongoing=True), band=0)
    polls.pushed.clear()
    quiet(polls.redis, 1)  # its last poll to commit
    record = notify.record

    def failing(db_, player_id, event_id, found, url=None):
        if event_id == 1:
            raise RuntimeError("the database went away")
        return record(db_, player_id, event_id, found, url)

    monkeypatch.setattr(notify, "record", failing)
    with pytest.raises(RuntimeError):
        poll(1, side(1, status="*", ongoing=True), band=2, move=41)
    poll(2, side(2, status="*", ongoing=True), band=2, move=41)
    assert heard(polls.pushed) == {"lazavik": ["Denis Lazavik is in trouble"], "both": ["Denis Lazavik is in trouble"]}


@pytest.mark.parametrize("starts", ["delayed", "unknown"])
def test_a_board_is_one_game_though_its_fixtures_starts_differ_or_are_unknown(db, polls, starts):
    # A round put back: one player's fixtures refresh has read its new start, the other's not yet. Or a round with no
    # start at all. The board's two fixtures still are one game, by the players on it.
    if starts == "delayed":
        db.execute(text("UPDATE event SET start_utc = start_utc + interval '3 hours' WHERE id = 2"))
    else:
        db.execute(text("UPDATE event SET start_utc = NULL"))
    db.commit()
    phones(db)
    for event_id in (2, 1):
        poll(event_id, side(event_id, status="*"))
    for event_id in (2, 1):
        poll(event_id, side(event_id, status="*", ongoing=True), band=0)
    for event_id in (2, 1):
        poll(event_id, side(event_id, status="*", ongoing=True), band=2, move=41)
    tags = {tag for fan, _, tag, _ in polls.pushed}
    assert heard(polls.pushed)["both"] == ["Magnus Carlsen is playing", "Magnus Carlsen is winning"] and tags == {"e1"}
    server.mute(2, "both", db)
    assert sorted(db.scalars(select(NotificationMute.event_id).where(NotificationMute.fan_id == "both"))) == [1, 2]


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
    # Game 2: Lazavik, with White now, pulls ahead at the same move: the same band from White's side as game 1's,
    # another game's swing all the same.
    poll(2, match(2, 1), band=2, move=41)
    poll(1, match(1, 1), band=2, move=41)
    tags = {(fan, title): tag for fan, title, tag, _ in polls.pushed}
    assert {tags[("carlsen", "Magnus Carlsen is winning")], tags[("lazavik", "Denis Lazavik is winning")], tags[("both", "Denis Lazavik is winning")]} == {"e1"}
    assert [title for fan, title, *_ in polls.pushed if fan == "both"] == ["Magnus Carlsen is playing", "Magnus Carlsen is winning", "Denis Lazavik is winning"]
    assert [title for fan, title, *_ in polls.pushed if fan == "carlsen"] == ["Magnus Carlsen is playing", "Magnus Carlsen is winning", "Magnus Carlsen is in trouble"]
    assert len({tag for *_, tag, _ in polls.pushed}) == 1  # every push of the match, either side's, on one notification


def test_a_tiebreak_round_tells_who_won_its_match_once_its_over_not_its_first_game(db, polls, monkeypatch):
    # A World Championship's tiebreak round, its games shown one at a time (BUG-210): Carlsen wins game 1 with White,
    # Lazavik games 2 and 3. Through the source's own reads: game 1 over with game 2 not out yet tells nothing and the
    # board is still polled; once the round's over, each fan hears the match, 1–2, once, and so does a fan following
    # the game itself.
    round_now: dict = {}
    monkeypatch.setattr(lichess_chess, "get_json", lambda url, ttl, text=False: round_now)
    chess = lichess_chess.LichessChess()
    db.add_all([MatchFollow(fan_id="watcher", event_id=1, alert_rules={}, keys=[]),
                PushSubscription(fan_id="watcher", endpoint="https://fcm.googleapis.com/fcm/send/watcher", p256dh="k", auth="a")])
    phones(db)

    def look(*results: str, **when: bool) -> None:
        round_now.clear()
        round_now.update(tiebreak(*results, **when))
        for event_id in (1, 2):
            poll(event_id, chess.snapshot({"round": ROUND, "fide": SIDES[event_id][1]}))

    look("*", to_come=True)
    look("*")
    polls.pushed.clear()  # the start
    look("1-0")
    assert heard(polls.pushed) == {} and {db.get(Event, e).status for e in (1, 2)} == {"live"}  # type: ignore[union-attr]
    look("1-0", "*")
    look("1-0", "1-0")
    assert heard(polls.pushed) == {}
    look("1-0", "1-0", "0-1", finished=True)
    db.expire_all()
    assert {db.get(Event, e).status for e in (1, 2)} == {"final"}  # type: ignore[union-attr]
    assert heard(polls.pushed) == {"carlsen": ["Magnus Carlsen: Lost the match 1–2 vs Denis Lazavik"], "lazavik": ["Denis Lazavik: Won the match 2–1 vs Magnus Carlsen"],
                                   "both": ["Denis Lazavik: Won the match 2–1 vs Magnus Carlsen"], "watcher": ["Magnus Carlsen vs Denis Lazavik: Denis Lazavik won the match (3 games)"]}


def test_a_tiebreak_round_lichess_calls_finished_between_its_games_tells_only_its_match(db, polls, monkeypatch):
    # BUG-210 reopened (R4): Lichess calls a round finished as soon as every game in it has a result, and on again as the
    # next game's first moves come. The World Championship's tiebreak in that shape (the recording's players: Carlsen
    # wins game 1 with White, Lazavik games 2 and 3), through the source's own reads and the poller: nothing told between
    # games, neither "Won with White" after game 1 nor "Match drawn" at 1–1, and both events still polled; GRACE after
    # game 3 with no game 4, each fan hears the match, 2–1, once, and so does a fan following the game itself.
    round_now: dict = {}
    monkeypatch.setattr(lichess_chess, "get_json", lambda url, ttl, text=False: round_now)
    chess = lichess_chess.LichessChess()
    db.add_all([MatchFollow(fan_id="watcher", event_id=1, alert_rules={}, keys=[]),
                PushSubscription(fan_id="watcher", endpoint="https://fcm.googleapis.com/fcm/send/watcher", p256dh="k", auth="a")])
    phones(db)

    def look(*results: str, **when) -> set[str]:
        round_now.clear()
        round_now.update(tiebreak(*results, **when))
        for event_id in (1, 2):
            poll(event_id, chess.snapshot({"round": ROUND, "fide": SIDES[event_id][1]}))
        db.expire_all()
        return {db.get(Event, e).status for e in (1, 2)}  # type: ignore[union-attr]

    look("*", to_come=True)
    look("*")
    polls.pushed.clear()  # the start
    assert look("1-0", finished=True, ago=0) == {"live"} and heard(polls.pushed) == {}  # game 1 over, the round "finished"
    assert look("1-0", "*") == {"live"}
    assert look("1-0", "1-0", finished=True, ago=0) == {"live"} and heard(polls.pushed) == {}  # 1–1
    assert look("1-0", "1-0", "*") == {"live"}
    assert look("1-0", "1-0", "0-1", finished=True, ago=0) == {"live"} and heard(polls.pushed) == {}
    assert look("1-0", "1-0", "0-1", finished=True, ago=lichess_chess.GRACE) == {"final"}
    assert heard(polls.pushed) == {"carlsen": ["Magnus Carlsen: Lost the match 1–2 vs Denis Lazavik"], "lazavik": ["Denis Lazavik: Won the match 2–1 vs Magnus Carlsen"],
                                   "both": ["Denis Lazavik: Won the match 2–1 vs Magnus Carlsen"], "watcher": ["Magnus Carlsen vs Denis Lazavik: Denis Lazavik won the match (3 games)"]}


def test_the_same_two_meeting_again_later_are_another_game(db, polls):
    # Carlsen and Lazavik meet again three hours on, in another round: Carlsen's new fixture has its board, Lazavik's
    # hasn't been read yet. Game 1, which a fan muted, isn't game 2: its start reaches them, on a notification of its own.
    def rematch(fide: str, status: str = "live") -> Snapshot:
        data = load("lichess/round-final")
        to_come = status == "scheduled"
        rnd = {**data["round"], "id": "rematch1", "name": "Playoffs Grand Final, rematch", "finished": False, "ongoing": not to_come,
               "startsAt": int((NOW + timedelta(days=1 if to_come else -1)).timestamp() * 1000)}
        return lichess_chess.parse_board({**data, "round": rnd, "games": [{**data["games"][0], "status": "*"}]}, fide, NOW)

    later = db.get(Event, 1).start_utc + timedelta(hours=3)  # type: ignore[union-attr]
    for event_id, (player_id, fide) in ((3, SIDES[1]), (4, SIDES[2])):
        db.add(Event(id=event_id, key=f"lichess_chess:rematch1-{fide}", sport="Chess", title="Magnus Carlsen vs Denis Lazavik", competition="Esports World Cup 2026",
                     start_utc=later, status="scheduled", live_binding=binding(fide)))
        db.flush()
        db.add(EventPlayer(event_id=event_id, player_id=player_id))
    db.commit()
    phones(db)
    for event_id in (1, 2):
        poll(event_id, side(event_id, status="*"))
        poll(event_id, side(event_id, status="*", ongoing=True), band=0)
    for fan in ("both", "carlsen"):
        server.mute(1, fan, db)
    polls.pushed.clear()
    binding3 = binding(CARLSEN)
    structured.apply_poll(3, binding3, SOURCE, [(5, CARLSEN)], rematch(CARLSEN, "scheduled"))
    structured.apply_poll(3, binding3, SOURCE, [(5, CARLSEN)], rematch(CARLSEN))
    assert {(fan, title, tag) for fan, title, tag, _ in polls.pushed} == {("both", "Magnus Carlsen is playing", "e3"), ("carlsen", "Magnus Carlsen is playing", "e3")}
    assert structured.game_events(db, 3) == [3]


def test_a_moment_left_to_a_side_that_crashes_is_sent_late_and_once(db, polls, monkeypatch):
    # Carlsen's fixture told its side a moment ago, so Lazavik's leaves the swing to it; then Carlsen's crashes before
    # finding it. Past TELLS_WITHIN the fan of both is sent Lazavik's word for it, on the game's notification; and
    # when Carlsen's recovers and finds it after all, they aren't told twice.
    phones(db)
    for event_id in (1, 2):
        poll(event_id, side(event_id, status="*"))
        poll(event_id, side(event_id, status="*", ongoing=True), band=0)
    polls.pushed.clear()
    poll(2, side(2, status="*", ongoing=True), band=2, move=41)
    assert heard(polls.pushed) == {"lazavik": ["Denis Lazavik is in trouble"]}
    record = notify.record

    def failing(db_, player_id, event_id, found, url=None):
        if event_id == 1:
            raise RuntimeError("the database went away")
        return record(db_, player_id, event_id, found, url)

    monkeypatch.setattr(notify, "record", failing)
    with pytest.raises(RuntimeError):
        poll(1, side(1, status="*", ongoing=True), band=2, move=41)
    assert notify.held_due() == []  # Carlsen's side still has time to say it
    later = datetime.now(timezone.utc) + timedelta(seconds=structured.TELLS_WITHIN + 30)
    held = notify.held_due(later)  # Lazavik's start too, left to Carlsen's side, which said it
    forget = polls.redis.hdel

    def down(*args):
        raise ConnectionError("Redis went away")

    monkeypatch.setattr(polls.redis, "hdel", down)  # sent, but not forgotten: the next tick finds it again
    assert len(held) == 2 and notify.send_held(held, later) == 1
    assert [(fan, title, tag) for fan, title, tag, _ in polls.pushed] == [("both", "Denis Lazavik is in trouble", "e1")]
    monkeypatch.setattr(polls.redis, "hdel", forget)
    assert notify.held_due(later) == held and notify.send_held(held, later) == 0  # it reached them already: not again
    assert len(polls.pushed) == 1 and notify.held_due(later) == []
    polls.pushed.clear()
    monkeypatch.setattr(notify, "record", record)
    poll(1, side(1, status="*", ongoing=True), band=2, move=41)
    assert heard(polls.pushed) == {"carlsen": ["Magnus Carlsen is winning"]}


def test_a_moment_that_cant_be_kept_for_late_is_sent_now(db, polls, monkeypatch):
    # Left to Carlsen's side, but Redis won't keep it to send late should that side never say it: Lazavik's word goes
    # to the fan of both now, and Carlsen's, coming after, isn't said too.
    phones(db)
    for event_id in (1, 2):
        poll(event_id, side(event_id, status="*"))
        poll(event_id, side(event_id, status="*", ongoing=True), band=0)
    polls.pushed.clear()

    def down(*args):
        raise ConnectionError("Redis went away")

    monkeypatch.setattr(polls.redis, "hset", down)
    poll(2, side(2, status="*", ongoing=True), band=2, move=41)
    assert heard(polls.pushed) == {"lazavik": ["Denis Lazavik is in trouble"], "both": ["Denis Lazavik is in trouble"]}
    poll(1, side(1, status="*", ongoing=True), band=2, move=41)
    assert heard(polls.pushed) == {"carlsen": ["Magnus Carlsen is winning"]}


def test_a_board_that_cant_say_which_meeting_it_is_is_told_from_each_side(db, polls):
    # A broadcast whose tournament has no name: its two fixtures can't be told from another meeting of the same two,
    # so they aren't taken for one game, and the fan of both hears each side's word.
    def unnamed(event_id: int, **kwargs) -> Snapshot:
        snap = side(event_id, **kwargs)
        snap.state["tournament"] = None
        return snap

    phones(db)
    for event_id in (1, 2):
        poll(event_id, unnamed(event_id, status="*"))
        poll(event_id, unnamed(event_id, status="*", ongoing=True), band=0)
    assert heard(polls.pushed)["both"] == ["Magnus Carlsen is playing", "Denis Lazavik is playing"]
    assert structured.boards(db, [1, 2]) == {}


def test_a_moment_its_other_side_says_in_time_isnt_sent_late(db, polls):
    phones(db)
    for event_id in (1, 2):
        poll(event_id, side(event_id, status="*"))
        poll(event_id, side(event_id, status="*", ongoing=True), band=0)
    polls.pushed.clear()
    poll(2, side(2, status="*", ongoing=True), band=2, move=41)
    poll(1, side(1, status="*", ongoing=True), band=2, move=41)
    assert heard(polls.pushed)["both"] == ["Magnus Carlsen is winning"]
    later = datetime.now(timezone.utc) + timedelta(seconds=structured.TELLS_WITHIN + 30)
    held = notify.held_due(later)  # the swing, and Lazavik's start
    assert len(held) == 2 and notify.send_held(held, later) == 0  # Carlsen's words for both went
    assert polls.pushed == [] and notify.held_due(later) == []


def test_a_look_between_the_two_sides_polls_is_still_one_swing(db, polls, monkeypatch):
    # The engine's readings as the lane leaves them, each side settling the board's one evaluation (engine.settled).
    # Lazavik's side polls after move 42's look (Carlsen winning: the band moved at 42); the lane looks at move 43
    # before Carlsen's side polls. Both word the swing as the one at 42 (settle's "since"), so a fan of both hears it
    # once, from Carlsen's side, and nothing late: the moment held for Carlsen's side to say was said.
    monkeypatch.setattr(engine, "enabled", lambda: True)
    for asked in ("request", "drop"):
        monkeypatch.setattr(engine, asked, lambda *args, **kwargs: None)  # the lane's queue isn't what's tested here
    monkeypatch.setattr(engine, "deepens", lambda event_id: False)
    phones(db)
    for event_id in (1, 2):
        poll(event_id, side(event_id, status="*"))
    game = side(1, status="*", ongoing=True).state["game_id"]

    def reading(move: int, cp: int, at: int) -> None:
        polls.redis.set(f"engine:latest:{game}", json.dumps({"cp": cp, "mate": None, "depth": 20, "look": {"cp": cp, "mate": None, "depth": 20},
                                                              "fen": f"8/8/8/8/8/8/8/8 w - - 0 {move}", "at": at, "first": at}))

    for at, (move, cp) in enumerate([(40, 20), (41, 340)], 1):
        reading(move, cp, at)
        for event_id in (1, 2):
            poll(event_id, side(event_id, status="*", ongoing=True))
    polls.pushed.clear()
    reading(42, 340, 3)
    poll(2, side(2, status="*", ongoing=True))
    assert heard(polls.pushed) == {"lazavik": ["Denis Lazavik is in trouble"]}
    reading(43, 345, 4)
    poll(1, side(1, status="*", ongoing=True))
    assert heard(polls.pushed) == {"carlsen": ["Magnus Carlsen is winning"], "both": ["Magnus Carlsen is winning"]}
    swings = db.scalars(select(Moment.key).where(Moment.kind == "swing").order_by(Moment.id)).all()
    assert swings == [f"2:6:swing:{game}w2m42", f"1:5:swing:{game}w2m42"]  # each side's word for one play
    later = datetime.now(timezone.utc) + timedelta(seconds=structured.TELLS_WITHIN + 30)
    held = notify.held_due(later)  # Lazavik's swing and his start, each left to Carlsen's side
    assert notify.send_held(held, later) == 0 and polls.pushed == []
