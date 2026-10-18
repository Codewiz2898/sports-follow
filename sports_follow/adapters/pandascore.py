"""PandaScore (pandascore.co): licensed esports data, one client for the adapter and the registry import.

Runs only with a token: SPORTS_FOLLOW_PANDASCORE_TOKEN, or the file ~/.config/sports-follow/pandascore-token
(kept outside the repo). The token travels in the Authorization header, never in a URL, so it can't end
up in logs. The free plan allows 1,000 requests an hour across every endpoint, kept to by the source
budget (budget.py, BUDGET); the remaining count comes back in each response's headers and corrects the
count, and a 429 pauses every lane for a minute.

Free plan (pandascore.co/pricing): schedules, results, teams and players for every title; no player
stats (those start at €400 a game a month) and no live in-game data. Birthdays are on paid plans only,
so a player is known by their PandaScore id, never by name and birth date. Stats plans are for
non-betting use only; whether a commercial app may use the free plan is still to be confirmed in writing
(BACKLOG.md, item 8).
"""

from __future__ import annotations

import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from ..config import USER_AGENT
from . import budget, cache
from .base import AdapterError, Provider

BASE = "https://api.pandascore.co"
TOKEN_FILE = Path(os.environ.get("SPORTS_FOLLOW_PANDASCORE_TOKEN_FILE", Path.home() / ".config" / "sports-follow" / "pandascore-token"))
PAGE = 100  # rows a page, PandaScore's most
MAX_PAGES = 5
HOURLY = 1000
# Requests kept back for live series: background reads (refreshes, the registry import, history.py) stop
# when the hour's remaining allowance falls this low, and start again when the hour resets.
LIVE_RESERVE = int(os.environ.get("SPORTS_FOLLOW_PANDASCORE_RESERVE", 300))
BUDGET = budget.Budget("pandascore", windows=(budget.Window(3600, HOURLY),), reserve=LIVE_RESERVE, urls=(BASE + "/",))

_client = httpx.Client(headers={"user-agent": USER_AGENT, "accept": "application/json"}, timeout=20)
_cache = cache.Cache(BUDGET.name)


PROVIDER = Provider(
    name="PandaScore",
    url="https://pandascore.co",
    what="series schedules and results, map by map, teams, and the pro players search finds.",
    note="Collected by PandaScore; not official tournament records. Results are the team's: player stats aren't included.",
    budgets=(BUDGET,),
)


RateLimited = budget.RateLimited


class NotFound(AdapterError):
    """No such player or match."""


@lru_cache(maxsize=1)
def token() -> str | None:
    value = os.environ.get("SPORTS_FOLLOW_PANDASCORE_TOKEN", "").strip()
    if not value and TOKEN_FILE.is_file():
        value = TOKEN_FILE.read_text().strip()
    return value or None


def _observe(res: httpx.Response) -> None:
    """What PandaScore says is left of the hour, which it resets on the hour."""
    header = res.headers.get("x-rate-limit-remaining") or res.headers.get("x-ratelimit-remaining") or ""
    if header.isdigit():
        budget.observe(BUDGET, "", int(header), 3600 - time.time() % 3600)


def get_json(path: str, ttl: float, **params: Any) -> Any:
    """GET a PandaScore document ("/players/123", "/matches"), cached for ttl seconds (0: fetched every
    time and never kept), in Redis too (cache.py). Filter, range and page parameters go as their bracketed
    names: get_json("/matches", 60, **{"filter[id]": "1,2"})."""
    return _cache.read(f"{path}?{urlencode(sorted(params.items()))}", ttl, lambda: _fetch(path, params))


def _fetch(path: str, params: dict[str, Any]) -> Any:
    if not token():
        raise AdapterError("no PandaScore token (~/.config/sports-follow/pandascore-token)")
    budget.take(BUDGET)
    try:
        res = _client.get(f"{BASE}{path}", params=params, headers={"authorization": f"Bearer {token()}"})
    except httpx.HTTPError as exc:
        raise AdapterError(f"PandaScore unreachable: {exc}") from exc
    _observe(res)
    if res.status_code == 429:
        budget.pause(BUDGET)
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
    return budget.spare(BUDGET)
