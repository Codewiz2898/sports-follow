"""The API and realtime layer (design section 7).

Fans are anonymous for now: a random id in a cookie. Everything a fan does is a subscription; all
data work happens in the worker, keyed by player.
"""

from __future__ import annotations

import asyncio
import json
import logging
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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import bus, pipeline, structured
from .agent import MODEL
from .config import FAN_COOKIE, REDIS_URL
from .db import get_db
from .models import Follow, Player, PlayerAlias, PlayerCard

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
    ids = db.scalars(select(Follow.player_id).where(Follow.fan_id == fan).order_by(Follow.created_at)).all()
    cards = [c for c in [await read_card(db, pid) for pid in ids] if c]
    return {"players": [_summary(c) for c in cards]}


@app.post("/api/follows")
async def follow(body: dict = Body(...), fan: str = Depends(fan_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    query = str(body.get("query") or "").strip()
    if not 2 <= len(query) <= 80:
        raise HTTPException(400, "Type a player's name, 2 to 80 characters.")
    player = pipeline.resolve_or_create(db, query)
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
    following = bool(db.scalar(select(Follow.id).where(Follow.fan_id == fan, Follow.player_id == player_id)))
    return {**card, "following": following}


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


@app.get("/api/search")
def search(q: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Players already in the registry whose name or aliases match. New names are followed by POST /api/follows."""
    needle = pipeline.slugify(q)
    if len(needle) < 2:
        return {"players": []}
    rows = db.execute(
        select(Player.id, Player.name, Player.sport, Player.status, Player.teams, func.count(Follow.id))
        .join(PlayerAlias, PlayerAlias.player_id == Player.id)
        .outerjoin(Follow, Follow.player_id == Player.id)
        .where(PlayerAlias.alias.contains(needle))
        .group_by(Player.id)
        .order_by(func.count(Follow.id).desc())
        .limit(8)
    ).all()
    return {"players": [{"player_id": r[0], "name": r[1], "sport": r[2], "status": r[3], "teams": r[4], "followers": r[5]} for r in rows]}


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


# ---------------------------------------------------------------- the built frontend (production)

if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        return FileResponse(WEB_DIST / "index.html")
