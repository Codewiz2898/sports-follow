"""Sportmonks (sportmonks.com): licensed football and cricket data, one client for both APIs.

The Sportmonks adapters run only with a token: SPORTS_FOLLOW_SPORTMONKS_TOKEN, or the file
~/.config/sports-follow/sportmonks-token (kept outside the repo). The token travels in the
Authorization header, never in a URL, so it can't end up in logs (both APIs accept it there, though
cricket's documentation shows it as a query parameter). Football is limited per entity per hour
(2,500 on Growth, in the response body); cricket to 180 requests a minute (in the response headers).
A 429 pauses reads for a minute, like Lichess.

Terms (sportmonks.com/terms-of-service): commercial use in our own app and storing the data in our
own database are allowed; reselling it is not. Logos and player photos are not licensed.
"""

from __future__ import annotations

import os
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from . import espn
from .base import AdapterError

BASE = "https://api.sportmonks.com/v3"
CRICKET = "https://cricket.sportmonks.com/api/v2.0"
TOKEN_FILE = Path(os.environ.get("SPORTS_FOLLOW_SPORTMONKS_TOKEN_FILE", Path.home() / ".config" / "sports-follow" / "sportmonks-token"))
MAX_PAGES = 8  # 25 rows a page; a year of one team's fixtures is two or three
# Requests kept back for live matches: background reads (history.py) stop when an entity's remaining
# allowance falls this low, and start again when the window resets. Football counts per hour
# (of 2,500), cricket per minute (of 180).
LIVE_RESERVE = int(os.environ.get("SPORTS_FOLLOW_SPORTMONKS_RESERVE", 1000))
CRICKET_RESERVE = 60

_client = httpx.Client(headers={"user-agent": espn.USER_AGENT, "accept": "application/json"}, timeout=20)
_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()
_state = {"paused_until": 0.0}
remaining: dict[str, tuple[int, float]] = {}  # entity -> (requests left this hour, when the hour resets), from the last response


class RateLimited(AdapterError):
    """Sportmonks said the hourly limit is used up for an entity."""


@lru_cache(maxsize=1)
def token() -> str | None:
    value = os.environ.get("SPORTS_FOLLOW_SPORTMONKS_TOKEN", "").strip()
    if not value and TOKEN_FILE.is_file():
        value = TOKEN_FILE.read_text().strip()
    return value or None


def get_json(path: str, ttl: float, **params: Any) -> Any:
    """GET a Sportmonks document, cached for ttl seconds: a football path after /v3
    ("/football/fixtures/1") or a full cricket URL (f"{CRICKET}/fixtures/1")."""
    key = f"{path}?{urlencode(sorted(params.items()))}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if not token():
        raise AdapterError("no Sportmonks token (~/.config/sports-follow/sportmonks-token)")
    if time.time() < _state["paused_until"]:
        raise RateLimited("Sportmonks hourly limit reached; paused for a minute")
    try:
        res = _client.get(path if path.startswith("https://") else f"{BASE}{path}", params=params, headers={"authorization": token()})
    except httpx.HTTPError as exc:
        raise AdapterError(f"Sportmonks unreachable: {exc}") from exc
    if res.status_code == 429:
        _state["paused_until"] = time.time() + 60
        raise RateLimited("Sportmonks hourly limit reached (429); paused for a minute")
    if res.status_code == 401:
        raise AdapterError("Sportmonks rejected the token (HTTP 401)")
    if res.status_code != 200:
        message = ""
        try:
            message = res.json().get("message") or ""
        except ValueError:
            pass
        raise AdapterError(f"Sportmonks returned HTTP {res.status_code} for {path}: {message}"[:300])
    data = res.json()
    limit = data.get("rate_limit") or {}
    if limit.get("requested_entity"):
        remaining[limit["requested_entity"]] = (int(limit.get("remaining") or 0), time.time() + int(limit.get("resets_in_seconds") or 3600))
    elif res.headers.get("x-ratelimit-remaining", "").isdigit():
        remaining["Cricket"] = (int(res.headers["x-ratelimit-remaining"]), time.time() + 60)
    with _lock:
        _cache[key] = (time.time(), data)
        if len(_cache) > 2000:
            for old, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:500]:
                _cache.pop(old, None)
    return data


def get_all(path: str, ttl: float, **params: Any) -> list[dict[str, Any]]:
    """Every row of a paginated list (25 a page in football, 100 in cricket), up to MAX_PAGES pages."""
    rows: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        data = get_json(path, ttl, **params, page=page)
        rows += data.get("data") or []
        meta = data.get("meta") or {}
        more = (data.get("pagination") or {}).get("has_more") or int(meta.get("current_page") or 1) < int(meta.get("last_page") or 1)
        if not more:
            break
    return rows


def spare(entity: str) -> bool:
    """Whether a background read of this entity ("Fixture", "Cricket") leaves the live reserve intact."""
    reserve = CRICKET_RESERVE if entity == "Cricket" else LIVE_RESERVE
    left, resets_at = remaining.get(entity, (reserve + 1, 0.0))
    return left > reserve or time.time() >= resets_at
