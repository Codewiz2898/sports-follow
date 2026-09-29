"""The API and realtime layer (design section 7).

Fans are anonymous for now: a random id in a cookie. Everything a fan does is a subscription; all
data work happens in the worker, keyed by player.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import redis.asyncio as aredis
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import bus, moments, notify, pipeline, registry, search as player_search, structured
from .adapters import ADAPTERS, AdapterError
from .agent import MODEL
from .config import ANDROID_CERT_SHA256, ANDROID_PACKAGE, FAN_COOKIE, REDIS_URL
from .db import get_db, session
from .models import Follow, Player, PlayerAlias, PlayerCard, PlayerIdentity, PushSubscription

log = logging.getLogger("sports_follow")
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
HEARTBEAT = 15

state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["arq"] = await create_pool(RedisSettings.from_dsn(REDIS_URL))
    state["redis"] = aredis.Redis.from_url(REDIS_URL, decode_responses=True)
    yield
    await state["arq"].aclose()
    await state["redis"].aclose()


app = FastAPI(title="Sports Follow", lifespan=lifespan)


def arq() -> ArqRedis:
    return state["arq"]


# ---------------------------------------------------------------- fan identity


def fan_id(request: Request, response: Response) -> str:
    fan = request.cookies.get(FAN_COOKIE)
    if not fan or len(fan) > 64:
        fan = secrets.token_urlsafe(18)
        response.set_cookie(FAN_COOKIE, fan, max_age=5 * 365 * 24 * 3600, httponly=True, samesite="lax")
    return fan


# ---------------------------------------------------------------- cards


async def read_card(db: Session, player_id: int) -> dict[str, Any] | None:
    raw = await state["redis"].get(bus.card_key(player_id))
    if raw:
        return json.loads(raw)
    row = db.get(PlayerCard, player_id)
    if row is None:
        return None
    # Redis was flushed or lost: rebuild the hot copy from Postgres.
    await state["redis"].set(bus.card_key(player_id), json.dumps(row.card, default=str))
    return row.card


def _summary(card: dict[str, Any]) -> dict[str, Any]:
    return {k: card.get(k) for k in ("player_id", "slug", "status", "version", "built_at", "player", "live", "upcoming", "freshness", "error")} | {
        "news": (card.get("news") or [])[:3]
    }


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {"model": MODEL}


@app.get("/api/me/following")
async def following(fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.execute(select(Follow.player_id, Follow.alert_rules).where(Follow.fan_id == fan).order_by(Follow.created_at)).all()
    out = []
    for player_id, rules in rows:
        card = await read_card(db, player_id)
        if card:
            out.append({**_summary(card), "alerts": (rules or {}).get("level", moments.DEFAULT_LEVEL)})
    return {"players": out}


# ---------------------------------------------------------------- AI research allowance


async def research_used(fan: str) -> int:
    return int(await state["redis"].get(player_search.research_key(fan)) or 0)


async def take_research(fan: str) -> bool:
    """Count one AI research against the fan's day; False (and nothing counted) when none are left."""
    key = player_search.research_key(fan)
    used = await state["redis"].incr(key)
    await state["redis"].expire(key, 2 * 24 * 3600)
    if used > player_search.RESEARCH_PER_DAY:
        await state["redis"].decr(key)
        return False
    return True


async def give_back_research(fan: str) -> None:
    await state["redis"].decr(player_search.research_key(fan))


@app.get("/api/me/research")
async def research(fan: str = Depends(fan_id)) -> dict[str, Any]:
    return player_search.research_status(await research_used(fan))


# ---------------------------------------------------------------- following


def _registry_athlete(db: Session, body: dict) -> registry.Person | None:
    qid = str(body.get("qid") or "")
    if not re.fullmatch(r"Q\d{1,12}", qid):
        return None
    athlete = registry.get(db, qid, str(body.get("sport") or "").lower() or None)
    return registry.Person.of(athlete) if athlete else None


async def _resolve(person: registry.Person) -> tuple[Any, Any] | None:
    try:
        return await asyncio.to_thread(registry.resolve, person)
    except AdapterError as exc:
        log.warning("resolve %s: %s", person.qid, exc)
        raise HTTPException(502, "Couldn't reach the live source to find this athlete. Try again in a moment.")


async def _follow_athlete(body: dict, fan: str, db: Session) -> int:
    """Follow the exact athlete a fan picked in search, by registry id or source id; never by name.

    A registry athlete is found in their sport's live source by the id Wikidata has, or by name and a
    matching birth date; one no source confirms is built by the research agent, which counts as an
    AI research.
    """
    person = _registry_athlete(db, body) if body.get("qid") else None
    if body.get("qid") and person is None:
        raise HTTPException(404, "That athlete isn't in the player registry.")
    if person and not body.get("system"):
        holder = db.scalar(select(PlayerIdentity.player_id).where(PlayerIdentity.system == "wikidata", PlayerIdentity.external_id == person.qid))
        if holder is not None:
            player_id, created, ref = holder, False, None
        else:
            found = await _resolve(person)
            if found is None:
                if not await take_research(fan):
                    raise HTTPException(429, "No live source has this athlete, so following them needs an AI research, and you've used today's. Try again tomorrow.")
                player_id, created = await asyncio.to_thread(pipeline.start_from_registry, person)
                if not created:
                    await give_back_research(fan)
                ref = None
            else:
                adapter, ref = found
                player_id, created = await asyncio.to_thread(pipeline.start_from_pick, adapter, ref)
    else:
        system = str(body.get("system") or "")
        athlete_id = str(body.get("athlete_id") or "").strip()
        league = body.get("league") if isinstance(body.get("league"), str) else None
        adapter = ADAPTERS.get(system)
        if adapter is None or not athlete_id or len(athlete_id) > 40:
            raise HTTPException(400, "Pick an athlete from the search results.")
        try:
            ref = await asyncio.to_thread(adapter.player, athlete_id, league)
        except AdapterError as exc:
            log.warning("follow %s:%s: %s", system, athlete_id, exc)
            raise HTTPException(502, "Couldn't reach the live source to confirm this athlete. Try again in a moment.")
        if ref is None:
            raise HTTPException(404, "That athlete isn't in the source any more.")
        player_id, created = await asyncio.to_thread(pipeline.start_from_pick, adapter, ref)
    if person and (ref is None or person.ids.get(ref.system) in (None, ref.athlete_id)):
        registry.link(db, player_id, person.qid)
    if not db.scalar(select(Follow).where(Follow.fan_id == fan, Follow.player_id == player_id)):
        db.add(Follow(fan_id=fan, player_id=player_id, alert_rules={}))
    db.commit()
    if created:
        # With a live source: scores and fixtures in seconds, the agent's profile and news in a couple
        # of minutes. Without one: the agent builds the whole page.
        if ref is not None:
            await arq().enqueue_job("refresh_structured", player_id)
        await arq().enqueue_job("build_card", player_id)
    return player_id


@app.post("/api/follows")
async def follow(body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    if body.get("system") or body.get("qid"):
        player_id = await _follow_athlete(body, fan, db)
        return {"player_id": player_id, "card": await read_card(db, player_id)}
    if body.get("player_id") is not None:
        # A player already here, by id: a name could belong to their namesake ("nikola-jokic-football").
        try:
            player = db.get(Player, int(body["player_id"]))
        except (TypeError, ValueError):
            player = None
        if player is None:
            raise HTTPException(404, "No such player.")
        known = db.get(PlayerCard, player.id)
        query = None
    else:
        query = str(body.get("query") or "").strip()
        if not 2 <= len(query) <= 80:
            raise HTTPException(400, "Type a player's name, 2 to 80 characters.")
        known = db.scalar(select(PlayerCard).join(PlayerAlias, PlayerAlias.player_id == PlayerCard.player_id).where(PlayerAlias.alias == pipeline.slugify(query)))
    # A name nobody has followed (or a page whose build failed) sends the research agent out: that is
    # the part with a daily allowance. Following a player already here costs nothing.
    counted = known is None or known.status == "failed"
    if counted and not await take_research(fan):
        raise HTTPException(429, "You've used today's AI researches. Pick a player from the search results, or try again tomorrow.")
    if query is not None:
        try:
            player = pipeline.resolve_or_create(db, query)
        except ValueError:
            if counted:
                await give_back_research(fan)
            raise HTTPException(400, "Type a player's name, 2 to 80 characters.")
    exists = db.scalar(select(Follow).where(Follow.fan_id == fan, Follow.player_id == player.id))
    if not exists:
        db.add(Follow(fan_id=fan, player_id=player.id, alert_rules={}))
    card_row = db.get(PlayerCard, player.id)
    needs_build = card_row is None or card_row.status in ("building", "failed")
    retrying = card_row is not None and card_row.status == "failed"
    if retrying:
        # A fan asking again is the retry for a failed build.
        card_row.status = "building"
        card_row.error = None
        card_row.card = {**pipeline.placeholder_card(player), "version": card_row.version}
    db.commit()
    if retrying:
        # Write through to the hot copy and tell every subscriber, or they keep seeing "failed".
        bus.store_card(player.id, card_row.card)
    if needs_build:
        await arq().enqueue_job("build_card", player.id)
    card = await read_card(db, player.id)
    return {"player_id": player.id, "card": card}


@app.put("/api/follows/{player_id}/alerts")
def set_alerts(player_id: int, body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Which of this player's moments the fan is told about: everything, key, results or off."""
    level = body.get("level")
    if level not in moments.LEVELS:
        raise HTTPException(400, f"level must be one of {', '.join(moments.LEVELS)}")
    row = db.scalar(select(Follow).where(Follow.fan_id == fan, Follow.player_id == player_id))
    if row is None:
        raise HTTPException(404, "Follow the player first.")
    row.alert_rules = {**(row.alert_rules or {}), "level": level}
    return {"player_id": player_id, "alerts": level}


# ---------------------------------------------------------------- push notifications

# Browsers hand us the address their push service delivers to. Only real push services are accepted,
# so a crafted subscription can't make this server post to an arbitrary host.
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com", ".push.apple.com", ".notify.windows.com")
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _push_host_ok(endpoint: str) -> bool:
    from urllib.parse import urlparse

    url = urlparse(endpoint)
    host = url.hostname or ""
    return url.scheme == "https" and any(host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


@app.get("/api/push")
def push_info() -> dict[str, Any]:
    key = notify.public_key()
    return {"enabled": key is not None, "public_key": key}


@app.post("/api/push/subscriptions")
def subscribe(body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Turn notifications on for this device (or update its quiet hours)."""
    sub = body.get("subscription") or {}
    endpoint, keys = str(sub.get("endpoint") or ""), sub.get("keys") or {}
    p256dh, auth = str(keys.get("p256dh") or ""), str(keys.get("auth") or "")
    if not (endpoint and len(endpoint) < 1000 and _push_host_ok(endpoint) and 0 < len(p256dh) < 200 and 0 < len(auth) < 100):
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
    if "quiet_start" not in body and body.get("previous_endpoint"):
        # The browser replaced this device's subscription: keep its quiet hours.
        old = db.scalar(select(PushSubscription).where(PushSubscription.fan_id == fan, PushSubscription.endpoint == str(body["previous_endpoint"])))
        if old is not None:
            start, end = old.quiet_start, old.quiet_end
            db.delete(old)
    stmt = insert(PushSubscription).values(fan_id=fan, endpoint=endpoint, p256dh=p256dh, auth=auth, timezone=tz, quiet_start=start, quiet_end=end)
    db.execute(stmt.on_conflict_do_update(index_elements=["endpoint"], set_={"fan_id": fan, "p256dh": p256dh, "auth": auth, "timezone": tz, "quiet_start": start, "quiet_end": end, "failures": 0}))
    return {"ok": True, "timezone": tz, "quiet_start": start, "quiet_end": end}


@app.delete("/api/push/subscriptions")
def unsubscribe(body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, bool]:
    db.execute(delete(PushSubscription).where(PushSubscription.fan_id == fan, PushSubscription.endpoint == str(body.get("endpoint") or "")))
    return {"ok": True}


@app.post("/api/push/test")
async def push_test(fan: str = Depends(fan_id)) -> dict[str, int]:
    if notify.public_key() is None:
        raise HTTPException(503, "Notifications aren't set up on this server yet.")
    return {"sent": await asyncio.to_thread(notify.send_test, fan)}


@app.delete("/api/follows/{player_id}")
def unfollow(player_id: int, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, bool]:
    pipeline.unfollow(db, fan, player_id)
    return {"ok": True}


@app.get("/api/players/{player_id}/card")
async def card(player_id: int, request: Request, response: Response, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> Any:
    card = await read_card(db, player_id)
    if card is None:
        raise HTTPException(404, "No such player.")
    etag = f'"{player_id}-{card.get("version", 0)}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "public, max-age=5" if (card.get("live") or {}).get("is_live") else "public, max-age=60"
    rules = db.scalar(select(Follow.alert_rules).where(Follow.fan_id == fan, Follow.player_id == player_id))
    following = rules is not None
    return {**card, "following": following, "alerts": (rules or {}).get("level", moments.DEFAULT_LEVEL) if following else None}


@app.post("/api/players/{player_id}/refresh")
async def refresh(player_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(PlayerCard, player_id)
    if row is None:
        raise HTTPException(404, "No such player.")
    if row.status != "ready":
        return {"queued": False, "reason": "The player's first card is still being built."}
    if structured.binding(db, player_id) is not None:
        # A bound player's live score is already polled; a refresh re-reads fixtures and stats.
        if bus.is_locked(f"structured:{player_id}"):
            return {"queued": False, "reason": "A refresh is already running."}
        await arq().enqueue_job("refresh_structured", player_id)
        return {"queued": True}
    if bus.is_locked(f"live:{player_id}"):
        return {"queued": False, "reason": "A live check is already running."}
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
async def registry_preview(qid: str, sport: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """A registry athlete before following: their live-source preview once the source is found, or
    what the registry knows when no source has them (following then builds the page with AI)."""
    person = _registry_athlete(db, {"qid": qid, "sport": sport})
    if person is None:
        raise HTTPException(404, "No such athlete.")
    holder = db.scalar(select(PlayerIdentity.player_id).where(PlayerIdentity.system == "wikidata", PlayerIdentity.external_id == person.qid))
    if holder is not None:
        return {"player_id": holder}
    found = await _resolve(person)
    base = {"qid": person.qid, "born": person.birth_date.isoformat() if person.birth_date else None}
    if found is None:
        athlete = registry.get(db, person.qid, person.sport)
        return {
            **base, "system": None, "athlete_id": None, "league": person.league, "name": person.name, "sport": person.sport.title(),
            "teams": person.teams, "source": "Wikidata", "source_url": f"https://www.wikidata.org/wiki/{person.qid}",
            "next": None, "last": None, "stats": [], "stats_note": "", "live": False, "country": athlete.country if athlete else None,
        }
    adapter, ref = found
    holder = db.scalar(select(PlayerIdentity.player_id).where(PlayerIdentity.system == ref.system, PlayerIdentity.external_id == ref.athlete_id))
    if holder is not None:
        return {"player_id": holder}
    try:
        shown = await asyncio.to_thread(player_search.preview, ref.system, ref.athlete_id, ref.league)
    except AdapterError as exc:
        log.warning("preview %s: %s", person.qid, exc)
        raise HTTPException(502, "The live source didn't answer. Try again in a moment.")
    return {**(shown or {}), **base, "live": True}


@app.get("/api/athletes/{system}/{athlete_id}")
async def athlete_preview(system: str, athlete_id: str, league: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """What following a search result would show, before following. An athlete already on Sports
    Follow answers with their player id; the app opens their page instead."""
    if system not in ADAPTERS or not athlete_id or len(athlete_id) > 40:
        raise HTTPException(404, "No such athlete.")
    holder = db.scalar(select(PlayerIdentity.player_id).where(PlayerIdentity.system == system, PlayerIdentity.external_id == athlete_id))
    if holder is not None:
        return {"player_id": holder}
    try:
        found = await asyncio.to_thread(player_search.preview, system, athlete_id, league)
    except AdapterError as exc:
        log.warning("preview %s:%s: %s", system, athlete_id, exc)
        raise HTTPException(502, "The live source didn't answer. Try again in a moment.")
    if found is None:
        raise HTTPException(404, "No such athlete.")
    return found


# ---------------------------------------------------------------- realtime


async def _stream(channels: list[str], initial: list[dict[str, Any]], request: Request):
    pubsub = state["redis"].pubsub()
    await pubsub.subscribe(*channels)
    try:
        for event in initial:
            yield f"data: {json.dumps(event, default=str)}\n\n"
        while not await request.is_disconnected():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=HEARTBEAT)
            if msg is None:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {msg['data']}\n\n"
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()


def _sse(body) -> StreamingResponse:
    return StreamingResponse(body, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/players/{player_id}/stream")
async def player_stream(player_id: int, request: Request, db: Session = Depends(get_db)) -> StreamingResponse:
    card = await read_card(db, player_id)
    if card is None:
        raise HTTPException(404, "No such player.")
    return _sse(_stream([bus.channel(player_id)], [{"type": "card", "player_id": player_id, "card": card}], request))


@app.get("/api/me/stream")
async def my_stream(request: Request, fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> StreamingResponse:
    """One connection for every player the fan follows: browsers cap connections per origin, so one per player won't scale."""
    ids = db.scalars(select(Follow.player_id).where(Follow.fan_id == fan)).all()
    if not ids:
        async def idle():
            while not await request.is_disconnected():
                yield ": keep-alive\n\n"
                await asyncio.sleep(HEARTBEAT)
        return _sse(idle())
    return _sse(_stream([bus.channel(i) for i in ids], [], request))


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
