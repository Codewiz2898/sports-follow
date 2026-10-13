"""PandaScore (pandascore.co): licensed esports data, one client for the adapter and the registry import.

Runs only with a token: SPORTS_FOLLOW_PANDASCORE_TOKEN, or the file ~/.config/sports-follow/pandascore-token
(kept outside the repo). The token travels in the Authorization header, never in a URL, so it can't end
up in logs. The free plan allows 1,000 requests an hour across every endpoint; the remaining count comes
back in each response's headers, and a 429 pauses reads for a minute, like Sportmonks.

Free plan (pandascore.co/pricing): schedules, results, teams and players for every title; no player
stats (those start at €400 a game a month) and no live in-game data. Birthdays are on paid plans only,
so a player is known by their PandaScore id, never by name and birth date. Stats plans are for
non-betting use only; whether a commercial app may use the free plan is still to be confirmed in writing
(BACKLOG.md, item 8).
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

BASE = "https://api.pandascore.co"
TOKEN_FILE = Path(os.environ.get("SPORTS_FOLLOW_PANDASCORE_TOKEN_FILE", Path.home() / ".config" / "sports-follow" / "pandascore-token"))
PAGE = 100  # rows a page, PandaScore's most
MAX_PAGES = 5
HOURLY = 1000
# Requests kept back for live series: background reads (the registry import, history.py) stop when the
# hour's remaining allowance falls this low, and start again when the hour resets.
LIVE_RESERVE = int(os.environ.get("SPORTS_FOLLOW_PANDASCORE_RESERVE", 300))

_client = httpx.Client(headers={"user-agent": espn.USER_AGENT, "accept": "application/json"}, timeout=20)
_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()
_state = {"paused_until": 0.0}
# (requests left this hour, when the hour resets), from the last response's headers, else counted here.
remaining = {"left": HOURLY, "resets_at": 0.0}


class RateLimited(AdapterError):
    """PandaScore said the hour's requests are used up."""


class NotFound(AdapterError):
    """No such player or match."""


@lru_cache(maxsize=1)
def token() -> str | None:
    value = os.environ.get("SPORTS_FOLLOW_PANDASCORE_TOKEN", "").strip()
    if not value and TOKEN_FILE.is_file():
        value = TOKEN_FILE.read_text().strip()
    return value or None


def _count(res: httpx.Response) -> None:
    now = time.time()
    if now >= remaining["resets_at"]:
        remaining.update(left=HOURLY, resets_at=now + 3600)
    header = res.headers.get("x-rate-limit-remaining") or res.headers.get("x-ratelimit-remaining") or ""
    remaining["left"] = int(header) if header.isdigit() else max(0, remaining["left"] - 1)


def get_json(path: str, ttl: float, **params: Any) -> Any:
    """GET a PandaScore document ("/players/123", "/matches"), cached for ttl seconds (0: fetched every
    time and never kept). Filter, range and page parameters go as their bracketed names:
    get_json("/matches", 60, **{"filter[id]": "1,2"})."""
    key = f"{path}?{urlencode(sorted(params.items()))}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if not token():
        raise AdapterError("no PandaScore token (~/.config/sports-follow/pandascore-token)")
    if time.time() < _state["paused_until"]:
        raise RateLimited("PandaScore hourly limit reached; paused for a minute")
    try:
        res = _client.get(f"{BASE}{path}", params=params, headers={"authorization": f"Bearer {token()}"})
    except httpx.HTTPError as exc:
        raise AdapterError(f"PandaScore unreachable: {exc}") from exc
    _count(res)
    if res.status_code == 429:
        _state["paused_until"] = time.time() + 60
        raise RateLimited("PandaScore hourly limit reached (429); paused for a minute")
    if res.status_code in (401, 403):
        raise AdapterError(f"PandaScore rejected the token (HTTP {res.status_code})")
    if res.status_code == 404:
        raise NotFound(f"PandaScore has nothing at {path}")
    if res.status_code != 200:
        message = ""
        try:
            message = str(res.json().get("error") or res.json().get("message") or "")
        except (ValueError, AttributeError):
            pass
        raise AdapterError(f"PandaScore returned HTTP {res.status_code} for {path}: {message}"[:300])
    try:
        data = res.json()
    except ValueError as exc:  # a captive portal's or a CDN's error page, with a 200
        raise AdapterError(f"PandaScore returned non-JSON for {path}") from exc
    if not isinstance(data, (dict, list)):
        raise AdapterError(f"PandaScore returned {type(data).__name__} for {path}")
    if ttl <= 0:
        return data
    with _lock:
        _cache[key] = (time.time(), data)
        if len(_cache) > 2000:
            for old, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:500]:
                _cache.pop(old, None)
    return data


def get_all(path: str, ttl: float, pages: int = MAX_PAGES, **params: Any) -> list[dict[str, Any]]:
    """Every row of a list, PAGE a page, up to `pages` pages (a short page is the last)."""
    rows: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        got = get_json(path, ttl, **params, **{"page[size]": PAGE, "page[number]": page})
        got = got if isinstance(got, list) else []
        rows += got
        if len(got) < PAGE:
            break
    return rows


def spare() -> bool:
    """Whether a background read leaves the hour's live reserve intact."""
    return remaining["left"] > LIVE_RESERVE or time.time() >= remaining["resets_at"]
