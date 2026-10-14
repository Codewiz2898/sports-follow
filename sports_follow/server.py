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
from collections.abc import Awaitable, Callable
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
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from . import bus, credits as credits_page, games, moments, notify, pipeline, registry, results, search as player_search, structured
from .adapters import ADAPTERS, AdapterError
from .agent import MODEL
from .config import AGENT_LIVE, ANDROID_CERT_SHA256, ANDROID_PACKAGE, COOKIE_SECRET, COOKIE_SECRET_FILE, FAN_COOKIE, REDIS_URL, RESEARCH_PER_ADDRESS, RESEARCH_PER_HOUR
from .db import get_db, session
from .models import Event, Follow, Moment, Notification, NotificationMute, Player, PlayerAlias, PlayerCard, PlayerIdentity, PushSubscription

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
    state["arq"] = await create_pool(RedisSettings.from_dsn(REDIS_URL))
    state["redis"] = aredis.Redis.from_url(REDIS_URL, decode_responses=True)
    yield
    await state["arq"].aclose()
    await state["redis"].aclose()


app = FastAPI(title="Sports Follow", lifespan=lifespan)

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
            return await JSONResponse({"detail": "This API only takes changes from the Sports Follow app."}, status_code=403)(scope, receive, send)
        await self.app(scope, receive, send)


app.add_middleware(SameOriginWrites)


def arq() -> ArqRedis:
    return state["arq"]


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
    are never taken unsigned: seeing one in the database isn't enough to be that fan."""
    if not 0 < len(value) <= 64 or not value.isprintable() or NEW_ID.fullmatch(value):
        return False
    with session() as db:
        return _owns_anything(db, value)


def fan_id(request: Request, response: Response) -> str:
    value = request.cookies.get(FAN_COOKIE) or ""
    signed = _signed(value)
    if signed:
        fan, day = signed
        if _today() - day < REFRESH_DAYS:
            return fan
    else:
        fan = value if _unsigned_fan(value) else secrets.token_urlsafe(24)
    # Secure once served over HTTPS (behind the proxy, uvicorn --proxy-headers sets the scheme).
    response.set_cookie(FAN_COOKIE, sign_fan(fan), max_age=COOKIE_DAYS * 24 * 3600, httponly=True, samesite="lax", secure=request.url.scheme == "https")
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
        # The newest result: the app's player tiles and Today's big moment show it.
        "recent_results": (card.get("recent_results") or [])[:1],
    }


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {"model": MODEL}


@app.get("/api/credits")
def credits() -> dict[str, Any]:
    """Where this build's scores, ratings, evaluations, words and code come from."""
    return credits_page.credits()


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
    limits.append((player_search.research_hour_key(now), RESEARCH_PER_HOUR, 2 * 3600, "Sports Follow has done all the AI research it can this hour. Try again later."))
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
    if not re.fullmatch(r"Q[0-9]{1,12}", qid) or len(sport) > 60 or not sport.isprintable():
        return None
    athlete = registry.get(db, qid, sport or None)
    return registry.Person.of(athlete) if athlete else None


# What each source's athlete ids look like: digits, unless listed here. An id goes into the source's URL
# or query as it is, so one of any other shape is refused before anything is sent.
SOURCE_IDS = {
    "liquipedia_esports": re.compile(r"[a-z0-9]{1,30}:[^\s\[\]{}|#<>]{1,100}"),  # wiki:Page_name
    "pubg_esports": re.compile(r"account\.[0-9a-f]{32}"),
}
DIGITS = re.compile(r"[0-9]{1,12}")
LEAGUE = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")  # "nba", "wta", "cs-go", "pubgmobile"


def _source_id_ok(system: str, athlete_id: str) -> bool:
    return athlete_id.isprintable() and bool(SOURCE_IDS.get(system, DIGITS).fullmatch(athlete_id))


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
    AI research.
    """
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
        # of minutes. Without one: the agent builds the whole page.
        if ref is not None:
            await arq().enqueue_job("refresh_structured", player_id)
        await arq().enqueue_job("build_card", player_id)
    return player_id


@app.post("/api/follows")
async def follow(request: Request, body: dict = Body(...), fan: str = Depends(fan_id)) -> dict[str, Any]:
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
        known = await in_db(lambda db: db.scalar(select(PlayerCard).join(PlayerAlias, PlayerAlias.player_id == PlayerCard.player_id).where(PlayerAlias.alias == pipeline.slugify(query))))
        player = None
    # A name nobody has followed (or a page whose build failed) sends the research agent out: that is
    # the part with a daily allowance. Following a player already here costs nothing.
    counted = known is None or known.status == "failed"
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
        await arq().enqueue_job("build_card", player_id)
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
def subscribe(body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Turn notifications on for this device (or update its quiet hours)."""
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
    stmt = insert(PushSubscription).values(fan_id=fan, endpoint=endpoint, p256dh=p256dh, auth=auth, timezone=tz, quiet_start=start, quiet_end=end)
    # A device already here moves to another fan only with its own keys: a browser subscribing again sends
    # the ones it has, while someone who learned just the endpoint would take the device's alerts away.
    same_keys = (PushSubscription.p256dh == stmt.excluded.p256dh) & (PushSubscription.auth == stmt.excluded.auth)
    stmt = stmt.on_conflict_do_update(
        index_elements=["endpoint"],
        set_={"fan_id": fan, "p256dh": p256dh, "auth": auth, "timezone": tz, "quiet_start": start, "quiet_end": end, "failures": 0},
        where=(PushSubscription.fan_id == fan) | same_keys,
    )
    if db.execute(stmt.returning(PushSubscription.id)).first() is None:
        raise HTTPException(409, "That device's notifications belong to someone else.")
    # This device and the fan's newest others, up to notify.MAX_DEVICES: every push goes to each of them,
    # so piled-up subscriptions mustn't turn one alert into hundreds of sends.
    others = PushSubscription.fan_id == fan, PushSubscription.endpoint != endpoint
    newest = select(PushSubscription.id).where(*others).order_by(PushSubscription.id.desc()).limit(notify.MAX_DEVICES - 1)
    db.execute(delete(PushSubscription).where(*others, PushSubscription.id.not_in(newest)))
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
    phone: the app's activity list. Players set to off are left out. Page with ?before=<next>."""
    limit = max(1, min(limit, 50))
    level = func.coalesce(Follow.alert_rules["level"].astext, moments.DEFAULT_LEVEL)
    query = (
        select(Moment, Player.name, Event.title, NotificationMute.event_id.is_not(None))
        .join(Follow, and_(Follow.player_id == Moment.player_id, Follow.fan_id == fan))
        .join(Player, Player.id == Moment.player_id)
        .outerjoin(Event, Event.id == Moment.event_id)
        .outerjoin(NotificationMute, and_(NotificationMute.fan_id == fan, NotificationMute.event_id == Moment.event_id))
        .where(level != "off", Moment.created_at >= Follow.created_at, Moment.created_at >= func.now() - timedelta(days=ACTIVITY_DAYS))
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
        await arq().enqueue_job("refresh_structured", player_id)
        return {"queued": True}
    if not AGENT_LIVE:
        return {"queued": False, "reason": "Live scores aren't available for this sport yet."}
    if bus.is_locked(f"live:{player_id}"):
        return {"queued": False, "reason": "A live check is already running."}
    await _refresh_due(player_id)
    await arq().enqueue_job("refresh_live", player_id)
    return {"queued": True}


# ---------------------------------------------------------------- search


def _search(q: str, sport: str | None, fan: str) -> dict[str, Any]:
    with session() as db:
        return player_search.run(db, q, sport, fan)


@app.get("/api/search")
async def search(q: str, sport: str | None = None, fan: str = Depends(fan_id)) -> dict[str, Any]:
    """Athletes matching a name, from players on Sports Follow and the live sources (search.py)."""
    result = await asyncio.to_thread(_search, q[:80], (sport or "").lower() or None, fan)
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
    base = {"qid": person.qid, "born": person.birth_date.isoformat() if person.birth_date else None}
    if found is None:
        athlete = await in_db(registry.get, person.qid, person.sport)
        return {
            **base, "system": None, "athlete_id": None, "league": person.league, "name": person.name, "sport": games.label(person.sport),
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
    if system not in ADAPTERS or not _source_id_ok(system, athlete_id) or not _league_ok(league):
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
    return found


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


# ---------------------------------------------------------------- the built frontend (production)

# Served fresh every time, so a new build reaches installed apps: the page, the service worker that
# caches everything else, and the manifest.
NO_CACHE = {"index.html", "sw.js", "manifest.webmanifest"}
MEDIA = {".webmanifest": "application/manifest+json", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png"}

if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        """Files the build puts at the root (service worker, manifest, icons); every other path is the app."""
        root = WEB_DIST.resolve()
        file = (root / path).resolve()
        if not (path and file.is_file() and root in file.parents):
            file = root / "index.html"
        cache = "no-cache" if file.name in NO_CACHE else "public, max-age=86400"
        return FileResponse(file, media_type=MEDIA.get(file.suffix), headers={"Cache-Control": cache})
