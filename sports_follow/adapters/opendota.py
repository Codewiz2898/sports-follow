"""OpenDota (opendota.com): live Dota 2 pro games and finished matches' player numbers, from Valve's data.

Not a source of its own: it adds to PandaScore's Dota 2 series (pandascore_esports), which have the
schedule and the series and map scores but nothing inside a map. No key needed: the free tier allows 60
requests a minute and 3,000 a day, counted by the source budget (budget.py, BUDGET) and never spent past
FREE_* less a margin. Finished matches never change, so each is kept in Redis once it's complete.

OpenDota is community-run and publishes no data licence (best effort, no guarantee), so a public build
leaves it off until its terms are checked: SPORTS_FOLLOW_OPENDOTA=0.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from .. import bus
from ..config import USER_AGENT
from . import budget
from .base import AdapterError

BASE = "https://api.opendota.com/api"
ENABLED = os.environ.get("SPORTS_FOLLOW_OPENDOTA", "1") != "0"
FREE_MINUTE, FREE_DAY = 60, 3000
MARGIN_MINUTE, MARGIN_DAY = 5, 200
BUDGET = budget.Budget("opendota", windows=(budget.Window(60, FREE_MINUTE, margin=MARGIN_MINUTE), budget.Window(86400, FREE_DAY, margin=MARGIN_DAY)))

_client = httpx.Client(headers={"user-agent": USER_AGENT, "accept": "application/json"}, timeout=20)
_memo: dict[str, tuple[float, Any]] = {}  # short-lived answers (the live list), per process


RateLimited = budget.RateLimited


def _get(path: str, kind: type = list) -> Any:
    """An OpenDota document: a list (live games, pro matches, heroes, pros), or a dict (a match)."""
    if not ENABLED:
        raise AdapterError("OpenDota is turned off (SPORTS_FOLLOW_OPENDOTA=0)")
    budget.take(BUDGET)
    try:
        res = _client.get(f"{BASE}{path}")
    except httpx.HTTPError as exc:
        raise AdapterError(f"OpenDota unreachable: {exc}") from exc
    if res.status_code == 429:
        budget.pause(BUDGET)
        raise RateLimited("OpenDota said too many requests (429)")
    if res.status_code != 200:
        raise AdapterError(f"OpenDota returned HTTP {res.status_code} for {path}"[:300])
    try:
        data = res.json()
    except ValueError as exc:  # a captive portal's or a CDN's error page, with a 200
        raise AdapterError(f"OpenDota returned non-JSON for {path}") from exc
    if not isinstance(data, kind):
        raise AdapterError(f"OpenDota returned {type(data).__name__} for {path}")
    return data


def _memoized(key: str, ttl: float, fetch: Any) -> Any:
    hit = _memo.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = fetch()
    _memo[key] = (time.time(), value)
    return value


def _kept(key: str, ttl: int, fetch: Any, complete: Any = bool) -> Any:
    """Kept in Redis for ttl seconds once complete(value) (a match whose players' numbers are in)."""
    try:
        raw = bus.sync_redis().get(key)
    except Exception:
        raw = None
    if raw:
        return json.loads(raw)
    value = fetch()
    if complete(value):
        try:
            bus.sync_redis().set(key, json.dumps(value), ex=ttl)
        except Exception:
            pass
    return value


def live() -> list[dict[str, Any]]:
    """Games being played now (the top 100 by viewers, league games among them), read at most every 30 s."""
    return _memoized("live", 30, lambda: _get("/live"))


def pro_matches() -> list[dict[str, Any]]:
    """The last 100 finished pro matches (both teams' names, start time, score), read at most once a minute."""
    return _memoized("proMatches", 60, lambda: _get("/proMatches"))


def match(match_id: int | str) -> dict[str, Any] | None:
    """A finished match's details (each player's hero and numbers), kept once complete."""
    def fetch() -> dict[str, Any] | None:
        try:
            return _get(f"/matches/{match_id}", dict)
        except RateLimited:
            raise
        except AdapterError:
            return None  # not in OpenDota yet

    return _kept(f"opendota:match:{match_id}", 15 * 86400, fetch, lambda d: bool(d and d.get("players") and all(p.get("kills") is not None for p in d["players"])))


def heroes() -> dict[int, str]:
    """Hero id -> name ("Faceless Void")."""
    rows = _kept("opendota:heroes", 7 * 86400, lambda: _get("/heroes"))
    return {int(h["id"]): h.get("localized_name") or h.get("name") for h in rows or []}


def pro_players() -> list[dict[str, Any]]:
    """Every pro OpenDota knows: Steam account, pro name, team name and tag."""
    return _kept("opendota:proPlayers", 86400, lambda: _get("/proPlayers"))
