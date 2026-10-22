"""The API and realtime layer (design section 7).

Fans are anonymous for now: a random id in a cookie this server signs. Everything a fan does is a
subscription; all data work happens in the worker, keyed by player.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import time
from collections import Counter
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, TypeVar, cast
from urllib.parse import urlparse

import anyio
import redis.asyncio as aredis
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi import Path as InPath
from fastapi.routing import APIRoute
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased
from starlette.datastructures import Headers
from starlette.routing import Match

from . import accounts, admin, alerts, bus, campaigns, clips, clipstore, controls, credits as credits_page, fansports, fcm, feed, follows, google_signin, league, linear, live, logs, mail, match, matches, metrics, moments, notify, photos, pipeline, predictions, registry, results, search as player_search, share, sports, starters, structured, teams, tracing, watch, web_errors, week
from .adapters import ADAPTERS, KNOWN, AdapterError, budget, served
from .agent import MODEL
from .config import AGENT_LIVE, ANDROID_CERT_SHA256, API_DOCS, APP_NAME, ANDROID_PACKAGE, COMMIT, CONTACT_EMAIL, COOKIE_SECRET, COOKIE_SECRET_FILE, FAN_COOKIE, MAIL_CODES_PER_DAY, REDIS_URL, RESEARCH_PER_ADDRESS, RESEARCH_PER_HOUR, SITE_URL, SOURCE_LOOKUPS_PER_ADDRESS, SOURCE_LOOKUPS_PER_MINUTE, SOURCE_SEARCHES_PER_MINUTE
from .db import get_db, released, session
from .models import ClipKey, Event, FanSource, Follow, MatchFollow, Moment, Notification, NotificationMute, Player, PlayerAlias, PlayerCard, PlayerIdentity, PushSubscription, Question, Team

logs.setup()  # under uvicorn, after its own logging config: ours, with uvicorn's lines through it
tracing.setup("sports-follow-api")
log = logging.getLogger("sports_follow")
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
HEARTBEAT = 15
# An event, not a comment: EventSource hides comments, and the app counts on hearing something every
# HEARTBEAT to know its stream is alive (a proxy can keep it open after the API behind it is gone).
PING = "event: ping\ndata:\n\n"
# A fan stream's last word when its session has ended elsewhere (_stream's `still`): the app asks who it is now.
SIGNED_OUT = f"data: {json.dumps({'type': 'signed_out'})}\n\n"
# A live stream (_stream) holds a Redis connection while it's open, from a pool of the streams' own, so one
# client can't hold enough to starve the worker's polling, locks and limits (BUG-184). So many are open at
# once for one fan, for one client address, for an IPv6 /48 and in this process; past that the next is refused, and
# the app tries again a little later. A connection a phone dropped can stay open some minutes before the server hears
# of it, so a fan's share allows for a few; it's their own stream's, which each of their devices holds one of. An IPv6
# /64 is a household; an IPv4 address can be a phone network's, shared by thousands of its fans (carrier-grade NAT, as
# India's networks use), so it holds more. A /48's 65,536 /64s, free from a tunnel broker, together hold well under the
# process's; an IPv4 /24's addresses together are only logged at that (_at_ceiling), since each address is bought and
# has its own share, and a phone network's NAT puts many fans in a few /24s. And a stream opened with neither a fan's
# cookie nor a session (the app's page always has one) is one of the few kept for those, the rest being fans' (BUG-299).
STREAMS_PER_FAN = 8
STREAMS_PER_ADDRESS = 60  # an IPv6 /64's
STREAMS_PER_IPV4 = 250  # an IPv4 address's
STREAMS_PER_PREFIX = 300  # an IPv6 /48's /64s together; an IPv4 /24's addresses together are logged here, not refused
STREAMS_WITHOUT_FAN = 200  # streams with no fan, together
STREAMS_PER_PROCESS = 1000
REDIS_CONNECTIONS = 100  # the API's other Redis connections, in each process: past them a command waits its turn

state: dict[str, Any] = {}
streaming: Counter[str] = Counter()  # this process's open streams: by "fan:<id>", "address:<address>", "prefix:<its /24 or /48>", "fanless" and "all"


@asynccontextmanager
async def lifespan(app: FastAPI):
    cookie_key()  # a server without its cookie key stops here, not on a fan's first request
    metrics.seed()
    # The admin panel's switches, read once before any request: a route asking for them first (sports.on) would wait
    # on the database on the event loop, holding every other request meanwhile.
    await asyncio.to_thread(controls.current)
    state["arq"] = await create_pool(RedisSettings.from_dsn(REDIS_URL))
    state["redis"] = aredis.Redis.from_pool(aredis.BlockingConnectionPool.from_url(REDIS_URL, decode_responses=True, max_connections=REDIS_CONNECTIONS, timeout=10))
    state["streams"] = aredis.Redis.from_url(REDIS_URL, decode_responses=True, max_connections=STREAMS_PER_PROCESS)  # each stream's pubsub
    yield
    await state["arq"].aclose()
    await state["redis"].aclose()
    await state["streams"].aclose()


# FastAPI traces requests itself once tracing is on, and records the query string (a search's words):
# RequestLog's span and metric stand for each request instead (tracing.request_span, metrics.request).
# Nor does it log validation failures, which carry what a client sent. Its own docs (/docs, /redoc and the schema
# they read) list every route, the admin panel's too: only on a development server that asks for them (API_DOCS).
app = FastAPI(title=APP_NAME, lifespan=lifespan, telemetry={"tracing": False, "operation_spans": False, "logs": False, "metrics": False},
              docs_url="/docs" if API_DOCS else None, redoc_url="/redoc" if API_DOCS else None, openapi_url="/openapi.json" if API_DOCS else None)


class Route(APIRoute):
    """Every route here: a sync one gives its database connection back as it returns (db.released), never
    holding it while it waits for a thread to check and send its answer."""

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        super().__init__(path, released(endpoint), **kwargs)


app.router.route_class = Route  # before the first route

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _same_origin(headers: Headers) -> bool:
    """Did this request come from a page of this site? Browsers say so in Sec-Fetch-Site, which a page
    can't set; older ones only send Origin. A client that isn't a browser sends neither."""
    site = headers.get("sec-fetch-site")
    if site:
        return site in ("same-origin", "none")
    origin = headers.get("origin")
    return not origin or urlparse(origin).netloc == headers.get("host")


class SameOriginWrites:
    """Every write to the API comes from the app's own pages and service worker. SameSite=Lax keeps the
    cookie off another site's posts, but not off a sibling subdomain's (every *.sslip.io server is one
    site), and some writes take no body, so nothing else stops a form or a fetch from there."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE and scope["path"].startswith("/api/") and not _same_origin(Headers(scope=scope)):
            return await JSONResponse({"detail": f"This API only takes changes from the {APP_NAME} app."}, status_code=403)(scope, receive, send)
        await self.app(scope, receive, send)


NUL_REFUSED = "That has a character in it (NUL) that can't be kept."


def _holds_nul(value: Any) -> bool:
    """Is there a NUL in any string of a JSON value, a key or a value, however deep?"""
    left = [value]
    while left:
        item = left.pop()
        if isinstance(item, str):
            if "\x00" in item:
                return True
        elif isinstance(item, dict):
            left.extend(item.keys())
            left.extend(item.values())
        elif isinstance(item, list):
            left.extend(item)
    return False


class NoNul:
    """A NUL character in a request's text (%00 in any path or query, \\u0000 in an API write's JSON body) is refused
    with a 400 before any route sees it. Postgres can't keep one, so a sign-in address, a clip's address or a league member's
    name with one in it failed in the query, a server error anyone could raise at will (BUG-096). A write's body is
    read whole here (the API's are small JSON) and handed on as it came."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        refused = JSONResponse({"detail": NUL_REFUSED}, status_code=400)
        # Any address's path or query, the app's pages too: a page's file is looked up by its address (spa).
        if "\x00" in scope["path"] or b"%00" in scope["query_string"]:
            return await refused(scope, receive, send)
        if not scope["path"].startswith("/api/") or scope["method"] not in UNSAFE:
            return await self.app(scope, receive, send)
        came: list[MutableMapping[str, Any]] = []
        while True:
            message = await receive()
            came.append(message)
            if message["type"] != "http.request" or not message.get("more_body"):
                break
        body = b"".join(m.get("body", b"") for m in came if m["type"] == "http.request")
        if b"\\u0000" in body:  # the only way JSON carries a NUL; the body may still spell one out as text ("\\u0000")
            try:
                held = _holds_nul(json.loads(body))
            except (ValueError, RecursionError):
                held = False  # not JSON: the route says so
            if held:
                return await refused(scope, receive, send)

        async def again() -> MutableMapping[str, Any]:
            return came.pop(0) if came else await receive()

        await self.app(scope, again, send)


app.add_middleware(NoNul)
app.add_middleware(SameOriginWrites)


class FanLane:
    """Whatever a request to the API asks of a source, a fan is waiting for (adapters/budget.py: the fan
    lane): a source too scarce for that refuses, and the lane's spend is counted apart."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        with budget.in_lane("fan"):
            await self.app(scope, receive, send)


app.add_middleware(FanLane)


request_log = logging.getLogger("sports_follow.requests")


class RequestLog:
    """Each request's id and its one line in the log (logs.py). The id is the X-Request-ID a proxy or load
    balancer in front sent, if well formed, else one made here; every line logged while serving the
    request, and every job it queues (enqueue), carries it, and the answer names it in X-Request-ID.
    The line gives the route, status and time taken: never the query string (a search's words) or the
    client's address. A passing health check and the web app's files aren't logged. An API request is
    also a span (tracing.py), the root of its trace, which its jobs continue.

    An exception that escapes a route is this line's, with its stack (logs.py groups it), and the answer
    is a 500 as Starlette's would be; uvicorn has nothing left to log. One that escapes once the answer
    has started (a stream) goes on to uvicorn, which ends the connection."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = logs.given_id(Headers(scope=scope).get("x-request-id")) or logs.new_id()
        logs.begin(rid=rid)
        status = 500  # unless an answer starts: an exception escaped, and the server answers 500
        answering = False
        failure: Exception | None = None
        streamed = False
        started = time.perf_counter()

        async def answer(message: MutableMapping[str, Any]) -> None:
            nonlocal status, answering
            if message["type"] == "http.response.start":
                status, answering = message["status"], True
                message = {**message, "headers": [*message.get("headers", []), (b"x-request-id", rid.encode())]}
            await send(message)

        method, path = scope["method"], scope["path"]
        with tracing.request_span(method, path) as span:
            try:
                await self.app(scope, receive, answer)
            except Exception as exc:
                failure, streamed = exc, answering
                if not streamed:
                    await PlainTextResponse("Internal Server Error", status_code=500)(scope, receive, answer)
            finally:
                route = getattr(scope.get("route"), "path", None)
                tracing.answered(span, method, route, status, failure, **{"request.id": rid, "enduser.pseudo.id": logs.current().get("fan")})
                ms = (time.perf_counter() - started) * 1000
                if tracing.measured(path):
                    metrics.request(method, route, status, ms / 1000)
                if status >= 400 or (path.startswith("/api/") and path != "/api/health"):
                    level = logging.ERROR if failure else logging.WARNING if status >= 500 else logging.INFO
                    request_log.log(level, "%s %s %d %.0fms", method, path, status, ms, exc_info=failure,
                                    extra={"method": method, "path": path, "route": route, "status": status, "duration_ms": round(ms, 1)})
        if failure is not None and streamed:
            raise failure


app.add_middleware(RequestLog)  # outermost of ours: requests SameOriginWrites turns away are logged too


def arq() -> ArqRedis:
    return state["arq"]


async def enqueue(function: str, *args: Any, **options: Any) -> None:
    """Queue a worker job that logs under this request's id (worker._logged)."""
    await arq().enqueue_job(function, *args, trace=logs.carry(), **options)


T = TypeVar("T")

# Row ids are Postgres integers: a number outside them names no row, and would fail in the query.
MAX_ID = 2**31 - 1
RowId = Annotated[int, InPath(ge=1, le=MAX_ID)]


async def in_db(fn: Callable[..., T], *args: Any) -> T:
    """fn(db, *args) on a worker thread, in a session of its own, for async routes: waiting for a pooled
    connection never blocks the event loop, and the connection goes back as soon as fn returns. Rows
    come back detached; their loaded columns stay readable (expire_on_commit=False)."""

    def unit() -> T:
        with session() as db:
            return fn(db, *args)

    # The same threads as the sync routes, not asyncio's default ones the slow source calls fill.
    return await run_in_threadpool(unit)


# ---------------------------------------------------------------- fan identity


# A fan is the id in their cookie, signed here: "<id>.<day issued>.<signature>", so only ids this server
# minted are fans. Cookies from before signing hold a bare id; one that owns something here (follows,
# devices, mutes, notifications) is still that fan, and gets the id back signed.
COOKIE_DAYS = 5 * 365
# A cookie this many days old is issued again, so an active fan's never runs out (nor reaches the 400 days
# Chrome caps any cookie at).
REFRESH_DAYS = 7
NEW_ID = re.compile(r"[A-Za-z0-9_-]{32}")  # what secrets.token_urlsafe(24) mints: never taken unsigned
SESSION_CACHE = 600  # seconds a signed-in session's lookup is kept in Redis; ending a session overwrites it at once


@lru_cache(maxsize=1)
def cookie_key() -> bytes:
    """The key fan cookies are signed with: COOKIE_SECRET, or the file, made (0600) the first time."""
    secret = COOKIE_SECRET or _secret_file(COOKIE_SECRET_FILE)
    if len(secret) < 32:
        raise RuntimeError("The fan cookie secret is shorter than 32 characters.")
    return secret.encode()


def _secret_file(path: Path) -> str:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        pass
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        tmp.unlink(missing_ok=True)
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            f.write(secrets.token_hex(32) + "\n")
        try:
            os.link(tmp, path)  # whole, and only if no other process got there first: then theirs is the key
        except FileExistsError:
            pass
        finally:
            tmp.unlink()
        return path.read_text().strip()
    except OSError as exc:
        raise RuntimeError(f"No fan cookie secret at {path}, and it can't be made there ({exc}). Set SPORTS_FOLLOW_COOKIE_SECRET, or on the server run deploy/push-secrets.sh.") from exc


def _today() -> int:
    return datetime.now(timezone.utc).toordinal()


def sign_fan(fan: str, day: int | None = None) -> str:
    """A fan's cookie, issued on `day` (a UTC day's ordinal; today by default)."""
    payload = f"{fan}.{_today() if day is None else day}"
    mac = hmac.new(cookie_key(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"


def _signed(value: str) -> tuple[str, int] | None:
    """(fan id, day issued) from a cookie this server signed, while it's within its lifetime."""
    parts = value.rsplit(".", 2)
    if len(parts) != 3 or not 0 < len(parts[0]) <= 64 or not re.fullmatch(r"[0-9]{1,7}", parts[1]):
        return None
    fan, day = parts[0], int(parts[1])
    if not 0 <= _today() - day <= COOKIE_DAYS or not hmac.compare_digest(value.encode(), sign_fan(fan, day).encode()):
        return None
    return fan, day


def _owns_anything(db: Session, fan: str) -> bool:
    """Has this fan id followed, turned on notifications, muted a game or been notified here?"""
    return bool(db.scalar(select(or_(*(exists().where(t.fan_id == fan) for t in (Follow, PushSubscription, NotificationMute, Notification))))))


def _unsigned_fan(value: str) -> bool:
    """Is this a bare id from before cookies were signed, of a fan with something here? Ids minted since
    are never taken unsigned: seeing one in the database isn't enough to be that fan. Nor is an account's
    fan id ever a cookie's (accounts.py): a signed-in device holds a session instead."""
    if not 0 < len(value) <= 64 or not value.isprintable() or NEW_ID.fullmatch(value) or value.startswith(accounts.FAN_PREFIX):
        return False
    with session() as db:
        return _owns_anything(db, value)


def _secure(request: Request) -> bool:
    # Behind the proxy, uvicorn --proxy-headers sets the scheme.
    return request.url.scheme == "https"


def _set_fan(request: Request, response: Response, fan: str) -> None:
    response.set_cookie(FAN_COOKIE, sign_fan(fan), max_age=COOKIE_DAYS * 24 * 3600, httponly=True, samesite="lax", secure=_secure(request))


def _set_session(request: Request, response: Response, token: str) -> None:
    response.set_cookie(accounts.SESSION_COOKIE, token, max_age=accounts.COOKIE_MAX_AGE, httponly=True, samesite="lax", secure=_secure(request))


def mac(purpose: str, value: str) -> str:
    """A MAC of `value` under the cookie key, for one purpose (a sign-in code, Google's sign-in cookie):
    it can't stand in for a fan cookie's, nor one purpose's for another's."""
    digest = hmac.new(cookie_key(), f"{purpose}:{value}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _seal(purpose: str, value: str) -> str:
    """`value` with its MAC, for a cookie that must come back unchanged."""
    return f"{value}.{mac(purpose, value)}"


def _unseal(purpose: str, sealed: str | None) -> str | None:
    """What _seal sealed, if its MAC holds."""
    value, _, given = (sealed or "").rpartition(".")
    return value if value and hmac.compare_digest(given, mac(purpose, value)) else None


def _cookie_fan(request: Request) -> str | None:
    """The anonymous fan in this device's cookie (signed here, or from before signing), without making one."""
    value = request.cookies.get(FAN_COOKIE) or ""
    signed = _signed(value)
    if signed:
        return signed[0]
    return value if _unsigned_fan(value) else None


async def account_of(request: Request) -> accounts.Signed | None:
    """The account this device is signed in to, from its session cookie (looked up once a few minutes)."""
    token = request.cookies.get(accounts.SESSION_COOKIE) or ""
    if not token or len(token) > 100:
        return None
    key = f"session:{accounts.token_hash(token)}"
    cached = await state["redis"].get(key)
    if cached is not None:
        return accounts.Signed(**json.loads(cached)) if cached else None
    signed = await in_db(accounts.session_for, accounts.token_hash(token))
    # Only into an empty key: a session that ended while this looked it up has left "" there (_forget_sessions).
    await state["redis"].set(key, json.dumps(signed.__dict__) if signed else "", ex=SESSION_CACHE, nx=True)
    return signed


def fan_id(request: Request, response: Response, signed_in: accounts.Signed | None = Depends(account_of)) -> str:
    """Whose follows, levels, mutes and devices a request reads and writes: the signed-in account's, else
    the anonymous fan in the cookie this server signed, else a new anonymous fan, whose cookie only a write
    sets (_fan). The request's log lines name the fan by a hash (logs.fan)."""
    fan = _fan(request, response, signed_in)
    logs.bind(fan=logs.fan(fan))
    return fan


async def _tell_devices(fan: str = Depends(fan_id)) -> AsyncIterator[None]:
    """For a route that changes what the fan follows, or a level (TellsDevices): once it has saved the change, say
    so on the fan's own channel, and each of their open streams sends their list again (my_stream), so a follow made
    on their phone reaches the app open on their laptop (BUG-165). A change refused (an error) says nothing; a change
    saved stands if Redis can't say so: other devices catch up when their streams connect again."""
    yield
    try:
        await state["redis"].publish(bus.follows_channel(fan), "{}")
    except Exception as exc:  # noqa: BLE001 — telling the other devices is a courtesy
        log.warning("follows: couldn't tell the fan's other devices: %s", exc)


TellsDevices = [Depends(_tell_devices, scope="function")]  # its word goes once the route's work is done, before the answer

# Follows (of a player, a team or a game) one fan, and one client address, make an hour. A cookie is free, and a follow
# without one is a new fan's, so a fan's own caps (follows.MAX, teams.MAX, matches.MAX) don't bound a script's
# (BUG-325). An IPv6 /64 is a household, and its /48 (_network) a home's or an office's every /64 at once; an IPv4
# address can be a phone network's, shared by thousands of its fans (carrier-grade NAT), so it holds more, as with
# STREAMS_PER_IPV4, and so does a /48, which a phone network may share among its subscribers too.
FOLLOWS_PER_FAN = 150  # room to fill all three caps in the hour
FOLLOWS_PER_ADDRESS = 300  # an IPv6 /64's
FOLLOWS_PER_IPV6_48 = 1200  # an IPv6 /48's, all its /64s together
FOLLOWS_PER_IPV4 = 1200  # an IPv4 address's
FOLLOWS_HOUR = 3600
FOLLOWS_FROM_NETWORK = "A lot of follows have come from your network this hour. Try again later."


async def _take_follows(fan: str, address: str | None) -> list[str]:
    """Count a follow against the fan's hour and their address's: FOLLOWS_PER_FAN; an IPv4 address's FOLLOWS_PER_IPV4,
    or an IPv6 client's /64's FOLLOWS_PER_ADDRESS and its /48's FOLLOWS_PER_IPV6_48. Past any, nothing is counted and
    the fan gets a 429. Returns the counts taken, for give_back_research to give back should the follow not be made."""
    hour = int(time.time() // FOLLOWS_HOUR)
    limits = [(f"follows:fan:{fan}:{hour}", FOLLOWS_PER_FAN, "You've followed a lot this hour. Try again later.")]
    if address and ":" in address:
        limits += [(f"follows:address:{address}:{hour}", FOLLOWS_PER_ADDRESS, FOLLOWS_FROM_NETWORK),
                   (f"follows:network:{_network(address)}:{hour}", FOLLOWS_PER_IPV6_48, FOLLOWS_FROM_NETWORK)]
    elif address:
        limits.append((f"follows:address:{address}:{hour}", FOLLOWS_PER_IPV4, FOLLOWS_FROM_NETWORK))
    taken: list[str] = []
    for key, limit, refusal in limits:
        used = await state["redis"].incr(key)
        taken.append(key)
        await state["redis"].expire(key, 2 * FOLLOWS_HOUR)
        if used > limit:
            await give_back_research(taken)
            if key.startswith("follows:network:"):
                _at_ceiling("follows", _network(address))
            raise HTTPException(429, refusal)
    return taken


async def _follow_counted(request: Request, fan: str = Depends(fan_id)) -> AsyncIterator[None]:
    """For a route that follows a player, a team or a game (CountsFollow): one follow, counted against the fan's hour and
    their address's (_take_follows) before the route runs. A follow refused (an error), by these or for anything else,
    is given back: it isn't counted."""
    taken = await _take_follows(fan, client_address(request))
    try:
        yield
    except BaseException:
        await give_back_research(taken)
        raise


CountsFollow = [Depends(_follow_counted, scope="function")]


def _fan(request: Request, response: Response, signed_in: accounts.Signed | None, make: bool | None = None) -> str:
    """The fan, as fan_id says. A device without a fan of its own keeps a new one (its cookie) only from a request
    that `make`s one: by default a write, not a read. A read is answered as a new fan who owns nothing, for that
    request alone, and sets no cookie: an app's first requests go out together, before any cookie is back, and each
    keeping a fan of its own left the device with whichever answered last, and a follow made in between with a fan
    no device holds (BUG-152). The app's page makes one (spa), as does the Android app's first request (POST
    /api/me/start)."""
    if signed_in is not None:
        return signed_in.fan_id
    value = request.cookies.get(FAN_COOKIE) or ""
    signed = _signed(value)
    if signed:
        fan, day = signed
        if _today() - day < REFRESH_DAYS:
            return fan
    elif _unsigned_fan(value):
        fan = value
    else:
        fan = secrets.token_urlsafe(24)
        if not (request.method not in ("GET", "HEAD") if make is None else make):
            return fan
    _set_fan(request, response, fan)
    return fan


# ---------------------------------------------------------------- health


@app.get("/api/health")
async def health(response: Response) -> dict[str, Any]:
    """For the proxy's and an uptime monitor's checks: the database and Redis answer. 503 if either doesn't.
    Also the commit this image was built from (null outside a deployed one), which the deploy workflow checks."""
    checks = {"db": False, "redis": False}
    try:
        checks["db"] = await in_db(lambda db: db.execute(select(1)).scalar() == 1)
    except Exception:  # noqa: BLE001 — any failure is "down"
        pass
    try:
        checks["redis"] = bool(await state["redis"].ping())
    except Exception:  # noqa: BLE001
        pass
    ok = all(checks.values())
    if not ok:
        response.status_code = 503
    return {"ok": ok, **checks, "commit": COMMIT or None}


# ---------------------------------------------------------------- cards


async def read_card(player_id: int) -> dict[str, Any] | None:
    raw = await state["redis"].get(bus.card_key(player_id))
    if raw:
        return json.loads(raw)
    row = await in_db(lambda db: db.get(PlayerCard, player_id))
    if row is None:
        return None
    # Redis was flushed or lost: rebuild the hot copy from Postgres.
    await state["redis"].set(bus.card_key(player_id), json.dumps(row.card, default=str))
    return row.card


def _summary(card: dict[str, Any]) -> dict[str, Any]:
    return {k: card.get(k) for k in ("player_id", "slug", "status", "version", "built_at", "player", "live", "upcoming", "freshness", "error")} | {
        # The publishers' stories the curator served (rank.cards), else the research agent's news.
        "news": (card.get("stories") or card.get("news") or [])[:3],
        # The results the card keeps, newest first: structured.RECENT_LIMIT for a bound player, and no more
        # than that of what the agent wrote for one only it covers. Today's results rail shows them all, the
        # tiles the newest.
        "recent_results": (card.get("recent_results") or [])[: structured.RECENT_LIMIT],
    }


@app.get("/api/config")
def config(request: Request) -> dict[str, Any]:
    """What this server offers the app: its name, the model and whether it researches players no source has
    (config.RESEARCH), the ways to sign in, who to write to about privacy, and how to draw the fan's Feed
    (feed.layout: "edge" or "full", which the admin panel changes live; each page of the Feed says it too)."""
    from .config import RESEARCH

    return {"name": APP_NAME, "model": MODEL, "research": RESEARCH, "email_sign_in": mail.configured(_secure(request)), "google_sign_in": google_signin.configured(),
            "contact_email": CONTACT_EMAIL, "feed_layout": feed.layout()}


@app.get("/api/sports")
def sports_list(response: Response) -> dict[str, Any]:
    """The sports this app knows, in the order it lists them (sports.ALL): each one's name, the free-text
    names that are it, what its alerts say, and whether this build runs it and has live scores for it.
    "other" is any sport no module knows; "groups" the sports each standing for several of them, not listed
    (esports: every game), as a game no source covers ("Call of Duty: Mobile") is drawn."""
    response.headers["cache-control"] = "public, max-age=300"
    live = served()
    return {"sports": [sports.shown(s, s.key in live) for s in sports.ALL], "other": sports.shown(sports.OTHER, False),
            "groups": [sports.shown(g, any(m.key in live for m in sports.members(g))) for g in sports.GROUPS]}


@app.get("/api/sports.css")
def sports_css() -> Response:
    """Each sport's colour, for [data-sport] (index.html links it, so a page never shows the wrong colour)."""
    return Response(sports.css(), media_type="text/css", headers={"cache-control": "public, max-age=300"})


@app.get("/api/credits")
def credits(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Where this build's scores, ratings, evaluations, words, photos and code come from."""
    return credits_page.credits(photos=photos.used(db))


@app.get("/api/photos/{name}")
async def player_photo(name: str) -> Response:
    """A player's photo (photos.py), from our own copy: its address is its content, so it's kept a year."""
    found = await in_db(photos.serve, name)
    if found is None:
        raise HTTPException(404, "No such photo.")
    data, media_type = found
    return Response(data, media_type=media_type, headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- link previews (share.py)


async def _preview(player_id: int, game: int | None) -> share.Preview | None:
    """The preview of a player's page, or of a game of theirs (a This week poster's link); None for no such
    player. A game with no poster (not theirs, nothing to show yet) previews their page."""
    card = await read_card(player_id)
    if card is None:
        return None
    poster = await in_db(week.game, player_id, game) if game is not None else None
    return (share.of_game(card, poster) if poster else None) or share.of_card(card)


# Pictures are drawn one at a time per process (tens of milliseconds each): anyone may ask for one, and a
# script asking for many new ones would otherwise take the threads every database call waits for.
DRAWING = asyncio.Semaphore(1)


async def _picture(picture: share.Picture) -> Response:
    """A preview's picture, from those drawn lately or drawn now. Its address carries a digest of what it
    shows, so a crawler sees a new address once it changes; a few minutes' cache is for the crawlers that
    ask again at once."""
    data = share.drawn(picture)
    if data is None:
        async with DRAWING:
            data = await run_in_threadpool(share.png, picture)
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"})


@app.get("/api/players/{player_id}/share.png")
async def player_picture(player_id: RowId, g: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None) -> Response:
    """The picture a link to the player's page shows in WhatsApp, X and the rest: who they are and what
    they're doing now; with ?g=<event id>, that game's big moment."""
    preview = await _preview(player_id, g)
    if preview is None:
        raise HTTPException(404, "No such player.")
    return await _picture(preview.picture)


@app.get("/api/predictions/{question_id}/share.png")
async def question_picture(question_id: RowId) -> Response:
    """The picture a link to a question shows (share.of_question): how fans split on it, or how few called it."""
    q = await in_db(predictions.public, question_id)
    if q is None:
        raise HTTPException(404, "No such question.")
    return await _picture(share.of_question(q).picture)


@app.get("/api/share.png")
async def app_picture() -> Response:
    """The picture a link to any other page shows: the app's own."""
    return await _picture(share.APP)


async def _following(fan: str) -> list[dict[str, Any]]:
    """The fan's players as the app lists them: each card's summary and the fan's alert level. Not a turned-off
    sport's: the follow stays, out of sight, for when it's back (as teams.mine). Their hot copies are read in one
    request (each stream's opening, and its every reconnect, reads them all: BUG-269)."""
    rows = await in_db(lambda db: db.execute(select(Follow.player_id, Follow.alert_rules).where(Follow.fan_id == fan).order_by(Follow.created_at)).all())
    raws = await state["redis"].mget([bus.card_key(player_id) for player_id, _ in rows]) if rows else []
    out = []
    for (player_id, rules), raw in zip(rows, raws):
        card = json.loads(raw) if raw else await read_card(player_id)  # one Redis lost: from Postgres
        if card and sports.on((card.get("player") or {}).get("sport")):
            out.append({**_summary(card), "alerts": (rules or {}).get("level", moments.DEFAULT_LEVEL)})
    return out


@app.get("/api/me/following")
async def following(fan: str = Depends(fan_id)) -> dict[str, Any]:
    """The fan's players, and the teams they follow (teams.py), each with its next game, last result and game on now."""
    return {"players": await _following(fan), "teams": await in_db(teams.mine, fan)}


@app.get("/api/me/sports")
async def my_sports(fan: str = Depends(fan_id)) -> dict[str, Any]:
    """The sports the fan said they watch (fansports.py), in their order: of those the app offers now."""
    return {"sports": await in_db(fansports.mine, fan)}


@app.put("/api/me/sports", dependencies=TellsDevices)
def set_my_sports(body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """The fan's sports are these now, in this order ({"sports": [keys]}): the welcome's Next, and Following's Edit.
    A key of a sport the app doesn't offer is left out; a sport the fan had that's turned off now stays, out of
    sight. Answers with the list as GET does."""
    keys = body.get("sports")
    if not isinstance(keys, list) or len(keys) > fansports.GIVEN_MOST or not all(isinstance(k, str) for k in keys):
        raise HTTPException(400, "sports must be a list of sport keys")
    saved = fansports.replace(db, fan, keys)
    db.commit()
    return {"sports": saved}


@app.get("/api/live")
async def live_now(fan: str = Depends(fan_id)) -> dict[str, Any]:
    """Every game the running sources have on now, by sport, each with players this fan can follow from it
    (live.py). Served from the last read; a source's read older than a minute queues another for the
    worker, so "refreshing" says newer games may come in a moment."""
    games, stale = await in_db(live.offer, fan)
    for system in stale:
        try:
            # One read asked for a minute, whether the last one came or failed: a source that's down isn't
            # asked again on every fan's look.
            if await state["redis"].set(f"live:asked:{system}", "1", nx=True, ex=live.FRESH):
                await enqueue("read_live", system, _job_id=f"read_live:{system}")
        except Exception:
            log.warning("live: couldn't ask for %s's games", system, exc_info=True)
    return {"sports": games, "refreshing": bool(stale)}  # a read is on its way, or was asked for within the minute


@app.get("/api/match/{system}/{game}")
async def match_page(system: str, game: str, fan: str = Depends(fan_id)) -> dict[str, Any]:
    """One game's match page (match.py): its board, key moments and both sides' players this fan can follow,
    by its source and the id a fixture of it has there. Served from what's stored and last read; a read
    over a minute old queues another for the worker, so "refreshing" says a newer one may come in a moment.
    A game no source has named here, or of a sport turned off, is a 404: an address made up asks nothing of a
    source."""
    if system not in KNOWN or not match.ID.fullmatch(game):
        raise HTTPException(404, "No such game.")

    def view(db: Session) -> tuple[dict[str, Any] | None, bool]:
        page, ask = match.view(db, fan, system, game)
        if page is not None:
            page["follow"] = matches.offer(db, fan, system, game, page)  # the Follow button: following, and whether alerts can come
            page["predictions"] = []  # the questions fans call before it starts (predictions.py): a fault there leaves the page as it was
            try:
                with db.begin_nested():
                    page["predictions"] = predictions.for_page(db, fan, system, game, page)
            except Exception:
                log.exception("match %s:%s: predictions", system, game)
        return page, ask

    page, ask = await in_db(view)
    if ask:
        try:
            # One read a minute of a game, whether the last one came or failed, and at most match.READS a
            # minute of a source's allowance (Sportmonks' football and cricket share one), whichever games
            # fans have open.
            if await state["redis"].set(f"match:asked:{system}:{game}", "1", nx=True, ex=match.FRESH):
                spent = f"match:reads:{match.allowance(system)}:{int(time.time() // 60)}"
                if await state["redis"].incr(spent) <= match.READS:
                    await enqueue("read_match", system, game, _job_id=f"read_match:{system}:{game}")
                await state["redis"].expire(spent, 120, nx=True)
        except Exception:
            log.warning("match: couldn't ask for %s:%s", system, game, exc_info=True)
    if page is None:
        raise HTTPException(404, "This game isn't here: it may have ended before anyone opened it.")
    if page.get("status") == "live":
        try:
            # Someone has it open, on the web or Android: the engine deepens its position past its first look
            # while they do (engine.deepens), whichever of its fixtures polls it.
            for event_id in page.get("event_ids") or []:
                await state["redis"].set(bus.watching_key(event_id), "1", ex=bus.WATCH_SECONDS)
        except Exception:
            log.warning("match: couldn't note %s:%s as watched", system, game, exc_info=True)
    return {**page, "refreshing": ask}  # a read is on its way, or was asked for within the minute


@app.put("/api/match/{system}/{game}/follow", dependencies=[*TellsDevices, *CountsFollow])
def follow_match(system: str, game: str, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Follow a game by itself (matches.py): its key moments, from both sides, reach this fan until it ends.
    A game no source has named here is a 404, so a made-up address makes no follow and no poll; one that's
    over, or that no running source can poll, a 409; a fan following matches.MAX games already, or past the hour's
    follows (CountsFollow), a 429."""
    try:
        event_id = matches.follow(db, fan, system, game)
    except matches.Refused as refused:
        db.rollback()
        raise HTTPException(refused.status, refused.message)
    db.commit()  # before the answer: the tick that arms and polls it reads it from now on
    return {"following": True, "event_id": event_id}


@app.delete("/api/match/{system}/{game}/follow", dependencies=TellsDevices)
def unfollow_match(system: str, game: str, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Stop following a game by itself: nothing more of it is sent to this fan (their players in it still are)."""
    if system in KNOWN:
        matches.unfollow(db, fan, system, game)
    db.commit()
    return {"following": False}


@app.get("/api/me/matches")
def my_matches(fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """The games this fan follows by themselves, soonest first, each with its board as last polled: Today shows
    them with the fan's own live games and in Coming up."""
    return {"matches": matches.mine(db, fan)}


@app.delete("/api/me/matches/{event_id}", dependencies=TellsDevices)
def unfollow_match_event(event_id: RowId, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Stop following a game by the event the follow is on (the match page's follow state, or /api/me/matches):
    whichever of a game's addresses the follow was made at, or has moved to (a chess board's)."""
    matches.unfollow_event(db, fan, event_id)
    db.commit()
    return {"event_id": event_id, "following": False}


@app.put("/api/me/matches/{event_id}/alerts", dependencies=TellsDevices)
def set_match_alerts(event_id: RowId, body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Which of a followed game's own moments the fan is told about: everything, key, results or off, as for a player."""
    level = body.get("level")
    if level not in moments.LEVELS:
        raise HTTPException(400, f"level must be one of {', '.join(moments.LEVELS)}")
    row = db.scalar(select(MatchFollow).where(MatchFollow.fan_id == fan, MatchFollow.event_id == event_id))
    if row is None:
        raise HTTPException(404, "Follow the game first.")
    row.alert_rules = {**(row.alert_rules or {}), "level": level}
    db.commit()
    return {"event_id": event_id, "alerts": level}


# ---------------------------------------------------------------- predictions with friends (predictions.py)

LeagueCode = Annotated[str, InPath(min_length=4, max_length=16, pattern=r"^[A-Za-z0-9]+$")]
LEAGUES_MADE_PER_HOUR = 5  # leagues one fan makes in an hour
INVITES_PER_HOUR = 60  # invite pages one fan opens, and leagues they join, in an hour: a code can't be guessed by trying them all
INVITES_PER_ADDRESS = 300  # invite pages opened from one client address in an hour: a request with no cookie is a new fan each time
INVITES_PER_IPV6_48 = 1200  # an hour, from one IPv6 /48's /64s together (_address_limits)
INVITES_UNKNOWN_ADDRESS = 3000  # an hour, from every client without a public address together
PICKS_PER_ADDRESS = 300  # picks from one client address in an hour, by fans without an account: new fans from one place can't stuff a split
PICKS_PER_IPV6_48 = 1200  # an hour, from one IPv6 /48's /64s together (_address_limits)
PICKS_UNKNOWN_ADDRESS = 3000  # an hour, from every client without a public address together
PICKS_PER_ACCOUNT = 300  # a signed-in fan's in an hour, wherever from: fans behind one carrier address don't use up each other's
# Fans without an account whose picks on one question count (Pick.counted), from one client address: any device gets
# a fan cookie (spa), so a split is stuffed only from as many addresses as the picks it would add. A home or a phone
# network sharing one address has this many fans' calls in a game's split; the rest are theirs, and scored. An IPv6
# /48's /64s together have a few households' worth: a phone network's subscribers, or one person's free /48 (BUG-106).
COUNTED_PER_ADDRESS = 5
COUNTED_PER_IPV6_48 = 20
COUNTED_FOR = 30 * 24 * 3600  # seconds an address's counted fans on a question are kept: longer than any question is open


def _refused(db: Session, refused: predictions.Refused) -> HTTPException:
    db.rollback()
    return HTTPException(refused.status, refused.message)


@app.put("/api/predictions/{question_id}/pick")
async def make_pick(request: Request, question_id: RowId, body: dict = Body(...), fan: str = Depends(fan_id),
                    signed: accounts.Signed | None = Depends(account_of)) -> dict[str, Any]:
    """Call a game (or a map): this fan's answer to one of its questions, or a new one in place of theirs, while
    it's open. No account needed. Answers with the question as the app shows it, how fans split now included.
    A question closed is a 409, an answer it doesn't offer a 400; PICKS_PER_ADDRESS an hour from one address, or for
    a signed-in fan PICKS_PER_ACCOUNT an hour of their own (a phone network may put many fans behind one address).
    It counts in the split and in what right picks score from an account, or a fan whose cookie came with it, up to
    COUNTED_PER_ADDRESS of those on a question from one address (_counted_here): a fan this request made up (no
    cookie), or past that, has their pick, but it moves nobody else's numbers (predictions.pick)."""
    option = body.get("option")
    if not isinstance(option, str) or not 0 < len(option) <= 64:
        raise HTTPException(400, "option is one of the question's answers")
    address = client_address(request)
    hour = int(time.time() // 3600)
    if signed is not None:
        await _take(f"picks:fan:{fan}:{hour}", PICKS_PER_ACCOUNT, 7200, "That's a lot of picks for one hour. Try again in a while.")
    else:
        await _take_address("picks", _address_limits(address, PICKS_PER_ADDRESS, PICKS_PER_IPV6_48, PICKS_UNKNOWN_ADDRESS), str(hour), 7200,
                            "That's a lot of picks from your network. Try again in a while.")

    counted = signed is not None or (_cookie_fan(request) == fan and await _counted_here(question_id, address, fan))

    def call(db: Session) -> dict[str, Any]:
        try:
            q = predictions.pick(db, fan, question_id, option, counted=counted)
        except predictions.Refused as refused:
            raise _refused(db, refused)
        db.commit()  # before the answer: the tick polls its game from now on
        return predictions.shown(db, fan, [q], datetime.now(timezone.utc))[0]

    return {"question": await in_db(call)}


async def _counted_here(question_id: int, address: str | None, fan: str) -> bool:
    """Whether a fan without an account counts on this question from this address: one of the first
    COUNTED_PER_ADDRESS there, and from an IPv6 /64 of the first COUNTED_PER_IPV6_48 from its /48 (_address_limits),
    or counted there before (a changed pick)."""
    redis = state["redis"]
    added: list[str] = []
    for n, (who, most) in enumerate(_address_limits(address, COUNTED_PER_ADDRESS, COUNTED_PER_IPV6_48, COUNTED_PER_ADDRESS)):
        key = f"picks-counted:{question_id}:{who}"
        if not await redis.sadd(key, fan):
            if n == 0:
                return True  # counted here before
            continue  # counted from another of the /48's /64s before
        added.append(key)
        await redis.expire(key, COUNTED_FOR)
        if await redis.scard(key) > most:
            for taken in added:
                await redis.srem(taken, fan)  # past the address's share, or its /48's: not one of them
            if who.endswith("/48"):
                _at_ceiling("picks-counted", who)
            return False
    return True


@app.delete("/api/predictions/{question_id}/pick")
def take_back_pick(question_id: RowId, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Take a pick back while its question's open."""
    try:
        predictions.unpick(db, fan, question_id)
    except predictions.Refused as refused:
        raise _refused(db, refused)
    db.commit()
    q = db.get_one(Question, question_id)
    return {"question": predictions.shown(db, fan, [q], datetime.now(timezone.utc))[0]}


@app.get("/api/me/predictions")
def my_predictions(fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Today's calls: the questions still open on this fan's games (their players', teams', games they follow, and
    any they've picked) and on Today's big games coming up, soonest first, and their picks settled lately."""
    return predictions.today(db, fan)


@app.get("/api/me/leagues")
def my_leagues(fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """The leagues this fan is in, with their place in each (none for a fan who isn't signed in)."""
    return {"leagues": predictions.mine(db, fan)}


@app.post("/api/leagues")
async def make_league(body: dict = Body(...), fan: str = Depends(fan_id), signed: accounts.Signed | None = Depends(account_of)) -> dict[str, Any]:
    """A new league ({name, member}: the league's name, and the maker's in it), its maker in it. Needs an account (a
    401 without), LEAGUES_MADE_PER_HOUR a fan an hour."""
    if signed is None:
        raise HTTPException(401, predictions.SIGN_IN)
    await _take(f"leagues:made:{fan}:{int(time.time() // 3600)}", LEAGUES_MADE_PER_HOUR, 7200, "That's a lot of leagues for one hour. Try again later.")

    def make(db: Session) -> dict[str, Any]:
        try:
            made = predictions.create(db, fan, body.get("name"), body.get("member"))
        except predictions.Refused as refused:
            raise _refused(db, refused)
        db.flush()
        return predictions.league(db, fan, made.code)

    return {"league": await in_db(make)}


@app.get("/api/leagues/{code}")
def league_page(code: LeagueCode, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """A league's page, for its members: its table and its members' calls (each friend's once the question's
    closed). A 404 for anyone else: the invite page is theirs."""
    try:
        return {"league": predictions.league(db, fan, code)}
    except predictions.Refused as refused:
        raise _refused(db, refused)


@app.get("/api/leagues/{code}/invite")
async def league_invite(request: Request, code: LeagueCode, fan: str = Depends(fan_id), signed: accounts.Signed | None = Depends(account_of)) -> dict[str, Any]:
    """What an invite link shows anyone: the league's name, how many are in it, who made it, and whether this fan's
    in it already. INVITES_PER_HOUR a fan an hour, and INVITES_PER_ADDRESS from one address (a request with no cookie
    is a new fan each time). Counted as opened only for an account, or a fan whose cookie came with it."""
    hour = int(time.time() // 3600)
    address = client_address(request)
    await _take(f"leagues:invites:{fan}:{hour}", INVITES_PER_HOUR, 7200, "That's a lot of invites for one hour. Try again later.")
    await _take_address("leagues:invites-from", _address_limits(address, INVITES_PER_ADDRESS, INVITES_PER_IPV6_48, INVITES_UNKNOWN_ADDRESS), str(hour), 7200,
                        "That's a lot of invites from your network. Try again later.")
    counted = signed is not None or _cookie_fan(request) == fan

    def look(db: Session) -> dict[str, Any]:
        try:
            return predictions.invite(db, code, fan, counted)
        except predictions.Refused as refused:
            raise _refused(db, refused)

    return {"invite": await in_db(look)}


@app.post("/api/leagues/{code}/join")
async def join_league(code: LeagueCode, body: dict = Body(...), fan: str = Depends(fan_id), signed: accounts.Signed | None = Depends(account_of)) -> dict[str, Any]:
    """Join a league by its invite ({member}: this fan's name in it; joined already, their name changes). Needs an
    account (a 401 without); INVITES_PER_HOUR a fan an hour, with the invite pages they open."""
    if signed is None:
        raise HTTPException(401, predictions.SIGN_IN)
    await _take(f"leagues:invites:{fan}:{int(time.time() // 3600)}", INVITES_PER_HOUR, 7200, "That's a lot of invites for one hour. Try again later.")

    def join(db: Session) -> dict[str, Any]:
        try:
            joined = predictions.join(db, fan, code, body.get("member"))
        except predictions.Refused as refused:
            raise _refused(db, refused)
        db.commit()
        return predictions.league(db, fan, joined.code)

    return {"league": await in_db(join)}


@app.delete("/api/leagues/{code}/me")
def leave_league(code: LeagueCode, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Leave a league; the last one out takes it with them."""
    predictions.leave(db, fan, code)
    db.commit()
    return {"left": True}


@app.post("/api/leagues/{code}/shared")
def league_shared(code: LeagueCode, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, bool]:
    """A member shared the league's invite from the app (counted for the launch numbers)."""
    predictions.was_shared(db, fan, code)
    db.commit()
    return {"ok": True}


@app.get("/api/teams/{team_id}")
async def team_page(team_id: RowId, fan: str = Depends(fan_id)) -> dict[str, Any]:
    """A team's page (teams.py): who it is, whether this fan follows it and at what level, its game on now, its
    games to come and last results from its side, and its squad to follow. Served from its last read; one that's
    stale queues another for the worker (at most every teams.LOOK_AGAIN a team, and teams.READS a minute of a
    source's allowance, whichever teams fans look at: a crawler walking every team's page spends no more), so
    "refreshing" says newer games may come in a moment."""

    def view(db: Session) -> tuple[dict[str, Any] | None, str | None]:
        team = db.get(Team, team_id)
        if team is None:
            return None, None
        shown = teams.page(db, team_id, fan)
        return shown, team.system if shown is not None and teams.look_again(team) else None

    shown, asking = await in_db(view)
    if shown is None:
        raise HTTPException(404, "No such team.")
    refreshing = asking is not None and await _read_team(asking, team_id)
    if asking is not None and not refreshing:
        await asyncio.to_thread(teams.look_later, team_id)  # no room this minute: the next look asks again
    return {**shown, "refreshing": refreshing}


async def _read_team(system: str, team_id: int) -> bool:
    """Queue a read of a team's games, unless fans' looks and follows have asked teams.READS of its source's
    allowance this minute (a followed team is read by the next tick anyway). Whether it was queued."""
    try:
        spent = f"teams:reads:{match.allowance(system)}:{int(time.time() // 60)}"
        ask = await state["redis"].incr(spent) <= teams.READS
        await state["redis"].expire(spent, 120, nx=True)
        if ask:
            await enqueue("refresh_team", team_id, _job_id=f"refresh-team:{team_id}")
        return ask
    except Exception:
        log.warning("teams: couldn't ask for team %s's games", team_id, exc_info=True)
        return False


@app.put("/api/teams/{team_id}/follow", dependencies=[*TellsDevices, *CountsFollow])
async def follow_team(team_id: RowId, fan: str = Depends(fan_id)) -> dict[str, Any]:
    """Follow a team (teams.py): each of its games' moments, from both sides, reach this fan by their level, and
    its results from its side. No such team, or one of a sport turned off, is a 404; one whose source isn't
    running, a 409; a fan following teams.MAX teams already, or past the hour's follows (CountsFollow), a 429."""

    def save(db: Session) -> str | None:
        team = teams.follow(db, fan, team_id)
        db.commit()  # before the answer: the tick that reads and polls its games reads it from now on
        return team.system if teams.stale(team) else None

    try:
        stale = await in_db(save)
    except teams.Refused as refused:
        raise HTTPException(refused.status, refused.message)
    if stale is not None:
        await _read_team(stale, team_id)  # its games now, not at the next tick, within what fans' looks may ask
    return {"team_id": team_id, "following": True}


@app.delete("/api/teams/{team_id}/follow", dependencies=TellsDevices)
def unfollow_team(team_id: RowId, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Stop following a team: nothing more of its games is sent to this fan (their players in them still are, and
    a game they follow by itself)."""
    teams.unfollow(db, fan, team_id)
    db.commit()
    return {"team_id": team_id, "following": False}


@app.put("/api/teams/{team_id}/alerts", dependencies=TellsDevices)
def set_team_alerts(team_id: RowId, body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Which of a followed team's games' moments the fan is told about: everything, key, results or off, as for a player."""
    level = body.get("level")
    if level not in moments.LEVELS:
        raise HTTPException(400, f"level must be one of {', '.join(moments.LEVELS)}")
    if not teams.set_level(db, fan, team_id, level):
        raise HTTPException(404, "Follow the team first.")
    db.commit()
    return {"team_id": team_id, "alerts": level}


@app.get("/api/league")
async def league_games(response: Response, tz: Annotated[int, Query(ge=-12 * 60, le=14 * 60)] = 0) -> dict[str, Any]:
    """Today's big results (since yesterday, else the latest day that had some: days where the fan is, tz
    minutes east of UTC) and big games coming up (the next fortnight) in the competitions the running
    sources cover, whoever follows them, ranked by each sport, the sports taking turns (league.py). The same
    for every fan in a time zone, so no cookie and a public cache. Served from the last reads; a source's
    read a few hours old queues another for the worker, so "refreshing" says new games may come in a moment."""
    games, stale = await in_db(league.cached, tz)
    for system in stale:
        try:
            # One read asked for an hour, whether the last one came or failed: a source that's down isn't
            # asked again on every look.
            if await state["redis"].set(f"league:asked:{system}", "1", nx=True, ex=league.ASK):
                await enqueue("read_league", system, _job_id=f"read_league:{system}")
        except Exception:
            log.warning("league: couldn't ask for %s's games", system, exc_info=True)
    # While a read is due, the app looks again in a moment: nothing in between may keep this answer.
    response.headers["cache-control"] = "no-cache" if stale else f"public, max-age={league.CACHE}"
    return {**games, "refreshing": bool(stale)}  # a read is on its way, or was asked for within the hour


@app.get("/api/starters")
async def starter_players(fan: str = Depends(fan_id)) -> dict[str, Any]:
    """Who a new fan is offered to follow, by sport (starters.py): the welcome's picker, an empty Today's,
    and search's "Try" names. Rows are search results, so following one is the same call."""
    return {"sports": await in_db(starters.offer, fan)}


# ---------------------------------------------------------------- campaign pages (campaigns.py)


def _campaign_slug(slug: str) -> str:
    """The campaign at a link's address, whatever its case (stann.in/Gukesh is /gukesh); a 404 for one no campaign can have."""
    found = campaigns.address(slug)
    if found is None:
        raise HTTPException(404, "No such page.")
    return found


@app.get("/api/campaigns/{slug}")
async def campaign(slug: str, fan: str = Depends(fan_id)) -> dict[str, Any]:
    """A campaign page (/<slug>): its words, its players and its teams' players as result rows (whether this
    fan follows each), its games, and the questions of its players' games to call (predictions.py)."""

    def view(db: Session) -> dict[str, Any] | None:
        shown = campaigns.page(db, _campaign_slug(slug), fan)
        if shown is not None:
            rows = [*shown["players"], *(p for t in shown["teams"] for p in t["players"])]
            shown["predictions"] = []  # a fault there leaves the page as it was
            try:
                with db.begin_nested():
                    shown["predictions"] = predictions.for_players(db, fan, [r["player_id"] for r in rows if r.get("player_id")])
            except Exception:
                log.exception("campaign %s: predictions", slug)
        return shown

    shown = await in_db(view)
    if shown is None:
        raise HTTPException(404, "No such page.")
    return shown


CAMPAIGN_FOLLOWS_AT_ONCE = 3  # a whole team's follows, a few at a time: each may ask its source and take a pooled connection


async def _follow_row(row: dict[str, Any], fan: str, address: str | None, turns: asyncio.Semaphore) -> int | None:
    """Follow one of a campaign's rows as the app would (key alerts, the default); None if it couldn't be, for
    whatever reason, so one player's failure never fails the rest."""
    target = campaigns.to_follow(row)
    if target is None:
        return None
    async with turns:
        try:
            if "player_id" in target:
                # On Sports Follow with a card that isn't failed (search._ours offers no other): the follow row, and
                # the build again for a card still building, as /api/follows does, in case its job was lost.
                def save(db: Session) -> str | None:
                    _room_to_follow(db, fan, target["player_id"])
                    db.execute(insert(Follow).values(fan_id=fan, player_id=target["player_id"], alert_rules={}).on_conflict_do_nothing())
                    return db.scalar(select(PlayerCard.status).where(PlayerCard.player_id == target["player_id"]))

                if await in_db(save) == "building":
                    await enqueue("build_card", target["player_id"])
                return target["player_id"]
            return await _follow_athlete(target, fan, address)
        except HTTPException as exc:
            log.info("campaign follow of %s: %s", row.get("key"), exc.detail)
        except Exception:
            log.warning("campaign follow of %s failed", row.get("key"), exc_info=True)
        return None


@app.post("/api/campaigns/{slug}/follow", dependencies=TellsDevices)
async def campaign_follow(request: Request, slug: str, body: dict = Body(...), fan: str = Depends(fan_id)) -> dict[str, Any]:
    """Follow a whole team in one tap ({"team": its id}: every player on its roster), or all of the campaign's
    own players ({"players": true}). Each is an ordinary follow with key alerts, of a player a running source
    has, so none is an AI research; all of them are one follow in the hour's (_take_follows: past them, a 429).
    Returns the players now followed and how many couldn't be."""
    shown = await in_db(campaigns.page, _campaign_slug(slug), fan)
    if shown is None:
        raise HTTPException(404, "No such page.")
    if isinstance(body.get("team"), str):
        rows = next((t["players"] for t in shown["teams"] if t["team_id"] == body["team"]), None)
        if rows is None:
            raise HTTPException(404, "That team isn't on this page.")
    elif body.get("players") is True:
        rows = shown["players"]
    else:
        raise HTTPException(400, "Say which: a team, or the players.")
    address = client_address(request)
    already = [r["player_id"] for r in rows if r.get("following") and r.get("player_id")]
    to_follow = [r for r in rows if not (r.get("following") and r.get("player_id"))]
    # One follow in the hour's, however many players: a campaign's are an admin's few, and a launch link's first tap
    # from a phone network's address mustn't use up its hour. Given back if none could be followed.
    taken = await _take_follows(fan, address) if to_follow else []
    turns = asyncio.Semaphore(CAMPAIGN_FOLLOWS_AT_ONCE)
    done = await asyncio.gather(*(_follow_row(r, fan, address, turns) for r in to_follow))
    if all(pid is None for pid in done):
        await give_back_research(taken)
    followed = already + [pid for pid in done if pid is not None]
    return {"player_ids": list(dict.fromkeys(followed)), "failed": sum(pid is None for pid in done)}


@app.get("/api/campaigns/{slug}/share.png")
async def campaign_picture(slug: str) -> Response:
    """The picture a link to a campaign page shows: its title on its sport's colour, its next game."""
    shown = await in_db(campaigns.page, _campaign_slug(slug), None)
    if shown is None:
        raise HTTPException(404, "No such page.")
    return await _picture(share.of_campaign(shown).picture)


# ---------------------------------------------------------------- where fans come from

TAG_MOST = 60  # a link's tag, in characters (FanSource's columns)
SOURCES_PER_ADDRESS = 100  # visits kept a UTC day from one client address: a script can't fill a campaign's numbers
SOURCES_PER_IPV6_48 = 400  # a day, from one IPv6 /48's /64s together (_address_limits)
SOURCES_UNKNOWN_ADDRESS = 1000  # a day, from every client without a public address together


def _tag(value: Any) -> str | None:
    """A link's tag as a post writes it (s=share, utm_source=whatsapp, utm_campaign=Gukesh v Sindarov): any printable
    text, its spaces trimmed and made single, up to TAG_MOST characters; None for anything else."""
    tag = " ".join(value.split()) if isinstance(value, str) else ""
    return tag if 0 < len(tag) <= TAG_MOST and tag.isprintable() else None


@app.post("/api/me/source")
async def visit_source(request: Request, body: dict = Body(...), fan: str = Depends(fan_id), signed: accounts.Signed | None = Depends(account_of)) -> dict[str, bool]:
    """Where this fan came from: the tags on the first link they opened (s=, utm_source, utm_medium,
    utm_campaign) and its page, kept once (FanSource); a later link's are not. A tag in any other shape is
    left out (_tag), and a visit with none isn't kept. Nor is one from a fan the request itself made up (no
    cookie: the app's page, and the Android app's first request, make the device's fan before it tells this),
    nor past SOURCES_PER_ADDRESS a day from one address (or SOURCES_PER_IPV6_48 from an IPv6 /48's /64s together):
    a script can't fill the launch numbers."""
    tags = {key: tag for key in ("s", "utm_source", "utm_medium", "utm_campaign") if (tag := _tag(body.get(key)))}
    landing = body.get("path")
    if not tags or not isinstance(landing, str) or not landing.startswith("/") or len(landing) > 200 or not landing.isprintable():
        return {"ok": True}
    if signed is None and _cookie_fan(request) != fan:
        return {"ok": True}
    for who, most in _address_limits(client_address(request), SOURCES_PER_ADDRESS, SOURCES_PER_IPV6_48, SOURCES_UNKNOWN_ADDRESS):
        key = f"sources:{who}:{int(time.time() // DAY)}"
        used = await state["redis"].incr(key)
        await state["redis"].expire(key, 2 * DAY)
        if used > most:
            if who.endswith("/48"):
                _at_ceiling("sources", who)
            return {"ok": True}
    row = {"fan_id": fan, "tag": tags.get("s"), "utm_source": tags.get("utm_source"), "utm_medium": tags.get("utm_medium"), "utm_campaign": tags.get("utm_campaign"), "landing": landing}

    def keep(db: Session) -> None:
        db.execute(insert(FanSource).values(**row).on_conflict_do_nothing(index_elements=["fan_id"]))

    await in_db(keep)
    return {"ok": True}


# ---------------------------------------------------------------- AI research allowance


DAY = 24 * 3600


def client_address(request: Request) -> str | None:
    """Who is asking, for per-address limits: the client's IP as uvicorn sees it (behind the proxy,
    --proxy-headers sets it), or an IPv6 client's /64, since one home or phone holds a whole one. None
    for an address that isn't public: this machine, or the proxy when its forwarded address wasn't
    trusted, would put every fan behind one address."""
    try:
        ip = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_global:
        return None
    return str(ipaddress.ip_network(f"{ip}/64", strict=False)) if ip.version == 6 else str(ip)


# How big "one address" is, for every per-address limit (BUG-106, BUG-299, BUG-326). An IPv4 address is one, though it
# may be a phone network's, shared by thousands of its fans (carrier-grade NAT), so its limits are generous. An IPv6
# client's is its /64 (client_address), a household's or a phone's. Its /48 (_network) is neither: a free tunnel or a
# fibre plan gives one person a whole /48, and a phone network may share one among many subscribers. So an IPv6 limit
# is a /64's, and a /48's /64s together have a ceiling of their own, several /64s' worth (_address_limits); a /48 at
# its ceiling is logged (_at_ceiling), a script or a crowd for someone to look at.
limits_log = logging.getLogger("sports_follow.limits")
_ceilings_logged: dict[tuple[str, str], float] = {}  # (limit, network): when this process last logged it at its ceiling


def _address_limits(address: str | None, per_address: int, per_48: int, unknown: int | None = None) -> list[tuple[str, int]]:
    """Who a per-address limit counts a client as, and how many each may: an IPv4 address, `per_address`; an IPv6
    client's /64, `per_address`, and its /48, `per_48` for all of its /64s together; a client without a public
    address, `unknown` for every such client together (none at all when None)."""
    if not address:
        return [] if unknown is None else [("unknown", unknown)]
    if ":" not in address:
        return [(address, per_address)]
    return [(address, per_address), (_network(address), per_48)]


def _at_ceiling(limit: str, network: str) -> None:
    """Log a network (an IPv6 /48, or an IPv4 /24's streams) at its ceiling for a limit, once an hour in each process:
    so much from all its addresses together is a script, or a phone network's crowd that the ceiling may need to make
    room for. Named by a hash, as fans are: the log never holds a client's address (RequestLog)."""
    now = time.monotonic()
    if now - _ceilings_logged.get((limit, network), float("-inf")) < 3600:
        return
    if len(_ceilings_logged) >= 1000:  # a process's memory of them stays small
        _ceilings_logged.clear()
    _ceilings_logged[limit, network] = now
    limits_log.warning("limits: a /%s reached its ceiling for %s (%s)", network.rpartition("/")[2], limit, hashlib.sha256(network.encode()).hexdigest()[:12])


async def _take_address(limit: str, address_limits: list[tuple[str, int]], when: str, ttl: int, refusal: str) -> None:
    """Count one against each of a client's per-address limits (_address_limits) in turn, keyed "<limit>:<who>:<when>";
    past any, a 429 (the counts taken stay: the limit is on attempts), and a /48 past its ceiling is logged."""
    for who, most in address_limits:
        try:
            await _take(f"{limit}:{who}:{when}", most, ttl, refusal)
        except HTTPException:
            if who.endswith("/48"):
                _at_ceiling(limit, who)
            raise


async def research_used(fan: str) -> int:
    return int(await state["redis"].get(player_search.research_key(fan)) or 0)


RESEARCH_PER_IPV6_48 = 4 * RESEARCH_PER_ADDRESS  # a UTC day, from one IPv6 /48's /64s together (_address_limits)


async def take_research(fan: str, address: str | None, used_up: str) -> list[str]:
    """Count one AI research against the fan's UTC day, their address's day (and an IPv6 /64's /48's) and this hour
    across all fans. Returns the counts taken, for give_back_research; when any is used up, nothing is counted and
    the fan gets a 429 (`used_up` when it's their own three)."""
    now = datetime.now(timezone.utc)
    limits = [(player_search.research_key(fan, now), player_search.RESEARCH_PER_DAY, 2 * DAY, used_up)]
    limits += [(player_search.research_address_key(who, now), most, 2 * DAY, "Your network has used today's AI researches. Try again tomorrow.")
               for who, most in _address_limits(address, RESEARCH_PER_ADDRESS, RESEARCH_PER_IPV6_48)]
    limits.append((player_search.research_hour_key(now), RESEARCH_PER_HOUR, 2 * 3600, f"{APP_NAME} has done all the AI research it can this hour. Try again later."))
    taken: list[str] = []
    for key, limit, ttl, refusal in limits:
        used = await state["redis"].incr(key)
        taken.append(key)
        await state["redis"].expire(key, ttl)
        if used > limit:
            await give_back_research(taken)
            if address and ":" in address and key == player_search.research_address_key(_network(address), now):
                _at_ceiling("research", _network(address))
            raise HTTPException(429, refusal)
    return taken


# One count back, only while the count is above zero: a key that expired or was lost since it was taken
# isn't made again (without its expiry, and below zero).
GIVE_BACK = "local n = tonumber(redis.call('GET', KEYS[1])) or 0 if n > 0 then return redis.call('DECR', KEYS[1]) end return 0"


async def give_back_research(taken: list[str]) -> None:
    """A research that didn't happen goes back on the counts it was taken from (the day it was taken)."""
    for key in taken:
        await state["redis"].eval(GIVE_BACK, 1, key)


@app.get("/api/me/research")
async def research(fan: str = Depends(fan_id)) -> dict[str, Any]:
    return player_search.research_status(await research_used(fan))


# ---------------------------------------------------------------- following

def _room_to_follow(db: Session, fan: str, player_id: int | None = None) -> None:
    """Refuse (429) a follow past follows.MAX, of the follows that count (follows.counted: not a turned-off sport's).
    A player the fan follows already is no new follow. With the player, in the follow's transaction: one at a time
    per fan, so two follows at once can't both pass the count; without, before anything is asked of a source."""
    if player_id is not None:
        db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"follow:{fan}", 0))))
        if db.scalar(select(Follow.id).where(Follow.fan_id == fan, Follow.player_id == player_id)) is not None:
            return
    if follows.counted(db, fan) >= follows.MAX:
        raise HTTPException(429, f"You follow {follows.MAX} players already: unfollow one to follow another.")


def _registry_athlete(db: Session, body: dict) -> registry.Person | None:
    qid, sport = str(body.get("qid") or ""), str(body.get("sport") or "").lower()
    if not re.fullmatch(r"Q[0-9]{1,12}", qid) or len(sport) > 60 or not sport.isprintable() or not sports.on(sport):
        return None
    # Of the sports that run: with no sport given, a row of a turned-off one isn't taken for them.
    athlete = registry.get(db, qid, sport or None, only_running=True)
    return registry.Person.of(athlete) if athlete else None


def _registry_holder(db: Session, person: registry.Person, said: bool) -> int | None:
    """The player already here for a registry athlete (pipeline.registry_holder) of a sport that runs. With no sport
    said, whoever holds their qid, unless that's their player in a sport turned off: then their player in the sport
    _registry_athlete took, as if it had been said. Still one of a sport turned off, a 404 (BUG-097)."""
    def runs(player_id: int) -> bool:
        return sports.on(db.scalar(select(Player.sport).where(Player.id == player_id)))

    holder = pipeline.registry_holder(db, person.qid, person.sport if said else None)
    if holder is not None and not said and not runs(holder):
        holder = pipeline.registry_holder(db, person.qid, person.sport)
    if holder is not None and not runs(holder):
        raise HTTPException(404, "No such athlete.")
    return holder


LEAGUE = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")  # "nba", "wta", "cs-go", "pubgmobile"


def _source_id_ok(system: str, athlete_id: str) -> bool:
    """An id goes into the source's URL or query as it is, so one of any other shape than the source's
    (adapters.base.Adapter.id_pattern) is refused before anything is sent."""
    adapter = KNOWN.get(system)
    return adapter is not None and athlete_id.isprintable() and bool(adapter.id_pattern.fullmatch(athlete_id))


def _league_ok(league: Any) -> bool:
    return league is None or (isinstance(league, str) and bool(LEAGUE.fullmatch(league)))


def _source_has_none(exc: AdapterError) -> bool:
    """The source answered that it has no such athlete (a 404), as opposed to not answering."""
    return type(exc).__name__ == "NotFound" or "HTTP 404" in str(exc)


def _holder(db: Session, system: str, external_id: str) -> int | None:
    """The player already here with this source (or registry) id."""
    return db.scalar(select(PlayerIdentity.player_id).where(PlayerIdentity.system == system, PlayerIdentity.external_id == external_id))


def _same_athlete(person: registry.Person, ref: Any) -> bool:
    """Is the registry athlete sent with a pick the picked source athlete? The registry has this id for
    them, or has none in this source and the source's own record of the athlete (read by id here) matches
    them by name and birth date, or name and team (registry.same_person). The client sends both, so a
    crafted pair mustn't point the registry athlete at someone else, a namesake or a stranger born the same
    day, for every fan after."""
    adapter = ADAPTERS.get(ref.system)
    if adapter is None or person.sport not in adapter.sports:
        return False
    known = person.ids.get(ref.system)
    return known == ref.athlete_id if known else registry.same_person(person, ref)


async def _resolve(person: registry.Person) -> tuple[Any, Any] | None:
    try:
        return await asyncio.to_thread(registry.resolve, person)
    except AdapterError as exc:
        log.warning("resolve %s: %s", person.qid, exc)
        raise HTTPException(502, "Couldn't reach the live source to find this athlete. Try again in a moment.")


async def _follow_athlete(body: dict, fan: str, address: str | None) -> int:
    """Follow the exact athlete a fan picked in search, by registry id or source id; never by name.

    A registry athlete is found in their sport's live source by the id Wikidata has, or by name and a
    matching birth date; one no source confirms is built by the research agent, which counts as an
    AI research, and without research (config.RESEARCH) can't be followed. Asking a source counts against
    the fan's and their address's minute (_source_lookup), and a fan follows at most follows.MAX players.
    """
    from .config import RESEARCH

    person = await in_db(_registry_athlete, body) if body.get("qid") else None
    if body.get("qid") and person is None:
        raise HTTPException(404, "That athlete isn't in the player registry.")
    await in_db(_room_to_follow, fan)
    taken: list[str] = []
    if person and not body.get("system"):
        holder = await in_db(_registry_holder, person, bool(body.get("sport")))
        if holder is not None:
            player_id, created, ref = holder, False, None
        else:
            await _source_lookup(fan, address)
            found = await _resolve(person)
            if found is None and not RESEARCH:
                raise HTTPException(404, f"No live source has {person.name}'s games, so they can't be followed here yet.")
            if found is None:
                taken = await take_research(fan, address, "No live source has this athlete, so following them needs an AI research, and you've used today's. Try again tomorrow.")
                try:
                    player_id, created = await asyncio.to_thread(pipeline.start_from_registry, person)
                except Exception:
                    await give_back_research(taken)
                    raise
                if not created:
                    await give_back_research(taken)  # another fan's research is building them
                    taken = []
                ref = None
            else:
                adapter, ref = found
                if await in_db(registry.removed, structured.sport_name(adapter, ref), ref):
                    raise HTTPException(404, f"That athlete isn't on {APP_NAME}.")  # another registry row of someone removed
                if not sports.on(structured.sport_name(adapter, ref)):
                    raise HTTPException(404, "No such athlete.")  # an esports pro's game turned off, though their source runs
                player_id, created = await asyncio.to_thread(pipeline.start_from_pick, adapter, ref)
    else:
        system = str(body.get("system") or "")
        athlete_id = str(body.get("athlete_id") or "").strip()
        league = body.get("league") or None
        adapter = ADAPTERS.get(system)
        if adapter is None or not _source_id_ok(system, athlete_id) or not _league_ok(league):
            raise HTTPException(400, "Pick an athlete from the search results.")
        if (system, athlete_id) in await in_db(registry.blocked):
            raise HTTPException(404, f"That athlete isn't on {APP_NAME}.")  # removed by an operator (players.py)
        await _source_lookup(fan, address)
        try:
            ref = await asyncio.to_thread(adapter.player, athlete_id, league)
        except AdapterError as exc:
            log.warning("follow %s:%s: %s", system, athlete_id, exc)
            raise HTTPException(502, "Couldn't reach the live source to confirm this athlete. Try again in a moment.")
        if ref is None:
            raise HTTPException(404, "That athlete isn't in the source any more.")
        if await in_db(registry.removed, structured.sport_name(adapter, ref), ref):
            # Removed, in a source that had no id for them then: their name and birth date say who they are.
            raise HTTPException(404, f"That athlete isn't on {APP_NAME}.")
        if not sports.on(structured.sport_name(adapter, ref)):
            # A source of several games runs while any of them does: a pro of one turned off isn't followed.
            raise HTTPException(404, "No such athlete.")
        player_id, created = await asyncio.to_thread(pipeline.start_from_pick, adapter, ref)

    def save(db: Session) -> None:
        _room_to_follow(db, fan, player_id)
        if person and (ref is None or _same_athlete(person, ref)):
            registry.link(db, player_id, person.qid)
        db.execute(insert(Follow).values(fan_id=fan, player_id=player_id, alert_rules={}).on_conflict_do_nothing())

    try:
        await in_db(save)  # committed before the jobs are queued and the fan is answered
    except Exception:
        await give_back_research(taken)
        raise
    if created:
        # With a live source: scores and fixtures in seconds, and the profile from Wikidata and Wikipedia.
        # Without one: the profile, and the agent's research for an athlete Wikipedia has no article on.
        if ref is not None:
            await enqueue("refresh_structured", player_id, _job_id=f"refresh-structured:{player_id}")
        await enqueue("build_card", player_id)
    return player_id


@app.post("/api/follows", dependencies=[*TellsDevices, *CountsFollow])
async def follow(request: Request, body: dict = Body(...), fan: str = Depends(fan_id)) -> dict[str, Any]:
    from .config import RESEARCH

    if body.get("system") or body.get("qid"):
        player_id = await _follow_athlete(body, fan, client_address(request))
        return {"player_id": player_id, "card": await read_card(player_id)}
    if body.get("player_id") is not None:
        # A player already here, by id: a name could belong to their namesake ("nikola-jokic-football").
        # A whole number only: int() made 1.9 and true player 1.
        wanted = body["player_id"]
        if not isinstance(wanted, int) or isinstance(wanted, bool):
            raise HTTPException(400, "player_id must be a whole number.")
        player = await in_db(lambda db: db.get(Player, wanted)) if 1 <= wanted <= MAX_ID else None
        if player is None or not sports.on(player.sport):  # a turned-off sport's isn't offered, by id or otherwise
            raise HTTPException(404, "No such player.")
        known = await in_db(lambda db: db.get(PlayerCard, player.id))
        query = None
    else:
        query = str(body.get("query") or "").strip()
        if not 2 <= len(query) <= 80 or not query.isprintable():
            raise HTTPException(400, "Type a player's name, 2 to 80 characters.")
        # The name is kept as typed and shown to every fan of the player: no markup in it, even though
        # the web app only ever shows it as text.
        if "<" in query or ">" in query:
            raise HTTPException(400, "A player's name can't have < or > in it.")
        known = await in_db(lambda db: db.scalar(select(PlayerCard).join(PlayerAlias, PlayerAlias.player_id == PlayerCard.player_id).where(PlayerAlias.alias == pipeline.slugify(query))))
        player = None
    if known is not None and known.status == "failed" and await state["redis"].get(pipeline.not_found_key(known.player_id)):
        # The sources found only athletes nobody here follows by the name today, so no research was run:
        # asking again would find the same, and isn't counted or built again until the day is out.
        raise HTTPException(404, pipeline.NOT_FOUND_TEXT)
    # A name nobody has followed (or a page whose build failed) sends the research agent out: that is
    # the part with a daily allowance. Following a player already here costs nothing.
    counted = known is None or known.status == "failed"
    if counted and not RESEARCH:
        # Without research (config.RESEARCH) a page comes from a live source, picked in search, or not at all.
        raise HTTPException(404, "No live source has this player, so their page can't be built." if known is not None else "Pick an athlete from the search results: pages here come from live sources.")
    taken = await take_research(fan, client_address(request), "You've used today's AI researches. Pick a player from the search results, or try again tomorrow.") if counted else []

    def save(db: Session) -> tuple[int, bool, dict[str, Any] | None] | None:
        """The follow, in one transaction: (player id, needs a build, the card to republish), or None for a name with nothing to search by."""
        try:
            target = pipeline.resolve_or_create(db, query) if query is not None else player
        except ValueError:
            return None
        if not sports.on(cast(Player, target).sport):
            raise HTTPException(404, "No such player.")  # a turned-off sport's page, by its name (by id: above)
        _room_to_follow(db, fan, cast(Player, target).id)  # (a player asked for by id was found above)
        db.execute(insert(Follow).values(fan_id=fan, player_id=target.id, alert_rules={}).on_conflict_do_nothing())
        card_row = db.get(PlayerCard, target.id)
        needs_build = card_row is None or card_row.status in ("building", "failed")
        retrying = card_row is not None and card_row.status == "failed"
        if retrying:
            # A fan asking again is the retry for a failed build.
            card_row.status = "building"
            card_row.error = None
            card_row.card = {**pipeline.placeholder_card(target), "version": card_row.version}
        return target.id, needs_build, card_row.card if retrying else None

    try:
        saved = await in_db(save)
    except Exception:
        await give_back_research(taken)
        raise
    if saved is None:
        await give_back_research(taken)
        raise HTTPException(400, "Type a player's name, 2 to 80 characters.")
    player_id, needs_build, retried = saved
    if retried is not None:
        # Write through to the hot copy and tell every subscriber, or they keep seeing "failed".
        bus.store_card(player_id, retried)
    if needs_build:
        await enqueue("build_card", player_id)
    card = await read_card(player_id)
    return {"player_id": player_id, "card": card}


@app.put("/api/follows/{player_id}/alerts", dependencies=TellsDevices)
def set_alerts(player_id: RowId, body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Which of this player's moments the fan is told about: everything, key, results or off."""
    level = body.get("level")
    if level not in moments.LEVELS:
        raise HTTPException(400, f"level must be one of {', '.join(moments.LEVELS)}")
    row = db.scalar(select(Follow).where(Follow.fan_id == fan, Follow.player_id == player_id))
    if row is None:
        raise HTTPException(404, "Follow the player first.")
    row.alert_rules = {**(row.alert_rules or {}), "level": level}
    # Committed before the answer, as in every write route here: get_db's own commit runs only after the
    # response is sent, so a read straight after would see the old level, and a failed commit said OK.
    db.commit()
    return {"player_id": player_id, "alerts": level}


# ---------------------------------------------------------------- accounts (accounts.py)

CODE_LIFE = 600  # seconds an emailed code works
# A code's wrong tries, and an address's in a day, are counted by the network they came from: a client address (an IPv4
# address, or an IPv6 /64), not its /48, which a phone network shares among its subscribers (BUG-326).
CODE_TRIES = 5  # wrong codes from one network before it stops working there
CODE_TRIES_ELSEWHERE = 10  # wrong codes from every network but the one that asked for it, together: then it works only there
RESEND_AFTER = 60  # seconds before another code to the same address
CODES_PER_EMAIL = 5  # an hour, to one inbox (accounts.inbox): its plus-addresses don't each get five (BUG-296)
CODES_PER_ADDRESS = 20  # an hour, from one client address (an IPv4 address, or an IPv6 /64)
CODES_UNKNOWN_ADDRESS = 100  # an hour, from every client without a public address together
CODES_PER_ADDRESS_DAY = 20  # a UTC day, from one client address (an IPv4 address, or an IPv6 /64)
CODES_PER_NETWORK_DAY = 200  # at most, a UTC day, to new addresses from one IPv6 /48's /64s together (_new_codes_from_a_48)
CODES_PER_HOUR = 200  # an hour, to everyone: the mail provider's bill
KEPT_FOR_ACCOUNTS = 0.3  # of the day's codes (MAIL_CODES_PER_DAY), kept for accounts that have stood a day (accounts.standing)
FAILS_PER_DAY = 20  # wrong codes for one address from one network in a UTC day, across new codes
BLOCKED = "This account has been blocked."  # by an admin (admin.py)
TOO_MANY_WRONG = "Too many wrong codes. Ask for a new one."


def _new_codes_a_day() -> int:
    """The day's codes for addresses without an account that has stood a day: the mail plan's, less those kept."""
    return MAIL_CODES_PER_DAY - round(MAIL_CODES_PER_DAY * KEPT_FOR_ACCOUNTS)


def _new_codes_from_a_48() -> int:
    """The day's codes to new addresses from one IPv6 /48's /64s together: half of all new addresses' (and at most
    CODES_PER_NETWORK_DAY), so one /48, a free tunnel's, can't leave nobody else able to sign up that day (BUG-093)."""
    return min(CODES_PER_NETWORK_DAY, _new_codes_a_day() // 2)


async def _take(key: str, limit: int, ttl: int, refusal: str) -> None:
    """Count one against a limit; over it, a 429 (the count stays: the limit is on attempts)."""
    used = await state["redis"].incr(key)
    await state["redis"].expire(key, ttl)
    if used > limit:
        raise HTTPException(429, refusal)


def _network(address: str | None) -> str:
    """The network a client address (client_address's) is in: an IPv6 client's /48, whose /64s together have a
    ceiling on each per-address limit (_address_limits); else the address itself; "unknown" without a public one."""
    if not address:
        return "unknown"
    network = ipaddress.ip_network(address, strict=False)
    return str(network.supernet(new_prefix=48)) if network.version == 6 else address


def _clear_cookies_for_anonymous(request: Request, response: Response) -> None:
    """After signing out or deleting the account: this device is a new, empty anonymous fan."""
    response.delete_cookie(accounts.SESSION_COOKIE, httponly=True, samesite="lax", secure=_secure(request))
    _set_fan(request, response, secrets.token_urlsafe(24))


async def _forget_sessions(token_hashes: list[str]) -> None:
    """Ended sessions are signed out at once: their cached copies say so ("") rather than going, since a
    request that looked one up just before it ended would cache it again for SESSION_CACHE otherwise."""
    if token_hashes:
        async with state["redis"].pipeline() as ended:  # one round trip, and all of them or none (MULTI)
            for h in token_hashes:
                ended.set(f"session:{h}", "", ex=SESSION_CACHE)
            await ended.execute()


async def _tell_signed_out(fan: str | None) -> None:
    """Sessions of the account whose fan this is ended (signed out, everywhere or here, the account deleted or blocked),
    their cached copies already saying so: a word on the fan's own channel, and each of the account's open streams asks
    for its session again (my_stream). One whose session ended tells its app, which starts over as who it is now, and
    ends: an app left open in front was told nothing, and kept the account and its moments (BUG-162). A courtesy: an
    app also asks as it wakes, and its stream's next connect is as who it is."""
    if fan is None:
        return
    try:
        await state["redis"].publish(bus.follows_channel(fan), "{}")
    except Exception as exc:  # noqa: BLE001 — the sessions have ended either way
        log.warning("accounts: couldn't tell the account's open apps it was signed out: %s", exc)


async def _sign_in(request: Request, response: Response, account_id: int, signed: accounts.Signed | None) -> dict[str, Any]:
    """Sign this device in to the account, bringing what its anonymous fan had (accounts.adopt). A
    session this device already had (signing in again, or as someone else) ends. The answer says how many
    players came, and how many of the device's teams didn't fit in the account (teams.MAX), so the fan is told."""
    anon = await asyncio.to_thread(_cookie_fan, request)
    done = await in_db(accounts.sign_in, account_id, anon, signed.session_id if signed else None)
    if signed:
        await _forget_sessions([accounts.token_hash(request.cookies[accounts.SESSION_COOKIE])])
    if anon:
        # Today's AI researches stay counted: signing in doesn't give three more.
        mine, theirs = player_search.research_key(anon), player_search.research_key(done.fan_id)
        used = int(await state["redis"].get(mine) or 0)
        if used > 0:
            await state["redis"].incrby(theirs, used)
            await state["redis"].expire(theirs, 2 * DAY)
        await state["redis"].delete(mine)
        # A follow or a device sent just before this, still under way, lands on the anonymous fan: move it too.
        await enqueue("adopt_stragglers", anon, account_id, done.session_id, _defer_by=60)
    _set_session(request, response, done.token)
    response.delete_cookie(FAN_COOKIE, httponly=True, samesite="lax", secure=_secure(request))
    return {**(await in_db(accounts.describe, account_id) or {}), "players_added": done.players, "teams_dropped": done.teams_dropped}


@app.get("/api/me/account")
async def my_account(request: Request, response: Response, signed: accounts.Signed | None = Depends(account_of)) -> dict[str, Any]:
    """Who this device is signed in as. The app asks each time it starts, which also renews the session."""
    if signed is None:
        if request.cookies.get(accounts.SESSION_COOKIE):
            # Signed out elsewhere, deleted, or unused for a year: say so, and drop the cookie.
            response.delete_cookie(accounts.SESSION_COOKIE, httponly=True, samesite="lax", secure=_secure(request))
            return {"signed_in": False, "reason": "signed_out"}
        return {"signed_in": False}
    about = await in_db(accounts.describe, signed.account_id)
    if about is None:
        return {"signed_in": False, "reason": "signed_out"}
    if await in_db(accounts.bump, signed.session_id):
        _set_session(request, response, request.cookies[accounts.SESSION_COOKIE])
    return about


@app.post("/api/me/start")
async def app_start(request: Request, response: Response, signed: accounts.Signed | None = Depends(account_of)) -> dict[str, Any]:
    """The Android app's first request as it starts (mobile/src/account.tsx): who this device is signed in as,
    as GET /api/me/account answers, and, signed in to nothing, its anonymous fan, made now if it has none, so
    the requests the app sends together next are all one fan's (a read makes none: _fan). The web app's page
    does the same (spa)."""
    about = await my_account(request, response, signed)
    if not about.get("signed_in"):
        await asyncio.to_thread(_fan, request, response, None, True)
    return about


@app.post("/api/auth/email/start")
async def email_start(request: Request, body: dict = Body(...)) -> dict[str, Any]:
    """Email a 6-digit sign-in code. Part of the day's codes is kept for addresses whose account has stood a day
    (KEPT_FOR_ACCOUNTS)."""
    if not mail.configured(_secure(request)):
        raise HTTPException(503, "Signing in by email isn't set up on this server yet.")
    email = accounts.normal_email(body.get("email"))
    if email is None:
        raise HTTPException(400, "That doesn't look like an email address.")
    who, now = accounts.email_key(email), datetime.now(timezone.utc)
    hour, day = now.strftime("%Y-%m-%dT%H"), now.strftime("%Y-%m-%d")
    address = client_address(request)
    network = address or "unknown"  # where its wrong codes are counted
    # Wrong codes from this network only: anyone's elsewhere, another subscriber's on the fan's phone network among
    # them, don't shut the fan out for the day (BUG-092, BUG-326).
    if int(await state["redis"].get(f"auth-fails:{who}:{network}:{day}") or 0) >= FAILS_PER_DAY:
        raise HTTPException(429, "Too many wrong codes for this address today. Try again tomorrow.")
    if not await state["redis"].set(f"auth-wait:{who}", "1", nx=True, ex=RESEND_AFTER):
        # Asked again within the minute (a second tap, the app reloaded): the code sent then is the one.
        return {"ok": True, "resend_in": max(int(await state["redis"].ttl(f"auth-wait:{who}")), 1)}
    try:
        await _take(f"auth-email:{accounts.email_key(accounts.inbox(email))}:{hour}", CODES_PER_EMAIL, 2 * 3600, "That's a lot of codes for one address. Try again in an hour.")
        await _take(f"auth-address:{network}:{hour}", CODES_PER_ADDRESS if address else CODES_UNKNOWN_ADDRESS, 2 * 3600, "Your network has asked for a lot of codes. Try again in an hour.")
        standing = await in_db(accounts.standing, email)
        # A client address's day; and, for an address without an account that has stood a day, its IPv6 /48's /64s
        # together a ceiling (_new_codes_from_a_48). Fans with accounts on a phone network's /48 aren't held back by
        # its other subscribers' sign-ups (BUG-326).
        days = _address_limits(address, CODES_PER_ADDRESS_DAY, _new_codes_from_a_48())
        await _take_address("auth-address-day", days[:1] if standing else days, day, 2 * DAY, "Your network has asked for a lot of codes today. Try again tomorrow.")
        used_up = f"{APP_NAME} has sent all the sign-in codes it can today. Try again tomorrow."
        # Part of the day's codes is kept for fans whose account has stood a day: made-up addresses, and accounts made
        # for it minutes before, can't leave none (BUG-093).
        if not standing:
            await _take(f"auth-day-new:{day}", _new_codes_a_day(), 2 * DAY, used_up)
        await _take(f"auth-hour:{hour}", CODES_PER_HOUR, 2 * 3600, f"{APP_NAME} is sending a lot of codes right now. Try again in a few minutes.")
        await _take(f"auth-day:{day}", MAIL_CODES_PER_DAY, 2 * DAY, used_up)
    except HTTPException:
        # No code went out: asking again within the minute mustn't answer "the code sent then is the one".
        await state["redis"].delete(f"auth-wait:{who}")
        raise
    # A blocked account's address is answered as any other, so asking says nothing of whether one is (BUG-102): its code
    # goes out, and only whoever holds the inbox hears it's blocked, when the code signs nobody in (email_verify).
    code = f"{secrets.randbelow(10**6):06d}"
    # A new code: its tries are counted afresh, and the network that asked for it keeps its own (_code_tries).
    await state["redis"].set(f"auth-code:{who}", mac("code", f"{email}:{code}"), ex=CODE_LIFE)
    await state["redis"].set(f"auth-asked:{who}", network, ex=CODE_LIFE)
    try:
        await asyncio.to_thread(mail.send_code, email, code)
    except mail.MailError as exc:
        await state["redis"].delete(f"auth-code:{who}", f"auth-asked:{who}", f"auth-wait:{who}")
        if isinstance(exc, mail.Undeliverable):
            # The address, not the server: a 400, so a typo isn't counted as the API failing.
            raise HTTPException(400, "Emails can't be sent to that address. Check it, or use another one.")
        raise HTTPException(502, "Couldn't send the email just now. Try again in a moment.")
    return {"ok": True, "resend_in": RESEND_AFTER}


@app.post("/api/auth/email/verify")
async def email_verify(request: Request, response: Response, body: dict = Body(...), signed: accounts.Signed | None = Depends(account_of)) -> dict[str, Any]:
    """Sign in with the emailed code. The first sign-in with an address makes its account."""
    email = accounts.normal_email(body.get("email"))
    code = str(body.get("code") or "").strip()
    if email is None or not re.fullmatch(r"\d{6}", code):
        raise HTTPException(400, "Enter the 6-digit code from the email.")
    who, day = accounts.email_key(email), datetime.now(timezone.utc).strftime("%Y-%m-%d")
    network = client_address(request) or "unknown"  # as email_start counts it
    expected = await state["redis"].get(f"auth-code:{who}")
    if not expected:
        raise HTTPException(400, "That code has run out. Ask for a new one.")
    tries = f"auth-tries:{who}:{expected[:16]}"  # this code's: a new one's are counted afresh
    if not await _code_tries(tries, network, network == await state["redis"].get(f"auth-asked:{who}")):
        raise HTTPException(429, TOO_MANY_WRONG)
    if not hmac.compare_digest(expected, mac("code", f"{email}:{code}")):
        fails = f"auth-fails:{who}:{network}:{day}"
        await state["redis"].incr(fails)
        await state["redis"].expire(fails, 2 * DAY)
        raise HTTPException(400, "That code isn't right. Check the email and try again.")
    # Used up as it's checked: of requests that bring it at once (a double tap), one signs in (BUG-095).
    if not await state["redis"].delete(f"auth-code:{who}"):
        raise HTTPException(400, "That code has run out. Ask for a new one.")
    await state["redis"].delete(f"auth-asked:{who}", f"auth-wait:{who}", tries, f"{tries}:{network}")
    if await in_db(accounts.blocked, email):
        raise HTTPException(403, BLOCKED)
    account_id = await in_db(accounts.find_or_create, email)
    return await _sign_in(request, response, account_id, signed)


async def _code_tries(tries: str, network: str, asked_here: bool) -> bool:
    """Count a try at a code (its count's key `tries`) from this network, before it's checked, so tries sent at once
    can't all get in under the limit; whether it may be tried. Each network has CODE_TRIES at it, and every network
    but the one that asked for it CODE_TRIES_ELSEWHERE between them: wrong codes sent from elsewhere never end it for
    the fan it was sent to (BUG-092), and guesses at it stay few."""
    counts = [(f"{tries}:{network}", CODE_TRIES), *([] if asked_here else [(tries, CODE_TRIES_ELSEWHERE)])]
    allowed = True
    for key, limit in counts:
        used = await state["redis"].incr(key)
        await state["redis"].expire(key, CODE_LIFE)
        allowed = allowed and used <= limit
    return allowed


def _google_callback(request: Request) -> str:
    # Google sends the browser back only to an address registered for this app, so a forged Host fails there.
    return f"{request.url.scheme}://{request.headers.get('host', '')}/api/auth/google/callback"


@app.get("/api/auth/google/start")
def google_start(request: Request) -> Response:
    """Continue with Google: off to Google's sign-in, remembering this attempt in a short-lived cookie."""
    if not google_signin.configured():
        return RedirectResponse("/account?error=google_off", status_code=303)
    url, remembered = google_signin.start(_google_callback(request))
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(google_signin.COOKIE, _seal("google", remembered), max_age=google_signin.LIFE, httponly=True, samesite="lax", secure=_secure(request), path=google_signin.COOKIE_PATH)
    return response


@app.get("/api/auth/google/callback")
async def google_callback(request: Request, state: str = "", code: str = "", error: str = "", signed: accounts.Signed | None = Depends(account_of)) -> Response:
    """Back from Google: sign in (the first time makes the account, or links the one with this address)."""
    def back(to: str) -> Response:
        response = RedirectResponse(to, status_code=303)
        response.delete_cookie(google_signin.COOKIE, path=google_signin.COOKIE_PATH, httponly=True, samesite="lax", secure=_secure(request))
        return response

    if error or not code:
        return back("/account")  # the fan changed their mind at Google
    try:
        sub, email = await asyncio.to_thread(google_signin.finish, _unseal("google", request.cookies.get(google_signin.COOKIE)), state, code, _google_callback(request))
    except google_signin.GoogleError as exc:
        log.warning("google sign-in: %s", exc)
        return back("/account?error=google")
    account_id = await in_db(accounts.find_or_create_google, sub, email)
    if account_id is None:
        return back("/account?error=google_linked")
    if await in_db(accounts.blocked, None, account_id):
        return back("/account?error=blocked")
    response = back("/account")
    done = await _sign_in(request, response, account_id, signed)
    dropped = f"&teams_dropped={done['teams_dropped']}" if done["teams_dropped"] else ""
    response.headers["location"] = f"/account?welcome={done['players_added']}{dropped}"
    return response


@app.post("/api/auth/signout")
async def sign_out(request: Request, response: Response, body: dict | None = Body(None), signed: accounts.Signed | None = Depends(account_of)) -> dict[str, Any]:
    """Sign this device out, or every device ({"everywhere": true}). Their alerts stop; this device
    becomes a new, empty anonymous fan. A device signed in to nothing (signed out in another tab, or
    everywhere from another device) keeps the fan it has, and what it followed since (BUG-094); only a
    session cookie that ended elsewhere goes."""
    if signed is not None:
        everywhere = bool((body or {}).get("everywhere"))
        await _forget_sessions(await in_db(accounts.end_sessions, signed.account_id, None if everywhere else signed.session_id))
        await _tell_signed_out(signed.fan_id)  # this device's other tabs too: they share its cookie
        _clear_cookies_for_anonymous(request, response)
    elif request.cookies.get(accounts.SESSION_COOKIE):
        response.delete_cookie(accounts.SESSION_COOKIE, httponly=True, samesite="lax", secure=_secure(request))
    return {"signed_in": False}


@app.delete("/api/me/account")
async def delete_account(request: Request, response: Response, body: dict = Body(...), signed: accounts.Signed | None = Depends(account_of)) -> dict[str, Any]:
    """Delete the account and everything kept under it: follows, alert levels, muted games, the push log
    and every device's alerts. Players' pages are everyone's and stay."""
    if signed is None:
        raise HTTPException(401, "Sign in to delete your account.")
    if body.get("confirm") is not True:
        raise HTTPException(400, "Confirm to delete your account.")
    await _forget_account(await in_db(accounts.delete_account, signed.account_id))
    _clear_cookies_for_anonymous(request, response)
    return {"deleted": True}


async def _forget_account(gone: accounts.Deleted | None) -> None:
    """What Redis kept of a deleted account: its sessions' cached copies, its research count, its codes."""
    if gone is not None:
        await _forget_sessions(gone.token_hashes)
        await _tell_signed_out(gone.fan_id)
        who = accounts.email_key(gone.email)
        await state["redis"].delete(player_search.research_key(gone.fan_id), f"auth-code:{who}", f"auth-asked:{who}")


# ---------------------------------------------------------------- clips (clips.py)


def publisher(request: Request, db: Session = Depends(get_db)) -> ClipKey:
    """The publishing key a request carries (Authorization: Bearer slam_pk_…): the Mac recorder, for an admin."""
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    key = clips.key_from(db, token) if token else None
    if key is None:
        raise HTTPException(401, "Send a publishing key: Authorization: Bearer slam_pk_…")
    db.commit()  # its last use, and the connection back before the route waits for a thread (db.released)
    return key


@app.post("/api/clips")
def publish_clip(body: dict[str, Any] = Body(...), key: ClipKey = Depends(publisher), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Start publishing a clip: its row, and URLs to put its video and poster frame at, signed for a few minutes.
    Refused (400/403/404) for a source that mayn't be published, a clip too long or big, or an unknown game."""
    try:
        up = clips.create(db, key, body)
    except clips.Refused as refused:
        db.rollback()
        raise HTTPException(refused.status, refused.message)
    db.commit()
    return {"id": up.clip.slug, "video_put": up.video_put, "poster_put": up.poster_put, "video_type": clips.VIDEO_TYPE,
            "poster_type": clips.POSTER_TYPE, "expires_in": clipstore.SIGNED_FOR}


@app.post("/api/clips/{slug}/done")
def clip_uploaded(slug: str, key: ClipKey = Depends(publisher), db: Session = Depends(get_db)) -> dict[str, Any]:
    """The uploader put both files: checked, and the clip goes live (its public form back); else 422, the clip
    failed and its files gone, to start again."""
    try:
        clip = clips.finish(db, key, slug)
    except clips.Refused as refused:
        db.rollback()
        raise HTTPException(refused.status, refused.message)
    db.commit()
    if clip.status != "live":
        raise HTTPException(422, "The files didn't arrive, or are too big: start the upload again.")
    return clips.public(clip, clips.store())


@app.delete("/api/clips/{slug}", status_code=204)
def unpublish_clip(slug: str, key: ClipKey = Depends(publisher), db: Session = Depends(get_db)) -> None:
    """Its uploader takes a clip down: files deleted."""
    try:
        clips.remove(db, slug, key)
    except clips.Refused as refused:
        db.rollback()
        raise HTTPException(refused.status, refused.message)
    db.commit()


@app.get("/api/clips")
def clips_list(match: str | None = None, competition: str | None = None, player: int | None = None, before: str | None = None,
               db: Session = Depends(get_db)) -> dict[str, Any]:
    """Live clips, newest first: a game's (?match=<its match page's address>, its game feed), a competition's
    (?competition=…, the tournament feed), a player's (?player=…), or everyone's. ?before= (a clip's
    `published`) pages back."""
    since = None
    if before:
        try:
            since = datetime.fromisoformat(before.replace(" ", "+"))  # a "+" sent unencoded arrives as a space
        except ValueError:
            since = None
        if since is None or since.tzinfo is None:
            raise HTTPException(400, "before must be a clip's published time, with its zone.")
    return {"clips": clips.listed(db, match_=match, competition=competition, player=player, before=since)}


@app.get("/api/clips/{slug}")
def clip_page(slug: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    clip = clips.one(db, slug)
    if clip is None:
        raise HTTPException(404, "No such clip.")
    return clip


@app.post("/api/clips/{slug}/report")
def report_clip(slug: str, body: dict[str, Any] = Body(default={}), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """A fan says a clip shouldn't be up: once each, flagging it for the admin panel."""
    hidden = clips.report(db, slug, fan, str(body.get("reason") or ""))
    if hidden is None:
        raise HTTPException(404, "No such clip.")
    db.commit()
    return {"reported": True}


# ---------------------------------------------------------------- the admin panel (admin.py)

admin_log = logging.getLogger("sports_follow.admin")


async def _admin_keys() -> admin.Keys:
    """The admin's password hash and authenticator key; with none set the panel isn't there (404)."""
    keys = await asyncio.to_thread(admin.stored)
    if keys is None:
        raise HTTPException(404, "Not Found")
    return keys


async def require_admin(request: Request) -> admin.Keys:
    """An admin signed in to the panel. With no admin password set the panel isn't there (404); without a
    session, or with one opened under a password or code since changed, sign in (401)."""
    keys = await _admin_keys()
    token = request.cookies.get(admin.COOKIE) or ""
    held = await state["redis"].get(admin.session_key(token)) if token else None
    if held is None or not hmac.compare_digest(held, admin.fingerprint(keys)):
        raise HTTPException(401, "Sign in to the admin panel.")
    return keys


AdminOnly = [Depends(require_admin)]


@app.get("/api/admin/clips", dependencies=AdminOnly)
def admin_clips(status: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Clips, whatever their status, newest first, with their reports; and the publishing keys."""
    return {"clips": clips.for_admin(db, status), "keys": clips.keys(db), "set_up": clipstore.STORE is not None}


@app.post("/api/admin/clips/{slug}", dependencies=AdminOnly)
def admin_clip(slug: str, body: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Hide a clip ({"hidden": true}: off the app, and its files off their public address), or put a hidden one back
    ({"hidden": false}; its reports start again)."""
    hidden = _switch(body, "hidden")
    try:
        clip = clips.set_hidden(db, slug, hidden)
    except clips.Refused as refused:
        db.rollback()
        raise HTTPException(refused.status, refused.message) from None
    if clip is None:
        raise HTTPException(404, "No live or hidden clip by that id.")
    db.commit()
    admin_log.info("admin: clip %s %s", slug, "hidden" if hidden else "shown")
    return {"id": slug, "status": clip.status}


@app.delete("/api/admin/clips/{slug}", status_code=204, dependencies=AdminOnly)
def admin_remove_clip(slug: str, db: Session = Depends(get_db)) -> None:
    """Take a clip down for good: its files deleted."""
    try:
        found = clips.remove(db, slug)
    except clips.Refused as refused:
        raise HTTPException(refused.status, refused.message)
    if not found:
        raise HTTPException(404, "No such clip.")
    db.commit()
    admin_log.info("admin: clip %s removed", slug)


@app.post("/api/admin/clip-keys", dependencies=AdminOnly)
def admin_new_clip_key(body: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    """A new publishing key for the Mac app: shown this once."""
    row, token = clips.new_key(db, str(body.get("name") or ""))
    db.commit()
    admin_log.info("admin: publishing key %s made for %r", row.id, row.name)
    return {"id": row.id, "name": row.name, "key": token}


@app.delete("/api/admin/clip-keys/{key_id}", status_code=204, dependencies=AdminOnly)
def admin_revoke_clip_key(key_id: RowId, hide: bool = False, db: Session = Depends(get_db)) -> None:
    """Revoke a publishing key: it stops at once. ?hide=true hides its live clips too (a key that leaked)."""
    if not clips.revoke(db, key_id):
        raise HTTPException(404, "No such key, or revoked already.")
    db.commit()
    admin_log.info("admin: publishing key %s revoked", key_id)
    if hide:
        try:
            hidden = clips.hide_from(db, key_id)
        except clips.Refused as refused:
            db.commit()  # the clips hidden before the bucket failed stay hidden
            raise HTTPException(refused.status, f"Revoked, but not all its clips are hidden: {refused.message}") from None
        db.commit()
        admin_log.info("admin: publishing key %s's %d clips hidden", key_id, hidden)


def _switch(body: dict, field: str) -> bool:
    value = body.get(field)
    if not isinstance(value, bool):
        raise HTTPException(400, f'Say {{"{field}": true}} or {{"{field}": false}}.')
    return value


@app.post("/api/admin/login")
async def admin_login(request: Request, response: Response, body: dict = Body(...)) -> dict[str, Any]:
    """Sign in to the admin panel with its password and, once one is set, a code from the authenticator
    app; each code works once. Tries are limited from each client address, and wrong ones from everyone
    together, so neither can be guessed at speed. Past everyone's limit the right password and code still
    get in (BUG-101: else a few addresses guessing keep the admin out), and every other try is refused
    alike, so a guess learns nothing, not even whether its password was right; with no code set, nothing
    gets in until the hour is over. Every sign-in is counted (metrics.admin_sign_in), and one that works is
    emailed (app-rules.json)."""
    keys = await _admin_keys()
    now = time.time()
    await _take(f"admin-tries:{client_address(request) or 'unknown'}:{int(now // 900)}", admin.ATTEMPTS_PER_ADDRESS, 1800, "Too many tries. Wait a quarter of an hour.")
    failed = f"admin-fails:{int(now // 3600)}"
    walled = int(await state["redis"].get(failed) or 0) >= admin.FAILURES_PER_HOUR
    if walled and not keys.totp:
        raise HTTPException(429, "Too many wrong tries this hour. Try again in an hour.")
    # Counted as wrong before it's checked, so tries sent at once can't all get in under the limit.
    await state["redis"].incr(failed)
    await state["redis"].expire(failed, 7200)
    password = body.get("password")
    right = isinstance(password, str) and 0 < len(password) <= 1000 and await asyncio.to_thread(admin.try_password, password, keys.password)
    if right and keys.totp:
        step = admin.code_step(keys.totp, body.get("code"), now)
        # A code works once: one seen over someone's shoulder, or sent twice, is refused.
        right = step is not None and bool(await state["redis"].set(f"admin-code-used:{step}", "1", nx=True, ex=(admin.CODE_DRIFT * 2 + 2) * admin.CODE_STEP))
    if not right:
        metrics.admin_sign_in("wrong")
        admin_log.warning("admin panel: a wrong password or code")
        if walled:
            raise HTTPException(429, "Too many wrong tries this hour: until it's over, only the right password and code get in.")
        raise HTTPException(401, "That password or code isn't right." if keys.totp else "That isn't the admin password.")
    await state["redis"].decr(failed)
    token = secrets.token_urlsafe(32)
    await state["redis"].set(admin.session_key(token), admin.fingerprint(keys), ex=admin.SESSION_LIFE)
    response.set_cookie(admin.COOKIE, token, max_age=admin.SESSION_LIFE, path=admin.COOKIE_PATH, httponly=True, samesite="strict", secure=_secure(request))
    metrics.admin_sign_in("ok")
    admin_log.warning("admin panel: signed in%s", "" if keys.totp else " with the password alone (no authenticator code set)")
    return {"admin": True}


@app.post("/api/admin/logout")
async def admin_logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(admin.COOKIE)
    if token:
        await state["redis"].delete(admin.session_key(token))
    response.delete_cookie(admin.COOKIE, path=admin.COOKIE_PATH, httponly=True, samesite="strict", secure=_secure(request))
    return {"admin": False}


@app.get("/api/admin/me")
async def admin_me(keys: admin.Keys = Depends(require_admin)) -> dict[str, Any]:
    """Signed in; and whether sign-in asks for an authenticator code (the panel says so when it doesn't)."""
    return {"admin": True, "code": keys.totp is not None}


@app.get("/api/admin/sports", dependencies=AdminOnly)
def admin_sports() -> dict[str, Any]:
    return {"sports": admin.sports_view()}


@app.put("/api/admin/sports/{key}", dependencies=AdminOnly)
def admin_set_sport(key: str, body: dict = Body(...)) -> dict[str, Any]:
    """Turn a sport off or on, within what the server's settings allow: off, it isn't searched,
    imported or polled, and its players' pages stay as they are."""
    on = _switch(body, "on")
    try:
        admin.set_sport(key, on)
    except LookupError:
        raise HTTPException(404, "There's no such sport.") from None
    admin_log.info("admin panel: sport %s turned %s", key, "on" if on else "off")
    return {"sports": admin.sports_view()}


@app.get("/api/admin/sources", dependencies=AdminOnly)
def admin_sources() -> dict[str, Any]:
    return admin.sources_view()


@app.put("/api/admin/sources/{system}", dependencies=AdminOnly)
def admin_set_source(system: str, body: dict = Body(...)) -> dict[str, Any]:
    """Turn a source off or on, within what the server's settings allow. Its players move to the sport's
    next running source on their next refresh, if there is one; without one their pages stop updating."""
    on = _switch(body, "on")
    try:
        admin.set_source(system, on)
    except LookupError:
        raise HTTPException(404, "There's no such source.") from None
    admin_log.info("admin panel: source %s turned %s", system, "on" if on else "off")
    return admin.sources_view()


@app.put("/api/admin/budgets/{name}", dependencies=AdminOnly)
def admin_set_budget(name: str, body: dict = Body(...)) -> dict[str, Any]:
    """What a source may spend (admin.checked_limits): its allowance a minute, an hour or a day, the
    margin never spent, the reserve kept for live games and fans, the fans' share, the pause after a 429. One
    changed since the view this change was made from ("based_on") is a 409: the panel shows the newer one."""
    try:
        admin.set_limits(name, body)
    except LookupError:
        raise HTTPException(404, "There's no such allowance.") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except admin.Stale:
        raise HTTPException(409, "These limits were changed since you opened them (in another tab?): here they are now. Change them again if you still want to.") from None
    admin_log.info("admin panel: allowance %s set to %s", name, json.dumps(body, sort_keys=True))
    return admin.sources_view()


@app.delete("/api/admin/budgets/{name}", dependencies=AdminOnly)
def admin_reset_budget(name: str) -> dict[str, Any]:
    """Back to what the source declares."""
    try:
        admin.set_limits(name, None)
    except LookupError:
        raise HTTPException(404, "There's no such allowance.") from None
    admin_log.info("admin panel: allowance %s back to its source's own", name)
    return admin.sources_view()


@app.get("/api/admin/linear", dependencies=AdminOnly)
def admin_linear() -> dict[str, Any]:
    """Linear's plan and how many issues it holds against the Free plan's limit (linear.py). Each process asks
    Linear at most once an hour, and it answers even without a key or with Linear down, saying so."""
    return linear.status()


@app.get("/api/admin/feed", dependencies=AdminOnly)
def admin_feed(show: Annotated[str, Query(max_length=20)] = "all", offset: Annotated[int, Query(ge=0, le=MAX_ID)] = 0, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The Feed's candidates, newest found first, with the judge's verdicts and scores, and every video channel's trust."""
    try:
        return admin.feed_page(db, show, offset)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.put("/api/admin/feed/layout", dependencies=AdminOnly)
def admin_feed_layout(body: dict = Body(...)) -> dict[str, Any]:
    """How the apps draw the fan's Feed ({"layout": "edge" | "full" | null}; null: the server's setting), live in
    every process within a few seconds: no deploy. Before /api/admin/feed/{row_id}, which would take "layout" for a row."""
    try:
        admin.set_feed_layout(body.get("layout"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    admin_log.info("admin panel: feed layout %s", body.get("layout") or "back to the server's")
    return {"layout": admin.layout_view()}


@app.put("/api/admin/feed/channels", dependencies=AdminOnly)
def admin_channel_trust(body: dict = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    """How far the Feed trusts a video channel or a publisher's news feed ({"channel": its id or the feed's address,
    "trust": 1 | 0.7 | 0.4 | 0 | null}); 0 hides its items. In the body, as an address doesn't fit in a path; and before
    /api/admin/feed/{row_id}, which would take "channels" for a row."""
    channel = body.get("channel")
    try:
        admin.set_channel_trust(channel, body.get("trust"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    admin_log.info("admin panel: feed channel %s trust %s", channel, body.get("trust"))
    return {"channels": admin.channels_view(db)}


@app.put("/api/admin/feed/{row_id}", dependencies=AdminOnly)
async def admin_review_candidate(row_id: RowId, body: dict = Body(...)) -> dict[str, Any]:
    """An admin's word on a candidate ({"review": "approved" | "hidden" | "pinned" | null}), over the judge's. Fans'
    apps' votes that its video won't play start again from none (feed.unplayable_votes_key): approving or pinning one
    their reports took from every Feed puts it back (the Feed tab's list of them), and after that's taken back it takes
    as many networks as ever to take it out again, not one more."""
    said = body.get("review")
    try:
        found = await run_in_threadpool(admin.review_candidate, row_id, said if isinstance(said, str) or said is None else "?")
    except ValueError:
        raise HTTPException(400, "Review is approved, hidden, pinned, or null.") from None
    if found is None:
        raise HTTPException(404, "There's no such candidate.")
    if video := await in_db(feed.video_of_row, row_id):
        await state["redis"].delete(feed.unplayable_votes_key(video))
    admin_log.info("admin panel: feed candidate %s %s", row_id, said or "review taken back")
    return found



@app.post("/api/admin/feed/{row_id}/put-back", dependencies=AdminOnly)
async def admin_put_back(row_id: RowId) -> dict[str, Any]:
    """The video a candidate is plays after all, says an admin, from the Feed tab's list of those fans' apps took out:
    every player's row of it is served again as the judge and any admin's word on it say, with nothing approved
    (feed.put_back), and fans' apps' votes on it start again from none."""
    video = await in_db(feed.put_back, row_id)
    if video is None:
        raise HTTPException(404, "There's no such candidate.")
    await state["redis"].delete(feed.unplayable_votes_key(video))
    admin_log.info("admin panel: feed video %s put back", video)
    return {"put_back": video}


@app.get("/api/admin/users", dependencies=AdminOnly)
def admin_users(q: Annotated[str, Query(max_length=254)] = "", offset: Annotated[int, Query(ge=0, le=MAX_ID)] = 0, db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"overview": admin.overview(db), "predictions": predictions.numbers(db), **admin.account_page(db, q, offset)}


@app.get("/api/admin/users/{account_id}", dependencies=AdminOnly)
def admin_user(account_id: RowId, db: Session = Depends(get_db)) -> dict[str, Any]:
    found = admin.account(db, account_id)
    if found is None:
        raise HTTPException(404, "There's no such account.")
    return found


@app.post("/api/admin/users/{account_id}/signout", dependencies=AdminOnly)
async def admin_sign_out(account_id: RowId) -> dict[str, Any]:
    """Sign every device out of the account; their alerts stop."""
    ended = await in_db(admin.sign_out, account_id)
    if ended is None:
        raise HTTPException(404, "There's no such account.")
    await _forget_sessions(ended)
    if ended:
        await _tell_signed_out(await in_db(accounts.fan_of, account_id))
    admin_log.info("admin panel: account %s signed out everywhere", account_id)
    return {"signed_out": len(ended)}


@app.put("/api/admin/users/{account_id}/block", dependencies=AdminOnly)
async def admin_block(account_id: RowId, body: dict = Body(...)) -> dict[str, Any]:
    """Block an account (it can't sign in, and is signed out everywhere) or let it back in."""
    blocked = _switch(body, "blocked")
    ended = await in_db(admin.block, account_id, blocked)
    if ended is None:
        raise HTTPException(404, "There's no such account.")
    await _forget_sessions(ended)
    if ended:
        await _tell_signed_out(await in_db(accounts.fan_of, account_id))
    admin_log.info("admin panel: account %s %s", account_id, "blocked" if blocked else "unblocked")
    return {"blocked": blocked, "signed_out": len(ended)}


@app.delete("/api/admin/users/{account_id}", dependencies=AdminOnly)
async def admin_delete_user(account_id: RowId, body: dict = Body(...)) -> dict[str, Any]:
    """Delete an account and everything kept under it, as its fan can (DELETE /api/me/account)."""
    if body.get("confirm") is not True:
        raise HTTPException(400, "Confirm to delete the account.")
    gone = await in_db(accounts.delete_account, account_id)
    if gone is None:
        raise HTTPException(404, "There's no such account.")
    await _forget_account(gone)
    admin_log.info("admin panel: account %s deleted", account_id)
    return {"deleted": True}


# ---------------------------------------------------------------- push notifications

# Browsers hand us the address their push service delivers to. Only real push services are accepted: each one's own
# host (Windows' has one a region), on the usual port with no user name, under the paths its subscriptions have. So a
# crafted subscription can't make this server post to an arbitrary host, nor wait on a port of a real one that never
# answers while other fans' alerts wait behind it (BUG-181), nor name a new push service in the metrics (BUG-089).
PUSH_SERVICES = (
    (re.compile(r"fcm\.googleapis\.com"), ("/fcm/send/", "/wp/")),  # Chrome and the browsers built on it
    (re.compile(r"android\.googleapis\.com"), ("/gcm/send/",)),  # Chrome's older subscriptions
    (re.compile(r"updates\.push\.services\.mozilla\.com"), ("/wpush/", "/push/")),  # Firefox
    (re.compile(r"web\.push\.apple\.com"), ("/",)),  # Safari, and home-screen apps on an iPhone
    (re.compile(r"[a-z0-9-]+\.notify\.windows\.com"), ("/w/",)),  # Edge on Windows
)
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# New devices (none already stored) one client address turns notifications on for in a UTC day: an IPv4 address is often
# a carrier's or an office's, shared by many phones; an IPv6 /64 is one home or phone.
DEVICES_PER_ADDRESS, DEVICES_PER_IPV6 = 50, 20


def _push_host_ok(endpoint: str) -> bool:
    url = urlparse(endpoint)
    host = url.hostname or ""
    # The host alone, or with the usual port: no other port, no user name or password.
    if url.scheme != "https" or url.netloc not in (host, f"{host}:443"):
        return False
    return any(service.fullmatch(host) and url.path.startswith(paths) for service, paths in PUSH_SERVICES)


async def device_room(request: Request, body: dict = Body(...)) -> None:
    """Room for one more new device from this client's address: a browser's subscription or the Android app's token
    that isn't stored already, counted once a UTC day. Past the address's room (DEVICES_PER_ADDRESS, or
    DEVICES_PER_IPV6), a 429: new cookies are free, so a fan's own cap (notify.MAX_DEVICES) doesn't bound one
    network's (BUG-181). A device already on is never counted: each start of the app sends it again, and it moves
    to whoever signs in on it."""
    address = client_address(request)
    sub = body.get("subscription")
    device = _sent_text(sub.get("endpoint"), 999) if isinstance(sub, dict) else body.get("token")
    if address is None or not isinstance(device, str) or not (_push_host_ok(device) if isinstance(sub, dict) else fcm.valid_token(device)):
        return  # the route itself refuses what isn't a device
    if await in_db(_device_stored, device):
        return
    redis = state["redis"]
    key = f"push:devices:{address}:{datetime.now(timezone.utc).date().isoformat()}"
    seen = hashlib.sha256(device.encode()).hexdigest()[:16]  # never the token itself: only the device has that
    if await redis.sadd(key, seen):
        await redis.expire(key, 2 * DAY)
        if await redis.scard(key) > (DEVICES_PER_IPV6 if ":" in address else DEVICES_PER_ADDRESS):
            await redis.srem(key, seen)
            raise HTTPException(429, "Notifications have been turned on for a lot of devices on your network today. Try again tomorrow.")


def _device_stored(db: Session, device: str) -> bool:
    """Is this endpoint or token a device that gets notifications already, whoever's it is?"""
    return db.scalar(select(PushSubscription.id).where(PushSubscription.endpoint == device)) is not None


@app.get("/api/push")
def push_info() -> dict[str, Any]:
    """What this server can push by: Web Push with its public key (`enabled`), and FCM for the Android app (`fcm`)."""
    key = notify.public_key()
    return {"enabled": key is not None, "public_key": key, "fcm": fcm.enabled()}


def _sent_text(value: Any, longest: int) -> str | None:
    """A string from a request body that can be stored: 1 to `longest` characters, none of them control characters."""
    return value if isinstance(value, str) and 0 < len(value) <= longest and value.isprintable() else None


@app.post("/api/push/subscriptions", dependencies=[Depends(device_room)])
def subscribe(body: dict = Body(...), fan: str = Depends(fan_id), signed: accounts.Signed | None = Depends(account_of), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Turn notifications on for this device (or update its quiet hours). Signed in, the device belongs
    to this session: signing out turns it off."""
    sub = body.get("subscription")
    keys = sub.get("keys") if isinstance(sub, dict) else None
    endpoint = _sent_text(sub.get("endpoint"), 999) if isinstance(sub, dict) else None
    p256dh, auth = (_sent_text(keys.get("p256dh"), 199), _sent_text(keys.get("auth"), 99)) if isinstance(keys, dict) else (None, None)
    if not (endpoint and p256dh and auth and _push_host_ok(endpoint)):
        raise HTTPException(400, "That isn't a push subscription this server can deliver to.")
    tz, start, end = _device_settings(body)
    previous = _sent_text(body.get("previous_endpoint"), 999)
    if "quiet_start" not in body and previous:
        # The browser replaced this device's subscription: keep its quiet hours.
        old = db.scalar(select(PushSubscription).where(PushSubscription.fan_id == fan, PushSubscription.endpoint == previous))
        if old is not None:
            start, end = old.quiet_start, old.quiet_end
            db.delete(old)
    session_id = signed.session_id if signed else None
    stmt = insert(PushSubscription).values(fan_id=fan, endpoint=endpoint, p256dh=p256dh, auth=auth, timezone=tz, quiet_start=start, quiet_end=end, session_id=session_id)
    # A device already here moves to another fan only with its own keys: a browser subscribing again sends
    # the ones it has, while someone who learned just the endpoint would take the device's alerts away.
    same_keys = (PushSubscription.p256dh == stmt.excluded.p256dh) & (PushSubscription.auth == stmt.excluded.auth)
    stmt = stmt.on_conflict_do_update(
        index_elements=["endpoint"],
        set_={"fan_id": fan, "p256dh": p256dh, "auth": auth, "timezone": tz, "quiet_start": start, "quiet_end": end, "failures": 0, "session_id": session_id},
        where=(PushSubscription.fan_id == fan) | same_keys,
    )
    if db.execute(stmt.returning(PushSubscription.id)).first() is None:
        raise HTTPException(409, "That device's notifications belong to someone else.")
    # This device and the fan's newest others, up to notify.MAX_DEVICES: every push goes to each of them,
    # so piled-up subscriptions mustn't turn one alert into hundreds of sends.
    notify.trim_devices(db, fan, PushSubscription.endpoint == endpoint, notify.MAX_DEVICES - 1)
    db.commit()
    return {"ok": True, "timezone": tz, "quiet_start": start, "quiet_end": end}


@app.delete("/api/push/subscriptions")
def unsubscribe(body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, bool]:
    endpoint = _sent_text(body.get("endpoint"), 999)
    if endpoint is None:
        raise HTTPException(400, "That isn't a push subscription.")
    db.execute(delete(PushSubscription).where(PushSubscription.fan_id == fan, PushSubscription.endpoint == endpoint, PushSubscription.transport == "webpush"))
    db.commit()
    return {"ok": True}


# The zones browsers still call by an old name (Chrome's ICU says "Asia/Calcutta" in India), each with its current
# one, which every zone database has: Debian's in the image keeps the old names apart, in tzdata-legacy (BUG-267).
OLD_ZONE_NAMES = {
    "Africa/Asmera": "Africa/Asmara", "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
    "America/Catamarca": "America/Argentina/Catamarca", "America/Coral_Harbour": "America/Atikokan",
    "America/Cordoba": "America/Argentina/Cordoba", "America/Godthab": "America/Nuuk",
    "America/Indianapolis": "America/Indiana/Indianapolis", "America/Jujuy": "America/Argentina/Jujuy",
    "America/Kralendijk": "America/Curacao", "America/Louisville": "America/Kentucky/Louisville",
    "America/Lower_Princes": "America/Curacao", "America/Marigot": "America/Port_of_Spain",
    "America/Mendoza": "America/Argentina/Mendoza", "America/St_Barthelemy": "America/Port_of_Spain",
    "Arctic/Longyearbyen": "Europe/Oslo", "Asia/Calcutta": "Asia/Kolkata", "Asia/Katmandu": "Asia/Kathmandu",
    "Asia/Rangoon": "Asia/Yangon", "Asia/Saigon": "Asia/Ho_Chi_Minh", "Atlantic/Faeroe": "Atlantic/Faroe",
    "Europe/Bratislava": "Europe/Prague", "Europe/Busingen": "Europe/Zurich", "Europe/Kiev": "Europe/Kyiv",
    "Europe/Mariehamn": "Europe/Helsinki", "Europe/Podgorica": "Europe/Belgrade", "Europe/San_Marino": "Europe/Rome",
    "Europe/Vatican": "Europe/Rome", "Pacific/Enderbury": "Pacific/Kanton", "Pacific/Ponape": "Pacific/Pohnpei",
    "Pacific/Truk": "Pacific/Chuuk",
}


def _device_settings(body: dict) -> tuple[str, str | None, str | None]:
    """A device's timezone, by its current name (UTC unless it's a real one), and its quiet hours ("22:00" to
    "07:00", or none)."""
    tz = str(body.get("timezone") or "UTC")[:64]
    tz = OLD_ZONE_NAMES.get(tz, tz)
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz)
    except Exception:
        tz = "UTC"
    start, end = body.get("quiet_start"), body.get("quiet_end")
    start = start if isinstance(start, str) and TIME.match(start) else None
    end = end if isinstance(end, str) and TIME.match(end) else None
    return tz, start, end


@app.post("/api/push/fcm", dependencies=[Depends(device_room)])
def register_fcm(body: dict = Body(...), fan: str = Depends(fan_id), signed: accounts.Signed | None = Depends(account_of), db: Session = Depends(get_db)) -> dict[str, Any]:
    """The Android app turns notifications on for this device with its FCM token (or updates its quiet
    hours, or its live cards: `live_cards`, Alerts' Live scores), as a browser does with its subscription.
    Signed in, the device belongs to this session.

    The token is the device's alone (no other app, page or person is shown it, and this server never logs
    it), so presenting it is the proof a Web Push subscription's keys are: the app sends it again under
    whoever it is now, after a lost cookie, and the device moves with it. Not a device signed in to an account,
    though, which moves into it as it signs in (accounts.adopt) and goes with its session: no other fan takes it,
    nor does an account take an anonymous fan's device, which then couldn't take it back (BUG-103)."""
    if not fcm.enabled():
        raise HTTPException(503, "Notifications aren't set up on this server yet.")
    token = body.get("token")
    if not fcm.valid_token(token):
        raise HTTPException(400, "That isn't an FCM token.")
    tz, start, end = _device_settings(body)
    # A build of the app from before live cards could be turned off sends none: the device keeps what it has.
    live = body.get("live_cards") if isinstance(body.get("live_cards"), bool) else None
    stopped: list[int] = []
    previous = body.get("previous_token")
    if fcm.valid_token(previous) and previous != token:
        # FCM gave the app a new token: the old one goes, and its quiet hours and live cards carry over unless
        # new ones came.
        old = db.scalar(select(PushSubscription).where(PushSubscription.fan_id == fan, PushSubscription.endpoint == previous, PushSubscription.transport == "fcm"))
        if old is not None:
            if "quiet_start" not in body:
                start, end = old.quiet_start, old.quiet_end
            live = old.live_cards if live is None else live
            stopped = old.live_stopped
            db.delete(old)
    session_id = signed.session_id if signed else None
    stmt = insert(PushSubscription).values(fan_id=fan, transport="fcm", endpoint=token, p256dh=None, auth=None, timezone=tz, quiet_start=start, quiet_end=end, session_id=session_id,
                                           live_cards=True if live is None else live, live_stopped=stopped)
    stmt = stmt.on_conflict_do_update(
        index_elements=["endpoint"],
        set_={"fan_id": fan, "timezone": tz, "quiet_start": start, "quiet_end": end, "failures": 0, "session_id": session_id, **({} if live is None else {"live_cards": live})},
        where=(PushSubscription.transport == "fcm")
        & ((PushSubscription.fan_id == fan) | (PushSubscription.session_id.is_(None) & stmt.excluded.session_id.is_(None))),
    )
    if db.execute(stmt.returning(PushSubscription.id)).first() is None:
        # Another fan's signed-in device, an anonymous fan's for an account, or a Web Push endpoint by that name.
        raise HTTPException(409, "That device's notifications belong to someone else.")
    notify.trim_devices(db, fan, PushSubscription.endpoint == token, notify.MAX_DEVICES - 1)
    db.commit()
    return {"ok": True, "timezone": tz, "quiet_start": start, "quiet_end": end}


LIVE_STOPS_KEPT = 30  # the games a phone stopped the live card of that are kept: the newest, as the app keeps them


@app.put("/api/push/fcm/stopped/{event_id}")
def stop_live_card(event_id: RowId, body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """"Stop live card" on the Android app's card of a game: this phone (its token, which only it has) gets no more
    of the game's cards (livecards.android); its moments still come, and the fan's other phones keep the card. The
    app remembers the stop too, so a card already on its way, or every card if this never arrives, isn't shown."""
    token = body.get("token")
    if not fcm.valid_token(token):
        raise HTTPException(400, "That isn't an FCM token.")
    if db.get(Event, event_id) is None:
        raise HTTPException(404, "No such game.")
    device = db.scalar(select(PushSubscription).where(PushSubscription.fan_id == fan, PushSubscription.endpoint == token, PushSubscription.transport == "fcm").with_for_update())
    if device is None:
        raise HTTPException(404, "That device isn't getting notifications.")
    games = structured.game_events(db, event_id)  # a game under several fixtures stops under each (a chess board's two)
    device.live_stopped = [*(e for e in device.live_stopped if e not in games), *games][-LIVE_STOPS_KEPT:]
    db.commit()
    return {"event_id": event_id, "stopped": True}


@app.delete("/api/push/fcm")
def unregister_fcm(body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, bool]:
    token = body.get("token")
    if not fcm.valid_token(token):
        raise HTTPException(400, "That isn't an FCM token.")
    db.execute(delete(PushSubscription).where(PushSubscription.fan_id == fan, PushSubscription.endpoint == token, PushSubscription.transport == "fcm"))
    db.commit()
    return {"ok": True}


PUSH_TESTS_PER_FAN = 5  # an hour
PUSH_TESTS_PER_ADDRESS = 20  # an hour, from one client address (an IPv6 /64)


@app.post("/api/push/test")
async def push_test(request: Request, fan: str = Depends(fan_id)) -> dict[str, int]:
    """A test notification to each of this fan's devices. A fan and an address send only so many an hour: past
    either, a 429. The pushes go on notify's own threads and are waited for on none (notify.send_test), so devices
    that never answer hold up nothing but other test pushes, never search, following or sign-in (BUG-182)."""
    if notify.public_key() is None and not fcm.enabled():
        raise HTTPException(503, "Notifications aren't set up on this server yet.")
    address = client_address(request)
    limits = [(f"pushtest:fan:{fan}", PUSH_TESTS_PER_FAN)] + ([(f"pushtest:address:{address}", PUSH_TESTS_PER_ADDRESS)] if address else [])
    for key, limit in limits:
        used = await state["redis"].incr(key)
        await state["redis"].expire(key, 3600, nx=True)  # the hour's end, set once: more tries don't push it back
        if used > limit:
            raise HTTPException(429, "That's a lot of test notifications for one hour. Try again later.")
    devices = await in_db(notify.newest_devices, fan)
    return {"sent": await notify.send_test(devices)}


@app.post("/api/notifications/{notification_id}/opened")
def notification_opened(notification_id: RowId, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, bool]:
    """The service worker says the fan tapped this notification (for the open rate in `notify stats`)."""
    ok = notify.opened(db, fan, notification_id)
    db.commit()
    return {"ok": ok}


@app.get("/api/me/mutes")
def mutes(fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, list[int]]:
    """The games this fan muted, newest first."""
    return {"event_ids": list(db.scalars(select(NotificationMute.event_id).where(NotificationMute.fan_id == fan).order_by(NotificationMute.created_at.desc()).limit(100)))}


@app.put("/api/me/mutes/{event_id}")
def mute(event_id: RowId, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Nothing more from this game ("Mute this game" on a notification). Its moments stay in the activity list. A
    game under several fixtures (a chess board is one of each player's) is muted under every one of them."""
    if db.get(Event, event_id) is None:
        raise HTTPException(404, "No such game.")
    db.execute(insert(NotificationMute).values([{"fan_id": fan, "event_id": e} for e in structured.game_events(db, event_id)]).on_conflict_do_nothing())
    db.commit()
    return {"event_id": event_id, "muted": True}


@app.delete("/api/me/mutes/{event_id}")
def unmute(event_id: RowId, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    db.execute(delete(NotificationMute).where(NotificationMute.fan_id == fan, NotificationMute.event_id.in_(structured.game_events(db, event_id))))
    db.commit()
    return {"event_id": event_id, "muted": False}


ACTIVITY_DAYS = 30


@app.get("/api/me/activity")
def activity(before: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None, limit: int = 30, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Every moment of the fan's players since they followed them, and of the games they follow by themselves
    while they did (matches.py), or a team in since they followed it (teams.py), newest first, whatever reached
    the phone: the app's activity list. Players and
    games set to off are left out, a correction (a goal VAR ruled out) unless the list has the moment it takes
    back, and a game's moment that one of their players' in the list says too (alerts.same). Page with
    ?before=<next>."""
    limit = max(1, min(limit, 50))
    level = func.coalesce(Follow.alert_rules["level"].astext, moments.DEFAULT_LEVEL)
    taken = aliased(Moment)
    query = (
        select(Moment, Player.name, Event.title, NotificationMute.event_id.is_not(None))
        .join(Follow, and_(Follow.player_id == Moment.player_id, Follow.fan_id == fan))
        .join(Player, Player.id == Moment.player_id)
        .outerjoin(Event, Event.id == Moment.event_id)
        .outerjoin(NotificationMute, and_(NotificationMute.fan_id == fan, NotificationMute.event_id == Moment.event_id))
        .outerjoin(taken, taken.id == Moment.corrects)
        .where(level != "off", Moment.created_at >= Follow.created_at, Moment.created_at >= func.now() - timedelta(days=ACTIVITY_DAYS))
        .where(or_(Moment.corrects.is_(None), taken.created_at >= Follow.created_at))
        .order_by(Moment.id.desc())
        .limit(limit)
    )
    game_level = func.coalesce(MatchFollow.alert_rules["level"].astext, moments.DEFAULT_LEVEL)
    games = (
        select(Moment, Event.title, NotificationMute.event_id.is_not(None), Event.sport)
        .join(MatchFollow, and_(MatchFollow.event_id == Moment.event_id, MatchFollow.fan_id == fan))
        .join(Event, Event.id == Moment.event_id)
        .outerjoin(NotificationMute, and_(NotificationMute.fan_id == fan, NotificationMute.event_id == Moment.event_id))
        .outerjoin(taken, taken.id == Moment.corrects)
        .where(Moment.player_id.is_(None), game_level != "off", Moment.created_at >= MatchFollow.created_at, or_(MatchFollow.until.is_(None), Moment.created_at <= MatchFollow.until))
        .where(teams.for_fan(fan))  # a result told from a side's is for that team's fans, and once
        .where(Moment.created_at >= func.now() - timedelta(days=ACTIVITY_DAYS), or_(Moment.corrects.is_(None), taken.created_at >= MatchFollow.created_at))
        .order_by(Moment.id.desc())
        .limit(limit)
    )
    sided = teams.activity(fan, taken, ACTIVITY_DAYS).limit(limit)  # and of the games of the teams they follow
    if before is not None:
        query = query.where(Moment.id < before)
        games = games.where(Moment.id < before)
        sided = sided.where(Moment.id < before)
    # Each list's newest `limit` moments, each once (a game followed by itself and through a team is in two): the
    # newest `limit` of them all are the page, and while it's full there may be more after its last.
    found = [(m, None, event, muted, sport) for m, event, muted, sport in [*db.execute(games), *db.execute(sided)]]
    rows = sorted([(m, name, event, muted, None) for m, name, event, muted in db.execute(query)] + list({r[0].id: r for r in found}.values()), key=lambda r: -r[0].id)[:limit]
    # A game's moment that one of the fan's players' says too is listed once, as their player's; and so is a chess
    # board's that two of them say, once from each side, as the one's whose side tells it (alerts.voiced_by).
    planned = {i.id: i for i in notify.for_games(db, notify.on_boards(db, [r[0] for r in rows]))}
    theirs = [i for i in planned.values() if i.player_id is not None]
    items = [
        {
            "id": m.id, "kind": m.kind, "level": m.level, "priority": m.priority, "title": m.title, "body": m.body, "url": m.url,
            "player_id": m.player_id, "player": name, "event_id": m.event_id, "event": event, "muted": bool(muted), "at": m.created_at.isoformat(),
            **({"sport": sports.of(sport).key or "other"} if m.player_id is None else {}),  # a game's own: its sport, for its mark
        }
        for m, name, event, muted, sport in rows
        if not any(alerts.voiced_by(planned[m.id], p) if m.player_id is not None else alerts.same(planned[m.id], p) for p in theirs)
    ]
    return {"items": items, "next": rows[-1][0].id if len(rows) == limit else None}


@app.get("/api/me/week")
def this_week(fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Today's "This week" and the Wire (week.py): the big moments of the fan's players over the last seven
    days, as each sport words them for a poster, biggest first; and the Wire: their players' facts (who's out,
    who's back), line-ups and news, newest first, then facts about a few of the best-known players they don't
    follow, which a fan who follows nobody gets too. Kept a minute per fan (week.mine)."""
    return week.mine(db, fan)


@app.get("/api/players/{player_id}/feed")
def player_feed(player_id: RowId, response: Response, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> feed.Feed:
    """The player's Feed (feed.py): videos, posts and news about them, grouped around their moments, without
    what this fan hid. Read from rows only, never a model or a source; with nothing judged yet, their news."""
    found = feed.feed_for(db, player_id, fan)
    if found is None:
        raise HTTPException(404, "No such player.")
    response.headers.update({"Cache-Control": "private, no-cache", "Vary": "Cookie"})
    return found


@app.post("/api/players/{player_id}/feed/{item_id}/feedback")
def feed_feedback(player_id: RowId, item_id: str, body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """What the fan said about an item from its "…" sheet ({"verdict": one of feed.VERDICTS}): it leaves
    their feed at once, and the judge learns from it later."""
    verdict = body.get("verdict")
    try:
        feed.feedback(db, fan, player_id, item_id, verdict if isinstance(verdict, str) else "")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return {"item_id": item_id, "hidden": True}


def _voter(address: str | None, fan: str) -> str:
    """Whose vote a "won't play" report is (feed_unplayable): its network's, an IPv6 one's /48 (a home or an office
    may hold all of it, a /64 a device), else, with no public address to go by, the fan's."""
    if not address:
        return f"fan:{fan}"
    network = ipaddress.ip_network(address, strict=False)
    return str(network.supernet(new_prefix=48)) if network.version == 6 else address


@app.post("/api/players/{player_id}/feed/{item_id}/unplayable")
async def feed_unplayable(player_id: RowId, item_id: str, request: Request, body: dict | None = Body(None), fan: str = Depends(fan_id)) -> dict[str, Any]:
    """The fan's app says this video of the player's feed won't play inside it: the video's own player refused it.
    It leaves their Feed at once (feed.said_unplayable), and when the app is in the feed's country ({"timezone": the
    device's}, feed.counts_everywhere) it's their network's vote on whether it leaves every fan's: once
    feed.UNPLAYABLE_VOTES networks have said so, it does (feed.unplayable; "marked"). The admin panel's Feed tab lists
    it, and an admin approving or pinning it brings it back. The app opens it on its site instead. Saying it again
    changes nothing.
    A fan votes only so many times an hour, and so does an address: past either, the video still leaves the fan's
    Feed, and only their vote is dropped. Each count is taken before the report is acted on, so reports arriving
    together can't all pass, and given back when it isn't a vote; the address's too when it isn't a new one, so junk
    ids and a video said of again don't use up what a real one needs."""
    redis = state["redis"]
    address = client_address(request)
    limits = [(f"feed:unplayable:fan:{fan}", feed.UNPLAYABLE_PER_FAN)]
    if address:
        limits.append((f"feed:unplayable:address:{address}", feed.UNPLAYABLE_PER_ADDRESS))

    async def give_back(keys: list[str]) -> None:
        for key in keys:
            await redis.eval(GIVE_BACK, 1, key)

    taken: list[str] = []
    votes_now = True
    for key, limit in limits:
        used = await redis.incr(key)
        taken.append(key)
        await redis.expire(key, 3600, nx=True)  # the hour's end, set once
        if used > limit:  # their Feed still loses it; only the vote goes
            await give_back(taken)
            taken, votes_now = [], False
            break
    shared = taken[1:]  # the address's
    try:
        video = await in_db(feed.said_unplayable, fan, player_id, item_id)
    except ValueError as exc:
        await give_back(shared)
        raise HTTPException(422, str(exc)) from exc
    except LookupError as exc:
        await give_back(shared)
        raise HTTPException(404, str(exc)) from exc
    answer: dict[str, Any] = {"item_id": item_id, "unplayable": True, "marked": False}
    # Past an hour's votes, an admin vouched for it, the app is abroad (where it may not play when it does here), or
    # this network said so before.
    if (not votes_now or video is None or not feed.counts_everywhere((body or {}).get("timezone"))
            or not await redis.sadd(votes := feed.unplayable_votes_key(video), _voter(address, fan))):  # fmt: skip
        await give_back(shared)
        return answer
    await redis.expire(votes, feed.UNPLAYABLE_VOTES_FOR)
    if await redis.scard(votes) >= feed.UNPLAYABLE_VOTES:
        answer["marked"] = await in_db(feed.unplayable, video)
    return answer


@app.get("/api/me/feed")
def my_feed(fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, list[feed.Share]]:
    """Each followed player's top video or post (feed.shares_for), which Android builds from before its Feed tab
    (BHA-346) show on Today; both apps now show the top of the fan's Feed there instead (/api/me/feed/items). No
    news, which the Wire carries: empty until the curator has judged and ranked something about a followed player."""
    return {"shares": feed.shares_for(db, fan)}


@app.get("/api/me/feed/items")
def my_feed_items(
    response: Response,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    player: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
    limit: Annotated[int, Query(ge=0, le=feed.MAX_PAGE)] = feed.PAGE,
    tz: Annotated[int, Query(ge=-12 * 60, le=14 * 60)] = 0,
    tab: Annotated[str, Query(max_length=20)] = feed.FOLLOWING,
    fan: str = Depends(fan_id),
    db: Session = Depends(get_db),
) -> feed.Page:
    """The fan's Feed (feed.page): every followed player's videos, posts and news, by day where the fan is (tz:
    minutes east of UTC), players taking turns; ?tab=for_you mixes in videos of players they don't follow (the default,
    following, is theirs alone); ?player=<id> for one of them; ?cursor= for the next page (of the same tab). Read from
    rows only, never a model or a source. limit=0 answers only the circles, the count and the newest (the Feed tab's
    dot). Each page says how the apps draw it (layout)."""
    try:
        found = feed.page(db, fan, player=player, cursor=cursor, limit=limit, tz=tz, tab=tab)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    response.headers.update({"Cache-Control": "private, no-cache", "Vary": "Cookie"})
    return found


@app.delete("/api/follows/{player_id}", dependencies=TellsDevices)
def unfollow(player_id: RowId, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, bool]:
    pipeline.unfollow(db, fan, player_id)
    db.commit()  # or a follow sent straight after could find this one still there, and then lose it
    return {"ok": True}


@app.get("/api/players/{player_id}/results")
def player_results(player_id: RowId, before: str | None = None, limit: int = 20, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The player's results history, newest first; pass `next` back as `before` for older ones."""
    cursor = results.decode(before) if before else None
    if before and (cursor is None or not 1 <= cursor[1] <= MAX_ID):
        raise HTTPException(400, "That isn't a page of these results: start again from the first.")
    return results.page(db, player_id, before, limit)


@app.get("/api/players/{player_id}/results/{event_id}")
def player_result(player_id: RowId, event_id: RowId, db: Session = Depends(get_db)) -> dict[str, Any]:
    found = results.detail(db, player_id, event_id)
    if found is None:
        raise HTTPException(404, "No finished game with that id for this player.")
    return found


@app.get("/api/players/{player_id}/form")
def player_form(player_id: RowId, last: int = 10, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Form over the last few results (5 or 10 in the app), from the stored lines."""
    return results.player_form(db, player_id, last)


@app.get("/api/me/results")
def my_results(limit: int = 8, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """The newest results across every player the fan follows."""
    return {"results": results.latest_for_fan(db, fan, limit)}


@app.get("/api/players/{player_id}/card")
async def card(player_id: RowId, request: Request, response: Response, fan: str = Depends(fan_id)) -> Any:
    """The player's card, with whether this fan follows them and at what alert level. Those make the
    answer the fan's own: private, checked again on every use (a follow doesn't touch this URL), and
    its ETag is of the whole answer, since a build that fails changes it without a new version."""
    card = await read_card(player_id)
    if card is None:
        raise HTTPException(404, "No such player.")
    await _research_if_due(player_id, card)
    rules = await in_db(lambda db: db.scalar(select(Follow.alert_rules).where(Follow.fan_id == fan, Follow.player_id == player_id)))
    following = rules is not None
    body = {**card, "following": following, "alerts": (rules or {}).get("level", moments.DEFAULT_LEVEL) if following else None}
    digest = hashlib.blake2b(json.dumps(body, sort_keys=True, default=str).encode(), digest_size=8).hexdigest()
    etag = f'"{player_id}-{card.get("version", 0)}-{digest}"'
    response.headers.update({"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Cookie"})
    if etag in (t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")):
        return Response(status_code=304, headers=response.headers)  # with a cookie fan_id set, if any
    return body


async def _research_if_due(player_id: int, card: dict[str, Any]) -> None:
    """A page the research agent has a part on (a background with no Wikipedia article, monthly; with research
    on, the whole page of a player no source has) is researched again when a fan opens it and its words are
    stale (pipeline.agent_due), not on a clock. Never fails the page."""
    if card.get("status") != "ready":
        return
    try:
        if await asyncio.to_thread(pipeline.agent_due, player_id, card) and await state["redis"].set(f"research:{player_id}", "1", nx=True, ex=REFRESH_EVERY):
            await enqueue("build_card", player_id)
    except Exception:
        log.warning("research check for player %s failed", player_id, exc_info=True)


REFRESH_EVERY = 60  # seconds: one refresh of a player a minute, whoever asks


async def _refresh_due(player_id: int) -> None:
    """A refresh is a worker job and source (or model) calls, and what it reads is every follower's: one a
    minute per player is plenty, and a 429 for the rest."""
    if not await state["redis"].set(f"refresh:{player_id}", "1", nx=True, ex=REFRESH_EVERY):
        raise HTTPException(429, "This player was refreshed a moment ago. Their page updates by itself; try again in a minute.")


@app.post("/api/players/{player_id}/refresh")
async def refresh(player_id: RowId, fan: str = Depends(fan_id)) -> dict[str, Any]:
    """Read the player's source again now, for a fan following them."""
    row = await in_db(lambda db: db.get(PlayerCard, player_id))
    if row is None:
        raise HTTPException(404, "No such player.")
    if await in_db(lambda db: db.scalar(select(Follow.id).where(Follow.fan_id == fan, Follow.player_id == player_id))) is None:
        raise HTTPException(403, "Follow this player to refresh them.")
    if row.status != "ready":
        return {"queued": False, "reason": "The player's first card is still being built."}
    if not sports.on(((row.card or {}).get("player") or {}).get("sport")):
        # Turned off in the admin panel: its source isn't read for it, though it may run for another sport (BUG-176).
        return {"queued": False, "reason": "This sport is turned off for now, so its players aren't read again."}
    if await in_db(structured.binding, player_id) is not None:
        # A bound player's live score is already polled; a refresh re-reads fixtures and stats.
        if bus.is_locked(f"structured:{player_id}"):
            return {"queued": False, "reason": "A refresh is already running."}
        await _refresh_due(player_id)
        await enqueue("refresh_structured", player_id, _job_id=f"refresh-structured:{player_id}")
        return {"queued": True}
    if not AGENT_LIVE:
        return {"queued": False, "reason": "Live scores aren't available for this sport yet."}
    if bus.is_locked(f"live:{player_id}"):
        return {"queued": False, "reason": "A live check is already running."}
    await _refresh_due(player_id)
    await enqueue("refresh_live", player_id)
    return {"queued": True}


# ---------------------------------------------------------------- search


def _search(q: str, sport: str | None, fan: str, may_ask: Callable[[], bool]) -> dict[str, Any]:
    with session() as db:
        found = player_search.run(db, q, sport, fan, may_ask)
        # And the teams by that name fans can follow (teams.py), from the registry alone: no source is asked.
        return {**found, "teams": teams.find(db, found.get("query") or q, found.get("sport"), fan)}


async def _in_minute(kind: str, fan: str, address: str | None, per_fan: int, per_address: int) -> bool:
    """Count one of a fan's requests of a kind against their minute and their address's; False when either is
    over. A minute starts at its first such request."""
    for key, limit in [(f"{kind}:fan:{fan}", per_fan), *([(f"{kind}:address:{address}", per_address)] if address else [])]:
        used = await state["redis"].incr(key)
        await state["redis"].expire(key, 60, nx=True)  # the minute's end, set once (a count without one would never end)
        if used > limit:
            return False
    return True


async def _source_search_allowed(fan: str, address: str | None) -> bool:
    """Count a search that goes on to the sources' own against the fan's minute and their address's.
    Over either, it doesn't go: a script sending a new name each time would otherwise keep the sources
    (and the search's threads) busy for everyone."""
    return await _in_minute("sourcesearch", fan, address, SOURCE_SEARCHES_PER_MINUTE, SOURCE_SEARCHES_PER_MINUTE)


async def _source_lookup(fan: str, address: str | None) -> None:
    """Count a preview or follow that asks a live source for an athlete not on the app yet against the fan's
    minute (SOURCE_LOOKUPS_PER_MINUTE) and their address's (SOURCE_LOOKUPS_PER_ADDRESS); over either, a 429.
    A script walking a source's ids would otherwise spend fans' share of it for everyone, and make a player
    for each."""
    if not await _in_minute("sourcelookup", fan, address, SOURCE_LOOKUPS_PER_MINUTE, SOURCE_LOOKUPS_PER_ADDRESS):
        raise HTTPException(429, "You've looked up a lot of new athletes this minute. Try again in a minute.")


@app.get("/api/search")
async def search(request: Request, q: str, sport: str | None = None, fan: str = Depends(fan_id)) -> dict[str, Any]:
    """Athletes matching a name, from players on Sports Follow and the live sources (search.py), and teams (teams.py)."""
    loop, address = asyncio.get_running_loop(), client_address(request)

    def may_ask() -> bool:
        # From the search's thread, and only when the sources would be asked: names the registry knows aren't counted.
        return asyncio.run_coroutine_threadsafe(_source_search_allowed(fan, address), loop).result()

    result = await asyncio.to_thread(_search, q[:80], (sport or "").lower() or None, fan, may_ask)
    return {**result, "research": player_search.research_status(await research_used(fan))}


@app.get("/api/athletes/wikidata/{qid}")
async def registry_preview(request: Request, qid: str, sport: str | None = None, fan: str = Depends(fan_id)) -> dict[str, Any]:
    """A registry athlete before following: their live-source preview once the source is found, or
    what the registry knows when no source has them (following then builds the page with AI). Looking
    them up in their source counts against the fan's and their address's minute (_source_lookup)."""
    person = await in_db(_registry_athlete, {"qid": qid, "sport": sport})
    if person is None:
        raise HTTPException(404, "No such athlete.")
    holder = await in_db(_registry_holder, person, bool(sport))
    if holder is not None:
        return {"player_id": holder}
    await _source_lookup(fan, client_address(request))
    found = await _resolve(person)
    athlete = await in_db(registry.get, person.qid, person.sport)
    photo = await in_db(photos.of_file, athlete.image) if athlete else None
    base = {"qid": person.qid, "born": person.birth_date.isoformat() if person.birth_date else None, "photo": photo}
    if found is None:
        return {
            **base, "system": None, "athlete_id": None, "league": person.league, "name": person.name, "sport": sports.label(person.sport),
            "teams": person.teams, "source": "Wikidata", "source_url": f"https://www.wikidata.org/wiki/{person.qid}",
            "next": None, "last": None, "stats": [], "stats_note": "", "live": False, "country": athlete.country if athlete else None,
        }
    adapter, ref = found
    holder = await in_db(_holder, ref.system, ref.athlete_id)
    if holder is not None:
        return {"player_id": holder}
    try:
        shown = await asyncio.to_thread(player_search.preview, ref.system, ref.athlete_id, ref.league)
    except AdapterError as exc:
        if _source_has_none(exc):
            raise HTTPException(404, "No such athlete.")
        log.warning("preview %s: %s", person.qid, exc)
        raise HTTPException(502, "The live source didn't answer. Try again in a moment.")
    return {**(shown or {}), **base, "live": True}


@app.get("/api/athletes/{system}/{athlete_id}")
async def athlete_preview(request: Request, system: str, athlete_id: str, league: str | None = None, fan: str = Depends(fan_id)) -> dict[str, Any]:
    """What following a search result would show, before following. An athlete already on Sports
    Follow answers with their player id; the app opens their page instead. A preview not kept from a
    moment ago asks the source, which counts against the fan's and their address's minute (_source_lookup)."""
    if system not in ADAPTERS or not _source_id_ok(system, athlete_id) or not _league_ok(league) or (system, athlete_id) in await in_db(registry.blocked):
        raise HTTPException(404, "No such athlete.")
    holder = await in_db(_holder, system, athlete_id)
    if holder is not None:
        return {"player_id": holder}
    if not player_search.previewed(system, athlete_id):
        await _source_lookup(fan, client_address(request))
    try:
        found = await asyncio.to_thread(player_search.preview, system, athlete_id, league)
    except AdapterError as exc:
        if _source_has_none(exc):
            raise HTTPException(404, "No such athlete.")  # a stale id, not an outage
        log.warning("preview %s:%s: %s", system, athlete_id, exc)
        raise HTTPException(502, "The live source didn't answer. Try again in a moment.")
    if found is None:
        raise HTTPException(404, "No such athlete.")
    return {**found, "photo": await in_db(photos.of_source, system, athlete_id)}


# ---------------------------------------------------------------- realtime


async def _stream(channels: list[str], first: Callable[[], Awaitable[list[dict[str, Any]]]], request: Request, again: str | None = None,
                  still: Callable[[], Awaitable[bool]] | None = None):
    """Every message on the channels, after `first()`: what they cover as it is now, read once
    subscribed so nothing published in between is missed. Each reconnect starts here, so it is how an
    app catches up; a word on the channel `again` sends `first()` again, unless `still()` says the stream's
    client isn't who it connected as (its session ended): then it says so (SIGNED_OUT) and ends. Holds no
    database connection, and one Redis connection, from the streams' own pool; its route takes its places
    first (_hold_streams).
    A client going away cancels the stream, and cancels each wait in its cleanup too, so the connection's
    return to the pool is shielded from it: unshielded, every stream that ended kept its connection, and a
    process's thousandth got none (BUG-184's round-two reopen)."""
    pubsub = state["streams"].pubsub()
    try:
        await pubsub.subscribe(*channels)
        for event in await first():
            yield f"data: {json.dumps(event, default=str)}\n\n"
        while not await request.is_disconnected():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=HEARTBEAT)
            if msg is None:
                yield PING
                continue
            if again is not None and msg.get("channel") == again:
                if still is not None and not await still():
                    yield SIGNED_OUT
                    return
                for event in await first():
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                continue
            yield f"data: {msg['data']}\n\n"
    finally:
        # The socket closed without waiting for it (it ends the subscriptions), then the connection given back.
        with anyio.CancelScope(shield=True):
            if pubsub.connection is not None:
                await pubsub.connection.disconnect(nowait=True)
            await pubsub.aclose()


def _prefix(address: str) -> str:
    """A client address's prefix, whose addresses' streams together have a ceiling (STREAMS_PER_PREFIX): an IPv4
    address's /24, an IPv6 /64's /48."""
    network = ipaddress.ip_network(address, strict=False)
    return str(network.supernet(new_prefix=24 if network.version == 4 else 48))


def _brought(request: Request, fan: str) -> bool:
    """Whether a request brought its fan, an account's (only a session gives one) or its fan cookie's, rather than
    fan_id making one up for it: a request with neither is a new fan each time, whose own share holds nothing back."""
    if fan.startswith(accounts.FAN_PREFIX):
        return True
    value = request.cookies.get(FAN_COOKIE) or ""
    return value == fan or (_signed(value) or ("", 0))[0] == fan


def _hold_streams(request: Request, fan: str | None, player: bool = False) -> list[str]:
    """Places for one more open stream: the fan's, for their own stream when the request brought them (_brought); one
    of the few kept for streams with no fan (STREAMS_WITHOUT_FAN) when it brought none; its client address's and its
    prefix's (a public one's); and this process's. A `player`'s stream with a fan takes no place in their share: each
    of their devices may have a player's page open besides their own stream. Past the fan's, the address's or an IPv6
    /48's share a 429 (an IPv4 /24 at its ceiling is only logged), past the fanless streams' or the process's a 503,
    and then it holds none. The stream's answer gives them back when it ends (_Stream)."""
    address = client_address(request)
    fan = fan if fan is not None and _brought(request, fan) else None
    shares = {f"fan:{fan}": STREAMS_PER_FAN} if fan and not player else {}
    counted: list[str] = []  # held, and never refused at
    if address:
        shares[f"address:{address}"] = STREAMS_PER_ADDRESS if ":" in address else STREAMS_PER_IPV4
        prefix = _prefix(address)
        if streaming[f"prefix:{prefix}"] >= STREAMS_PER_PREFIX:
            _at_ceiling("streams", prefix)
        if ":" in address:
            shares[f"prefix:{prefix}"] = STREAMS_PER_PREFIX
        else:
            counted.append(f"prefix:{prefix}")
    if any(streaming[key] >= share for key, share in shares.items()):
        raise HTTPException(429, "Too many live updates open at once from here. Close some, and they come back.")
    pooled = {"all": STREAMS_PER_PROCESS, **({} if fan else {"fanless": STREAMS_WITHOUT_FAN})}
    if any(streaming[key] >= share for key, share in pooled.items()):
        raise HTTPException(503, "Live updates are busy. The app tries again in a moment.")
    places = [*shares, *counted, *pooled]
    streaming.update(places)
    return places


class _Stream(StreamingResponse):
    """A stream's answer, which gives back its places (_hold_streams) when it ends, however it ends: its
    client gone, the server stopping, or an error."""

    def __init__(self, body: AsyncGenerator[str, None], places: list[str]) -> None:
        super().__init__(body, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        self.places = places
        self.stream = body

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                # A stream left at a yield (its client gone while a send waited) cleans up now, not when it's collected.
                with anyio.CancelScope(shield=True):
                    await self.stream.aclose()
            finally:
                streaming.subtract(self.places)
                for key in self.places:
                    if streaming[key] <= 0:
                        del streaming[key]


def _sse(body, places: list[str] | None = None) -> StreamingResponse:
    return _Stream(body, places or [])


@app.get("/api/players/{player_id}/stream")
async def player_stream(player_id: RowId, request: Request) -> StreamingResponse:
    if await read_card(player_id) is None:
        raise HTTPException(404, "No such player.")

    async def first() -> list[dict[str, Any]]:
        card = await read_card(player_id)
        return [{"type": "card", "player_id": player_id, "card": card}] if card else []

    # It needs no cookie. Without a fan's cookie or session it's one of the few kept for streams with no fan, so scripts
    # can't hold every fan's (BUG-299); with one, it's a fan's, outside their own share.
    signed = await account_of(request)
    places = _hold_streams(request, signed.fan_id if signed else await asyncio.to_thread(_cookie_fan, request), player=True)
    return _sse(_stream([bus.channel(player_id)], first, request), places)


@app.get("/api/me/stream")
async def my_stream(request: Request, fan: str = Depends(fan_id), signed: accounts.Signed | None = Depends(account_of)) -> StreamingResponse:
    """One connection for every player the fan follows: browsers cap connections per origin, so one per player won't scale.
    It opens with what /api/me/following returns, and the games they follow by themselves (/api/me/matches),
    so an app that reconnects (a phone back from the lock screen, a restart) gets every score and result it
    missed; then each followed game's board as it's polled, and its own moments (bus.match_channel): of the games
    they follow by themselves, and of their teams' games on now or soon (teams.event_ids), with their teams.
    A follow, an unfollow, a level or their sports (fansports.py) changed on another of their devices sends the list again (bus.follows_channel):
    the app takes it as the list (BUG-165), and connects again for the players it then has. A fan with nothing else
    to hear yet listens for that too; their list first, as on any stream, so an app whose fan was signed out
    elsewhere (it reads as nobody now), or stopped following everyone there, drops what it shows (BUG-162).
    A signed-in device's stream asks for its session again at each word there, which ending sessions says too
    (_tell_signed_out): ended, the stream tells its app (which starts over as who it is now) and ends, rather than
    carry on as the account it connected as."""
    ids, games = await in_db(lambda db: (db.scalars(select(Follow.player_id).where(Follow.fan_id == fan)).all(),
                                         list(dict.fromkeys([*matches.event_ids(db, fan), *teams.event_ids(db, fan)]))))

    async def first() -> list[dict[str, Any]]:
        return [{"type": "following", "players": await _following(fan), "matches": await in_db(matches.mine, fan), "teams": await in_db(teams.mine, fan),
                 "sports": await in_db(fansports.mine, fan)}]

    async def still() -> bool:
        """Whether the device is still signed in with the session it connected with; one that wasn't has none to end."""
        if signed is None:
            return True
        now = await account_of(request)
        return now is not None and now.session_id == signed.session_id

    places = _hold_streams(request, fan)
    mine = bus.follows_channel(fan)
    return _sse(_stream([*(bus.channel(i) for i in ids), *(bus.match_channel(e) for e in games), mine], first, request, again=mine, still=still), places)


# ---------------------------------------------------------------- the Android app


@app.get("/.well-known/assetlinks.json")
def assetlinks() -> list[dict[str, Any]]:
    """Digital Asset Links: proves this site belongs to the Android app, so Android opens the app for a
    stann.in link to a page it has (App Links, mobile/app.config.ts). Empty until the app's package and
    signing certificates are configured."""
    if not ANDROID_PACKAGE or not ANDROID_CERT_SHA256:
        return []
    return [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {"namespace": "android_app", "package_name": ANDROID_PACKAGE, "sha256_cert_fingerprints": ANDROID_CERT_SHA256},
    }]


# ---------------------------------------------------------------- the web app's errors (BHA-196)


@app.post("/api/errors", status_code=204)
async def web_error(request: Request, report: web_errors.Report, fan: str = Depends(fan_id)) -> None:
    """An error in the web app (src/errors.ts), logged beside the server's own (web_errors.py). A fan, an
    address and everyone together send only so many an hour: past that, a 429 and nothing logged."""
    address = client_address(request)
    limits = [(f"weberrors:fan:{fan}", web_errors.PER_FAN), ("weberrors:all", web_errors.PER_HOUR)]
    if address:
        limits.insert(1, (f"weberrors:address:{address}", web_errors.PER_ADDRESS))
    for key, limit in limits:
        used = await state["redis"].incr(key)
        await state["redis"].expire(key, 3600, nx=True)  # the hour's end, set once
        if used > limit:
            raise HTTPException(429, "This app has sent all the error reports it may this hour.")
    await run_in_threadpool(web_errors.record, report, _route_of)


def _route_of(method: str, path: str) -> str | None:
    """The API route a request to `path` goes to ("/api/players/{player_id}/card"), if any."""
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/") and route.matches({"type": "http", "path": path, "method": method})[0] == Match.FULL:
            return route.path
    return None


# ---------------------------------------------------------------- a stream's player alone (the Android app's)


@app.get("/watch/{player}/{plays}", include_in_schema=False)
def watch_page(player: str, plays: str, request: Request) -> Response:
    """A match page's stream played by the app's own player, alone on a page of the site (watch.page): what the
    Android app's match page plays, as Twitch's player must sit in a page on the site it's told. It brings
    its own Content-Security-Policy, which the Caddyfile leaves it. No such player, or what it plays of another
    shape, is a 404; nothing else is read."""
    made = watch.page(player, plays, request.url.hostname or "")
    if made is None:
        raise HTTPException(404, "Not Found")
    body, policy = made
    return HTMLResponse(body, headers={"Content-Security-Policy": policy, "Cache-Control": "public, max-age=3600", "X-Robots-Tag": "noindex, nofollow"})


# ---------------------------------------------------------------- link previews of invites, teams, clips and games


@app.get("/api/leagues/{code}/share.png")
async def invite_picture(code: LeagueCode) -> Response:
    """The picture a league's invite link shows (share.of_invite): its name, who asked and how many are in it, never
    its code. Only read: drawing it counts no opening."""
    invite = await in_db(predictions.about, code)
    if invite is None:
        raise HTTPException(404, "No such league.")
    return await _picture(share.of_invite(invite).picture)


@app.get("/api/teams/{team_id}/share.png")
async def team_picture(team_id: RowId) -> Response:
    """The picture a link to a team's page shows (share.of_team): who it is, and its game on now, last or next."""
    team = await in_db(teams.page, team_id, None)
    if team is None:
        raise HTTPException(404, "No such team.")
    preview = share.of_team(team)
    if preview is None:
        raise HTTPException(404, "No such team.")
    return await _picture(preview.picture)


@app.get("/api/clips/{slug}/share.png")
async def clip_picture(slug: str) -> Response:
    """The picture a link to a clip shows (share.of_clip): its caption, its moment and its game, drawn."""
    clip = await in_db(clips.one, slug)
    if clip is None:
        raise HTTPException(404, "No such clip.")
    return await _picture(share.of_clip(clip).picture)


@app.get("/api/match/{system}/{game}/share.png")
async def game_picture(system: str, game: str) -> Response:
    """The picture a link to a game's page shows (share.of_match): the game, and the score, who won or when it starts.
    From what's stored: a made-up address asks nothing of a source."""
    shown = (await in_db(lambda db: match.view(db, None, system, game)[0])) if system in KNOWN and match.ID.fullmatch(game) else None
    preview = share.of_match(shown) if shown else None
    if preview is None:
        raise HTTPException(404, "No such game.")
    return await _picture(preview.picture)


# ---------------------------------------------------------------- the built frontend (production)

# Served fresh every time, so a new build reaches installed apps: the page, the service worker that
# caches everything else, and the manifest.
NO_CACHE = {"index.html", "sw.js", "manifest.webmanifest"}
MEDIA = {".webmanifest": "application/manifest+json", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png"}

# A player's page, and a game of theirs (?g=<event id>): served with their own link preview.
PLAYER_PAGE = re.compile(r"player/([0-9]{1,10})/?")
# A game's page, with a question of it (?q=<question id>, the link its card shares): the question's preview.
MATCH_PAGE = re.compile(r"match/([a-z_]{1,40})/([A-Za-z0-9][A-Za-z0-9_.:-]{0,62})/?")
GAME = re.compile(r"[0-9]{1,10}")
# A league's invite (/j/<code>, the link a member shares), a team's page, and a clip's: each with its own preview.
INVITE_PAGE = re.compile(r"j/([A-Za-z0-9]{4,16})/?")
TEAM_PAGE = re.compile(r"team/([0-9]{1,10})/?")
CLIP_PAGE = re.compile(r"clip/([A-Za-z0-9_-]{1,16})/?")

# The build's files by their content's hash, typed by Python's mimetypes: a .wasm (a chess analysis board's engine)
# as application/wasm, the only type a browser compiles as it streams in, which tests/test_share.py guards.
if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


def _site(request: Request) -> str:
    """The app's public address, for a preview's links: SPORTS_FOLLOW_SITE_URL, else this request's."""
    return SITE_URL or f"{request.url.scheme}://{request.url.netloc}"


async def _looked_up(path: str, read: Callable[[Session], T]) -> T | None:
    """What a page's preview reads, or None when that fails."""
    try:
        return await in_db(read)
    except Exception:  # noqa: BLE001 — the page is the app: it opens with the app's own preview instead
        log.warning("link preview of /%s", path, exc_info=True)
        return None


async def _page_preview(path: str, request: Request) -> share.Preview | None:
    """The link preview of the page at `path`: a player's, a game of theirs, a campaign's, a question's (a game's page
    with ?q=), a league's invite, a team's, a clip's or a game's, or None for the app's own. Never as a fan sees it:
    a crawler is no one."""
    if (slug := campaigns.address(path.rstrip("/"))) and slug not in campaigns.TAKEN:
        try:
            shown = await in_db(campaigns.page, slug, None)
        except Exception:  # noqa: BLE001 — the page is the app: it opens with the app's own preview instead
            log.warning("link preview of /%s", path, exc_info=True)
            return None
        return share.of_campaign(shown) if shown else None
    question = request.query_params.get("q", "")
    if (g := MATCH_PAGE.fullmatch(path)) and GAME.fullmatch(question) and 0 < int(question) <= MAX_ID:
        try:
            q = await in_db(predictions.public, int(question))
        except Exception:  # noqa: BLE001 — the page is the app: it opens with the app's own preview instead
            log.warning("link preview of /%s", path, exc_info=True)
            return None
        return share.of_question(q) if q and q["match"] == f"{g[1]}/{g[2]}" else None
    if found := await _shared(path):
        return found
    m = PLAYER_PAGE.fullmatch(path)
    if not m or int(m[1]) > MAX_ID:
        return None
    game = request.query_params.get("g", "")
    try:
        return await _preview(int(m[1]), int(game) if GAME.fullmatch(game) and 0 < int(game) <= MAX_ID else None)
    except Exception:  # noqa: BLE001 — the page is the app: it opens with the app's own preview instead
        log.warning("link preview of /%s", path, exc_info=True)
        return None


async def _shared(path: str) -> share.Preview | None:
    """The preview of a league's invite, a team's page, a clip's or a game's at `path`; None for another page, or one
    of these there's none of (no such league, a team of a sport turned off, a game no source has named here)."""
    if m := INVITE_PAGE.fullmatch(path):
        invite = await _looked_up(path, lambda db: predictions.about(db, m[1]))
        return share.of_invite(invite) if invite else None
    if (m := TEAM_PAGE.fullmatch(path)) and int(m[1]) <= MAX_ID:
        team = await _looked_up(path, lambda db: teams.page(db, int(m[1]), None))
        return share.of_team(team) if team else None
    if m := CLIP_PAGE.fullmatch(path):
        clip = await _looked_up(path, lambda db: clips.one(db, m[1]))
        return share.of_clip(clip) if clip else None
    if (m := MATCH_PAGE.fullmatch(path)) and m[1] in KNOWN and match.ID.fullmatch(m[2]):
        game = await _looked_up(path, lambda db: match.view(db, None, m[1], m[2])[0])  # what's stored: a preview asks no source
        return share.of_match(game) if game else None
    return None


def _built(path: str) -> Path | str | None:
    """What the build has at `path`: a file it puts at the root (service worker, manifest, icons), else the
    app's page (its text); None without a build."""
    root = WEB_DIST.resolve()
    index = root / "index.html"
    file = (root / path).resolve()
    if path and file.is_file() and root in file.parents and file != index:
        return file
    return index.read_text(encoding="utf-8") if index.is_file() else None


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str, request: Request) -> Response:
    """Files the build puts at the root; every other path is the app, with the page's link preview in its
    head (share.py), since the crawlers that read those run no JavaScript. Never under /api/: an API path
    that isn't one is a JSON 404, not the app. Nothing here without a build (`npm run build` in web/)."""
    if path == "api" or path.startswith("api/"):
        raise HTTPException(404, "Not Found")
    found = await run_in_threadpool(_built, path)
    if found is None:
        raise HTTPException(404, "Not Found")
    if isinstance(found, Path):
        cache = "no-cache" if found.name in NO_CACHE else "public, max-age=86400"
        return FileResponse(found, media_type=MEDIA.get(found.suffix), headers={"Cache-Control": cache})
    # private: it can carry this device's fan cookie (below), which no shared cache may keep for others.
    response = HTMLResponse(share.page(found, await _page_preview(path, request), _site(request)), headers={"Cache-Control": "private, no-cache", **_not_indexed(path)})
    # The app's page is its first request: it makes this device's fan (a read doesn't: _fan), so the calls the app
    # sends together as it starts are all one fan's (BUG-152). Not a signed-in device's, which is its account.
    if not request.cookies.get(accounts.SESSION_COOKIE):
        try:
            await asyncio.to_thread(_fan, request, response, None, True)
        except Exception:  # noqa: BLE001 — the page is the app: without its fan for now, its first write makes one
            log.warning("the app's page: couldn't make this device's fan", exc_info=True)
    return response


def _not_indexed(path: str) -> dict[str, str]:
    """The admin panel's page tells search engines not to list it. Not robots.txt, which anyone can read
    to find it."""
    return {"X-Robots-Tag": "noindex, nofollow"} if path == "admin" or path.startswith("admin/") else {}
