"""The API's database use and its streams: async routes never wait for a connection on the event loop,
a stream holds none for its life (BUG-010), and the fan stream opens with the fan's players, so a
reconnect catches up (BUG-026). A hundred requests at once all get answers, and health stays quick: no
request holds a connection while it waits for a thread (BUG-264; against Postgres, skipped without one).
A fan, an address and a process hold only so many streams, whose Redis connections come from a pool of
their own, and the API's other Redis connections have a ceiling (BUG-184)."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import sys
import time
from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException, Response
from fastapi.routing import APIRoute
from starlette.requests import ClientDisconnect

from sports_follow import db as db_module, server
from sports_follow.db import engine, get_db

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")

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
    games the fan follows by themselves, of which `games` are, the teams they follow, none, and their sports, `sports`."""

    games: list[int] = []
    sports: list[str] = []

    def execute(self, stmt):
        if "match_follow" in str(stmt) or "team_follow" in str(stmt):
            return Rows([])  # the followed games' rows: the stream's first frame lists none; and no team
        return Rows(FOLLOWS if stmt.get_final_froms() else [(1,)])  # the fan's follows, or health's select(1)

    def scalars(self, stmt):
        if "team_follow" in str(stmt):
            return Rows([])  # the fan's teams' games: none
        if "fan_sport" in str(stmt):
            return Rows(self.sports)
        if "match_follow" in str(stmt):
            return Rows(self.games)
        return Rows(player_id for player_id, _ in FOLLOWS)

    def scalar(self, stmt):
        return {"level": "off"}

    def get(self, model, key):
        return SimpleNamespace(card=STORED[key]) if key in STORED else None


class PubSub:
    connection = None  # no socket of its own to close

    def __init__(self, log: list[str]):
        self.log = log

    async def subscribe(self, *channels):
        self.log.append("subscribe " + " ".join(channels))

    async def get_message(self, ignore_subscribe_messages, timeout):
        await asyncio.sleep(0)

    async def aclose(self):
        self.log.append("close")


class Redis:
    def __init__(self):
        self.cards = {f"card:{k}": json.dumps(v) for k, v in HOT.items()}
        self.log: list[str] = []

    async def get(self, key):
        self.log.append(f"get {key}")
        return self.cards.get(key)

    async def mget(self, keys):
        self.log.append(f"mget {' '.join(keys)}")
        return [self.cards.get(k) for k in keys]

    async def set(self, key, value):
        self.cards[key] = value

    async def ping(self):
        return True

    def pubsub(self):
        return PubSub(self.log)


class Request:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}  # no fan's cookie, no session
    client = None  # no public address: only the fan's streams are counted

    async def is_disconnected(self):
        return False


class From(Request):
    """A request from a public address, with `fan`'s cookie if given."""

    def __init__(self, address: str, fan: str | None = None):
        self.client = SimpleNamespace(host=address)
        if fan:
            self.cookies = {server.FAN_COOKIE: server.sign_fan(fan)}


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
    monkeypatch.setitem(server.state, "streams", seen.redis)
    monkeypatch.setattr(server, "streaming", Counter(), raising=False)  # no stream open from another test
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
    assert event["matches"] == [] and event["teams"] == [] and event["sports"] == []
    assert held == 0 and api.opened == ["thread"] * 5  # the follow ids, the players, the followed games, the teams, the sports: each closed at once
    # Read once subscribed, so a card published while the stream connects can't fall in a gap. The fan's own channel
    # too: a follow on another device (BUG-165).
    assert api.redis.log == ["subscribe player:1 player:2 follows:fan", "mget card:1 card:2", "close"]  # every card in one request (BUG-269)


def test_a_fan_who_follows_nothing_hears_so_first(api, monkeypatch):
    """BUG-162 (BHA-483): with nothing to follow (a fan signed out elsewhere reads as nobody, as does one who stopped
    following everyone there) the stream only pinged, so an open app kept the list it showed. It opens with the empty
    list, as any stream opens with the fan's list."""
    monkeypatch.setattr(sys.modules[__name__], "FOLLOWS", [])
    response = asyncio.run(server.my_stream(Request(), fan=""))
    event, held = first_frame(response, api)
    assert event == {"type": "following", "players": [], "matches": [], "teams": [], "sports": []} and held == 0


def test_the_fan_stream_carries_the_games_the_fan_follows_by_themselves(api, monkeypatch):
    monkeypatch.setattr(Db, "games", [41, 42])
    response = asyncio.run(server.my_stream(Request(), fan="fan"))
    first_frame(response, api)
    # Each followed game's channel: its board as it's polled, and its own moments (bus.match_channel).
    assert api.redis.log[0] == "subscribe player:1 player:2 match:41 match:42 follows:fan"


class Told(PubSub):
    """A stream's pubsub with one message waiting on the fan's own channel: what they follow changed."""

    def __init__(self, log: list[str]):
        super().__init__(log)
        self.waiting = [{"type": "message", "channel": "follows:fan", "data": "{}"}]

    async def get_message(self, ignore_subscribe_messages, timeout):
        await asyncio.sleep(0)
        return self.waiting.pop(0) if self.waiting else None


def frames(response, n: int, between) -> list[dict]:
    """The stream's first n events, `between` run after the first; within seconds, or the test fails."""

    async def read():
        out, body = [], response.body_iterator
        try:
            while len(out) < n:
                frame = await anext(body)
                if frame.startswith("data: "):
                    out.append(json.loads(frame.removeprefix("data: ")))
                    if len(out) == 1:
                        between()
        finally:
            await body.aclose()
        return out

    return asyncio.run(asyncio.wait_for(read(), 5))


@pytest.mark.parametrize("before", [FOLLOWS, []])
def test_a_follow_on_another_device_reaches_the_fans_open_streams(api, monkeypatch, before):
    """BUG-165: a stream's channels are fixed as it connects, and a follow or unfollow elsewhere told it nothing, so
    an open app never saw a player followed on the fan's phone. Every fan stream listens on the fan's own channel too
    (with nothing else to hear yet as well), and answers a word there by sending the fan's list again: their sports
    too (BHA-653)."""
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "FOLLOWS", before)
    monkeypatch.setattr(api.redis, "pubsub", lambda: Told(api.redis.log))
    response = asyncio.run(server.my_stream(Request(), fan="fan", signed=None))

    def elsewhere():
        monkeypatch.setattr(module, "FOLLOWS", [(2, {"level": "off"})])
        monkeypatch.setattr(Db, "sports", ["chess", "cricket"])

    first, again = frames(response, 2, elsewhere)
    assert [p["player_id"] for p in first["players"]] == [p for p, _ in before] and first["sports"] == []
    assert again == {"type": "following", "players": [{**again["players"][0], "player_id": 2, "alerts": "off"}], "matches": [], "teams": [], "sports": ["chess", "cricket"]}
    assert api.redis.log[0].startswith("subscribe") and api.redis.log[0].endswith("follows:fan")


def to_the_end(response) -> list[dict]:
    """Every event a stream sends till it ends; within seconds, or the test fails."""

    async def read():
        return [json.loads(frame.removeprefix("data: ")) async for frame in response.body_iterator if frame.startswith("data: ")]

    return asyncio.run(asyncio.wait_for(read(), 5))


def test_a_stream_whose_session_ended_tells_its_app_and_ends(api, monkeypatch):
    """BUG-162, round two: an app left open in front, its device signed out elsewhere (everywhere from another device,
    the account deleted or blocked), kept its stream, which kept the account it had connected as: the account's
    moments kept coming, and the app, told nothing, kept showing the account. Ending sessions says so on the account's
    own channel (tests/test_accounts.py); a stream hearing it asks for its session again, and if it has ended, tells its
    app so (the app asks who it is now, and starts over) and ends, its connection back in the pool."""
    sana = server.accounts.Signed(account_id=1, fan_id="fan", session_id=7)
    sessions = {"sana": sana}

    async def account_of(request):
        return sessions.get("sana")

    monkeypatch.setattr(server, "account_of", account_of)
    monkeypatch.setattr(api.redis, "pubsub", lambda: Told(api.redis.log))
    # Still signed in (a follow on another device, say): the list again.
    first, again = frames(asyncio.run(server.my_stream(Request(), fan="fan", signed=sana)), 2, lambda: None)
    assert first["type"] == again["type"] == "following"
    # Signed out elsewhere: the app is told, and the stream ends.
    sessions.clear()
    said = to_the_end(asyncio.run(server.my_stream(Request(), fan="fan", signed=sana)))
    assert [e["type"] for e in said] == ["following", "signed_out"]
    assert api.redis.log[-1] == "close"  # its connection given back


def test_the_player_stream_rebuilds_a_lost_hot_copy_without_holding_a_session(api):
    response = asyncio.run(server.player_stream(3, Request()))
    event, held = first_frame(response, api)
    assert event == {"type": "card", "player_id": 3, "card": STORED[3]}
    assert held == 0 and api.opened == ["thread"]
    assert json.loads(api.redis.cards["card:3"]) == STORED[3]
    with pytest.raises(HTTPException) as missing:
        asyncio.run(server.player_stream(99, Request()))
    assert missing.value.status_code == 404 and api.open == 0


def ended(response) -> None:
    """A stream's answer run as the server runs it, until its client goes away."""

    async def run():
        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            pass

        await response({"type": "http", "method": "GET", "path": "/api/me/stream", "headers": []}, receive, send)

    asyncio.run(run())


class Socket:
    """A pubsub's connection, which notes how it was closed: waiting for the close to finish could hold an ended
    stream's cleanup, which nothing can cancel."""

    def __init__(self, log: list[str]):
        self.log = log

    async def disconnect(self, nowait: bool = False):
        self.log.append("closed without waiting" if nowait else "closed, waiting")


class OverTheNetwork(PubSub):
    """A stream's pubsub whose every call waits on the network, as a real one's does, and which notes when its
    connection goes back to the pool."""

    def __init__(self, log: list[str]):
        super().__init__(log)
        self.connection = Socket(log)

    async def get_message(self, ignore_subscribe_messages, timeout):
        await asyncio.sleep(timeout)  # nothing published

    async def aclose(self):
        await asyncio.sleep(0.001)
        self.log.append("connection back")


def dropped(response, log: list[str]) -> None:
    """A stream's answer run as the server runs it, its client going away once it's streaming."""

    async def run():
        async def receive():
            while not any(line.startswith("subscribe") for line in log):
                await asyncio.sleep(0.001)
            await asyncio.sleep(0.01)  # streaming, waiting for the next message
            return {"type": "http.disconnect"}

        async def send(message):
            pass

        await response({"type": "http", "method": "GET", "path": "/api/players/1/stream", "headers": []}, receive, send)

    asyncio.run(run())


def test_a_stream_whose_client_went_away_gives_its_redis_connection_back(api, monkeypatch):
    """BUG-184, round two: a client going away cancels its stream, and the cancellation reached the waits in its
    cleanup too, so the connection never went back to the streams' pool: every stream that ended kept one, the
    counter of open streams went down all the same, and a process's thousandth stream since it started was
    refused a connection, live updates gone for every fan on it."""
    monkeypatch.setattr(api.redis, "pubsub", lambda: OverTheNetwork(api.redis.log))
    for _ in range(3):
        dropped(asyncio.run(server.player_stream(1, Request())), api.redis.log)
    assert api.redis.log.count("connection back") == api.redis.log.count("closed without waiting") == 3 and not server.streaming


def test_a_stream_whose_client_went_away_while_a_send_waited_gives_its_connection_back_at_once(api, monkeypatch):
    """A server on ASGI 2.4 says a client is gone by failing the send, and the stream is left at its yield: its
    cleanup ran only once Python collected it, and the connection stayed out of the pool until then."""
    monkeypatch.setattr(api.redis, "pubsub", lambda: OverTheNetwork(api.redis.log))

    async def run():
        response = await server.player_stream(1, Request())

        async def receive():
            await asyncio.sleep(3600)

        async def send(message):
            if message.get("body"):
                raise OSError("the client is gone")

        with pytest.raises(ClientDisconnect):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}, "method": "GET", "path": "/api/players/1/stream", "headers": []}, receive, send)
        return api.redis.log.count("connection back")  # while the response, and its stream, are still about

    assert asyncio.run(run()) == 1 and not server.streaming


def test_a_fan_an_address_and_the_process_hold_only_so_many_streams_and_one_that_ends_frees_its_place(api, monkeypatch):
    """BUG-184: each stream holds a Redis connection, and one client could open as many as it liked (200 took
    Redis from 25 connections to 227), connections the worker's polling, locks and limits need too. Past a
    fan's or an address's share the next is refused (429), past the process's too (503), and a stream that
    ends gives its place back."""
    monkeypatch.setattr(server, "cookie_key", lambda: b"a test key for fan cookies, 32+ bytes")
    monkeypatch.setattr(server, "STREAMS_PER_FAN", 2, raising=False)
    monkeypatch.setattr(server, "STREAMS_PER_ADDRESS", 1, raising=False)  # an IPv6 /64's
    monkeypatch.setattr(server, "STREAMS_PER_IPV4", 3, raising=False)
    monkeypatch.setattr(server, "STREAMS_PER_PROCESS", 5, raising=False)

    def refused(stream) -> int:
        with pytest.raises(HTTPException) as refusal:
            asyncio.run(stream)
        return refusal.value.status_code

    first = asyncio.run(server.my_stream(From("81.2.69.142", fan="fan"), fan="fan"))
    asyncio.run(server.my_stream(From("81.2.69.143", fan="fan"), fan="fan"))
    assert refused(server.my_stream(From("81.2.69.144", fan="fan"), fan="fan")) == 429  # the fan's third, from anywhere
    ended(first)
    asyncio.run(server.my_stream(From("81.2.69.142", fan="fan"), fan="fan"))  # its place is free again
    # A player's stream needs no cookie: its address's share holds it.
    for _ in range(2):
        asyncio.run(server.player_stream(1, From("81.2.69.142")))
    assert refused(server.player_stream(1, From("81.2.69.142"))) == 429
    last = asyncio.run(server.player_stream(1, From("81.2.69.143")))
    assert refused(server.player_stream(1, From("81.2.69.145"))) == 503  # the process holds five
    ended(last)
    assert server.client_address(From("2a02:c7c:1::1")) == "2a02:c7c:1::/64"
    asyncio.run(server.player_stream(1, From("2a02:c7c:1::1")))  # an IPv6 household holds fewer than an IPv4 address
    assert refused(server.player_stream(1, From("2a02:c7c:1::2"))) == 429
    assert server.streaming["all"] == 5  # a refused stream holds nothing


def held(stream) -> int:
    """A stream route's answer as a status: 200 for a stream that opened (and holds its places), else the refusal's."""
    try:
        asyncio.run(stream)
    except HTTPException as refusal:
        return refusal.status_code
    return 200


def test_streams_with_no_fan_take_only_part_of_a_process_so_fans_still_get_their_live_updates(api, monkeypatch):
    """BUG-299: a player's stream needs no cookie, and its address's share was all that held it, so four IPv4
    addresses (or seventeen /64s of one /48) held every stream of a process, and every web fan's live scores got a
    503. A stream with neither a fan's cookie nor a session takes one of the few kept for those now
    (STREAMS_WITHOUT_FAN): the rest are fans'. A player's stream with a fan's cookie is held in that fan's share."""
    monkeypatch.setattr(server, "cookie_key", lambda: b"a test key for fan cookies, 32+ bytes")
    monkeypatch.setattr(server, "STREAMS_PER_FAN", 2, raising=False)
    monkeypatch.setattr(server, "STREAMS_PER_IPV4", 3, raising=False)
    monkeypatch.setattr(server, "STREAMS_WITHOUT_FAN", 6, raising=False)
    monkeypatch.setattr(server, "STREAMS_PER_PROCESS", 10, raising=False)
    assert [held(server.player_stream(1, From(host))) for host in ("81.2.69.1", "82.2.69.1") for _ in range(3)] == [200] * 6  # two networks, no cookie
    assert held(server.player_stream(1, From("83.2.69.1"))) == 503  # the streams kept for no fan are all held
    assert held(server.my_stream(From("83.2.69.1"), fan="made-up")) == 503  # as is a fan's own without its cookie: a new fan each request
    assert held(server.my_stream(From("84.2.69.1", fan="asha"), fan="asha")) == 200  # a fan's own still opens
    assert held(server.player_stream(1, From("84.2.69.1", fan="asha"))) == 200  # and a player's, with her cookie: in her share
    assert held(server.player_stream(1, From("85.2.69.1", fan="asha"))) == 429  # which holds two, from anywhere
    assert server.streaming["fanless"] == 6 and server.streaming["fan:asha"] == 2 and server.streaming["all"] == 8


def test_an_ipv4_24s_or_an_ipv6_48s_addresses_together_hold_only_so_many_streams(api, monkeypatch, caplog):
    """BUG-299: each address had its share and nothing else did, so a /24's addresses, or a /48's 65,536 /64s,
    together held every stream of a process. A prefix (an IPv4 /24, an IPv6 /48) has a ceiling of its own now
    (STREAMS_PER_PREFIX), well under the process's; one reaching it is logged."""
    caplog.set_level(logging.WARNING, "sports_follow.limits")
    monkeypatch.setattr(server, "_ceilings_logged", {}, raising=False)
    monkeypatch.setattr(server, "STREAMS_PER_IPV4", 3, raising=False)
    monkeypatch.setattr(server, "STREAMS_PER_ADDRESS", 2, raising=False)
    monkeypatch.setattr(server, "STREAMS_PER_PREFIX", 4, raising=False)
    monkeypatch.setattr(server, "STREAMS_PER_PROCESS", 100, raising=False)
    assert [held(server.my_stream(From(f"81.2.69.{1 + i // 3}"), fan=f"fan-{i}")) for i in range(5)] == [200] * 4 + [429]  # three from one, one from the next
    assert held(server.my_stream(From("81.2.70.1"), fan="other")) == 200  # another /24
    assert [held(server.my_stream(From(f"2a00:1450:4009:{i // 2:x}::1"), fan=f"v6-{i}")) for i in range(5)] == [200] * 4 + [429]
    assert held(server.my_stream(From("2a00:1450:400a::1"), fan="other-v6")) == 200  # another /48
    assert len([r for r in caplog.records if r.name == "sports_follow.limits" and "ceiling for streams" in r.getMessage()]) == 2


def test_the_apis_redis_connections_have_a_ceiling_and_streams_a_pool_of_their_own(monkeypatch):
    """BUG-184: the API's Redis client had no ceiling, and every stream's pubsub came from it. Now its pool has
    one, at which a burst waits its turn, and streams' connections come from a pool of their own, no bigger
    than the streams a process holds: streams never take the connections sessions, limits and locks need."""
    from redis import asyncio as aredis

    async def arq(settings):
        return SimpleNamespace(aclose=lambda: asyncio.sleep(0))

    monkeypatch.setattr(server, "create_pool", arq)
    monkeypatch.setattr(server, "cookie_key", lambda: b"a test key for fan cookies, 32+ bytes")
    monkeypatch.setattr(server, "state", {})
    pools = {}

    async def run():
        async with server.lifespan(server.app):
            pools.update({name: server.state[name].connection_pool for name in ("redis", "streams")})

    asyncio.run(run())
    assert isinstance(pools["redis"], aredis.BlockingConnectionPool) and pools["redis"].max_connections == server.REDIS_CONNECTIONS
    assert pools["streams"] is not pools["redis"] and pools["streams"].max_connections == server.STREAMS_PER_PROCESS


def test_async_routes_use_the_database_off_the_event_loop(api):
    assert asyncio.run(server.health(Response())) == {"ok": True, "db": True, "redis": True, "commit": None}
    assert [p["alerts"] for p in asyncio.run(server.following(fan="fan"))["players"]] == ["key", "off"]
    card = asyncio.run(server.card(1, Request(), Response(), fan="fan"))
    assert (card["following"], card["alerts"]) == (True, "off")
    assert api.opened == ["thread"] * 4 and api.open == 0  # health's, the follows' and the teams', the card's


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


def test_every_sync_route_gives_its_connection_back_as_it_returns():
    """server.Route has each sync route commit get_db's session on its own thread (db.released); a route added
    some other way (a router of its own) would hold its connection until its answer was sent (BUG-264)."""
    sync = [r for r in server.app.routes if isinstance(r, APIRoute) and not inspect.iscoroutinefunction(r.endpoint)]
    assert {"/api/me/matches", "/api/me/week", "/api/me/activity"} <= {r.path for r in sync}
    assert [r.path for r in sync if getattr(r.endpoint, "__wrapped__", None) is None] == []


@pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")
def test_a_hundred_requests_at_once_all_get_answers_and_health_stays_quick(monkeypatch):
    """One API process with its own pool (15 connections) and anyio's 40 threads, 100 requests at once to a sync
    route that reads the database, as every open app's Today asks after a goal. A sync route's answer is checked on
    one of those threads, and get_db let go of its connection only after the answer was sent: the 15 connections
    were held by answers waiting for a thread, the 40 threads by requests waiting for a connection, and nothing
    moved until the pool gave up after 5 s (40 of 70 failed; health took 5-15 s)."""
    monkeypatch.setattr(db_module, "DATABASE_URL", URL)
    monkeypatch.setitem(server.state, "redis", Redis())
    monkeypatch.setattr(server, "cookie_key", lambda: b"a test key for fan cookies, 32+ bytes")
    was = db_module.engine
    db_module.size_pool(15)  # the API's pool, on the test database
    cookie = {"cookie": f"{server.FAN_COOKIE}={server.sign_fan('a-fan-in-the-crowd')}"}

    async def burst():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as client:
            async def ask(path: str) -> tuple[int, float]:
                started = time.perf_counter()
                answer = await client.get(path, headers=cookie)
                return answer.status_code, time.perf_counter() - started

            crowd = asyncio.gather(*(ask("/api/me/matches") for _ in range(100)))
            await asyncio.sleep(0.1)  # the crowd's requests are in
            health = await ask("/api/health")
            return await crowd, health

    try:
        answers, (health, took) = asyncio.run(burst())
    finally:
        db_module.engine.dispose()
        db_module.engine = was
        db_module.SessionLocal.configure(bind=was)
    assert Counter(status for status, _ in answers) == {200: 100}
    assert health == 200 and took < 2, f"health took {took:.1f}s"
    assert max(seconds for _, seconds in answers) < 4
