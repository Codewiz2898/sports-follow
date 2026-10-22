"""OpenDota (opendota.com): live Dota 2 pro games and finished matches' player numbers, from Valve's data.

Not a source of its own: it adds to PandaScore's Dota 2 series (pandascore_esports), which have the
schedule and the series and map scores but nothing inside a map. No key needed: the free tier allows 60
requests a minute and 3,000 a day, counted by the source budget (budget.py, BUDGET) and never spent past
FREE_* less a margin. What's kept long (a finished match, the heroes, the items, the pros) is in the source
cache (cache.py), shared by the API and the worker; the live list and the last pro matches only a moment,
per process.

A finished match comes in two steps: from Valve's own record first (each player's hero, kills, items), and
minutes later, once OpenDota has parsed its replay, with the rest (the gold lead by minute, the draft's
order, Roshan and every tower). Only a parsed match is kept for good; one that isn't yet is asked again
every PARSE_WAIT.

OpenDota is community-run and publishes no data licence (best effort, no guarantee). The public build runs
it all the same, credited on the Credits page (BHA-208, 20 Oct 2026); SPORTS_FOLLOW_OPENDOTA=0 turns it off.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from ..config import USER_AGENT
from . import budget, cache
from .base import AdapterError

BASE = "https://api.opendota.com/api"
ENABLED = os.environ.get("SPORTS_FOLLOW_OPENDOTA", "1") != "0"
FREE_MINUTE, FREE_DAY = 60, 3000
MARGIN_MINUTE, MARGIN_DAY = 5, 200
BUDGET = budget.Budget("opendota", windows=(budget.Window(60, FREE_MINUTE, margin=MARGIN_MINUTE), budget.Window(86400, FREE_DAY, margin=MARGIN_DAY)), urls=(BASE + "/",))
MATCH_KEEP = 15 * 86400  # a parsed match never changes
PARSE_WAIT = 300  # a match not parsed yet is asked again this often: a pro game's replay is parsed minutes after it ends
MISS_WAIT = 60  # and one OpenDota doesn't have at all, this often (per process)

_client = httpx.Client(headers={"user-agent": USER_AGENT, "accept": "application/json"}, timeout=20)
_cache = cache.Cache(BUDGET.name, size=200)  # a match is a big document: this process keeps a few (cache.ROOM), Redis the rest
_memo: dict[str, tuple[float, Any]] = {}  # short-lived answers (the live list), and matches not there yet, per process


RateLimited = budget.RateLimited


def _get(path: str, kind: type = list) -> Any:
    """An OpenDota document: a list (live games, pro matches, heroes, pros), or a dict (a match, the items)."""
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


def live() -> list[dict[str, Any]]:
    """Games being played now (the top 100 by viewers, league games among them), read at most every 30 s."""
    return _memoized("live", 30, lambda: _get("/live"))


def pro_matches() -> list[dict[str, Any]]:
    """The last 100 finished pro matches (both teams' names, start time, score), read at most once a minute."""
    return _memoized("proMatches", 60, lambda: _get("/proMatches"))


def parsed(details: dict[str, Any] | None) -> bool:
    """Whether OpenDota has parsed a match's replay: it then has the gold by minute, the draft, the objectives."""
    return bool(details and details.get("version") is not None and details.get("players"))


def match(match_id: int | str) -> dict[str, Any] | None:
    """A finished match's details (each player's hero and numbers; once parsed, the rest), or None while
    OpenDota hasn't got it. A parsed one is kept MATCH_KEEP; one that isn't is asked again every
    PARSE_WAIT, and kept as it is when asking fails."""
    path = f"/matches/{match_id}"
    kept = _cache.peek(path, MATCH_KEEP)
    if parsed(kept):
        return kept
    failed = _memo.get(path)
    if failed and time.time() - failed[0] < MISS_WAIT:
        return kept
    try:
        return _cache.read(path, PARSE_WAIT if kept else MATCH_KEEP, lambda: _get(path, dict), keep=MATCH_KEEP)
    except RateLimited:
        raise
    except AdapterError:  # not in OpenDota yet, or it's down: nothing new kept, asked again in a minute
        _memo[path] = (time.time(), None)
        return kept


def _listed(path: str, kind: type = list) -> Any:
    """A list OpenDota must have something in (the heroes, the items, the pros): an empty one isn't kept."""
    data = _get(path, kind)
    if not data:
        raise AdapterError(f"OpenDota returned nothing for {path}")
    return data


def heroes() -> dict[int, str]:
    """Hero id -> name ("Faceless Void")."""
    rows = _cache.read("/heroes", 7 * 86400, lambda: _listed("/heroes"))
    return {int(h["id"]): h.get("localized_name") or h.get("name") for h in rows or []}


def items() -> dict[int, str]:
    """Item id -> name ("Black King Bar"), from OpenDota's constants (keyed by Valve's names, "black_king_bar")."""
    rows = _cache.read("/constants/items", 7 * 86400, lambda: _listed("/constants/items", dict))
    return {int(i["id"]): i.get("dname") or key.replace("_", " ").title() for key, i in (rows or {}).items() if isinstance(i, dict) and i.get("id") is not None}


def pro_players() -> list[dict[str, Any]]:
    """Every pro OpenDota knows: Steam account, pro name, team name and tag."""
    return _cache.read("/proPlayers", 86400, lambda: _listed("/proPlayers"))
