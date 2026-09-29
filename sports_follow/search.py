"""Player search as you type (design: "How player search works").

One request per pause in typing, answered from Postgres: players already on Sports Follow, then
the player registry (registry.py: every athlete Wikidata knows in the five sports, ranked by how many
Wikipedias write about them). ESPN's search and Lichess's FIDE search are asked only to fill gaps,
when the registry has fewer than FILL_BELOW matches (someone too new or too obscure for Wikidata).
Every result names an exact athlete, by registry id or source id, so following one never guesses
from the name. College athletes are left out.

The live sources are cached and cut off after SOURCE_TIMEOUT, so a slow one only drops its own results.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import registry, structured
from .adapters import ADAPTERS, AdapterError, espn, lichess_chess
from .models import Athlete, Follow, Player, PlayerAlias, PlayerCard, PlayerIdentity
from .pipeline import slugify

log = logging.getLogger("sports_follow.search")

SOURCE_TIMEOUT = 2.5  # seconds a source gets before its results are left out of this answer
ESPN_LIMIT = 8
CHESS_LIMIT = 4  # FIDE rows among all sports; a chess-only search shows more
LOCAL_LIMIT = 6
REGISTRY_LIMIT = 12
FILL_BELOW = 3  # registry matches under which ESPN and Lichess are asked too
NOTABLE = 10  # Wikipedia editions that make one registry match enough
SUGGEST_MIN = 0.78  # how close a spelling must be to be offered as "did you mean"

# ESPN uid prefix -> (our sport, adapter). Leagues an adapter can't follow come back as "AI only".
ESPN_SPORTS = {"s:600": ("football", "espn_soccer"), "s:200": ("cricket", "espn_cricket"), "s:40": ("basketball", "espn_basketball"), "s:850": ("tennis", "espn_tennis")}
ADAPTER_LEAGUES = {"espn_basketball": {"46": "nba", "59": "wnba"}, "espn_tennis": {"851": "atp", "900": "wta"}}
COLLEGE_LEAGUES = {"23", "41", "54"}  # NCAAF, NCAAM, NCAAW
OTHER_SPORTS = {"football": "American football", "racing": "Motor racing", "mma": "MMA", "hockey": "Ice hockey", "baseball": "Baseball", "golf": "Golf", "soccer": "Football", "boxing": "Boxing", "rugby": "Rugby"}
SPORTS = ("football", "cricket", "basketball", "tennis", "chess")

# FIDE federation codes for the countries most players come from; others show as the code.
FEDERATIONS = {
    "NOR": "Norway", "IND": "India", "USA": "United States", "CHN": "China", "RUS": "Russia", "FID": "FIDE", "NED": "Netherlands",
    "GER": "Germany", "FRA": "France", "ESP": "Spain", "ENG": "England", "ITA": "Italy", "POL": "Poland", "UKR": "Ukraine",
    "ARM": "Armenia", "AZE": "Azerbaijan", "HUN": "Hungary", "UZB": "Uzbekistan", "IRI": "Iran", "DEN": "Denmark", "SWE": "Sweden",
    "CZE": "Czechia", "ROU": "Romania", "SRB": "Serbia", "TUR": "Türkiye", "ISR": "Israel", "CAN": "Canada", "BRA": "Brazil",
    "ARG": "Argentina", "PER": "Peru", "VIE": "Vietnam", "PHI": "Philippines", "GEO": "Georgia", "KAZ": "Kazakhstan", "AUS": "Australia",
}

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="search")


@dataclass
class Result:
    key: str  # "espn_basketball:4433403"; "player:12" for a player no source knows
    name: str
    sport: str  # "Basketball", "MMA"
    detail: str  # "Indiana Fever · WNBA", "GM · Norway · 2823 classical"
    system: str | None = None
    athlete_id: str | None = None
    league: str | None = None
    player_id: int | None = None  # set when the athlete is already on Sports Follow
    following: bool = False
    followers: int = 0
    live_scores: bool = False  # a source we follow live covers them; otherwise the agent builds the page
    inactive: bool = False
    exact: bool = False  # the name is exactly what was typed
    status: str | None = None  # the card's status, for players on Sports Follow
    next: dict[str, Any] | None = None  # {"title", "start_utc", "competition"} or {"live": "headline"}
    qid: str | None = None  # the athlete's Wikidata id, when the registry knows them
    born: int | None = None  # birth year, shown to tell namesakes apart
    matched: bool = True  # False: only alike ("catalin clark" ~ Caitlin Clark), offered as "did you mean"
    popularity: int = 0  # Wikipedia editions for registry athletes; a headshot or title for live hits
    _rank: tuple = ()


def _key(name: str) -> list[str]:
    return structured._name_key(name)


def _prefix_match(query: str, name: str) -> bool:
    """Every typed word starts some word of the name: "caitl" -> Caitlin Clark, "v kohli" -> Virat Kohli."""
    words = _key(name)
    return all(any(w.startswith(q) for w in words) for q in _key(query))


def closeness(query: str, name: str) -> float:
    """How alike a query is to a name, ignoring case, accents and word order: the whole name
    ("catalin clark" ~ "Caitlin Clark": 0.85), or as many of its words as were typed ("sabalnka" ~
    Sabalenka: 0.94)."""
    q, n = _key(query), registry.words(name)
    typed = " ".join(q)
    spans = [" ".join(sorted(n[i : i + len(q)])) for i in range(max(1, len(n) - len(q) + 1))]
    return max(SequenceMatcher(None, typed, x).ratio() for x in [" ".join(_key(name)), *spans])


# ---------------------------------------------------------------- sources


def parse_espn(data: dict[str, Any], query: str) -> list[Result]:
    """ESPN search hits as results, college athletes left out, in ESPN's order."""
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
            sport_uid = f"s:{parts.get('s')}"
            covered = ESPN_SPORTS.get(sport_uid)
            system, league = (covered[1], None) if covered else (None, None)
            if system in ADAPTER_LEAGUES:
                league = ADAPTER_LEAGUES[system].get(league_id or "")
                if league is None:
                    system = None  # a league the adapter doesn't read (EuroLeague, ITF): the agent covers them
            if covered:
                sport = covered[0].title()
            else:
                sport = OTHER_SPORTS.get(item.get("sport") or "", (item.get("sport") or description or "Other").title())
            if covered and covered[0] == "cricket":
                detail = subtitle  # "India": ESPNcricinfo's description is just "Cricket"
            elif covered and covered[0] == "tennis":
                detail = (league or description).upper()
            else:
                detail = " · ".join(x for x in (subtitle, description if description.lower() != sport.lower() else "") if x)
            out.append(Result(
                key=f"{system}:{parts['a']}" if system else f"espn:{uid}",
                name=name,
                sport=sport,
                detail=detail,
                system=system,
                athlete_id=parts["a"] if system else None,
                league=league,
                live_scores=system is not None,
                exact=_key(name) == _key(query),
                popularity=5 if item.get("image") else 0,
            ))
    return out


def parse_fide(records: list[dict[str, Any]], query: str, limit: int) -> list[Result]:
    """FIDE records as results: active, titled and higher-rated players first. Amateurs only show
    up when the name is exactly what was typed or the search is chess only (limit > CHESS_LIMIT)."""
    out = []
    for r in records:
        if not r.get("id") or not r.get("name"):
            continue
        name = lichess_chess.display_name(r["name"])
        if not _prefix_match(query, name):
            continue  # FIDE search matches any one word: "max verstappen" returns every titled Max
        exact = _key(name) == _key(query)
        notable = bool(r.get("title")) or (r.get("standard") or 0) >= 2000
        if not (notable or exact or limit > CHESS_LIMIT):
            continue
        fed = FEDERATIONS.get(r.get("federation") or "", r.get("federation") or "")
        rating = f"{r['standard']} classical" if r.get("standard") else ""
        detail = " · ".join(x for x in (r.get("title") or "", fed, rating, "inactive" if r.get("inactive") else "") if x)
        out.append(Result(
            key=f"lichess_chess:{r['id']}",
            name=name,
            sport="Chess",
            detail=detail,
            system="lichess_chess",
            athlete_id=str(r["id"]),
            live_scores=True,
            inactive=bool(r.get("inactive")),
            exact=exact,
            popularity=5 if r.get("title") and not r.get("inactive") else 0,
            _rank=(bool(r.get("title")) and not r.get("inactive"), r.get("standard") or 0),
        ))
    out.sort(key=lambda x: (x.inactive, -x._rank[1]))
    return out[:limit]


def _espn(query: str, limit: int = 20) -> list[Result]:
    data = espn.get_json(f"{espn.WEB}/search/v2?query={espn.quote(query)}&limit={limit}", ttl=3600)
    return parse_espn(data, query)


_fide_lock = threading.Lock()


def _fide(query: str, limit: int) -> list[Result]:
    # Lichess asks for one request at a time; a burst of keystrokes shouldn't queue up behind each
    # other, so a search that can't get the line within a second gives up on chess this time.
    url = f"{lichess_chess.API}/fide/player?q={espn.quote(query)}"
    records = lichess_chess.peek(url, ttl=24 * 3600)
    if records is None:
        if not _fide_lock.acquire(timeout=1.0):
            raise AdapterError("chess search busy")
        try:
            records = lichess_chess.get_json(url, ttl=24 * 3600)
        finally:
            _fide_lock.release()
    return parse_fide(records if isinstance(records, list) else [], query, limit)


def local(db: Session, query: str, fan: str | None, sport: str | None) -> list[Result]:
    """Players on Sports Follow whose name or any alias fans typed is like the query (typos allowed)."""
    needle = slugify(query)
    if len(needle) < 2:
        return []
    # Aliases are slugs of every name fans typed for the player ("virat-kohli", "kohli", "cr7"); a word
    # of one starting with the query is a match, and trigram likeness catches typos for "did you mean".
    starts = or_(PlayerAlias.alias.startswith(needle), PlayerAlias.alias.contains(f"-{needle}"))
    sim = func.greatest(func.similarity(PlayerAlias.alias, needle), func.word_similarity(needle, PlayerAlias.alias))
    best = func.max(sim).label("sim")
    direct = func.bool_or(starts).label("direct")
    q = (
        select(Player, PlayerCard.status, PlayerCard.card, best, direct)
        .join(PlayerAlias, PlayerAlias.player_id == Player.id)
        .join(PlayerCard, PlayerCard.player_id == Player.id)
        .where(or_(starts, sim > 0.4), PlayerCard.status != "failed")
        .group_by(Player.id, PlayerCard.player_id)
        .order_by(best.desc())
        .limit(LOCAL_LIMIT * 2)
    )
    if sport:
        q = q.where(func.lower(Player.sport) == sport)
    rows = db.execute(q).all()
    if not rows:
        return []
    ids = [r[0].id for r in rows]
    followers = dict(db.execute(select(Follow.player_id, func.count(Follow.id)).where(Follow.player_id.in_(ids)).group_by(Follow.player_id)).all())
    mine = set(db.scalars(select(Follow.player_id).where(Follow.fan_id == fan, Follow.player_id.in_(ids)))) if fan else set()
    idents = {pid: (system, ext) for pid, system, ext in db.execute(select(PlayerIdentity.player_id, PlayerIdentity.system, PlayerIdentity.external_id).where(PlayerIdentity.player_id.in_(ids), PlayerIdentity.system.in_(list(ADAPTERS))))}
    qids = dict(db.execute(select(PlayerIdentity.player_id, PlayerIdentity.external_id).where(PlayerIdentity.player_id.in_(ids), PlayerIdentity.system == "wikidata")).all())
    out = []
    for player, status, card, similarity, by_alias in rows:
        system, ext = idents.get(player.id, (None, None))
        league = None
        if system:
            row = structured.binding(db, player.id)
            league = (row.locator or {}).get("league") if row else None
        qid = qids.get(player.id)
        out.append(Result(
            key=f"{system}:{ext}" if system else (f"wikidata:{qid}" if qid else f"player:{player.id}"),
            name=player.name,
            sport=(player.sport or "").title() or "Sport not known yet",
            detail=" · ".join(t for t in (player.teams or [])[:2] if t) or (player.role or ""),
            system=system,
            athlete_id=ext,
            league=league,
            player_id=player.id,
            following=player.id in mine,
            followers=int(followers.get(player.id, 0)),
            live_scores=system is not None,
            inactive=player.status == "retired",
            exact=_key(player.name) == _key(query),
            status=status,
            next=_next(card or {}),
            qid=qid,
            matched=bool(by_alias) or _prefix_match(query, player.name),
            _rank=(float(similarity or 0),),
        ))
    return out


def from_athlete(a: Athlete, query: str) -> Result:
    """A registry athlete as a result. Keyed by their live-source id when the registry knows it, so a
    player already followed through that source is the same row."""
    system = registry.LIVE[a.sport]
    live_id = (a.ids or {}).get(system)
    teams = list(a.teams or [])
    if a.sport == "chess":
        parts = [a.title or "", a.country or ""]
    elif a.sport == "tennis":
        parts = [(a.league or "").upper(), a.country or ""]
    elif a.sport == "basketball":
        parts = [*teams[:1], (a.league or "").upper() if a.current else ""]
    else:
        parts = [*teams[:2], a.country or ""]
    return Result(
        key=f"{system}:{live_id}" if live_id else f"wikidata:{a.qid}",
        name=a.name,
        sport=a.sport.title(),
        detail=" · ".join(dict.fromkeys(p for p in parts if p)),
        system=system if live_id else None,
        athlete_id=live_id,
        league=a.league,
        live_scores=True,  # every registry sport has a live source; an athlete it can't find goes to the agent
        inactive=not a.current,
        exact=_key(a.name) == _key(query) or any(_key(x) == _key(query) for x in a.aliases or []),
        qid=a.qid,
        born=a.birth_date.year if a.birth_date else None,
        matched=_prefix_match(query, a.name) or any(_prefix_match(query, x) for x in a.aliases or []),
        popularity=a.sitelinks or 0,
    )


def _next(card: dict[str, Any]) -> dict[str, Any] | None:
    live = card.get("live") or {}
    if live.get("is_live"):
        return {"live": live.get("headline") or live.get("score") or "Live now", "title": live.get("event")}
    upcoming = [u for u in card.get("upcoming") or [] if u.get("status") != "final"]
    if upcoming:
        u = upcoming[0]
        return {"title": u.get("title"), "start_utc": u.get("start_utc"), "competition": u.get("competition")}
    return None


# ---------------------------------------------------------------- merge and rank


def rank(query: str, local_results: list[Result], source_results: list[Result]) -> list[Result]:
    """One list: players on Sports Follow first (the fan's own, then by followers), then athletes the
    live sources know, then athletes in sports only the agent covers. Within a group, exact names,
    then names that start with what was typed, then notable ones (a headshot on ESPN, a title in
    chess), in the source's own order."""
    seen: dict[str, Result] = {}
    by_qid: dict[str, Result] = {}
    for r in local_results:
        if r.matched:  # a typo-level likeness is offered as "did you mean", not listed as the name
            seen[r.key] = r
            if r.qid:
                by_qid[r.qid] = r
    for r in source_results:
        held = seen.get(r.key) or (by_qid.get(r.qid) if r.qid else None)
        if held is None:
            seen[r.key] = r
            if r.qid:
                by_qid[r.qid] = r
            continue
        # The same athlete from two places: one row, keeping what each knows.
        held.qid = held.qid or r.qid
        held.born = held.born or r.born
        held.popularity = max(held.popularity, r.popularity)
        if not held.system and r.system:
            held.system, held.athlete_id, held.league = r.system, r.athlete_id, r.league
        if r.detail and (held.sport in ("Chess", "Tennis") or len(r.detail) > len(held.detail)):
            # The source's line is current ("GM · Norway"); ours is whatever the agent last called
            # their teams, which for a chess player can be a league side.
            held.detail = r.detail

    def order(pair: tuple[int, Result]) -> tuple:
        i, r = pair
        group = 0 if r.player_id else (1 if r.live_scores else 2)
        # Popularity counts double for players of today (registry.score), and triple for the exact
        # name: "djok" is still Djokovic, and "ronaldo" is Cristiano before the Brazilian Ronaldo.
        weight = (1 if r.inactive else 2) * (3 if r.exact else 1)
        return (group, not r.following, -r.followers if r.player_id else 0, not _prefix_match(query, r.name), -r.popularity * weight, not r.exact, i)

    return [r for _, r in sorted(enumerate(seen.values()), key=order)]


def run(db: Session, query: str, sport: str | None = None, fan: str | None = None) -> dict[str, Any]:
    """Search every source in parallel and merge. sport narrows to one of SPORTS."""
    query = " ".join(query.split())[:80]
    sport = sport if sport in SPORTS else None
    if len(query) < 2:
        return {"query": query, "sport": sport, "results": [], "suggestions": [], "namesakes": 0, "partial": []}
    mine = local(db, query, fan, sport)
    listed = [from_athlete(a, query) for a in registry.find(db, query, sport, REGISTRY_LIMIT)]
    found: list[Result] = []
    partial: list[str] = []
    matched = [r for r in listed if r.matched]
    ours = [r for r in mine if r.matched]
    suggestions: list[Result] = []
    if not matched and not ours:
        # Nothing starts with that: a typo the registry can place ("kohly") needs no live source.
        suggestions = suggest(db, query, mine, sport, live=False)
    if len(matched) < FILL_BELOW and not suggestions and not any(r.exact or r.popularity >= NOTABLE for r in matched):
        # Too new or too obscure for Wikidata: ask the live sources. A name the registry knows well
        # ("bumrah") doesn't need them.
        jobs: dict[str, Any] = {}
        if sport != "chess":
            # ESPN's search has no sport filter; one sport's athletes are a few of every fifty hits.
            jobs["ESPN"] = _pool.submit(_espn, query, 50 if sport else 20)
        if sport == "chess" and len(query) >= 3:
            # The registry has the titled players; Lichess's FIDE search finds the other 1.9 million.
            jobs["Lichess"] = _pool.submit(_fide, query, 10)
        found, partial = _collect(jobs)
        if sport:
            found = [r for r in found if r.sport.lower() == sport]
        # A live hit with the name of a registry athlete in the same sport is that athlete: the
        # registry's row, confirmed by birth date when followed, stands for both.
        named = {(tuple(_key(r.name)), r.sport) for r in [*mine, *listed]}
        found = [r for r in found if (tuple(_key(r.name)), r.sport) not in named]
    espn_hits = [r for r in found if r.system != "lichess_chess"][: ESPN_LIMIT + 4]
    results = rank(query, mine, [*matched, *espn_hits, *[r for r in found if r.system == "lichess_chess"]])
    if not suggestions and not any(r.exact or _prefix_match(query, r.name) for r in results):
        suggestions = suggest(db, query, mine, sport)
    if suggestions:
        results = [r for r in results if r.key not in {s.key for s in suggestions}]
    namesakes = sum(1 for r in results if r.exact)
    for r in results:
        if namesakes > 1 and r.exact and r.born and str(r.born) not in r.detail:
            r.detail = " · ".join(x for x in (r.detail, f"born {r.born}") if x)
    return {
        "query": query,
        "sport": sport,
        "results": [_public(r) for r in results[: ESPN_LIMIT + LOCAL_LIMIT]],
        "suggestions": [_public(r) for r in suggestions],
        "namesakes": namesakes if namesakes > 1 else 0,
        "partial": partial,
    }


def _collect(jobs: dict[str, Any]) -> tuple[list[Result], list[str]]:
    found: list[Result] = []
    partial: list[str] = []
    deadline = time.monotonic() + SOURCE_TIMEOUT
    for name, job in jobs.items():
        try:
            found.extend(job.result(timeout=max(0.05, deadline - time.monotonic())))
        except FutureTimeout:
            partial.append(name)
        except AdapterError as exc:
            log.info("search: %s left out: %s", name, exc)
            partial.append(name)
    return found, partial


def suggest(db: Session, query: str, mine: list[Result], sport: str | None, live: bool = True) -> list[Result]:
    """Did you mean: names a few letters away from the query, from our players and the registry's
    trigram index. Only if neither has one is ESPN asked, each longer word on its own (its search has
    no typo tolerance: "catalin clark" -> "clark" finds Caitlin Clark), keeping hits whose whole name
    is close to what was typed."""
    pool: dict[str, Result] = {r.key: r for r in mine}
    for a in registry.near(db, query, sport):
        r = from_athlete(a, query)
        held = pool.setdefault(r.key, r)
        held.popularity = max(held.popularity, r.popularity)  # a followed player keeps the registry's fame
    # Close enough spellings, the famous first: "kohly" means Virat Kohli before Marius Köhl.
    close = sorted(((closeness(query, r.name), r) for r in pool.values()), key=lambda x: -(x[0] + 0.1 * math.log10(1 + x[1].popularity)))
    if any(score >= SUGGEST_MIN for score, _ in close) or not live:
        return [r for score, r in close if score >= SUGGEST_MIN][:3]
    words = [w for w in _key(query) if len(w) >= 3]
    jobs = {f"ESPN:{w}": _pool.submit(_espn, w, 50 if sport else 20) for w in words[:3]} if sport != "chess" else {}
    found, _ = _collect(jobs)
    for r in found:
        if (not sport or r.sport.lower() == sport) and r.live_scores:
            pool.setdefault(r.key, r)
    scored = sorted(((closeness(query, r.name), r) for r in pool.values()), key=lambda x: -x[0])
    return [r for score, r in scored if score >= SUGGEST_MIN][:3]


def _public(r: Result) -> dict[str, Any]:
    out = asdict(r)
    for hidden in ("_rank", "matched", "popularity"):
        out.pop(hidden, None)
    return out


# ---------------------------------------------------------------- AI research allowance


RESEARCH_PER_DAY = 3


def research_key(fan: str, now: datetime | None = None) -> str:
    day = (now or datetime.now(timezone.utc)).date().isoformat()
    return f"research:{fan}:{day}"


def research_status(used: int, now: datetime | None = None) -> dict[str, Any]:
    """A fan's AI research allowance today. Days are UTC days; resets_at lets the app say when in local time."""
    now = now or datetime.now(timezone.utc)
    tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    return {"used": min(used, RESEARCH_PER_DAY), "limit": RESEARCH_PER_DAY, "left": max(0, RESEARCH_PER_DAY - used), "resets_at": tomorrow.isoformat()}


# ---------------------------------------------------------------- preview before following


_previews: dict[str, tuple[float, dict[str, Any]]] = {}
PREVIEW_TTL = 600


def preview(system: str, athlete_id: str, league: str | None) -> dict[str, Any] | None:
    """What following this athlete would show: who they are, their next game, their last game with
    their line, and their stats. None if the source has no such athlete."""
    adapter = ADAPTERS[system]
    cache_key = f"{system}:{athlete_id}:{league}"
    hit = _previews.get(cache_key)
    if hit and time.time() - hit[0] < PREVIEW_TTL:
        return hit[1]
    ref = adapter.player(athlete_id, league)
    if ref is None:
        return None
    out: dict[str, Any] = {
        "system": system,
        "athlete_id": ref.athlete_id,
        "league": ref.league,
        "name": ref.name,
        "sport": adapter.sports[0].title(),
        "teams": [t for t in ref.team_names if t and t != ref.name],
        "source": structured.SOURCE_NAMES.get(system, system),
        "source_url": ref.profile_url,
        "next": None,
        "last": None,
        "stats": [],
        "stats_note": "",
    }
    recent = []
    if system != "lichess_chess":
        # A chess player's tournaments take a dozen rate-limited Lichess reads to find; the preview
        # shows ratings only and the page finds the tournaments once followed.
        now = datetime.now(timezone.utc)
        fixtures = adapter.fixtures(ref)
        ahead = [f for f in fixtures if f.status in ("scheduled", "live") and not (f.status == "scheduled" and f.start_utc and f.start_utc < now - timedelta(hours=12))]
        finals = [f for f in fixtures if f.status == "final"]
        if ahead:
            f = ahead[0]
            out["next"] = {"title": f.title, "competition": f.competition, "notes": f.detail, "start_utc": f.start_utc.isoformat() if f.start_utc else None, "status": f.status, "team": structured._team_of(f, ref)}
        if finals:
            f = finals[-1]
            try:
                snap = adapter.snapshot(f.locator, final=True)
            except AdapterError:
                snap = None
            out["last"] = structured.result_item(adapter, f, snap, None, ref)
            if snap:
                recent.append((f, snap))
    out["stats"], out["stats_note"] = adapter.stats(ref, recent)
    _previews[cache_key] = (time.time(), out)
    if len(_previews) > 500:
        for key, _ in sorted(_previews.items(), key=lambda kv: kv[1][0])[:100]:
            _previews.pop(key, None)
    return out
