"""The shared ESPN client: public JSON endpoints behind espn.com and espncricinfo.com.

ESPN's API refuses browser-like user agents (403) and serves plain clients, so requests identify
this app honestly. Responses are cached in-process: live summaries for a few seconds, calendars
for minutes, finished matches for good. Unofficial endpoints can change or disappear without
notice; that is what binding health and the agent fallback are for (design section 5).
"""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from typing import Any
from urllib.parse import quote

import httpx

from .base import AdapterError

USER_AGENT = "sports-follow/0.1 (+https://github.com/Codewiz2898/sports-follow)"
SITE = "https://site.api.espn.com/apis/site/v2/sports"
WEB = "https://site.web.api.espn.com/apis"

_client = httpx.Client(headers={"user-agent": USER_AGENT, "accept": "application/json"}, timeout=15, follow_redirects=True)
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def get_json(url: str, ttl: float) -> Any:
    """GET a JSON document, served from cache while younger than ttl seconds."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(url)
    if hit and now - hit[0] < ttl:
        return hit[1]
    res = None
    for attempt in range(2):
        # ESPN's edge answers the odd request with a 502; the same request a moment later works.
        try:
            res = _client.get(url)
        except httpx.HTTPError as exc:
            if attempt:
                raise AdapterError(f"ESPN unreachable: {exc}") from exc
        else:
            if res.status_code < 500:
                break
        time.sleep(0.5)
    if res is None or res.status_code != 200:
        raise AdapterError(f"ESPN returned HTTP {res.status_code if res is not None else '?'} for {url}")
    try:
        data = res.json()
    except ValueError as exc:
        raise AdapterError(f"ESPN returned non-JSON for {url}") from exc
    with _cache_lock:
        _cache[url] = (now, data)
        if len(_cache) > 2000:  # keep memory bounded; oldest entries go first
            for key, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:500]:
                _cache.pop(key, None)
    return data


def fold(text: str) -> str:
    """Case- and accent-insensitive form for comparing names ("Jokić" == "jokic")."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def team_key(name: str) -> str:
    """A team name reduced for exact comparison: "Australia 'A'" == "australia a"; never a substring match."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", fold(name)).split())


def names_match(wanted: str, found: str) -> bool:
    """Accept a search hit only if the surnames agree, so a bare "Ronaldo" can't bind the wrong athlete."""
    w, f = fold(wanted).split(), fold(found).split()
    return bool(w and f) and (w[-1] == f[-1] or w[-1] in f)


def search_athletes(name: str, sport_uid: str) -> list[dict[str, Any]]:
    """ESPN search hits of type player in one sport (uid prefix "s:600~" soccer, "s:200~" cricket)."""
    data = get_json(f"{WEB}/search/v2?query={quote(name)}&limit=10", ttl=3600)
    hits = []
    for group in data.get("results", []):
        if group.get("type") != "player":
            continue
        for item in group.get("contents", []):
            uid = item.get("uid") or ""
            if uid.startswith(sport_uid) and "~a:" in uid:
                league = next((part[2:] for part in uid.split("~") if part.startswith("l:")), None)  # "s:40~l:46~a:1966" -> "46"
                hits.append({"athlete_id": uid.split("~a:")[1], "league_id": league, "name": item.get("displayName", ""), "subtitle": item.get("subtitle"), "url": (item.get("link") or {}).get("web")})
    return hits
