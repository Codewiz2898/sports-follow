"""The shared ESPN client: public JSON endpoints behind espn.com and espncricinfo.com.

ESPN's API refuses browser-like user agents (403) and serves plain clients, so requests identify
this app honestly. Responses are cached in-process: live summaries for a few seconds, calendars
for minutes, finished matches for good. Unofficial endpoints can change or disappear without
notice; that is what binding health and the agent fallback are for (design section 5).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from .. import naming, sports
from ..config import USER_AGENT
from . import budget
from .base import AdapterError, LiveSearch, PlayerRef, Provider, SearchResult

if TYPE_CHECKING:
    from .base import Adapter

log = logging.getLogger("sports_follow.espn")

SITE = "https://site.api.espn.com/apis/site/v2/sports"
WEB = "https://site.web.api.espn.com/apis"

_client = httpx.Client(headers={"user-agent": USER_AGENT, "accept": "application/json"}, timeout=15, follow_redirects=True)
BUDGET = budget.Budget("espn")  # no published limit: counted, by lane, never refused
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
        budget.take(BUDGET)
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


def athletes(adapter: Adapter, name: str, league: str | None = None) -> list[PlayerRef]:
    """The athletes ESPN's search finds by this name in an ESPN adapter's sport, read through the adapter:
    an ESPN adapter's search(), the registry's lookup of an athlete Wikidata has no ESPN id for."""
    refs = []
    for hit in search_athletes(name, adapter.espn_uid)[:6]:
        if not naming.names_match(name, hit["name"]):
            continue
        try:
            ref = adapter.player(hit["athlete_id"], league)
        except AdapterError:
            continue
        if ref:
            refs.append(ref)
    return refs


# ---------------------------------------------------------------- what the four ESPN adapters share

COLLEGE_LEAGUES = {"23", "41", "54"}  # NCAAF, NCAAM, NCAAW
# ESPN's names for sports no source here covers, as fans say them.
OTHER_SPORTS = {"football": "American football", "racing": "Motor racing", "mma": "MMA", "hockey": "Ice hockey", "baseball": "Baseball", "golf": "Golf", "soccer": "Football", "boxing": "Boxing", "rugby": "Rugby"}


def parse_search(data: dict[str, Any], query: str, adapters: list[Adapter]) -> list[SearchResult]:
    """ESPN search hits as results. A hit in the sport of one of the ESPN adapters given can be followed
    live; one in a league that adapter doesn't read (EuroLeague, ITF), or in another sport, is AI only.
    College athletes are left out."""
    by_uid = {a.espn_uid: a for a in adapters}
    out = []
    for group in data.get("results", []):
        if group.get("type") != "player":
            continue
        for item in group.get("contents", []):
            uid = item.get("uid") or ""
            if "~a:" not in uid:
                continue
            parts = dict(p.split(":", 1) for p in uid.split("~") if ":" in p)
            league_id = parts.get("l")
            description = item.get("description") or ""
            if league_id in COLLEGE_LEAGUES or description.upper().startswith("NCAA"):
                continue
            name = (item.get("displayName") or "").strip()
            subtitle = (item.get("subtitle") or "").strip()
            adapter = by_uid.get(f"s:{parts.get('s')}~")
            system, league = (adapter.system, None) if adapter else (None, None)
            leagues = getattr(adapter, "espn_leagues", None)
            if leagues is not None:
                league = leagues.get(league_id or "")
                if league is None:
                    system = None  # a league the adapter doesn't read (EuroLeague, ITF): the agent covers them
            if adapter:
                sport = sports.label(adapter.sport)
                detail = adapter.search_detail(subtitle, description, league)
            else:
                sport = OTHER_SPORTS.get(item.get("sport") or "", (item.get("sport") or description or "Other").title())
                detail = search_detail(subtitle, description, sport)
            out.append(SearchResult(
                key=f"{system}:{parts['a']}" if system else f"espn:{uid}",
                name=name,
                sport=sport,
                detail=detail,
                system=system,
                athlete_id=parts["a"] if system else None,
                league=league,
                live_scores=system is not None,
                exact=naming.same(query, name),
                popularity=5 if item.get("image") else 0,
            ))
    return out


def search_detail(subtitle: str, description: str, sport: str) -> str:
    """A search hit's line: its team and its league ("Indiana Fever · WNBA"), without repeating the sport."""
    return " · ".join(x for x in (subtitle, description if description.lower() != sport.lower() else "") if x)


def search(query: str, sport: str | None, adapters: list[Adapter]) -> list[SearchResult]:
    # ESPN's search has no sport filter; one sport's athletes are a few of every fifty hits.
    data = get_json(f"{WEB}/search/v2?query={quote(query)}&limit={50 if sport else 20}", ttl=3600)
    return parse_search(data, query, adapters)


def recognise(query: str, adapters: list[Adapter]) -> tuple[Adapter, PlayerRef] | bool | None:
    """The one ESPN athlete with exactly this name, if one of these adapters has them.

    ESPN knows athletes in every sport, so it can rule out namesakes: two with the typed name anywhere
    (any sport, covered or not) is a question for the agent, since "John Smith" is dozens of people and
    the first one found is rarely the one meant (False). So is an athlete in a sport we don't cover (Max
    Verstappen). No athlete by that name leaves it to the next provider (None)."""
    wanted = sorted(naming.words(query))
    try:
        data = get_json(f"{WEB}/search/v2?query={quote(query)}&limit=10", ttl=3600)
    except AdapterError:
        return False
    namesakes = [item for group in data.get("results", []) if group.get("type") == "player" for item in group.get("contents", []) if sorted(naming.words(item.get("displayName", ""))) == wanted]
    if len(namesakes) > 1:
        # ESPN keeps a separate college record ("A'Ja Wilson, South Carolina, NCAAW" beside the Aces
        # star). One professional among college namesakes is the one a fan means.
        pros = [n for n in namesakes if not (n.get("description") or "").upper().startswith("NCAA")]
        if len(pros) != 1:
            log.info("identify %r: %d athletes share the name; leaving it to the agent", query, len(namesakes))
            return False
        namesakes = pros
    found = []
    for adapter in adapters:
        try:
            ref = adapter.find_player(query)
        except AdapterError:
            continue
        # It must be the one ESPN athlete with the typed name (their record may carry a longer legal
        # name: "Jasprit Bumrah" is "Jasprit Jasbirsingh Bumrah" on ESPNcricinfo), not another person
        # the adapter's own lookup happened to reach.
        if ref and len(namesakes) == 1 and (namesakes[0].get("uid") or "").endswith(f"~a:{ref.athlete_id}"):
            found.append((adapter, ref))
    if len(found) == 1:
        return found[0]
    if found or namesakes:
        # Claimed by two of our sources, or an ESPN athlete in a sport we don't cover (Max Verstappen).
        log.info("identify %r: not a single covered athlete; leaving it to the agent", query)
        return False
    return None


PROVIDER = Provider(
    name="ESPN",
    url="https://www.espn.com",
    what="schedules, live scores and player stats.",
    note="Development builds only; a public build uses licensed sources.",
    licensed=False,
    search=LiveSearch(find=search, by_word=True),
    recognise=recognise,
)


class EspnAdapter:
    """What the four ESPN adapters share (mixed in before base.Adapter)."""

    espn_uid: str  # the sport's prefix in ESPN's uids: "s:600~" football, "s:200~" cricket
    espn_leagues: dict[str, str] | None = None  # ESPN league id -> the league the adapter reads, where it reads only some
    provider = PROVIDER

    def search(self, name: str, league: str | None = None) -> list[PlayerRef]:
        return athletes(self, name, league)

    def search_detail(self, subtitle: str, description: str, league: str | None) -> str:
        """This sport's line for a search hit."""
        return search_detail(subtitle, description, self.sport)
