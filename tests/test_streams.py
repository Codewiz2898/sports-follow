"""The API's database use and its streams: async routes never wait for a connection on the event loop,
a stream holds none for its life (BUG-010), and the fan stream opens with the fan's players, so a
reconnect catches up (BUG-026)."""

from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from fastapi.routing import APIRoute

from sports_follow import server
from sports_follow.db import engine, get_db

FOLLOWS = [(1, {}), (2, {"level": "off"})]  # (player_id, alert_rules), as the follow table has them
HOT = {1: {"player_id": 1, "version": 4, "status": "ready", "live": {"is_live": True, "score": "1-0"}}, 2: {"player_id": 2, "version": 9, "status": "ready", "live": {"is_live": False, "score": "2-2"}}}
STORED = {3: {"player_id": 3, "version": 2, "status": "ready", "live": {"is_live": False}}}  # in Postgres, not in Redis


class Rows(list):
    def all(self):
        return list(self)

    def scalar(self):
        return self[0][0]


class Db:
    """Answers the few queries the async routes make; the statements themselves aren't checked, but for the
    games the fan follows by themselves, of which GAMES are."""

    games: list[int] = []

    def execute(self, stmt):
        if "match_follow" in str(stmt):
            return Rows([])  # the followed games' rows: the stream's first frame lists none
        return Rows(FOLLOWS if stmt.get_final_froms() else [(1,)])  # the fan's follows, or health's select(1)

    def scalars(self, stmt):
        if "match_follow" in str(stmt):
            return Rows(self.games)
        return Rows(player_id for player_id, _ in FOLLOWS)

    def scalar(self, stmt):
        return {"level": "off"}

    def get(self, model, key):
        return SimpleNamespace(card=STORED[key]) if key in STORED else None


class PubSub:
    def __init__(self, log: list[str]):
        self.log = log

    async def subscribe(self, *channels):
        self.log.append("subscribe " + " ".join(channels))

    async def get_message(self, ignore_subscribe_messages, timeout):
        await asyncio.sleep(0)

    async def unsubscribe(self):
        self.log.append("unsubscribe")

    async def aclose(self):
        pass


class Redis:
    def __init__(self):
        self.cards = {f"card:{k}": json.dumps(v) for k, v in HOT.items()}
        self.log: list[str] = []

    async def get(self, key):
        self.log.append(f"get {key}")
        return self.cards.get(key)

    async def set(self, key, value):
        self.cards[key] = value

    async def ping(self):
        return True

    def pubsub(self):
        return PubSub(self.log)


class Request:
    headers: dict[str, str] = {}

    async def is_disconnected(self):
        return False


@pytest.fixture
def api(monkeypatch):
    """The server with fake Redis and sessions that note where they were opened and how many are open."""
    seen = SimpleNamespace(opened=[], open=0, redis=Redis())

    @contextmanager
    def session():
        try:
            asyncio.get_running_loop()
            seen.opened.append("event loop")
        except RuntimeError:
            seen.opened.append("thread")
        seen.open += 1
        try:
            yield Db()
        finally:
            seen.open -= 1

    monkeypatch.setattr(server, "session", session)
    monkeypatch.setitem(server.state, "redis", seen.redis)
    return seen


def first_frame(response, seen) -> tuple[dict, int]:
    """The stream's first event, and how many sessions are open once it is streaming."""

    async def read():
        frames = response.body_iterator
        frame = await anext(frames)
        held = seen.open
        await frames.aclose()
        return json.loads(frame.removeprefix("data: ")), held

    return asyncio.run(read())


def test_the_fan_stream_opens_with_the_fans_players_and_holds_no_session(api):
    response = asyncio.run(server.my_stream(Request(), fan="fan"))
    event, held = first_frame(response, api)
    assert event["type"] == "following"
    assert [(p["player_id"], p["version"], p["live"]["score"], p["alerts"]) for p in event["players"]] == [(1, 4, "1-0", "key"), (2, 9, "2-2", "off")]
    assert event["matches"] == []
    assert held == 0 and api.opened == ["thread"] * 3  # the follow ids, the players, the followed games: each closed at once
    # Read once subscribed, so a card published while the stream connects can't fall in a gap.
    assert api.redis.log == ["subscribe player:1 player:2", "get card:1", "get card:2", "unsubscribe"]


def test_the_fan_stream_carries_the_games_the_fan_follows_by_themselves(api, monkeypatch):
    monkeypatch.setattr(Db, "games", [41, 42])
    response = asyncio.run(server.my_stream(Request(), fan="fan"))
    first_frame(response, api)
    # Each followed game's channel: its board as it's polled, and its own moments (bus.match_channel).
    assert api.redis.log[0] == "subscribe player:1 player:2 match:41 match:42"


def test_the_player_stream_rebuilds_a_lost_hot_copy_without_holding_a_session(api):
    response = asyncio.run(server.player_stream(3, Request()))
    event, held = first_frame(response, api)
    assert event == {"type": "card", "player_id": 3, "card": STORED[3]}
    assert held == 0 and api.opened == ["thread"]
    assert json.loads(api.redis.cards["card:3"]) == STORED[3]
    with pytest.raises(HTTPException) as missing:
        asyncio.run(server.player_stream(99, Request()))
    assert missing.value.status_code == 404 and api.open == 0


def test_async_routes_use_the_database_off_the_event_loop(api):
    assert asyncio.run(server.health(Response())) == {"ok": True, "db": True, "redis": True, "commit": None}
    assert [p["alerts"] for p in asyncio.run(server.following(fan="fan"))["players"]] == ["key", "off"]
    card = asyncio.run(server.card(1, Request(), Response(), fan="fan"))
    assert (card["following"], card["alerts"]) == (True, "off")
    assert api.opened == ["thread"] * 3 and api.open == 0


def test_no_async_route_takes_a_request_session():
    """get_db's session lives until the response is sent: in an async route its queries run on the event
    loop, and a stream would hold it, and a pooled connection, for as long as the app is open."""

    def calls(dependant):
        for d in dependant.dependencies:
            yield d.call
            yield from calls(d)

    routes = [r for r in server.app.routes if isinstance(r, APIRoute) and inspect.iscoroutinefunction(r.endpoint)]
    assert {"/api/health", "/api/me/stream", "/api/players/{player_id}/stream", "/api/follows"} <= {r.path for r in routes}
    assert [r.path for r in routes if get_db in calls(r.dependant)] == []


def test_a_full_pool_gives_up_in_seconds():
    assert engine.pool.timeout() == 5
