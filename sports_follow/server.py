"""FastAPI app: serves the UI and streams agent progress over Server-Sent Events."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from llm_providers import Client, LlmError

from .agent import GATEWAY_URL, MODEL, AgentError, follow_player, gateway, live_update

logger = logging.getLogger("sports_follow")
STATIC = Path(__file__).parent / "static"
REPORT_TTL = 10 * 60
LIVE_TTL = 60

app = FastAPI(title="Sports Follow")
_llm: Client | None = None
_cache: dict[tuple[str, ...], tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def llm() -> Client:
    global _llm
    if _llm is None:
        _llm = gateway()
    return _llm


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _cached_stream(
    key: tuple[str, ...], ttl: int, run: Callable[[], Iterator[dict[str, Any]]]
) -> Iterator[str]:
    with _cache_lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        yield _sse({"type": "result", "data": hit[1], "cached": True})
        return
    try:
        for event in run():
            if event["type"] == "result":
                with _cache_lock:
                    _cache[key] = (time.time(), event["data"])
            yield _sse(event)
    except AgentError as exc:
        yield _sse({"type": "error", "message": str(exc)})
    except LlmError as exc:
        yield _sse({"type": "error", "message": f"LLM gateway error ({exc.kind}): {exc}"})
    except Exception as exc:  # last resort: the response is already streaming, so report instead of hanging the UI
        logger.exception("Agent run failed")
        yield _sse({"type": "error", "message": f"Unexpected error: {exc}"})


def _event_stream(body: Iterator[str]) -> StreamingResponse:
    return StreamingResponse(
        body,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {"model": MODEL, "gateway": GATEWAY_URL}


@app.get("/api/follow")
def follow(player: str = Query(min_length=2, max_length=80)) -> StreamingResponse:
    player = player.strip()
    key = ("report", player.lower())
    return _event_stream(_cached_stream(key, REPORT_TTL, lambda: follow_player(llm(), player)))


@app.get("/api/live")
def live(
    player: str = Query(min_length=2, max_length=80),
    sport: str = Query(max_length=40),
    teams: list[str] = Query(default=[]),
) -> StreamingResponse:
    key = ("live", player.lower())
    return _event_stream(_cached_stream(key, LIVE_TTL, lambda: live_update(llm(), player, sport, teams)))
