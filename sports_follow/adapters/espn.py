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


# Letters Unicode doesn't decompose into a base letter plus an accent. Ð (U+00D0, the Icelandic
# capital eth) is often typed for Đ.
_LETTERS = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ß": "ss", "đ": "d", "Đ": "D", "Ð": "D", "ł": "l", "Ł": "L", "þ": "th", "Þ": "Th", "ð": "d", "ı": "i"})
# Letters with a second plain spelling: Đ is "dj" in English (Wikidata writes Novak Djokovic), and ü,
# ö, ä are "ue", "oe", "ae" in German (Thomas Müller is Thomas Mueller).
NATIVE = str.maketrans({"đ": "dj", "Đ": "Dj", "Ð": "Dj", "ü": "ue", "Ü": "Ue", "ö": "oe", "Ö": "Oe", "ä": "ae", "Ä": "Ae"})


def fold(text: str) -> str:
    """Case- and accent-insensitive form for comparing names ("Jokić" == "jokic", "Øen" == "oen")."""
    return unicodedata.normalize("NFKD", text.translate(_LETTERS)).encode("ascii", "ignore").decode().lower().strip()


def words(text: str) -> list[str]:
    """A name as plain lower-case ASCII words, the one form names are compared in: "A'ja Wilson" and
    "A’ja Wilson" -> ["aja", "wilson"], "Jokić" -> ["jokic"], "M.S. Dhoni" -> ["m", "s", "dhoni"]."""
    return re.sub(r"[^a-z0-9]+", " ", fold(re.sub(r"['’`]", "", text or ""))).split()


def spellings(text: str) -> list[list[str]]:
    """A name's words in each of its plain spellings: words(text), then, if it has Đ, ü, ö or ä, with
    those spelled out ("Thomas Müller" -> ["thomas", "muller"] and ["thomas", "mueller"])."""
    plain = words(text)
    native = words(unicodedata.normalize("NFC", text or "").translate(NATIVE))
    return [plain] if native == plain else [plain, native]


def team_key(name: str) -> str:
    """A team name reduced for exact comparison: "Australia 'A'" == "australia a"; never a substring match."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", fold(name)).split())


def names_match(wanted: str, found: str) -> bool:
    """Accept a search hit only if the surnames agree and every word of the shorter name is in the
    longer one (an initial stands for its word): "Ronaldo" is Cristiano Ronaldo and "Jasprit Bumrah" is
    "Jasprit Jasbirsingh Bumrah", but "Ahmed Salah" isn't Mohamed Salah."""
    w, f = words(wanted), words(found)
    if not (w and f) or not (w[-1] == f[-1] or w[-1] in f):
        return False
    short, long = (w, f) if len(w) <= len(f) else (f, w)
    return all(any(a == b or (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b)) for b in long) for a in short)


def birth_date(athlete: dict[str, Any]) -> str | None:
    """An athlete record's birth date as ISO. ESPN writes it day/month/year ("5/11/1988" is 5 November)."""
    parts = (athlete.get("displayDOB") or "").split("/")
    if len(parts) != 3 or not all(x.isdigit() for x in parts):
        return None
    day, month, year = (int(x) for x in parts)
    return f"{year:04d}-{month:02d}-{day:02d}" if 1 <= month <= 12 and 1 <= day <= 31 else None


def profile_link(athlete: dict[str, Any]) -> str | None:
    """The athlete's page on espn.com / espncricinfo.com, from an athlete record's links."""
    return next((link.get("href") for link in athlete.get("links") or [] if (link.get("href") or "").startswith("http")), None)


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
