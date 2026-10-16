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
from collections.abc import Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, TypeVar
from urllib.parse import urlparse

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

from . import accounts, admin, bus, credits as credits_page, google_signin, league, live, logs, mail, metrics, moments, notify, photos, pipeline, registry, results, search as player_search, share, sports, starters, structured, tracing, web_errors, week
from .adapters import ADAPTERS, KNOWN, AdapterError, budget
from .agent import MODEL
from .config import AGENT_LIVE, ANDROID_CERT_SHA256, APP_NAME, ANDROID_PACKAGE, CONTACT_EMAIL, COOKIE_SECRET, COOKIE_SECRET_FILE, FAN_COOKIE, MAIL_CODES_PER_DAY, REDIS_URL, RESEARCH_PER_ADDRESS, RESEARCH_PER_HOUR, SITE_URL, SOURCE_SEARCHES_PER_MINUTE
from .db import get_db, session
from .models import Event, Follow, Moment, Notification, NotificationMute, Player, PlayerAlias, PlayerCard, PlayerIdentity, PushSubscription

logs.setup()  # under uvicorn, after its own logging config: ours, with uvicorn's lines through it
tracing.setup("sports-follow-api")
log = logging.getLogger("sports_follow")
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
HEARTBEAT = 15
# An event, not a comment: EventSource hides comments, and the app counts on hearing something every
# HEARTBEAT to know its stream is alive (a proxy can keep it open after the API behind it is gone).
PING = "event: ping\ndata:\n\n"

state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    cookie_key()  # a server without its cookie key stops here, not on a fan's first request
    metrics.seed()
    state["arq"] = await create_pool(RedisSettings.from_dsn(REDIS_URL))
    state["redis"] = aredis.Redis.from_url(REDIS_URL, decode_responses=True)
    yield
    await state["arq"].aclose()
    await state["redis"].aclose()


# FastAPI traces requests itself once tracing is on, and records the query string (a search's words):
# RequestLog's span and metric stand for each request instead (tracing.request_span, metrics.request).
# Nor does it log validation failures, which carry what a client sent.
app = FastAPI(title=APP_NAME, lifespan=lifespan, telemetry={"tracing": False, "operation_spans": False, "logs": False, "metrics": False})

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
    the anonymous fan in the cookie this server signed, else a new anonymous fan. The request's log lines
    name the fan by a hash (logs.fan)."""
    fan = _fan(request, response, signed_in)
    logs.bind(fan=logs.fan(fan))
    return fan


def _fan(request: Request, response: Response, signed_in: accounts.Signed | None) -> str:
    if signed_in is not None:
        return signed_in.fan_id
    value = request.cookies.get(FAN_COOKIE) or ""
    signed = _signed(value)
    if signed:
        fan, day = signed
        if _today() - day < REFRESH_DAYS:
            return fan
    else:
        fan = value if _unsigned_fan(value) else secrets.token_urlsafe(24)
    _set_fan(request, response, fan)
    return fan


# ---------------------------------------------------------------- health


@app.get("/api/health")
async def health(response: Response) -> dict[str, Any]:
    """For the proxy's and an uptime monitor's checks: the database and Redis answer. 503 if either doesn't."""
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
    return {"ok": ok, **checks}


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
        "news": (card.get("news") or [])[:3],
        # The results the card keeps, newest first: structured.RECENT_LIMIT for a bound player, and no more
        # than that of what the agent wrote for one only it covers. Today's results rail shows them all, the
        # tiles the newest.
        "recent_results": (card.get("recent_results") or [])[: structured.RECENT_LIMIT],
    }


@app.get("/api/config")
def config(request: Request) -> dict[str, Any]:
    """What this server offers the app: its name, the model and whether it researches players no source has
    (config.AGENT), the ways to sign in, who to write to about privacy."""
    from .config import AGENT

    return {"name": APP_NAME, "model": MODEL, "research": AGENT, "email_sign_in": mail.configured(_secure(request)), "google_sign_in": google_signin.configured(), "contact_email": CONTACT_EMAIL}


@app.get("/api/sports")
def sports_list(response: Response) -> dict[str, Any]:
    """The sports this app knows, in the order it lists them (sports.ALL): each one's name, the free-text
    names that are it, what its alerts say, and whether this build runs it and has live scores for it.
    "other" is any sport no module knows."""
    response.headers["cache-control"] = "public, max-age=300"
    live = {a.sport for a in ADAPTERS.values()}
    return {"sports": [sports.shown(s, s.key in live) for s in sports.ALL], "other": sports.shown(sports.OTHER, False)}


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


@app.get("/api/share.png")
async def app_picture() -> Response:
    """The picture a link to any other page shows: the app's own."""
    return await _picture(share.APP)


async def _following(fan: str) -> list[dict[str, Any]]:
    """The fan's players as the app lists them: each card's summary and the fan's alert level."""
    rows = await in_db(lambda db: db.execute(select(Follow.player_id, Follow.alert_rules).where(Follow.fan_id == fan).order_by(Follow.created_at)).all())
    out = []
    for player_id, rules in rows:
        card = await read_card(player_id)
        if card:
            out.append({**_summary(card), "alerts": (rules or {}).get("level", moments.DEFAULT_LEVEL)})
    return out


@app.get("/api/me/following")
async def following(fan: str = Depends(fan_id)) -> dict[str, Any]:
    return {"players": await _following(fan)}


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


@app.get("/api/league")
async def league_games(response: Response, tz: Annotated[int, Query(ge=-12 * 60, le=14 * 60)] = 0) -> dict[str, Any]:
    """Today's big results (since yesterday, else the latest day that had some: days where the fan is, tz
    minutes east of UTC) and big games coming up (the next fortnight) in the competitions the running
    sources cover, whoever follows them, ranked by each sport (league.py). The same for every fan in a time
    zone, so no cookie and a public cache. Served from the last reads; a source's read a few hours old queues
    another for the worker, so "refreshing" says new games may come in a moment."""
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


async def research_used(fan: str) -> int:
    return int(await state["redis"].get(player_search.research_key(fan)) or 0)


async def take_research(fan: str, address: str | None, used_up: str) -> list[str]:
    """Count one AI research against the fan's UTC day, their address's day and this hour across all
    fans. Returns the counts taken, for give_back_research; when any is used up, nothing is counted and
    the fan gets a 429 (`used_up` when it's their own three)."""
    now = datetime.now(timezone.utc)
    limits = [(player_search.research_key(fan, now), player_search.RESEARCH_PER_DAY, 2 * DAY, used_up)]
    if address:
        limits.append((player_search.research_address_key(address, now), RESEARCH_PER_ADDRESS, 2 * DAY, "Your network has used today's AI researches. Try again tomorrow."))
    limits.append((player_search.research_hour_key(now), RESEARCH_PER_HOUR, 2 * 3600, f"{APP_NAME} has done all the AI research it can this hour. Try again later."))
    taken: list[str] = []
    for key, limit, ttl, refusal in limits:
        used = await state["redis"].incr(key)
        taken.append(key)
        await state["redis"].expire(key, ttl)
        if used > limit:
            await give_back_research(taken)
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


def _registry_athlete(db: Session, body: dict) -> registry.Person | None:
    qid, sport = str(body.get("qid") or ""), str(body.get("sport") or "").lower()
    if not re.fullmatch(r"Q[0-9]{1,12}", qid) or len(sport) > 60 or not sport.isprintable() or not sports.on(sport):
        return None
    athlete = registry.get(db, qid, sport or None)
    return registry.Person.of(athlete) if athlete else None


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
    them, or has none in this source and they match by birth date (or name and team). The client sends
    both, so a crafted pair mustn't point the registry athlete at someone else for every fan after."""
    adapter = ADAPTERS.get(ref.system)
    if adapter is None or person.sport != adapter.sport:
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
    AI research, and with the agent off (config.AGENT) can't be followed.
    """
    from .config import AGENT

    person = await in_db(_registry_athlete, body) if body.get("qid") else None
    if body.get("qid") and person is None:
        raise HTTPException(404, "That athlete isn't in the player registry.")
    taken: list[str] = []
    if person and not body.get("system"):
        holder = await in_db(pipeline.registry_holder, person.qid, person.sport if body.get("sport") else None)
        if holder is not None:
            player_id, created, ref = holder, False, None
        else:
            found = await _resolve(person)
            if found is None and not AGENT:
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
        try:
            ref = await asyncio.to_thread(adapter.player, athlete_id, league)
        except AdapterError as exc:
            log.warning("follow %s:%s: %s", system, athlete_id, exc)
            raise HTTPException(502, "Couldn't reach the live source to confirm this athlete. Try again in a moment.")
        if ref is None:
            raise HTTPException(404, "That athlete isn't in the source any more.")
        player_id, created = await asyncio.to_thread(pipeline.start_from_pick, adapter, ref)

    def save(db: Session) -> None:
        if person and (ref is None or _same_athlete(person, ref)):
            registry.link(db, player_id, person.qid)
        db.execute(insert(Follow).values(fan_id=fan, player_id=player_id, alert_rules={}).on_conflict_do_nothing())

    try:
        await in_db(save)  # committed before the jobs are queued and the fan is answered
    except Exception:
        await give_back_research(taken)
        raise
    if created:
        # With a live source: scores and fixtures in seconds, the agent's profile and news in a couple
        # of minutes (none with the agent off). Without one: the agent builds the whole page.
        if ref is not None:
            await enqueue("refresh_structured", player_id)
        if ref is None or AGENT:
            await enqueue("build_card", player_id)
    return player_id


@app.post("/api/follows")
async def follow(request: Request, body: dict = Body(...), fan: str = Depends(fan_id)) -> dict[str, Any]:
    from .config import AGENT

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
        if player is None:
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
    # A name nobody has followed (or a page whose build failed) sends the research agent out: that is
    # the part with a daily allowance. Following a player already here costs nothing.
    counted = known is None or known.status == "failed"
    if counted and not AGENT:
        # Without the agent (config.AGENT) a page comes from a live source, picked in search, or not at all.
        raise HTTPException(404, "No live source has this player, so their page can't be built." if known is not None else "Pick an athlete from the search results: pages here come from live sources.")
    taken = await take_research(fan, client_address(request), "You've used today's AI researches. Pick a player from the search results, or try again tomorrow.") if counted else []

    def save(db: Session) -> tuple[int, bool, dict[str, Any] | None] | None:
        """The follow, in one transaction: (player id, needs a build, the card to republish), or None for a name with nothing to search by."""
        try:
            target = pipeline.resolve_or_create(db, query) if query is not None else player
        except ValueError:
            return None
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


@app.put("/api/follows/{player_id}/alerts")
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
CODE_TRIES = 5  # wrong codes before it stops working
RESEND_AFTER = 60  # seconds before another code to the same address
CODES_PER_EMAIL = 5  # an hour
CODES_PER_ADDRESS = 20  # an hour, from one client address (an IPv6 /64)
CODES_UNKNOWN_ADDRESS = 100  # an hour, from every client without a public address together
CODES_PER_ADDRESS_DAY = 20  # a UTC day, from one client address: one network can't spend the day's mail
CODES_PER_HOUR = 200  # an hour, to everyone: the mail provider's bill
FAILS_PER_DAY = 20  # wrong codes for one address in a UTC day, across new codes
BLOCKED = "This account has been blocked."  # by an admin (admin.py)


async def _take(key: str, limit: int, ttl: int, refusal: str) -> None:
    """Count one against a limit; over it, a 429 (the count stays: the limit is on attempts)."""
    used = await state["redis"].incr(key)
    await state["redis"].expire(key, ttl)
    if used > limit:
        raise HTTPException(429, refusal)


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


async def _sign_in(request: Request, response: Response, account_id: int, signed: accounts.Signed | None) -> dict[str, Any]:
    """Sign this device in to the account, bringing what its anonymous fan had (accounts.adopt). A
    session this device already had (signing in again, or as someone else) ends."""
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
    return {**(await in_db(accounts.describe, account_id) or {}), "players_added": done.players}


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


@app.post("/api/auth/email/start")
async def email_start(request: Request, body: dict = Body(...)) -> dict[str, Any]:
    """Email a 6-digit sign-in code. The answer is the same whether or not the address has an account."""
    if not mail.configured(_secure(request)):
        raise HTTPException(503, "Signing in by email isn't set up on this server yet.")
    email = accounts.normal_email(body.get("email"))
    if email is None:
        raise HTTPException(400, "That doesn't look like an email address.")
    who, now = accounts.email_key(email), datetime.now(timezone.utc)
    hour, day = now.strftime("%Y-%m-%dT%H"), now.strftime("%Y-%m-%d")
    if int(await state["redis"].get(f"auth-fails:{who}:{day}") or 0) >= FAILS_PER_DAY:
        raise HTTPException(429, "Too many wrong codes for this address today. Try again tomorrow.")
    if not await state["redis"].set(f"auth-wait:{who}", "1", nx=True, ex=RESEND_AFTER):
        # Asked again within the minute (a second tap, the app reloaded): the code sent then is the one.
        return {"ok": True, "resend_in": max(int(await state["redis"].ttl(f"auth-wait:{who}")), 1)}
    address = client_address(request)
    try:
        await _take(f"auth-email:{who}:{hour}", CODES_PER_EMAIL, 2 * 3600, "That's a lot of codes for one address. Try again in an hour.")
        await _take(f"auth-address:{address or 'unknown'}:{hour}", CODES_PER_ADDRESS if address else CODES_UNKNOWN_ADDRESS, 2 * 3600, "Your network has asked for a lot of codes. Try again in an hour.")
        if address:
            await _take(f"auth-address-day:{address}:{day}", CODES_PER_ADDRESS_DAY, 2 * DAY, "Your network has asked for a lot of codes today. Try again tomorrow.")
        await _take(f"auth-hour:{hour}", CODES_PER_HOUR, 2 * 3600, f"{APP_NAME} is sending a lot of codes right now. Try again in a few minutes.")
        await _take(f"auth-day:{day}", MAIL_CODES_PER_DAY, 2 * DAY, f"{APP_NAME} has sent all the sign-in codes it can today. Try again tomorrow.")
    except HTTPException:
        # No code went out: asking again within the minute mustn't answer "the code sent then is the one".
        await state["redis"].delete(f"auth-wait:{who}")
        raise
    # After the limits, so a loop of addresses can't learn quickly which accounts are blocked.
    if await in_db(accounts.blocked, email):
        await state["redis"].delete(f"auth-wait:{who}")
        raise HTTPException(403, BLOCKED)
    code = f"{secrets.randbelow(10**6):06d}"
    await state["redis"].set(f"auth-code:{who}", mac("code", f"{email}:{code}"), ex=CODE_LIFE)
    await state["redis"].delete(f"auth-tries:{who}")
    try:
        await asyncio.to_thread(mail.send_code, email, code)
    except mail.MailError as exc:
        await state["redis"].delete(f"auth-code:{who}", f"auth-wait:{who}")
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
    expected = await state["redis"].get(f"auth-code:{who}")
    if not expected:
        raise HTTPException(400, "That code has run out. Ask for a new one.")
    # Counted before it's checked, so tries sent at once can't all get in under the limit.
    tries = await state["redis"].incr(f"auth-tries:{who}")
    await state["redis"].expire(f"auth-tries:{who}", CODE_LIFE)
    if tries > CODE_TRIES:
        await state["redis"].delete(f"auth-code:{who}")
        raise HTTPException(429, "Too many wrong codes. Ask for a new one.")
    if not hmac.compare_digest(expected, mac("code", f"{email}:{code}")):
        await state["redis"].incr(f"auth-fails:{who}:{day}")
        await state["redis"].expire(f"auth-fails:{who}:{day}", 2 * DAY)
        raise HTTPException(400, "That code isn't right. Check the email and try again.")
    await state["redis"].delete(f"auth-code:{who}", f"auth-tries:{who}", f"auth-wait:{who}")
    if await in_db(accounts.blocked, email):
        raise HTTPException(403, BLOCKED)
    account_id = await in_db(accounts.find_or_create, email)
    return await _sign_in(request, response, account_id, signed)


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
    response.headers["location"] = f"/account?welcome={done['players_added']}"
    return response


@app.post("/api/auth/signout")
async def sign_out(request: Request, response: Response, body: dict | None = Body(None), signed: accounts.Signed | None = Depends(account_of)) -> dict[str, Any]:
    """Sign this device out, or every device ({"everywhere": true}). Their alerts stop; this device
    becomes a new, empty anonymous fan."""
    if signed is not None:
        everywhere = bool((body or {}).get("everywhere"))
        await _forget_sessions(await in_db(accounts.end_sessions, signed.account_id, None if everywhere else signed.session_id))
    _clear_cookies_for_anonymous(request, response)
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
        who = accounts.email_key(gone.email)
        await state["redis"].delete(player_search.research_key(gone.fan_id), f"auth-code:{who}", f"auth-tries:{who}")


# ---------------------------------------------------------------- the admin panel (admin.py)

admin_log = logging.getLogger("sports_follow.admin")


async def require_admin(request: Request) -> None:
    """An admin signed in to the panel. With no admin password set the panel isn't there (404); without a
    session, or with one opened under a password since changed, sign in (401)."""
    stored = await asyncio.to_thread(admin.stored_hash)
    if stored is None:
        raise HTTPException(404, "Not Found")
    token = request.cookies.get(admin.COOKIE) or ""
    held = await state["redis"].get(admin.session_key(token)) if token else None
    if held is None or not hmac.compare_digest(held, admin.fingerprint(stored)):
        raise HTTPException(401, "Sign in to the admin panel.")


AdminOnly = [Depends(require_admin)]


def _switch(body: dict, field: str) -> bool:
    value = body.get(field)
    if not isinstance(value, bool):
        raise HTTPException(400, f'Say {{"{field}": true}} or {{"{field}": false}}.')
    return value


@app.post("/api/admin/login")
async def admin_login(request: Request, response: Response, body: dict = Body(...)) -> dict[str, Any]:
    """Sign in to the admin panel with its password. Tries are limited from each client address, and
    wrong passwords from everyone together, so it can't be guessed at speed."""
    stored = await asyncio.to_thread(admin.stored_hash)
    if stored is None:
        raise HTTPException(404, "Not Found")
    now = time.time()
    await _take(f"admin-tries:{client_address(request) or 'unknown'}:{int(now // 900)}", admin.ATTEMPTS_PER_ADDRESS, 1800, "Too many tries. Wait a quarter of an hour.")
    failed = f"admin-fails:{int(now // 3600)}"
    if int(await state["redis"].get(failed) or 0) >= admin.FAILURES_PER_HOUR:
        raise HTTPException(429, "Too many wrong passwords this hour. Try again in an hour.")
    # Counted as wrong before it's checked, so tries sent at once can't all get in under the limit.
    await state["redis"].incr(failed)
    await state["redis"].expire(failed, 7200)
    password = body.get("password")
    if not isinstance(password, str) or not 0 < len(password) <= 1000 or not await asyncio.to_thread(admin.try_password, password, stored):
        admin_log.warning("admin panel: a wrong password")
        raise HTTPException(401, "That isn't the admin password.")
    await state["redis"].decr(failed)
    token = secrets.token_urlsafe(32)
    await state["redis"].set(admin.session_key(token), admin.fingerprint(stored), ex=admin.SESSION_LIFE)
    response.set_cookie(admin.COOKIE, token, max_age=admin.SESSION_LIFE, path=admin.COOKIE_PATH, httponly=True, samesite="strict", secure=_secure(request))
    admin_log.info("admin panel: signed in")
    return {"admin": True}


@app.post("/api/admin/logout")
async def admin_logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(admin.COOKIE)
    if token:
        await state["redis"].delete(admin.session_key(token))
    response.delete_cookie(admin.COOKIE, path=admin.COOKIE_PATH, httponly=True, samesite="strict", secure=_secure(request))
    return {"admin": False}


@app.get("/api/admin/me", dependencies=AdminOnly)
def admin_me() -> dict[str, Any]:
    return {"admin": True}


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
    margin never spent, the reserve kept for live games and fans, the fans' share, the pause after a 429."""
    try:
        admin.set_limits(name, body)
    except LookupError:
        raise HTTPException(404, "There's no such allowance.") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
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


@app.get("/api/admin/users", dependencies=AdminOnly)
def admin_users(q: Annotated[str, Query(max_length=254)] = "", offset: Annotated[int, Query(ge=0, le=MAX_ID)] = 0, db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"overview": admin.overview(db), **admin.account_page(db, q, offset)}


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

# Browsers hand us the address their push service delivers to. Only real push services are accepted,
# so a crafted subscription can't make this server post to an arbitrary host.
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com", ".push.apple.com", ".notify.windows.com")
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _push_host_ok(endpoint: str) -> bool:
    url = urlparse(endpoint)
    host = url.hostname or ""
    return url.scheme == "https" and any(host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


@app.get("/api/push")
def push_info() -> dict[str, Any]:
    key = notify.public_key()
    return {"enabled": key is not None, "public_key": key}


def _sent_text(value: Any, longest: int) -> str | None:
    """A string from a request body that can be stored: 1 to `longest` characters, none of them control characters."""
    return value if isinstance(value, str) and 0 < len(value) <= longest and value.isprintable() else None


@app.post("/api/push/subscriptions")
def subscribe(body: dict = Body(...), fan: str = Depends(fan_id), signed: accounts.Signed | None = Depends(account_of), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Turn notifications on for this device (or update its quiet hours). Signed in, the device belongs
    to this session: signing out turns it off."""
    sub = body.get("subscription")
    keys = sub.get("keys") if isinstance(sub, dict) else None
    endpoint = _sent_text(sub.get("endpoint"), 999) if isinstance(sub, dict) else None
    p256dh, auth = (_sent_text(keys.get("p256dh"), 199), _sent_text(keys.get("auth"), 99)) if isinstance(keys, dict) else (None, None)
    if not (endpoint and p256dh and auth and _push_host_ok(endpoint)):
        raise HTTPException(400, "That isn't a push subscription this server can deliver to.")
    tz = str(body.get("timezone") or "UTC")[:64]
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz)
    except Exception:
        tz = "UTC"
    start, end = body.get("quiet_start"), body.get("quiet_end")
    start = start if isinstance(start, str) and TIME.match(start) else None
    end = end if isinstance(end, str) and TIME.match(end) else None
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
    db.execute(delete(PushSubscription).where(PushSubscription.fan_id == fan, PushSubscription.endpoint == endpoint))
    db.commit()
    return {"ok": True}


@app.post("/api/push/test")
async def push_test(fan: str = Depends(fan_id)) -> dict[str, int]:
    if notify.public_key() is None:
        raise HTTPException(503, "Notifications aren't set up on this server yet.")
    return {"sent": await asyncio.to_thread(notify.send_test, fan)}


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
    """Nothing more from this game ("Mute this game" on a notification). Its moments stay in the activity list."""
    if db.get(Event, event_id) is None:
        raise HTTPException(404, "No such game.")
    db.execute(insert(NotificationMute).values(fan_id=fan, event_id=event_id).on_conflict_do_nothing())
    db.commit()
    return {"event_id": event_id, "muted": True}


@app.delete("/api/me/mutes/{event_id}")
def unmute(event_id: RowId, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    db.execute(delete(NotificationMute).where(NotificationMute.fan_id == fan, NotificationMute.event_id == event_id))
    db.commit()
    return {"event_id": event_id, "muted": False}


ACTIVITY_DAYS = 30


@app.get("/api/me/activity")
def activity(before: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None, limit: int = 30, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Every moment of the fan's players since they followed them, newest first, whatever reached the
    phone: the app's activity list. Players set to off are left out, and a correction (a goal VAR ruled
    out) unless the list has the moment it takes back. Page with ?before=<next>."""
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
    if before is not None:
        query = query.where(Moment.id < before)
    items = [
        {
            "id": m.id, "kind": m.kind, "level": m.level, "priority": m.priority, "title": m.title, "body": m.body, "url": m.url,
            "player_id": m.player_id, "player": name, "event_id": m.event_id, "event": event, "muted": bool(muted), "at": m.created_at.isoformat(),
        }
        for m, name, event, muted in db.execute(query)
    ]
    return {"items": items, "next": items[-1]["id"] if len(items) == limit else None}


@app.get("/api/me/week")
def this_week(fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Today's "This week" and the Wire (week.py): the big moments of the fan's players over the last seven
    days, as each sport words them for a poster, biggest first; and their line-ups and news, newest first."""
    return {"posters": week.posters(db, fan), "wire": week.wire(db, fan)}


@app.delete("/api/follows/{player_id}")
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
    rules = await in_db(lambda db: db.scalar(select(Follow.alert_rules).where(Follow.fan_id == fan, Follow.player_id == player_id)))
    following = rules is not None
    body = {**card, "following": following, "alerts": (rules or {}).get("level", moments.DEFAULT_LEVEL) if following else None}
    digest = hashlib.blake2b(json.dumps(body, sort_keys=True, default=str).encode(), digest_size=8).hexdigest()
    etag = f'"{player_id}-{card.get("version", 0)}-{digest}"'
    response.headers.update({"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Cookie"})
    if etag in (t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")):
        return Response(status_code=304, headers=response.headers)  # with a cookie fan_id set, if any
    return body


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
    if await in_db(structured.binding, player_id) is not None:
        # A bound player's live score is already polled; a refresh re-reads fixtures and stats.
        if bus.is_locked(f"structured:{player_id}"):
            return {"queued": False, "reason": "A refresh is already running."}
        await _refresh_due(player_id)
        await enqueue("refresh_structured", player_id)
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
        return player_search.run(db, q, sport, fan, may_ask)


async def _source_search_allowed(fan: str, address: str | None) -> bool:
    """Count a search that goes on to the sources' own against the fan's minute and their address's.
    Over either, it doesn't go: a script sending a new name each time would otherwise keep the sources
    (and the search's threads) busy for everyone. A minute starts at its first such search."""
    for key in [f"sourcesearch:fan:{fan}", *([f"sourcesearch:address:{address}"] if address else [])]:
        used = await state["redis"].incr(key)
        await state["redis"].expire(key, 60, nx=True)  # the minute's end, set once (a count without one would never end)
        if used > SOURCE_SEARCHES_PER_MINUTE:
            return False
    return True


@app.get("/api/search")
async def search(request: Request, q: str, sport: str | None = None, fan: str = Depends(fan_id)) -> dict[str, Any]:
    """Athletes matching a name, from players on Sports Follow and the live sources (search.py)."""
    loop, address = asyncio.get_running_loop(), client_address(request)

    def may_ask() -> bool:
        # From the search's thread, and only when the sources would be asked: names the registry knows aren't counted.
        return asyncio.run_coroutine_threadsafe(_source_search_allowed(fan, address), loop).result()

    result = await asyncio.to_thread(_search, q[:80], (sport or "").lower() or None, fan, may_ask)
    return {**result, "research": player_search.research_status(await research_used(fan))}


@app.get("/api/athletes/wikidata/{qid}")
async def registry_preview(qid: str, sport: str | None = None) -> dict[str, Any]:
    """A registry athlete before following: their live-source preview once the source is found, or
    what the registry knows when no source has them (following then builds the page with AI)."""
    person = await in_db(_registry_athlete, {"qid": qid, "sport": sport})
    if person is None:
        raise HTTPException(404, "No such athlete.")
    holder = await in_db(pipeline.registry_holder, person.qid, person.sport if sport else None)
    if holder is not None:
        return {"player_id": holder}
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
async def athlete_preview(system: str, athlete_id: str, league: str | None = None) -> dict[str, Any]:
    """What following a search result would show, before following. An athlete already on Sports
    Follow answers with their player id; the app opens their page instead."""
    if system not in ADAPTERS or not _source_id_ok(system, athlete_id) or not _league_ok(league) or (system, athlete_id) in await in_db(registry.blocked):
        raise HTTPException(404, "No such athlete.")
    holder = await in_db(_holder, system, athlete_id)
    if holder is not None:
        return {"player_id": holder}
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


async def _stream(channels: list[str], first: Callable[[], Awaitable[list[dict[str, Any]]]], request: Request):
    """Every message on the channels, after `first()`: what they cover as it is now, read once
    subscribed so nothing published in between is missed. Each reconnect starts here, so it is how an
    app catches up. Holds no database connection."""
    pubsub = state["redis"].pubsub()
    await pubsub.subscribe(*channels)
    try:
        for event in await first():
            yield f"data: {json.dumps(event, default=str)}\n\n"
        while not await request.is_disconnected():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=HEARTBEAT)
            if msg is None:
                yield PING
                continue
            yield f"data: {msg['data']}\n\n"
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()


def _sse(body) -> StreamingResponse:
    return StreamingResponse(body, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/players/{player_id}/stream")
async def player_stream(player_id: RowId, request: Request) -> StreamingResponse:
    if await read_card(player_id) is None:
        raise HTTPException(404, "No such player.")

    async def first() -> list[dict[str, Any]]:
        card = await read_card(player_id)
        return [{"type": "card", "player_id": player_id, "card": card}] if card else []

    return _sse(_stream([bus.channel(player_id)], first, request))


@app.get("/api/me/stream")
async def my_stream(request: Request, fan: str = Depends(fan_id)) -> StreamingResponse:
    """One connection for every player the fan follows: browsers cap connections per origin, so one per player won't scale.
    It opens with what /api/me/following returns, so an app that reconnects (a phone back from the lock
    screen, a restart) gets every score and result it missed."""
    ids = await in_db(lambda db: db.scalars(select(Follow.player_id).where(Follow.fan_id == fan)).all())
    if not ids:
        async def idle():
            while not await request.is_disconnected():
                yield PING
                await asyncio.sleep(HEARTBEAT)
        return _sse(idle())

    async def first() -> list[dict[str, Any]]:
        return [{"type": "following", "players": await _following(fan)}]

    return _sse(_stream([bus.channel(i) for i in ids], first, request))


# ---------------------------------------------------------------- the Android app


@app.get("/.well-known/assetlinks.json")
def assetlinks() -> list[dict[str, Any]]:
    """Digital Asset Links: proves this site belongs to the Android app (a Trusted Web Activity), so
    Android opens it full screen with no browser bar. Empty until the app's package and signing
    certificate are configured."""
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


# ---------------------------------------------------------------- the built frontend (production)

# Served fresh every time, so a new build reaches installed apps: the page, the service worker that
# caches everything else, and the manifest.
NO_CACHE = {"index.html", "sw.js", "manifest.webmanifest"}
MEDIA = {".webmanifest": "application/manifest+json", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png"}

# A player's page, and a game of theirs (?g=<event id>): served with their own link preview.
PLAYER_PAGE = re.compile(r"player/([0-9]{1,10})/?")
GAME = re.compile(r"[0-9]{1,10}")

if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


def _site(request: Request) -> str:
    """The app's public address, for a preview's links: SPORTS_FOLLOW_SITE_URL, else this request's."""
    return SITE_URL or f"{request.url.scheme}://{request.url.netloc}"


async def _page_preview(path: str, request: Request) -> share.Preview | None:
    """The link preview of the page at `path`: a player's, a game of theirs, or None for the app's own."""
    m = PLAYER_PAGE.fullmatch(path)
    if not m or int(m[1]) > MAX_ID:
        return None
    game = request.query_params.get("g", "")
    try:
        return await _preview(int(m[1]), int(game) if GAME.fullmatch(game) and 0 < int(game) <= MAX_ID else None)
    except Exception:  # noqa: BLE001 — the page is the app: it opens with the app's own preview instead
        log.warning("link preview of /%s", path, exc_info=True)
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
    head (share.py), since the crawlers that read those run no JavaScript. Nothing here without a build
    (`npm run build` in web/)."""
    found = await run_in_threadpool(_built, path)
    if found is None:
        raise HTTPException(404, "Not Found")
    if isinstance(found, Path):
        cache = "no-cache" if found.name in NO_CACHE else "public, max-age=86400"
        return FileResponse(found, media_type=MEDIA.get(found.suffix), headers={"Cache-Control": cache})
    return HTMLResponse(share.page(found, await _page_preview(path, request), _site(request)), headers={"Cache-Control": "no-cache"})
