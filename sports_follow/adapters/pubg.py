"""PUBG's official API (api.pubg.com, by KRAFTON): PUBG: Battlegrounds esports, one client for the
adapter and the registry import.

Runs only with a key: SPORTS_FOLLOW_PUBG_TOKEN, or the file ~/.config/sports-follow/pubg-token (kept outside
the repo), sent as a Bearer token. PC and console only: there are no BGMI or other mobile events.

The tournament endpoints allow 10 requests a minute; the minute is counted in Redis, shared by the API
and the worker, and reads stop short of it. Matches don't count against the limit and never change, so
each is downloaded once and kept in Redis as a short summary until PUBG drops it (14 days).

Terms (developer.pubg.com/tos): free; commercial apps may run ads or subscriptions, but not paywall
the data; storing the data for the app is allowed, reselling it is not. Pages showing the PUBG name
carry the trademark notice on the Credits page.
"""

from __future__ import annotations

import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from .. import bus
from ..config import USER_AGENT
from .base import AdapterError

BASE = "https://api.pubg.com"
TOKEN_FILE = Path(os.environ.get("SPORTS_FOLLOW_PUBG_TOKEN_FILE", Path.home() / ".config" / "sports-follow" / "pubg-token"))
PER_MINUTE = 10  # tournament endpoints; match downloads are free
MARGIN = 1
KEEP = 15 * 24 * 3600  # PUBG keeps matches 14 days

_client = httpx.Client(headers={"user-agent": USER_AGENT, "accept": "application/vnd.api+json"}, timeout=30)


class RateLimited(AdapterError):
    """This minute's tournament requests are spent (or PUBG said so)."""


class NotFound(AdapterError):
    """No such tournament or match (matches go after 14 days)."""


@lru_cache(maxsize=1)
def token() -> str | None:
    value = os.environ.get("SPORTS_FOLLOW_PUBG_TOKEN", "").strip()
    if not value and TOKEN_FILE.is_file():
        value = TOKEN_FILE.read_text().strip()
    return value or None


def _take() -> None:
    """Count one tournament request against the minute, or refuse it."""
    try:
        r = bus.sync_redis()
        key = f"pubg:requests:{int(time.time() // 60)}"
        used = r.incr(key)
        r.expire(key, 120)
    except Exception as exc:
        raise RateLimited(f"can't count PUBG requests (Redis): {exc}") from exc
    if used > PER_MINUTE - MARGIN:
        raise RateLimited(f"PUBG's {PER_MINUTE} tournament requests this minute are spent")


def _get(path: str, limited: bool) -> dict[str, Any]:
    if not token():
        raise AdapterError("no PUBG key (~/.config/sports-follow/pubg-token)")
    if limited:
        _take()
    try:
        res = _client.get(f"{BASE}{path}", headers={"authorization": f"Bearer {token()}"})
    except httpx.HTTPError as exc:
        raise AdapterError(f"PUBG unreachable: {exc}") from exc
    if res.status_code == 429:
        raise RateLimited("PUBG said too many requests (429)")
    if res.status_code in (401, 403):
        raise AdapterError(f"PUBG rejected the key (HTTP {res.status_code})")
    if res.status_code == 404:
        raise NotFound(f"PUBG has nothing at {path}")
    if res.status_code != 200:
        raise AdapterError(f"PUBG returned HTTP {res.status_code} for {path}"[:300])
    try:
        data = res.json()
    except ValueError as exc:  # a captive portal's or a CDN's error page, with a 200
        raise AdapterError(f"PUBG returned non-JSON for {path}") from exc
    if not isinstance(data, dict):
        raise AdapterError(f"PUBG returned {type(data).__name__} for {path}")
    return data


def _cached(key: str) -> Any:
    try:
        raw = bus.sync_redis().get(key)
    except Exception:
        return None
    return json.loads(raw) if raw else None


def _keep(key: str, value: Any, ttl: int) -> None:
    try:
        bus.sync_redis().set(key, json.dumps(value), ex=ttl)
    except Exception:
        pass


def _fresh(key: str, max_age: float, fetch: Any) -> Any:
    """A list kept in Redis with when it was read: used while it's at most max_age seconds old, else read
    again. A read that fails (the minute's requests spent) falls back to what's kept, however old."""
    hit = _cached(key)
    if hit and time.time() - hit["at"] <= max_age:
        return hit["rows"]
    try:
        rows = fetch()
    except RateLimited:
        if hit:
            return hit["rows"]
        raise
    _keep(key, {"at": time.time(), "rows": rows}, 2 * 24 * 3600)
    return rows


def tournaments(max_age: float = 600) -> list[dict[str, Any]]:
    """Every tournament: [{"id", "createdAt"}], newest first."""
    def fetch() -> list[dict[str, Any]]:
        rows = [{"id": t["id"], "createdAt": (t.get("attributes") or {}).get("createdAt")} for t in _get("/tournaments", limited=True).get("data") or []]
        return sorted(rows, key=lambda t: t["createdAt"] or "", reverse=True)

    return _fresh("pubg:tournaments", max_age, fetch)


def tournament_matches(tournament: str, max_age: float) -> list[dict[str, Any]]:
    """A tournament's matches: [{"id", "createdAt"}], oldest first."""
    def fetch() -> list[dict[str, Any]]:
        doc = _get(f"/tournaments/{tournament}", limited=True)
        rows = [{"id": m["id"], "createdAt": (m.get("attributes") or {}).get("createdAt")} for m in doc.get("included") or [] if m.get("type") == "match"]
        return sorted(rows, key=lambda m: m["createdAt"] or "")

    return _fresh(f"pubg:t:{tournament}", max_age, fetch)


def match(match_id: str, summarize: Any) -> dict[str, Any] | None:
    """One match as summarize(document) makes it, downloaded once and kept; None once PUBG has dropped it."""
    key = f"pubg:m:{match_id}"
    hit = _cached(key)
    if hit is not None:
        return hit or None
    try:
        doc = _get(f"/shards/tournament/matches/{match_id}", limited=False)
    except NotFound:
        _keep(key, {}, KEEP)  # gone for good: don't ask again
        return None
    summary = summarize(doc)
    _keep(key, summary, KEEP)
    return summary
