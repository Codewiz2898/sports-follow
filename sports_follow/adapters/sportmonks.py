"""Sportmonks (sportmonks.com): licensed football and cricket data, one client for both APIs.

The Sportmonks adapters run only with a token: SPORTS_FOLLOW_SPORTMONKS_TOKEN, or the file
~/.config/sports-follow/sportmonks-token (kept outside the repo). The token travels in the
Authorization header, never in a URL, so it can't end up in logs (both APIs accept it there, though
cricket's documentation shows it as a query parameter). Football allows 2,000 requests an hour per
entity on the Starter plan (counted in the response body); cricket 2,000 an hour per endpoint on
Major (in the response headers, without a reset time). Both are counted and kept to by the source budget
(budget.py, BUDGET): each entity or endpoint is a bucket of its own, and what Sportmonks says is left
corrects the count. A 429 pauses every lane for a minute.

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

from ..config import USER_AGENT
from . import budget
from .base import AdapterError, Provider

BASE = "https://api.sportmonks.com/v3"
CRICKET = "https://cricket.sportmonks.com/api/v2.0"
TOKEN_FILE = Path(os.environ.get("SPORTS_FOLLOW_SPORTMONKS_TOKEN_FILE", Path.home() / ".config" / "sports-follow" / "sportmonks-token"))
MAX_PAGES = 8  # 25 rows a page; a year of one team's fixtures is two or three
# Requests kept back for live matches, of each hour's 2,000: background reads (refreshes, history.py)
# stop when the allowance live polls draw on falls this low, and start again when the hour resets.
LIVE_RESERVE = int(os.environ.get("SPORTS_FOLLOW_SPORTMONKS_RESERVE", 1000))
CRICKET_MATCH = "Cricket fixtures/{id}"  # the cricket endpoint live matches are polled on
FIXTURES = "Fixture"  # the football entity live matches are polled on
BUDGET = budget.Budget("sportmonks", windows=(budget.Window(3600, 2000),), reserve=LIVE_RESERVE, urls=(BASE + "/",))

_client = httpx.Client(headers={"user-agent": USER_AGENT, "accept": "application/json"}, timeout=20)
_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


PROVIDER = Provider(
    name="Sportmonks",
    url="https://www.sportmonks.com",
    what="fixtures, live scores, line-ups, scorecards and player stats.",
    note="Collected by Sportmonks; not official league records.",
    budgets=(BUDGET,),
)


RateLimited = budget.RateLimited


@lru_cache(maxsize=1)
def token() -> str | None:
    value = os.environ.get("SPORTS_FOLLOW_SPORTMONKS_TOKEN", "").strip()
    if not value and TOKEN_FILE.is_file():
        value = TOKEN_FILE.read_text().strip()
    return value or None


def get_json(path: str, ttl: float, **params: Any) -> Any:
    """GET a Sportmonks document, cached for ttl seconds (0: fetched every time and never kept): a
    football path after /v3 ("/football/fixtures/1") or a full cricket URL (f"{CRICKET}/fixtures/1")."""
    key = f"{path}?{urlencode(sorted(params.items()))}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if not token():
        raise AdapterError("no Sportmonks token (~/.config/sports-follow/sportmonks-token)")
    bucket = _bucket(path)
    budget.take(BUDGET, bucket)
    try:
        res = _client.get(path if path.startswith("https://") else f"{BASE}{path}", params=params, headers={"authorization": token()})
    except httpx.HTTPError as exc:
        raise AdapterError(f"Sportmonks unreachable: {exc}") from exc
    if res.status_code == 429:
        budget.pause(BUDGET)
        raise RateLimited("Sportmonks hourly limit reached (429); paused for a minute")
    if res.status_code == 401:
        raise AdapterError("Sportmonks rejected the token (HTTP 401)")
    if res.status_code != 200:
        message = ""
        try:
            message = res.json().get("message") or ""
        except (ValueError, AttributeError):
            pass
        raise AdapterError(f"Sportmonks returned HTTP {res.status_code} for {path}: {message}"[:300])
    try:
        data = res.json()
    except ValueError as exc:  # a captive portal's or a CDN's error page, with a 200
        raise AdapterError(f"Sportmonks returned non-JSON for {path}") from exc
    if not isinstance(data, dict):
        raise AdapterError(f"Sportmonks returned {type(data).__name__} for {path}")
    limit = data.get("rate_limit") or {}
    if limit.get("requested_entity"):
        # Counted under the entity as the path names it, as take() counted it ("Fixture" either way).
        budget.observe(BUDGET, bucket, int(limit.get("remaining") or 0), int(limit.get("resets_in_seconds") or 3600))
    elif res.headers.get("x-ratelimit-remaining", "").isdigit():
        # Cricket doesn't say when its hour resets: at the latest an hour after this answer.
        budget.observe(BUDGET, bucket, int(res.headers["x-ratelimit-remaining"]), 3600)
    if ttl <= 0:
        return data
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


def _bucket(url: str) -> str:
    """The allowance a request counts against. Football counts by entity, which Sportmonks names in each
    answer: "/football/fixtures/multi/1,2" is a Fixture request. Cricket counts by endpoint, its ids left
    out: a match ("Cricket fixtures/{id}") and a list of fixtures ("Cricket fixtures") are counted apart."""
    if not url.startswith(CRICKET):
        parts = url.strip("/").split("/")
        if len(parts) > 1 and parts[1] == "livescores":
            return FIXTURES  # livescores are fixtures to Sportmonks, and count against that entity
        return parts[1].removesuffix("s").capitalize() if len(parts) > 1 else "Other"
    path = url.removeprefix(CRICKET).strip("/")
    return "Cricket " + "/".join("{id}" if part.isdigit() else part for part in path.split("/"))


def spare(bucket: str) -> bool:
    """Whether a background read of this entity or endpoint (FIXTURES, CRICKET_MATCH) leaves the live reserve intact."""
    return budget.spare(BUDGET, bucket)
