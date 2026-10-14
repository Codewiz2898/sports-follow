"""Liquipedia (liquipedia.net): esports results the community records, read through its database API
(LiquipediaDB, API v3). One client for the adapter and the registry import.

Runs only with a key: SPORTS_FOLLOW_LIQUIPEDIA_TOKEN, or the file ~/.config/sports-follow/liquipedia-token
(kept outside the repo), sent as "Authorization: Apikey …". Keys come from liquipedia.net/api.

Terms (liquipedia.net/api-terms-of-use): the free key is for non-commercial projects, at most 60
requests an hour; a public build needs a commercial plan. Every request names this project in its
user agent, results are cached as long as they can be, and the data is CC BY-SA 3.0, so the source is
credited wherever it shows. Going over the limit risks an IP ban, so the hour's budget is counted in
Redis, shared by the API and the worker, and reads stop short of it.
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

BASE = "https://api.liquipedia.net/api/v3"
TOKEN_FILE = Path(os.environ.get("SPORTS_FOLLOW_LIQUIPEDIA_TOKEN_FILE", Path.home() / ".config" / "sports-follow" / "liquipedia-token"))
HOURLY = int(os.environ.get("SPORTS_FOLLOW_LIQUIPEDIA_HOURLY", 60))  # the key's allowance; a paid plan raises it
MARGIN = 4  # requests never spent, in case another process counted late
# Requests kept for live lobbies and fixtures: background reads (the registry import, history.py) stop
# when the hour's spending reaches HOURLY - RESERVE.
RESERVE = int(os.environ.get("SPORTS_FOLLOW_LIQUIPEDIA_RESERVE", 30))
PAGE = 1000  # rows a request, the API's most
FANS = 10  # requests an hour fans' previews and follows may take: one follow is one, but a loop would spend the lobbies' share
BUDGET = budget.Budget("liquipedia", windows=(budget.Window(3600, HOURLY, margin=MARGIN),), reserve=RESERVE, fan=FANS)

_client = httpx.Client(headers={"user-agent": f"{USER_AGENT} esports results", "accept": "application/json", "accept-encoding": "gzip"}, timeout=30)
_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


PROVIDER = Provider(
    name="Liquipedia",
    url="https://liquipedia.net",
    what="battle royale standings, each game's placings and finishes, and the pro players search finds.",
    licence="CC BY-SA 3.0",
    licence_url="https://creativecommons.org/licenses/by-sa/3.0/",
    note="Recorded by Liquipedia's editors during and after play; not official tournament records.",
)


RateLimited = budget.RateLimited


@lru_cache(maxsize=1)
def token() -> str | None:
    value = os.environ.get("SPORTS_FOLLOW_LIQUIPEDIA_TOKEN", "").strip()
    if not value and TOKEN_FILE.is_file():
        value = TOKEN_FILE.read_text().strip()
    return value or None


def spare() -> bool:
    """Whether a background read leaves the hour's reserve for live lobbies and fixtures."""
    return budget.spare(BUDGET)


def get(datatype: str, ttl: float, **params: Any) -> list[dict[str, Any]]:
    """The rows of one LiquipediaDB query ("match", "player"…), cached for ttl seconds (0: fetched every
    time and never kept): get("match", 1800, wiki="pubgmobile", conditions="[[opponent::X]]", limit=100)."""
    key = f"{datatype}?{urlencode(sorted(params.items()))}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if not token():
        raise AdapterError("no Liquipedia key (~/.config/sports-follow/liquipedia-token)")
    budget.take(BUDGET)
    try:
        res = _client.get(f"{BASE}/{datatype}", params=params, headers={"authorization": f"Apikey {token()}"})
    except httpx.HTTPError as exc:
        raise AdapterError(f"Liquipedia unreachable: {exc}") from exc
    if res.status_code == 429:
        budget.pause(BUDGET)
        raise RateLimited("Liquipedia said too many requests (429)")
    if res.status_code in (401, 403):
        raise AdapterError(f"Liquipedia rejected the key (HTTP {res.status_code})")
    if res.status_code != 200:
        raise AdapterError(f"Liquipedia returned HTTP {res.status_code} for {datatype}"[:300])
    try:
        data = res.json()
    except ValueError as exc:  # a captive portal's or a CDN's error page, with a 200
        raise AdapterError(f"Liquipedia returned non-JSON for {datatype}") from exc
    if not isinstance(data, dict):
        raise AdapterError(f"Liquipedia returned {type(data).__name__} for {datatype}")
    if data.get("error"):
        raise AdapterError(f"Liquipedia: {data['error']}"[:300])
    rows = data.get("result") or []
    if not isinstance(rows, list):
        raise AdapterError(f"Liquipedia returned no rows for {datatype}")
    if ttl > 0:
        with _lock:
            _cache[key] = (time.time(), rows)
            if len(_cache) > 1000:
                for old, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:200]:
                    _cache.pop(old, None)
    return rows


def any_of(field: str, values: list[str]) -> str:
    """A condition matching any of the values: "[[match2id::a]] OR [[match2id::b]]"."""
    return " OR ".join(f"[[{field}::{v}]]" for v in values)
