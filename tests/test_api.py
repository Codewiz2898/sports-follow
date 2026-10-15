"""What a client can make the API do, and what it does when two requests race.

AI research is capped per fan, per client address and per hour across all fans, so a new cookie doesn't
reset it (BUG-011), and a follow that fails gives it back. Only a follower can refresh a player, once a
minute per player (BUG-012). Writes are committed before the answer goes out (BUG-029). Two fans
following a new athlete at once land on one player (BUG-030). A pick links a registry athlete only when
the registry agrees they're the same person (BUG-033). A push goes to a fan's newest few devices
(BUG-055), and a device changes fans only with its own keys (BUG-058). Only cookies the server signed
are fans, but fans from before keep their ids (BUG-013). The card is private and its ETag covers the
fan's own fields (BUG-038). Ids no row can have, odd bodies and source ids of the wrong shape are
refused plainly, never a 500 (BUG-039, BUG-078). The research counter never goes below zero (BUG-079).
Each request is logged once, under an id its jobs take along (BHA-192), and is the root span of a
trace they continue (BHA-193).
Redis, the database and the job queue are fakes.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
from contextlib import contextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from opentelemetry.trace import SpanKind
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.dml import Delete, Insert

from sports_follow import alerts, bus, db as db_module, logs, notify, pipeline, registry, server, starters, structured
from sports_follow.adapters import ADAPTERS, KNOWN, AdapterError, PlayerRef
from sports_follow.models import Athlete, Event, Follow, Notification, Player, PlayerCard, PlayerIdentity, PushSubscription

PUBLIC = "81.2.69.142"
KEY = b"a test key for fan cookies, 32+ bytes"


def _param(stmt, column: str):
    """The value a statement binds for a column ("fan_id", "player_id")."""
    return next((v for k, v in stmt.compile().params.items() if k.startswith(column)), None)


class Rows(list):
    """What execute() returns: the rows, and rowcount for an UPDATE."""

    rowcount = 1

    def first(self):
        return self[0] if self else None

    def all(self):
        return list(self)


def _athlete_sports(stmt) -> bool:
    """Is this the lookup of a registry athlete's sports (pipeline._in_other_sport)?"""
    d = stmt.column_descriptions[0]
    return d["entity"] is Athlete and d["name"] == "sport"


def _holds_qid(stmt) -> bool:
    """Is this the lookup of who holds a registry qid (pipeline._qid_holder)?"""
    return [d["name"] for d in getattr(stmt, "column_descriptions", [])] == ["player_id", "sport"]


class Redis:
    """The calls the API makes. Keys don't expire: a test that needs a later minute deletes the key."""

    def __init__(self):
        self.data: dict[str, object] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def incr(self, key):
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]

    async def decr(self, key):
        self.data[key] = int(self.data.get(key, 0)) - 1
        return self.data[key]

    async def eval(self, script, numkeys, *keys):
        assert script == server.GIVE_BACK  # the one script the API runs: one back, while above zero
        if int(self.data.get(keys[0]) or 0) > 0:
            return await self.decr(keys[0])
        return 0
    async def incrby(self, key, amount):
        self.data[key] = int(self.data.get(key, 0)) + amount
        return self.data[key]

    async def delete(self, *keys):
        return sum(self.data.pop(k, None) is not None for k in keys)

    async def expire(self, key, seconds, nx=False):
        return True

    async def ttl(self, key):
        return 42 if key in self.data else -2


class Queue:
    def __init__(self):
        self.jobs: list[tuple] = []

    async def enqueue_job(self, name, *args, **options):
        self.jobs.append((name, *args))
        self.options = options


class Db:
    """One ready player (id 1), one game (id 5), the follows in `api.follows`, the push devices in
    `api.devices`, the registry qids players hold in `api.qids` and the registry's sports for a qid in
    `api.sports`. Other writes are noted and dropped."""

    def __init__(self, api):
        self.api = api

    def get(self, model, key):
        if model is PlayerCard and key == 1:
            return SimpleNamespace(player_id=1, status="ready", card={})
        if model is Player and key == 1:
            return SimpleNamespace(id=1, name="Player One")
        return SimpleNamespace(id=key) if model is Event and key == 5 else None

    def scalar(self, stmt):
        d = stmt.column_descriptions[0]
        if d.get("entity") is None and _param(stmt, "fan_id") is not None:
            return _param(stmt, "fan_id") in {fan for fan, _ in self.api.follows}  # does a fan id own anything here
        if d["expr"] is PlayerIdentity.player_id and _param(stmt, "system") == "wikidata":
            return self.api.qids.get(_param(stmt, "external_id"), (None,))[0]  # who holds a qid, by qid alone
        if d["entity"] is not Follow or (_param(stmt, "fan_id"), _param(stmt, "player_id")) not in self.api.follows:
            return None
        return SimpleNamespace(alert_rules={}) if d["expr"] is Follow else {} if d["expr"] is Follow.alert_rules else 1

    def scalars(self, stmt):
        return Rows(self.api.sports.get(_param(stmt, "qid"), []) if _athlete_sports(stmt) else [])

    def execute(self, stmt):
        self.api.statements.append(stmt)
        if isinstance(stmt, Insert) and stmt.table.name == "push_subscription":
            # Postgres's ON CONFLICT ... DO UPDATE WHERE: another fan's device changes hands only with its keys.
            endpoint, fan, keys = _param(stmt, "endpoint"), _param(stmt, "fan_id"), (_param(stmt, "p256dh"), _param(stmt, "auth"))
            held = self.api.devices.get(endpoint)
            if held and held[0] != fan and held[1:] != keys:
                return Rows([])
            self.api.devices[endpoint] = (fan, *keys)
            return Rows([SimpleNamespace(id=1)])
        held = self.api.qids.get(_param(stmt, "external_id")) if _holds_qid(stmt) else None
        return Rows([SimpleNamespace(player_id=held[0], sport=held[1])] if held else [])

    def add(self, row):
        pass

    def flush(self):
        pass

    def commit(self):
        self.api.log.append("commit")

    def rollback(self):
        self.api.log.append("rollback")


class Api:
    """The server with fake Redis, queue and sessions (in_db's and get_db's). `log` notes each commit and
    when the answer started going out."""

    def __init__(self):
        self.redis, self.queue = Redis(), Queue()
        self.follows: set[tuple[str, int]] = set()
        self.qids: dict[str, tuple[int, str]] = {}  # qid: (player id, Player.sport)
        self.sports: dict[str, list[str]] = {}  # qid: the registry's sports for them
        self.devices: dict[str, tuple[str, str, str]] = {}  # endpoint: (fan, p256dh, auth)
        self.log: list[str] = []
        self.statements: list = []

    @contextmanager
    def session(self):
        db = Db(self)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    def call(self, method: str, path: str, *, fan: str | None = None, cookie: str | None = None, address: str = "127.0.0.1", body: dict | None = None,
             content: str | None = None, headers: dict[str, str] | None = None) -> httpx.Response:
        """One request through the whole app, from `address` with `fan`'s cookie (signed by the server), or
        a `cookie` as it is; with a JSON body, or `content` sent as JSON as it is."""
        cookie = server.sign_fan(fan) if fan else cookie
        headers = {**({"Cookie": f"sf_fan={cookie}"} if cookie else {}), **({"content-type": "application/json"} if content else {}), **(headers or {})}

        async def recorded(scope, receive, send):
            async def sending(message):
                if message["type"] == "http.response.start":
                    self.log.append("response")
                await send(message)

            await server.app(scope, receive, sending)

        async def go():
            transport = httpx.ASGITransport(app=recorded, client=(address, 50000), raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, path, json=body, content=content, headers=headers)

        return asyncio.run(go())


@pytest.fixture
def api(monkeypatch):
    fake = Api()
    monkeypatch.setattr(server, "session", fake.session)
    monkeypatch.setattr(db_module, "session", fake.session)
    # Any other module's session (pipeline's, registry's) would reach a real database: fail instead.
    monkeypatch.setattr(db_module, "SessionLocal", lambda: pytest.fail("a test reached a real database session"))
    monkeypatch.setitem(server.state, "redis", fake.redis)
    monkeypatch.setitem(server.state, "arq", fake.queue)
    monkeypatch.setattr(server, "cookie_key", lambda: KEY)
    # A typed name nobody has followed: a new placeholder player, so following it counts as a research.
    monkeypatch.setattr(pipeline, "resolve_or_create", lambda db, query: SimpleNamespace(id=7, name=query))
    return fake


# ---------------------------------------------------------------- AI research (BUG-011)


def research(api: Api, address: str, fan: str) -> httpx.Response:
    """A follow of a name no source knows."""
    return api.call("POST", "/api/follows", fan=fan, address=address, body={"query": "Brand New Name"})


def test_new_cookies_from_one_address_share_its_daily_research(api, monkeypatch):
    monkeypatch.setattr(server, "RESEARCH_PER_HOUR", 100, raising=False)
    monkeypatch.setattr(server, "RESEARCH_PER_ADDRESS", 5, raising=False)
    answers = [research(api, PUBLIC, f"script-{i}") for i in range(6)]
    assert [r.status_code for r in answers] == [200] * 5 + [429]
    assert answers[-1].json()["detail"] == "Your network has used today's AI researches. Try again tomorrow."
    assert research(api, "81.2.69.143", "someone-else").status_code == 200  # another address isn't held up
    # A fan's own three a day are as before.
    assert [research(api, "81.2.69.144", "one-fan").status_code for _ in range(4)] == [200, 200, 200, 429]


def test_researches_across_all_fans_stop_at_the_hourly_ceiling(api, monkeypatch):
    monkeypatch.setattr(server, "RESEARCH_PER_HOUR", 4, raising=False)
    answers = [research(api, f"81.2.69.{10 + i}", f"fan-{i}") for i in range(5)]
    assert [r.status_code for r in answers] == [200] * 4 + [429]
    assert "this hour" in answers[-1].json()["detail"]
    # The refused one counted nowhere: not against its fan, its address or the hour.
    counts = api.redis.data
    assert counts[server.player_search.research_hour_key()] == 4
    assert counts[server.player_search.research_key("fan-4")] == counts[server.player_search.research_address_key("81.2.69.14")] == 0


def test_a_follow_that_fails_gives_its_research_back_everywhere_it_was_counted(api, monkeypatch):
    def broken(db, query):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(pipeline, "resolve_or_create", broken)
    assert research(api, PUBLIC, "fan").status_code == 500
    counts = {k: v for k, v in api.redis.data.items() if k.startswith("research")}
    assert len(counts) == 3 and set(counts.values()) == {0}  # the fan's day, the address's day, the hour


def test_a_research_that_fails_to_start_is_given_back(api, monkeypatch):
    """A registry athlete no source has: the agent builds them, and that counts, unless creating them fails."""
    person = registry.Person("Q99", "football", "Some One", [], date(2000, 1, 1), [], None, {})
    monkeypatch.setattr(server, "_registry_athlete", lambda db, body: person)

    async def nowhere(person):
        return None

    def broken(person):
        raise IntegrityError("INSERT INTO player", {}, Exception("duplicate key"))

    monkeypatch.setattr(server, "_resolve", nowhere)
    monkeypatch.setattr(pipeline, "start_from_registry", broken)
    assert api.call("POST", "/api/follows", fan="fan", address=PUBLIC, body={"qid": "Q99", "sport": "football"}).status_code == 500
    counts = {k: v for k, v in api.redis.data.items() if k.startswith("research")}
    assert len(counts) == 3 and set(counts.values()) == {0}


def test_searches_that_reach_the_live_sources_are_limited_per_fan_and_per_address(api, monkeypatch):
    monkeypatch.setattr(server, "SOURCE_SEARCHES_PER_MINUTE", 3)
    allowed = []

    def run(db, q, sport, fan, may_ask):
        allowed.append(may_ask())  # search.run asks only when it would go on to the sources
        return {"query": q, "results": [], "partial": []}

    monkeypatch.setattr(server.player_search, "run", run)

    def searching(fan, address):
        return api.call("GET", "/api/search?q=someone", fan=fan, address=address).status_code

    assert [searching("script", PUBLIC) for _ in range(4)] == [200] * 4  # still answered, from the registry
    assert allowed == [True, True, True, False]
    # A new cookie from the same address is over that address's limit; another address isn't held up.
    searching("new-cookie", PUBLIC)
    searching("someone-else", "81.2.69.143")
    assert allowed[4:] == [False, True]


def test_limits_count_the_public_address_a_client_comes_from():
    def address(host):
        return server.client_address(SimpleNamespace(client=SimpleNamespace(host=host) if host else None))

    assert address(PUBLIC) == PUBLIC
    assert address("::ffff:81.2.69.142") == PUBLIC
    # One home or phone has a whole IPv6 /64: counted as one.
    assert address("2a02:c7c:4c3a:9a00:1:2:3:4") == address("2a02:c7c:4c3a:9a00:ffff::1") == "2a02:c7c:4c3a:9a00::/64"
    # This machine, the proxy (if its forwarded address isn't trusted) or a test client would put every
    # fan behind one address: only the fan's and the hourly limits apply to them.
    assert [address(h) for h in ("127.0.0.1", "::1", "172.18.0.5", "10.0.0.2", "testclient", None)] == [None] * 6


# ---------------------------------------------------------------- writes from other sites


@pytest.mark.parametrize("headers, allowed", [
    ({}, True),  # not a browser
    ({"Sec-Fetch-Site": "same-origin"}, True),
    ({"Sec-Fetch-Site": "same-site"}, False),  # a sibling subdomain
    ({"Sec-Fetch-Site": "cross-site"}, False),
    ({"Origin": "http://test"}, True),  # an older browser, from this site
    ({"Origin": "https://evil.example"}, False),
    ({"Origin": "null"}, False),
])
def test_only_the_apps_own_pages_can_write(api, headers, allowed):
    api.follows.add(("fan", 1))
    answer = api.call("DELETE", "/api/me/mutes/5", fan="fan", headers=headers)
    assert (answer.status_code == 200) is allowed
    assert ("commit" in api.log) is allowed  # a refused write never reached the route
    # Reads aren't held up: a link from another site still opens the app.
    assert api.call("GET", "/api/me/mutes", fan="fan", headers=headers).status_code == 200


# ---------------------------------------------------------------- refresh (BUG-012)


def test_only_a_follower_can_refresh_and_only_once_a_minute_per_player(api, monkeypatch):
    monkeypatch.setattr(structured, "binding", lambda db, player_id: object())
    monkeypatch.setattr(bus, "is_locked", lambda name: False)
    api.follows |= {("fan-a", 1), ("fan-b", 1)}

    def refresh(fan=None):
        return api.call("POST", "/api/players/1/refresh", fan=fan)

    assert refresh().status_code == 403  # no cookie
    assert refresh("stranger").status_code == 403
    assert refresh("fan-a").json() == {"queued": True}
    assert refresh("fan-a").status_code == 429
    assert refresh("fan-b").status_code == 429  # the player was just re-read, for every follower
    assert api.queue.jobs == [("refresh_structured", 1)]
    del api.redis.data["refresh:1"]  # a minute later
    assert refresh("fan-b").json() == {"queued": True}
    assert len(api.queue.jobs) == 2


# ---------------------------------------------------------------- the request log (BHA-192)


def _request_lines(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "sports_follow.requests"]


def test_a_request_is_logged_once_under_an_id_its_jobs_take_along(api, monkeypatch, caplog):
    monkeypatch.setattr(structured, "binding", lambda db, player_id: object())
    monkeypatch.setattr(bus, "is_locked", lambda name: False)
    api.follows.add(("fan-a", 1))
    caplog.set_level(logging.INFO, "sports_follow")
    answer = api.call("POST", "/api/players/1/refresh", fan="fan-a")
    rid = answer.headers["x-request-id"]
    assert answer.json() == {"queued": True}
    assert api.queue.jobs == [("refresh_structured", 1)] and api.queue.options == {"trace": {"rid": rid}}  # the worker logs it under rid
    [line] = _request_lines(caplog)
    assert line.getMessage().startswith("POST /api/players/1/refresh 200 ")
    assert (line.route, line.status, line.duration_ms >= 0) == ("/api/players/{player_id}/refresh", 200, True)
    assert line.context == {"rid": rid, "fan": logs.fan("fan-a")}  # the fan by a hash, never their id


def test_a_request_is_the_root_span_of_a_trace_its_jobs_continue(api, monkeypatch, spans):
    monkeypatch.setattr(structured, "binding", lambda db, player_id: object())
    monkeypatch.setattr(bus, "is_locked", lambda name: False)
    api.follows.add(("fan-a", 1))
    answer = api.call("POST", "/api/players/1/refresh?from=card", fan="fan-a")
    [request] = spans.get_finished_spans()
    assert (request.name, request.kind) == ("POST /api/players/{player_id}/refresh", SpanKind.SERVER)
    assert request.attributes["url.path"] == "/api/players/1/refresh" and "from=card" not in str(dict(request.attributes))
    assert request.attributes["http.response.status_code"] == 200 and request.attributes["request.id"] == answer.headers["x-request-id"]
    assert request.attributes["enduser.pseudo.id"] == logs.fan("fan-a")  # never the fan's id
    traceparent = api.queue.options["trace"]["traceparent"]  # the refresh job continues this trace (worker._logged)
    assert traceparent.split("-")[1:3] == [format(request.context.trace_id, "032x"), format(request.context.span_id, "016x")]


def test_a_proxys_request_id_is_kept_and_the_query_string_never_logged(api, caplog):
    caplog.set_level(logging.INFO, "sports_follow")
    kept = api.call("GET", "/api/sports?q=private+words", headers={"X-Request-ID": "edge-0f8e2a6c"})
    made = api.call("GET", "/api/sports", headers={"X-Request-ID": "not an id"})
    assert kept.headers["x-request-id"] == "edge-0f8e2a6c"
    assert made.headers["x-request-id"] != "not an id" and logs.given_id(made.headers["x-request-id"])
    lines = _request_lines(caplog)
    assert [line.context["rid"] for line in lines] == [kept.headers["x-request-id"], made.headers["x-request-id"]]
    assert all(line.path == "/api/sports" and "private" not in line.getMessage() for line in lines)


# ---------------------------------------------------------------- writes (BUG-029)


SUBSCRIPTION = {"endpoint": "https://fcm.googleapis.com/fcm/send/newest", "keys": {"p256dh": "k", "auth": "a"}}


@pytest.mark.parametrize("method, path, body", [
    ("PUT", "/api/follows/1/alerts", {"level": "off"}),
    ("DELETE", "/api/follows/1", None),
    ("PUT", "/api/me/mutes/5", None),
    ("DELETE", "/api/me/mutes/5", None),
    ("POST", "/api/push/subscriptions", {"subscription": SUBSCRIPTION}),
    ("DELETE", "/api/push/subscriptions", {"endpoint": SUBSCRIPTION["endpoint"]}),
    ("POST", "/api/notifications/3/opened", None),
])
def test_a_write_is_committed_before_the_fan_hears_ok(api, method, path, body):
    """get_db's own commit runs after the response is sent: a read straight after saw the old row, and an
    unfollow then follow could lose the follow."""
    api.follows.add(("fan", 1))
    assert api.call(method, path, fan="fan", body=body).status_code == 200
    assert "commit" in api.log and api.log.index("commit") < api.log.index("response")


# ---------------------------------------------------------------- push devices (BUG-055)


class PlanDb:
    """What _plan reads: two fans following player 1, their devices as ordered (newest first), no earlier pushes."""

    def __init__(self, devices):
        self.devices, self.rows = devices, []

    def execute(self, stmt):
        return [("big", 1, {}), ("small", 1, {})]

    def scalars(self, stmt):
        return self.devices if stmt.column_descriptions[0]["entity"] is PushSubscription else []

    def add(self, row):
        self.rows.append(row)

    def flush(self):
        for i, row in enumerate(self.rows, 1):
            row.id = i


def test_a_push_goes_to_a_fans_newest_devices_however_many_are_stored():
    def device(i, fan):
        return SimpleNamespace(id=i, fan_id=fan, endpoint=f"https://fcm.googleapis.com/fcm/send/{i}", p256dh="k", auth="a", timezone="UTC", quiet_start=None, quiet_end=None)

    devices = sorted([device(i, "big") for i in range(1, 61)] + [device(61, "small")], key=lambda d: -d.id)
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    db = PlanDb(devices)
    jobs = notify._plan(db, [alerts.Item(1, 1, None, "news", "key", 75, "Injury news", "x", "/player/1", now)], now)
    fan_of = {d.id: d.fan_id for d in devices}
    sent: dict[str, list[int]] = {}
    for _, (device_id, *_), *_ in jobs:
        sent.setdefault(fan_of[device_id], []).append(device_id)
    assert sent == {"big": list(range(60, 60 - notify.MAX_DEVICES, -1)), "small": [61]}
    assert sorted((n.fan_id, n.devices) for n in db.rows if isinstance(n, Notification)) == [("big", notify.MAX_DEVICES), ("small", 1)]


def test_subscribing_drops_the_fans_oldest_devices_beyond_the_cap(api):
    assert api.call("POST", "/api/push/subscriptions", fan="fan-a", body={"subscription": SUBSCRIPTION}).status_code == 200
    dialect = postgresql.dialect()
    trims = [str(s.compile(dialect=dialect, compile_kwargs={"literal_binds": True})) for s in api.statements if isinstance(s, Delete) and s.table.name == "push_subscription"]
    assert len(trims) == 1
    # Only this fan's devices, never the one just subscribed; the newest MAX_DEVICES - 1 others stay.
    for part in ("push_subscription.fan_id = 'fan-a'", f"push_subscription.endpoint != '{SUBSCRIPTION['endpoint']}'", "NOT IN (SELECT push_subscription.id",
                 "ORDER BY push_subscription.id DESC", f"LIMIT {notify.MAX_DEVICES - 1}"):
        assert part in trims[0]


# ---------------------------------------------------------------- following a new athlete at once (BUG-030)


OTHER = 41  # the player the other request created


class Race:
    """Another request creates the same athlete while this one does, and commits first: this one's insert
    fails on the unique constraint (`conflict`: the player's slug or the athlete's identity), as in
    Postgres, and anything after sees the other's rows."""

    def __init__(self, conflict: type):
        self.conflict, self.attempts, self.committed = conflict, 0, []

    @contextmanager
    def session(self):
        self.attempts += 1
        db = RaceDb(self, other_visible=self.attempts > 1)
        yield db
        self.committed += db.added

    def record(self, player_id, ref, confidence=1.0):
        # Where the old code met the identity race: the other request's identity insert won.
        raise IntegrityError("INSERT INTO player_identity", {}, Exception("duplicate key value violates unique constraint"))


class RaceDb:
    def __init__(self, race: Race, other_visible: bool):
        self.race, self.other_visible, self.added = race, other_visible, []

    def scalar(self, stmt):
        if isinstance(stmt, Insert):
            return None  # ON CONFLICT DO NOTHING: the other request has the slug
        entity = stmt.column_descriptions[0]["entity"]
        if entity is PlayerIdentity and self.other_visible:
            return SimpleNamespace(player_id=OTHER)
        if entity is Player and stmt.column_descriptions[0]["expr"] is Player and self.other_visible:
            return SimpleNamespace(id=OTHER)  # the typed name's alias, now the other's
        return None

    def execute(self, stmt):
        held = _holds_qid(stmt) and self.other_visible
        return Rows([SimpleNamespace(player_id=OTHER, sport="Basketball")] if held else [])

    def add(self, row):
        self.added.append(row)

    def flush(self):
        if not self.other_visible and any(isinstance(r, self.race.conflict) for r in self.added):
            raise IntegrityError("INSERT", {}, Exception(f"duplicate key value violates unique constraint on {self.race.conflict.__tablename__}"))
        for i, row in enumerate(self.added, 100):
            row.id = getattr(row, "id", None) or i

    def get(self, model, key):
        return None


@pytest.mark.parametrize("conflict", [Player, PlayerIdentity])
def test_two_fans_picking_a_new_athlete_at_once_get_the_same_player(monkeypatch, conflict):
    race = Race(conflict)
    monkeypatch.setattr(pipeline, "session", race.session)
    monkeypatch.setattr(structured, "record", race.record)
    monkeypatch.setattr(bus, "store_card", lambda player_id, card: pytest.fail("the other request publishes the card"))
    ref = PlayerRef("espn_basketball", "5104157", "Victor Wembanyama", ["24"], ["San Antonio Spurs"], league="nba")
    assert pipeline.start_from_pick(ADAPTERS["espn_basketball"], ref) == (OTHER, False)
    assert race.committed == []  # no second player, nothing left half made


@pytest.mark.parametrize("conflict", [Player, PlayerIdentity])
def test_two_fans_following_a_new_registry_athlete_at_once_get_the_same_player(monkeypatch, conflict):
    race = Race(conflict)
    monkeypatch.setattr(pipeline, "session", race.session)
    monkeypatch.setattr(registry, "link", lambda db, player_id, qid: db.add(PlayerIdentity(player_id=player_id, system="wikidata", external_id=qid)) or db.flush())
    person = registry.Person("Q72941595", "basketball", "Victor Wembanyama", [], date(2004, 1, 4), [], "nba", {})
    assert pipeline.start_from_registry(person) == (OTHER, False)
    assert race.committed == []


def test_two_fans_typing_a_new_name_at_once_get_the_same_player():
    db = RaceDb(Race(Player), other_visible=False)
    looked = []

    def scalar(stmt):
        looked.append(stmt)
        db.other_visible = len(looked) > 2  # the alias, the insert (which waits for the other), then the alias again
        return RaceDb.scalar(db, stmt)

    db.scalar = scalar
    assert pipeline.resolve_or_create(db, "Brand New Name").id == OTHER
    assert db.added == []


# ---------------------------------------------------------------- the registry athlete sent with a pick (BUG-033)


SALAH = registry.Person("Q1354960", "football", "Mohamed Salah", [], date(1992, 6, 15), ["Liverpool"], None, {"espn_soccer": "173896"})


@pytest.mark.parametrize("person, pick, linked", [
    # amster, sent with Salah's qid: every fan picking Salah would follow amster from then on.
    (SALAH, PlayerRef("pandascore_esports", "31229", "amster", ["1"], ["6666"], game="CS2"), False),
    # The pair the app sends: the registry's own id for the athlete.
    (SALAH, PlayerRef("espn_soccer", "173896", "Mohamed Salah", ["364"], ["Liverpool"]), True),
    # The registry has another id in this source: someone else.
    (SALAH, PlayerRef("espn_soccer", "999", "Mohamed Salah", ["1"], ["Al Ahly"]), False),
    # No id in this source: the birth date decides.
    (registry.Person(**{**SALAH.__dict__, "ids": {}}), PlayerRef("espn_soccer", "173896", "Mohamed Salah", ["364"], ["Liverpool"], born="1992-06-15"), True),
    (registry.Person(**{**SALAH.__dict__, "ids": {}}), PlayerRef("espn_soccer", "555", "Mohamed Salah", ["1"], ["Al Ahly"], born="1995-03-02"), False),
])
def test_a_pick_links_the_registry_athlete_only_when_the_registry_agrees(api, monkeypatch, person, pick, linked):
    links = []
    monkeypatch.setattr(server, "_registry_athlete", lambda db, body: person)
    monkeypatch.setitem(ADAPTERS, pick.system, KNOWN[pick.system])  # PandaScore runs only with its token
    monkeypatch.setattr(ADAPTERS[pick.system], "player", lambda athlete_id, league=None: pick)
    monkeypatch.setattr(pipeline, "start_from_pick", lambda adapter, ref: (5, False))
    monkeypatch.setattr(registry, "link", lambda db, player_id, qid: links.append((player_id, qid)))
    body = {"system": pick.system, "athlete_id": pick.athlete_id, "qid": person.qid, "sport": "football"}
    assert api.call("POST", "/api/follows", fan="fan", body=body).json()["player_id"] == 5
    assert links == ([(5, person.qid)] if linked else [])


# ---------------------------------------------------------------- someone in two sports (BUG-069's follow-up)


CHAHAL = {sport: registry.Person("Q4991413", sport, "Yuzvendra Chahal", [], date(1990, 7, 23), [], None, {}) for sport in ("cricket", "chess")}


@pytest.fixture
def two_sports(api, monkeypatch):
    """The cricketer is followed already (player 3 holds the qid); the chess row is a different athlete row."""
    api.qids["Q4991413"] = (3, "Cricket")
    api.sports["Q4991413"] = ["cricket", "chess"]
    monkeypatch.setattr(server, "_registry_athlete", lambda db, body: CHAHAL.get(str(body.get("sport") or "")) or CHAHAL["cricket"])
    chess = PlayerRef("lichess_chess", "chahal", "Yuzvendra Chahal", ["chahal"], [])

    async def resolve(person):
        return (ADAPTERS["lichess_chess"], chess) if person.sport == "chess" else None

    monkeypatch.setattr(server, "_resolve", resolve)
    monkeypatch.setattr(pipeline, "start_from_pick", lambda adapter, ref: (9, True))
    monkeypatch.setattr(server.player_search, "preview", lambda system, athlete_id, league: {"system": system, "athlete_id": athlete_id, "name": "Yuzvendra Chahal"})
    return api


def test_picking_the_other_sport_of_a_two_sport_athlete_follows_that_sports_player(two_sports):
    def follow(body):
        return two_sports.call("POST", "/api/follows", fan="fan", body=body).json()["player_id"]

    assert follow({"qid": "Q4991413", "sport": "chess"}) == 9
    assert follow({"qid": "Q4991413", "sport": "cricket"}) == 3
    assert follow({"qid": "Q4991413"}) == 3  # no sport: whoever holds the qid, as before
    # A player whose page renamed the sport ("Soccer", "T20 cricket") is still theirs: only a sport the
    # registry has for them is another sport.
    two_sports.qids["Q4991413"] = (3, "T20 cricket")
    assert follow({"qid": "Q4991413", "sport": "cricket"}) == 3


def test_the_preview_of_the_other_sport_isnt_the_followed_player(two_sports):
    def preview(sport=None):
        return two_sports.call("GET", "/api/athletes/wikidata/Q4991413" + (f"?sport={sport}" if sport else "")).json()

    assert preview("chess")["system"] == "lichess_chess"
    assert preview("cricket") == preview() == {"player_id": 3}


class TwoSportDb:
    """The cricketer's player holds the qid, who is in the registry for cricket and chess; `made` lists
    players made for the qid since, as (id, sport)."""

    def __init__(self, holder_sport: str, made: list[tuple[int, str]]):
        self.holder_sport, self.made, self.added = holder_sport, made, []

    def execute(self, stmt):
        if _holds_qid(stmt):
            return Rows([SimpleNamespace(player_id=3, sport=self.holder_sport)])
        return Rows(self.made if [d["name"] for d in getattr(stmt, "column_descriptions", [])] == ["id", "sport"] else [])

    def scalar(self, stmt):
        return None

    def scalars(self, stmt):
        return Rows(["cricket", "chess"] if _athlete_sports(stmt) else [])

    def add(self, row):
        self.added.append(row)

    def flush(self):
        for row in self.added:
            row.id = getattr(row, "id", None) or 10


@pytest.mark.parametrize("holder_sport, made, expected", [
    ("Chess", [], (3, False)),  # the qid's player is the chess player
    ("Cricket", [], (10, True)),  # a new chess player, without the qid the cricketer holds
    ("Cricket", [(10, "Chess")], (10, False)),  # and found again by the next fan
])
def test_a_registry_athlete_in_a_second_sport_gets_their_own_player(monkeypatch, holder_sport, made, expected):
    db = TwoSportDb(holder_sport, made)

    @contextmanager
    def session():
        yield db

    monkeypatch.setattr(pipeline, "session", session)
    assert pipeline.start_from_registry(CHAHAL["chess"]) == expected
    assert not any(isinstance(r, PlayerIdentity) for r in db.added)  # one player per qid: the cricketer keeps it


# ---------------------------------------------------------------- fan cookies (BUG-013)


def _fan_of(response: httpx.Response) -> str | None:
    """The fan id in the cookie a response sets (checked against the server's signature), if it sets one."""
    value = response.cookies.get("sf_fan")
    if not value:
        return None
    fan, day, _ = value.rsplit(".", 2)
    assert server.sign_fan(fan, int(day)) == value
    return fan


def _fan_seen(api: Api) -> str:
    """The fan the last request's database lookups were for."""
    return next(_param(s, "fan_id") for s in reversed(api.statements) if _param(s, "fan_id"))


LEGACY = "Lx3kQ9_vYb2mN7pR4tW8zA1c"  # a cookie from before signing: secrets.token_urlsafe(18)
TODAY = datetime.now(timezone.utc).toordinal()  # the day a cookie says it was issued


def test_a_cookie_the_server_didnt_mint_is_a_new_fan(api):
    # An id minted since signing is only taken signed, even one with follows: seeing it in the database
    # isn't enough to be that fan.
    api.follows.add(("A" * 32, 1))
    for cookie in ("anything-I-like", "A" * 32, f"{'A' * 32}.{TODAY}.forged"):
        answer = api.call("GET", "/api/me/following", cookie=cookie)
        fan = _fan_of(answer)
        assert fan is not None and server.NEW_ID.fullmatch(fan) and fan != "A" * 32
        assert _fan_seen(api) == fan


def test_a_fan_from_before_signing_keeps_their_id_now_signed(api):
    api.follows.add((LEGACY, 1))
    first = api.call("GET", "/api/me/following", cookie=LEGACY)
    assert _fan_of(first) == LEGACY and _fan_seen(api) == LEGACY
    assert "httponly" in first.headers["set-cookie"].lower() and f"Max-Age={5 * 365 * 24 * 3600}" in first.headers["set-cookie"]
    # From then on the signed cookie: nothing new to set.
    again = api.call("GET", "/api/me/following", cookie=first.cookies["sf_fan"])
    assert _fan_of(again) is None and _fan_seen(api) == LEGACY
    # A bare id that owns nothing here was never a fan.
    stranger = api.call("GET", "/api/me/following", cookie="Qq3kQ9_vYb2mN7pR4tW8zA1c")
    assert _fan_of(stranger) not in (None, "Qq3kQ9_vYb2mN7pR4tW8zA1c")


def test_a_cookie_is_issued_again_after_a_week_and_ends_with_its_lifetime(api):
    six_days = api.call("GET", "/api/me/following", cookie=server.sign_fan("fan-a", TODAY - 6))
    assert _fan_of(six_days) is None and _fan_seen(api) == "fan-a"
    renewed = api.call("GET", "/api/me/following", cookie=server.sign_fan("fan-a", TODAY - 7))
    assert _fan_of(renewed) == "fan-a" and renewed.cookies["sf_fan"] == server.sign_fan("fan-a", TODAY)
    expired = api.call("GET", "/api/me/following", cookie=server.sign_fan("fan-a", TODAY - 5 * 365 - 1))
    assert _fan_of(expired) not in (None, "fan-a")


def test_a_fan_from_before_signing_is_known_by_anything_they_own():
    asked = []
    server._owns_anything(SimpleNamespace(scalar=lambda stmt: asked.append(str(stmt))), LEGACY)
    for table in ("follow", "push_subscription", "notification_mute", "notification"):
        assert f"{table}.fan_id = :fan_id" in asked[0]


def test_the_cookie_key_is_made_once_and_every_process_reads_the_same(tmp_path, monkeypatch):
    path = tmp_path / "sports-follow" / "cookie-secret"
    made = server._secret_file(path)
    assert len(made) == 64 and path.stat().st_mode & 0o777 == 0o600
    assert server._secret_file(path) == made
    assert [p.name for p in path.parent.iterdir()] == ["cookie-secret"]  # no temporary file left
    # Two processes making it at once: the one that links its file first has the key, for both.
    other = tmp_path / "other" / "cookie-secret"
    real_link = server.os.link

    def beaten(src, dst):
        other.write_text("the other process's key, which got there first\n")
        return real_link(src, dst)

    monkeypatch.setattr(server.os, "link", beaten)
    assert server._secret_file(other) == "the other process's key, which got there first"
    assert [p.name for p in other.parent.iterdir()] == ["cookie-secret"]


def test_the_cookie_key_comes_from_the_environment_first_and_is_never_made_up(tmp_path, monkeypatch):
    def read_only(path, flags, mode=0o777):
        raise OSError(errno.EROFS, "Read-only file system", str(path))

    monkeypatch.setattr(server, "COOKIE_SECRET_FILE", tmp_path / "cookie-secret")
    server.cookie_key.cache_clear()
    try:
        monkeypatch.setattr(server, "COOKIE_SECRET", "k" * 40)
        assert server.cookie_key() == b"k" * 40
        server.cookie_key.cache_clear()
        monkeypatch.setattr(server, "COOKIE_SECRET", "short")
        with pytest.raises(RuntimeError, match="shorter than 32"):
            server.cookie_key()
        # A container whose secrets mount has no key: it stops, rather than sign with a key of its own.
        # The mount is read-only, which stops root too (a 0o500 directory doesn't).
        monkeypatch.setattr(server, "COOKIE_SECRET", "")
        monkeypatch.setattr(server.os, "open", read_only)
        with pytest.raises(RuntimeError, match="push-secrets.sh"):
            server.cookie_key()
        assert not (tmp_path / "cookie-secret").exists()
    finally:
        server.cookie_key.cache_clear()


# ---------------------------------------------------------------- the card is the fan's own (BUG-038)


def test_the_card_is_private_and_its_etag_changes_with_anything_in_it(api):
    card = {"player_id": 1, "version": 37, "status": "building", "live": {"is_live": False}}
    api.redis.data[bus.card_key(1)] = json.dumps(card)

    def get(etag=None, fan="fan"):
        return api.call("GET", "/api/players/1/card", fan=fan, headers={"If-None-Match": etag} if etag else None)

    first = get()
    assert first.json()["following"] is False
    assert (first.headers.get("cache-control"), first.headers.get("vary")) == ("private, no-cache", "Cookie")
    etag = first.headers["etag"]
    unchanged = get(etag)
    assert unchanged.status_code == 304 and unchanged.headers["etag"] == etag
    # A new fan's cookie still reaches them on a 304.
    assert "sf_fan=" in api.call("GET", "/api/players/1/card", headers={"If-None-Match": etag}).headers["set-cookie"]
    api.follows.add(("fan", 1))
    followed = get(etag)
    assert followed.status_code == 200 and (followed.json()["following"], followed.json()["alerts"]) == (True, "key")
    # The build fails: the same version, another answer.
    api.redis.data[bus.card_key(1)] = json.dumps({**card, "status": "failed", "error": "The research agent gave up."})
    failed = get(followed.headers["etag"])
    assert failed.status_code == 200 and failed.json()["status"] == "failed"


# ---------------------------------------------------------------- ids and bodies (BUG-039)


@pytest.mark.parametrize("method, path, body", [
    ("GET", "/api/players/2147483648/card", None),
    ("GET", "/api/players/0/card", None),
    ("GET", "/api/players/1/results/99999999999999999999", None),
    ("GET", "/api/players/2147483648/form", None),
    ("PUT", "/api/follows/2147483648/alerts", {"level": "off"}),
    ("DELETE", "/api/follows/2147483648", None),
    ("POST", "/api/players/2147483648/refresh", None),
    ("PUT", "/api/me/mutes/99999999999999999999", None),
    ("DELETE", "/api/me/mutes/99999999999999999999", None),
    ("POST", "/api/notifications/99999999999999999999/opened", None),
    ("GET", "/api/me/activity?before=99999999999999999999", None),
])
def test_an_id_no_row_can_have_is_refused_before_it_reaches_the_database(api, method, path, body):
    answer = api.call(method, path, fan="fan", body=body)
    assert answer.status_code == 422
    assert "less than or equal to 2147483647" in answer.text or "greater than or equal to 1" in answer.text


def test_a_results_page_the_server_didnt_give_is_refused(api, monkeypatch):
    monkeypatch.setattr(server.results, "page", lambda db, player_id, before, limit: {"before": before})
    when = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for cursor in ("garbage", server.results.encode(when, 10**20)):
        assert api.call("GET", f"/api/players/1/results?before={cursor}").status_code == 400
    good = server.results.encode(when, 12)
    assert api.call("GET", f"/api/players/1/results?before={good}").json() == {"before": good}


@pytest.mark.parametrize("player_id, status", [("1.9", 400), ("true", 400), ("1e400", 400), ('"1"', 400), ("2147483648", 404), ("0", 404), ("1", 200)])
def test_following_by_id_takes_a_whole_number_only(api, player_id, status):
    answer = api.call("POST", "/api/follows", fan="fan", content=f'{{"player_id": {player_id}}}')
    assert answer.status_code == status
    followed = [_param(s, "player_id") for s in api.statements if isinstance(s, Insert) and s.table.name == "follow"]
    assert followed == ([1] if status == 200 else [])  # 1.9 and true used to follow player 1


def test_a_typed_name_with_a_control_character_is_refused(api):
    assert api.call("POST", "/api/follows", fan="fan", body={"query": "Brand\x00New Name"}).status_code == 400
    assert not any(k.startswith("research") for k in api.redis.data)  # and nothing was counted


@pytest.mark.parametrize("method, body", [
    ("POST", {"subscription": "x"}),
    ("POST", {"subscription": {**SUBSCRIPTION, "keys": "zz"}}),
    ("POST", {"subscription": {**SUBSCRIPTION, "keys": {"p256dh": 5, "auth": "a"}}}),
    ("POST", {"subscription": {**SUBSCRIPTION, "endpoint": SUBSCRIPTION["endpoint"] + "\x00"}}),
    ("DELETE", {"endpoint": ["x"]}),
    ("DELETE", {"endpoint": "\x00"}),
])
def test_a_malformed_push_subscription_is_refused_plainly(api, method, body):
    answer = api.call(method, "/api/push/subscriptions", fan="fan", body=body)
    assert answer.status_code == 400 and api.devices == {}


# ---------------------------------------------------------------- previews (BUG-078)


@pytest.fixture
def previews(api, monkeypatch):
    """A source that has athlete 231388 only; the ids it was asked for."""
    asked = []

    def preview(system, athlete_id, league):
        asked.append(athlete_id)
        if athlete_id != "231388":
            raise AdapterError(f"ESPN returned HTTP 404 for https://site.web.api.espn.com/apis/common/v3/sports/soccer/athletes/{athlete_id}")
        return {"system": system, "athlete_id": athlete_id, "name": "Someone"}

    monkeypatch.setattr(server.player_search, "preview", preview)
    return asked


@pytest.mark.parametrize("path, status, asked", [
    ("/api/athletes/espn_soccer/231388", 200, ["231388"]),
    ("/api/athletes/espn_soccer/999999999", 404, ["999999999"]),  # a stale id: the source has none, it isn't down
    ("/api/athletes/espn_soccer/%00", 404, []),
    ("/api/athletes/espn_soccer/231388%3Fx%3D1", 404, []),  # was read as 231388, with ?x=1 on the source's URL
    ("/api/athletes/espn_soccer/231388?league=nba%2Fathletes%2F1", 404, []),
])
def test_a_preview_asks_the_source_only_for_an_id_of_its_shape(previews, api, path, status, asked):
    assert api.call("GET", path).status_code == status
    assert previews == asked


def test_a_source_that_doesnt_answer_is_still_a_502(api, monkeypatch):
    def down(system, athlete_id, league):
        raise AdapterError("ESPN unreachable: timed out")

    monkeypatch.setattr(server.player_search, "preview", down)
    assert api.call("GET", "/api/athletes/espn_soccer/231388").status_code == 502


@pytest.mark.parametrize("system, athlete_id, ok", [
    ("espn_soccer", "231388", True),
    ("espn_soccer", "231388?x=1", False),
    ("lichess_chess", "46616543", True),
    ("lichess_chess", "46616543/x", False),
    ("pandascore_esports", "31229", True),
    ("liquipedia_esports", "pubgmobile:Jonathan", True),
    ("liquipedia_esports", "pubgmobile:Team_SouL_p1", True),
    ("liquipedia_esports", "pubgmobile:X]] OR [[pagename::Y", False),  # the page goes into a LiquipediaDB condition
    ("liquipedia_esports", "Jonathan", False),
    ("pubg_esports", "account.c0e530e9b7244b358def282782f893af", True),
    ("pubg_esports", "account.x", False),
])
def test_each_source_takes_ids_of_its_own_shape(system, athlete_id, ok):
    assert server._source_id_ok(system, athlete_id) is ok


def test_following_a_pick_sends_the_source_only_an_id_of_its_shape(api, monkeypatch):
    asked = []
    monkeypatch.setattr(ADAPTERS["espn_soccer"], "player", lambda athlete_id, league=None: asked.append(athlete_id))
    for pick in ({"athlete_id": "231388?x=1"}, {"athlete_id": "\x00"}, {"athlete_id": "231388", "league": "nba/athletes/1"}):
        assert api.call("POST", "/api/follows", fan="fan", body={"system": "espn_soccer", **pick}).status_code == 400
    assert asked == []


def test_an_athlete_an_operator_removed_cant_be_followed_or_previewed_by_their_source_id(api, monkeypatch):
    # players.py removed them: their ids in the sources are blocked, so a source's own search can't bring them back.
    monkeypatch.setattr(registry, "blocked", lambda db: {("espn_soccer", "231388")})
    monkeypatch.setattr(ADAPTERS["espn_soccer"], "player", lambda athlete_id, league=None: pytest.fail("asked the source"))
    assert api.call("POST", "/api/follows", fan="fan", body={"system": "espn_soccer", "athlete_id": "231388"}).status_code == 404
    assert api.call("GET", "/api/athletes/espn_soccer/231388").status_code == 404


def test_a_registry_athlete_is_looked_up_only_with_a_sport_that_can_be_stored(monkeypatch):
    monkeypatch.setattr(registry, "get", lambda db, qid, sport: pytest.fail(f"looked up {sport!r}"))
    assert server._registry_athlete(None, {"qid": "Q1354960", "sport": "foot\x00ball"}) is None


# ---------------------------------------------------------------- the research counter (BUG-079)


def test_the_research_counter_never_shows_a_count_below_zero(api):
    api.redis.data[server.player_search.research_key("fan")] = -2
    shown = api.call("GET", "/api/me/research", fan="fan").json()
    assert (shown["used"], shown["left"]) == (0, 3)


def test_a_research_given_back_never_takes_a_count_below_zero(api):
    """The count expired, or was lost when Redis restarted, between the take and the give back."""
    api.redis.data.update({"research:fan:2026-10-13": 2, "research:zero:2026-10-13": 0})
    asyncio.run(server.give_back_research(["research:fan:2026-10-13", "research:zero:2026-10-13", "research:lost:2026-10-13"]))
    assert api.redis.data == {"research:fan:2026-10-13": 1, "research:zero:2026-10-13": 0}  # and none made without an expiry


# ---------------------------------------------------------------- a device changes fans only with its keys (BUG-058)


def test_a_device_moves_to_another_fan_only_with_its_own_keys(api):
    endpoint = SUBSCRIPTION["endpoint"]

    def subscribe(fan, p256dh="k", auth="a"):
        return api.call("POST", "/api/push/subscriptions", fan=fan, body={"subscription": {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}})

    assert subscribe("fan-a").status_code == 200
    # Someone who learned the endpoint: refused, and the device stays fan-a's.
    taken = subscribe("fan-b", "their-key", "their-auth")
    assert taken.status_code == 409 and api.devices[endpoint] == ("fan-a", "k", "a")
    assert api.log.count("rollback") == 1
    upsert = next(s for s in reversed(api.statements) if isinstance(s, Insert) and s.table.name == "push_subscription")
    sql = str(upsert.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "WHERE push_subscription.fan_id = 'fan-b' OR push_subscription.p256dh = excluded.p256dh AND push_subscription.auth = excluded.auth" in sql
    # The same browser with a new cookie sends the device's own keys: it moves.
    assert subscribe("fan-b").status_code == 200 and api.devices[endpoint] == ("fan-b", "k", "a")
    # And a fan can update their own device's keys.
    assert subscribe("fan-b", "new-key", "new-auth").status_code == 200 and api.devices[endpoint] == ("fan-b", "new-key", "new-auth")
    trims = [s for s in api.statements if isinstance(s, Delete) and s.table.name == "push_subscription"]
    assert len(trims) == 3  # the device cap, after each subscription that went through


def test_starters_are_offered_as_the_fan_sees_them(api, monkeypatch):
    """GET /api/starters answers from starters.offer for this fan (whose follows mark the rows); starters.py
    and tests/test_players.py hold what's offered."""
    asked = []
    offer = [{"key": "chess", "label": "Chess", "players": [{"key": "lichess_chess:gukesh", "name": "Gukesh D", "following": True}]}]
    monkeypatch.setattr(starters, "offer", lambda db, fan: asked.append(fan) or offer)
    answer = api.call("GET", "/api/starters", fan="fan")
    assert answer.status_code == 200 and answer.json() == {"sports": offer} and asked == ["fan"]


# ---------------------------------------------------------------- without the research agent


def test_without_the_agent_a_pick_follows_from_its_source_and_nothing_waits_for_a_model(api, monkeypatch):
    # SPORTS_FOLLOW_AGENT=0 (no OpenRouter key): an esports pro picked in search is followed from PandaScore
    # with no build queued, and a name or registry athlete no source has is refused, counted nowhere.
    from sports_follow import config

    monkeypatch.setattr(config, "AGENT", False)
    tenz = PlayerRef("pandascore_esports", "8742", "TenZ", ["1"], ["Sentinels"], game="Valorant")
    monkeypatch.setitem(ADAPTERS, tenz.system, KNOWN[tenz.system])  # PandaScore runs only with its token
    monkeypatch.setattr(ADAPTERS[tenz.system], "player", lambda athlete_id, league=None: tenz)
    monkeypatch.setattr(pipeline, "start_from_pick", lambda adapter, ref: (5, True))
    answer = api.call("POST", "/api/follows", fan="fan", body={"system": tenz.system, "athlete_id": tenz.athlete_id})
    assert answer.status_code == 200 and answer.json()["player_id"] == 5
    assert [job[0] for job in api.queue.jobs] == ["refresh_structured"]

    person = registry.Person("Q99", "football", "Some One", [], date(2000, 1, 1), [], None, {})
    monkeypatch.setattr(server, "_registry_athlete", lambda db, body: person)

    async def nowhere(person):
        return None

    monkeypatch.setattr(server, "_resolve", nowhere)
    monkeypatch.setattr(pipeline, "start_from_registry", lambda person: pytest.fail("a page for the agent was started"))
    for body in ({"qid": "Q99", "sport": "football"}, {"query": "Brand New Name"}):
        answer = api.call("POST", "/api/follows", fan="fan", address=PUBLIC, body=body)
        assert answer.status_code == 404 and answer.json()["detail"].startswith("No live source has this player yet"), body
    assert [job[0] for job in api.queue.jobs] == ["refresh_structured"]
    assert not any(k.startswith("research") for k in api.redis.data)
    assert api.call("GET", "/api/config").json()["research"] is False
