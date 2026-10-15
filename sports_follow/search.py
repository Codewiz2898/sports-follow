"""Player search as you type (design: "How player search works").

One request per pause in typing, answered from Postgres: players already on Sports Follow, then
the player registry (registry.py: every athlete Wikidata knows in the five sports, ranked by how many
Wikipedias write about them, and the esports pros their sources know). The sources' own searches (ESPN's,
Lichess's FIDE list) are asked only to fill gaps, when the registry has fewer than FILL_BELOW matches
(someone too new or too obscure for Wikidata).
Every result names an exact athlete, by registry id or source id, so following one never guesses
from the name. College athletes are left out.

The live sources are cached and cut off after SOURCE_TIMEOUT, so a slow one only drops its own results.
"""

from __future__ import annotations

import functools
import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Callable, Sequence

from sqlalchemy import case, func, literal, or_, select, tuple_
from sqlalchemy.orm import Session

from . import naming, pipeline, registry, sports, structured
from .adapters import ADAPTERS, Adapter, AdapterError, PlayerRef, Provider, SearchResult, budget, providers, source_name
from .models import Athlete, Follow, Player, PlayerAlias, PlayerCard, PlayerIdentity
from .pipeline import slugify

log = logging.getLogger("sports_follow.search")

SOURCE_TIMEOUT = 2.5  # seconds a source gets before its results are left out of this answer
LIVE_LIMIT = 8  # results from the sources' own searches (each source's list is cut at LIVE_LIMIT + 4)
LOCAL_LIMIT = 6
REGISTRY_LIMIT = 12
FILL_BELOW = 3  # registry matches under which the sources' own searches are asked too
NOTABLE = 10  # Wikipedia editions that make one registry match enough
SUGGEST_MIN = 0.78  # how close a spelling must be to be offered as "did you mean"
SURELY = 0.9  # a "did you mean" this close is the name, misspelled: the live sources aren't asked as well
LIKENESS = 0.4  # trigram likeness of a fan's alias to the query that makes a player a "did you mean"

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="search")


def _key(name: str) -> list[str]:
    """A name's words as the registry indexes them, in any order: "N’Golo Kanté" is "ngolo kante"."""
    return structured._name_key(name)


def _aka(query: str, name: str, aliases: list[str]) -> str | None:
    """The alias a result was found by when its name doesn't start with what was typed: "jonathan"
    finds Cije as Christian Jonathan. An alias typed whole comes before one it only starts."""
    if naming.prefix_match(query, name):
        return None
    return next((x for x in aliases if x and naming.same(query, x)), None) or next((x for x in aliases if x and naming.prefix_match(query, x)), None)


def searchable(query: str) -> bool:
    """Whether a query has a name to search for: two letters or digits once folded to plain Latin.
    "--" hasn't, and neither has "विराट कोहली": names are matched in Latin letters, and the standard
    library has no transliteration for other scripts."""
    return len(" ".join(_key(query))) >= 2


def closeness(query: str, name: str) -> float:
    """How alike a query is to a name, ignoring case, accents and word order: the whole name
    ("catalin clark" ~ "Caitlin Clark": 0.85), or as many of its words as were typed ("sabalnka" ~
    Sabalenka: 0.94)."""
    q, n = _key(query), registry.words(name)
    typed = " ".join(q)
    spans = [" ".join(sorted(n[i : i + len(q)])) for i in range(max(1, len(n) - len(q) + 1))]
    return max(SequenceMatcher(None, typed, x).ratio() for x in [" ".join(_key(name)), *spans])


def surely(query: str, name: str) -> bool:
    """Whether a "did you mean" is surely the athlete meant: nearly the spelling typed ("sabalnka"), or
    the same surname with the first name spelled another way ("mohammad salah"). "shane warne" isn't
    Shane Watson, nor "kobe bryant" Em Bryant."""
    if closeness(query, name) >= SURELY:
        return True
    q, n = registry.words(query), registry.words(name)
    return len(q) >= 2 and len(n) >= 2 and q[-1] == n[-1] and q[0][0] == n[0][0]


def local(db: Session, query: str, fan: str | None, sport: str | None) -> list[SearchResult]:
    """Players on Sports Follow whose name or any alias fans typed is like the query (typos allowed)."""
    needle = slugify(query)
    if len(needle) < 2:
        return []
    # Aliases are slugs of every name fans typed for the player ("virat-kohli", "kohli", "cr7"); a word
    # of one starting with the query is a match, and trigram likeness catches typos for "did you mean".
    # Likeness filters with the operators the trigram index answers (% and <%, at LIKENESS); the
    # similarity functions only order what it found.
    db.execute(select(func.set_config("pg_trgm.similarity_threshold", str(LIKENESS), True), func.set_config("pg_trgm.word_similarity_threshold", str(LIKENESS), True)))
    starts = or_(PlayerAlias.alias.startswith(needle), PlayerAlias.alias.contains(f"-{needle}"))
    alike = or_(PlayerAlias.alias.op("%")(needle), literal(needle).op("<%")(PlayerAlias.alias))
    sim = func.greatest(func.similarity(PlayerAlias.alias, needle), func.word_similarity(needle, PlayerAlias.alias))
    best = func.max(sim).label("sim")
    by_alias = func.min(case((starts, PlayerAlias.alias))).label("by_alias")  # an alias the query starts a word of
    q = (
        select(Player, PlayerCard.status, PlayerCard.card, best, by_alias)
        .join(PlayerAlias, PlayerAlias.player_id == Player.id)
        .join(PlayerCard, PlayerCard.player_id == Player.id)
        .where(or_(starts, alike), PlayerCard.status != "failed")
        .group_by(Player.id, PlayerCard.player_id)
        .order_by(best.desc())
        .limit(LOCAL_LIMIT * 2)
    )
    if sport:
        q = q.where(func.lower(Player.sport).in_(sports.names_of(sports.BY_KEY[sport])))  # an esports pro's sport is their game
    rows = db.execute(q).all()
    if not rows:
        return []
    return _results(db, rows, fan, query)


def _results(db: Session, rows: Sequence[Any], fan: str | None, query: str) -> list[SearchResult]:
    """Players on Sports Follow as result rows: (player, card status, card, likeness, alias found by)."""
    ids = [r[0].id for r in rows]
    followers, mine = _fans(db, ids, fan)
    idents = {pid: (system, ext) for pid, system, ext in db.execute(select(PlayerIdentity.player_id, PlayerIdentity.system, PlayerIdentity.external_id).where(PlayerIdentity.player_id.in_(ids), PlayerIdentity.system.in_(list(ADAPTERS))))}
    qids = dict(db.execute(select(PlayerIdentity.player_id, PlayerIdentity.external_id).where(PlayerIdentity.player_id.in_(ids), PlayerIdentity.system == "wikidata")).all())
    out = []
    for player, status, card, similarity, alias in rows:
        system, ext = idents.get(player.id, (None, None))
        league = None
        if system:
            row = structured.binding(db, player.id)
            league = (row.locator or {}).get("league") if row else None
        qid = qids.get(player.id)
        out.append(SearchResult(
            key=f"{system}:{ext}" if system else (f"wikidata:{qid}:{player.sport.lower()}" if qid else f"player:{player.id}"),
            name=player.name,
            sport=sports.label(player.sport) or "Sport not known yet",
            detail=" · ".join(t for t in (player.teams or [])[:2] if t) or (player.role or ""),
            system=system,
            athlete_id=ext,
            league=league,
            player_id=player.id,
            following=player.id in mine,
            followers=int(followers.get(player.id, 0)),
            live_scores=system is not None,
            inactive=player.status == "retired",
            exact=naming.same(query, player.name),
            aka=alias.replace("-", " ") if alias and not naming.prefix_match(query, player.name) else None,
            status=status,
            next=next_up(card or {}),
            qid=qid,
            matched=bool(alias) or naming.prefix_match(query, player.name),
            _rank=(float(similarity or 0),),
        ))
    return out


def _fans(db: Session, ids: list[int], fan: str | None) -> tuple[dict[int, int], set[int]]:
    """Each player's follower count, and which of them this fan follows."""
    followers = dict(db.execute(select(Follow.player_id, func.count(Follow.id)).where(Follow.player_id.in_(ids)).group_by(Follow.player_id)).all())
    mine = set(db.scalars(select(Follow.player_id).where(Follow.fan_id == fan, Follow.player_id.in_(ids)))) if fan else set()
    return followers, mine


def from_athlete(a: Athlete, query: str) -> SearchResult:
    """A registry athlete as a result. Keyed by their live-source id when the registry knows it, so a
    player already followed through that source is the same row."""
    rules = sports.of(a.sport)
    system = registry.follow_system(a.sport, a.ids or {})
    live_id = (a.ids or {}).get(system) if system else None
    parts = rules.search_detail(a)
    aka = _aka(query, a.name, list(a.aliases or []))
    return SearchResult(
        # A person in two sports is two rows (Chahal plays cricket and chess), so the sport is in the key.
        key=f"{system}:{live_id}" if live_id else f"wikidata:{a.qid}:{a.sport}",
        name=a.name,
        sport=rules.result_sport(a),
        detail=" · ".join(dict.fromkeys(p for p in parts if p)),
        system=system if live_id else None,
        athlete_id=live_id,
        league=a.league,
        live_scores=system is not None,  # a running source; an athlete it can't find goes to the agent
        inactive=not a.current,
        exact=naming.same(query, a.name) or any(naming.same(query, x) for x in a.aliases or []),
        aka=aka,
        qid=a.qid if a.qid.startswith("Q") else None,  # esports rows are keyed by their PandaScore id instead
        born=a.birth_date.year if a.birth_date else None,
        matched=aka is not None or naming.prefix_match(query, a.name),
        popularity=a.sitelinks or 0,
    )


def next_up(card: dict[str, Any]) -> dict[str, Any] | None:
    """What's next on a card: its live game, or the first one not yet played."""
    live = card.get("live") or {}
    if live.get("is_live"):
        return {"live": live.get("headline") or live.get("score") or "Live now", "title": live.get("event")}
    upcoming = [u for u in card.get("upcoming") or [] if u.get("status") != "final"]
    if upcoming:
        u = upcoming[0]
        return {"title": u.get("title"), "start_utc": u.get("start_utc"), "competition": u.get("competition")}
    return None


# ---------------------------------------------------------------- merge and rank


def rank(query: str, local_results: list[SearchResult], source_results: list[SearchResult]) -> list[SearchResult]:
    """One list: players on Sports Follow first (the fan's own, then by followers), then athletes the
    live sources know, then athletes in sports only the agent covers. Within a group, exact names,
    then names that start with what was typed, then notable ones (a headshot on ESPN, a title in
    chess), in the source's own order."""
    seen: dict[str, SearchResult] = {}
    # One person in one sport: Chahal the cricketer and Chahal the chess player share a Wikidata id.
    by_qid: dict[tuple[str, str], SearchResult] = {}
    # A player on Sports Follow, however each source keys them (_ours found who they are).
    by_player: dict[int, SearchResult] = {}

    def hold(r: SearchResult) -> None:
        seen[r.key] = r
        if r.qid:
            by_qid[(r.qid, r.sport)] = r
        if r.player_id:
            by_player[r.player_id] = r

    for r in local_results:
        if r.matched:  # a typo-level likeness is offered as "did you mean", not listed as the name
            hold(r)
    for r in source_results:
        held = seen.get(r.key) or (by_qid.get((r.qid, r.sport)) if r.qid else None) or by_player.get(r.player_id or 0)
        if held is None:
            hold(r)
            continue
        # The same athlete from two places: one row, keeping what each knows.
        held.qid = held.qid or r.qid
        held.born = held.born or r.born
        held.popularity = max(held.popularity, r.popularity)
        if not held.system and r.system:
            held.system, held.athlete_id, held.league = r.system, r.athlete_id, r.league
        if r.player_id and not held.player_id:
            held.player_id, held.following, held.followers, held.status, held.next = r.player_id, r.following, r.followers, r.status, r.next
            by_player[r.player_id] = held
        if r.detail and (sports.of(held.sport).source_line_first or len(r.detail) > len(held.detail)):
            # The source's line is current ("GM · Norway"); ours is whatever the agent last called
            # their teams, which for a chess player can be a league side.
            held.detail = r.detail
        if not naming.prefix_match(query, held.name):
            # Found by another name: as a source writes it ("Dommaraju Gukesh"), before a fan's slug.
            held.aka = r.aka or (r.name if naming.prefix_match(query, r.name) else None) or held.aka

    def group(r: SearchResult) -> int:
        return 0 if r.player_id else (1 if r.live_scores else 2)

    def score(r: SearchResult) -> float:
        # Popularity counts double for players of today (registry.score), and triple for the exact
        # name: "djok" is still Djokovic, and "ronaldo" is Cristiano before the Brazilian Ronaldo.
        return r.popularity * (1 if r.inactive else 2) * (3 if r.exact else 1)

    # An exact name with no popularity to go by (an esports pro: their sources have none) comes after
    # the exact names whose popularity is known and before every partial match below them: "tenz" is
    # TenZ before the Tenzins, and "ronaldo" Cristiano, the Brazilian Ronaldo, then the LoL pro.
    floor: dict[int, float] = {}
    for r in seen.values():
        if r.exact and r.popularity:
            floor[group(r)] = min(floor.get(group(r), math.inf), score(r))

    def order(pair: tuple[int, SearchResult]) -> tuple:
        i, r = pair
        popularity = floor.get(group(r), math.inf) - 0.5 if r.exact and not r.popularity else score(r)
        starts = r.exact or naming.prefix_match(query, r.name)  # an alias typed exactly ("r9") counts too
        return (group(r), not r.following, -r.followers if r.player_id else 0, not starts, -popularity, not r.exact, r.inactive, i)

    return [r for _, r in sorted(enumerate(seen.values()), key=order)]


def run(db: Session, query: str, sport: str | None = None, fan: str | None = None, may_ask: Callable[[], bool] = lambda: True) -> dict[str, Any]:
    """Search every source in parallel and merge. sport narrows to one of the sports that run. may_ask is asked once,
    just before the first of the sources' own searches would go out: False leaves them out (a client over
    its limit, server.search) and the answer comes from Sports Follow and the registry."""
    from .config import AGENT

    query = " ".join(query.split())[:80]
    sport = sport if sport in {s.key for s in sports.ENABLED} else None
    ask = functools.cache(may_ask)
    if len(query) < 2 or not searchable(query):
        # Nothing to search by ("--", "विराट कोहली"), nor to research: a researched player is named by
        # the query's letters (pipeline.slugify).
        return {"query": query, "sport": sport, "results": [], "suggestions": [], "namesakes": 0, "partial": [], "researchable": False}
    mine = [r for r in local(db, query, fan, sport) if sports.on(r.sport)]
    # Without the research agent (config.AGENT) only athletes in a sport with a running source can be
    # followed: the others are left out before anything is decided on them (asking the sources, "did you mean").
    listed = [r for r in _registry(db, query, sport) if sports.on(r.sport) and (AGENT or r.live_scores)]
    found: list[SearchResult] = []
    partial: list[str] = []
    matched = [r for r in listed if r.matched]
    ours = [r for r in mine if r.matched]
    suggestions: list[SearchResult] = []
    if not matched and not ours:
        # Nothing starts with that: a spelling the registry can place. Only one that is surely the name
        # ("sabalnka") spares the live sources; "shane warne" is offered Shane Watson, and ESPN is asked.
        suggestions = suggest(db, query, mine, sport)
    sure = any(surely(query, s.name) for s in suggestions)
    if len(matched) < FILL_BELOW and not sure and not any(r.exact or r.popularity >= NOTABLE for r in matched):
        # Too new or too obscure for Wikidata: ask the sources' own searches (adapters.base.LiveSearch).
        # A name the registry knows well ("bumrah") doesn't need them.
        live = _live(query, sport)
        jobs = {provider.name: _pool.submit(budget.carry(provider.search.find), query, sport, adapters) for provider, adapters in live} if live and ask() else {}
        answers, partial = _collect(jobs)
        if live and not jobs:
            partial = [provider.name for provider, _ in live]
        # A live hit with the name of a registry athlete in the same sport is that athlete: the
        # registry's row, confirmed by birth date when followed, stands for both.
        named = {(tuple(_key(r.name)), r.sport) for r in [*mine, *listed, *suggestions]}
        for hits in answers:
            hits = [r for r in hits if (not sport or r.sport.lower() == sport) and sports.on(r.sport) and (tuple(_key(r.name)), r.sport) not in named]
            found += hits[: LIVE_LIMIT + 4]
    sources = [*matched, *found]
    _ours(db, sources, fan)
    results = rank(query, mine, sources)
    if not suggestions and not any(r.exact or naming.prefix_match(query, r.name) for r in results):
        suggestions = suggest(db, query, mine, sport, live=ask)
    _ours(db, suggestions, fan)
    if suggestions:
        results = [r for r in results if r.key not in {s.key for s in suggestions}]
    # An athlete an operator removed (players.py) isn't found by their ids in the sources either.
    blocked = registry.blocked(db)
    if blocked:
        results = [r for r in results if (r.system, r.athlete_id) not in blocked]
        suggestions = [r for r in suggestions if (r.system, r.athlete_id) not in blocked]
    if not AGENT:
        # And of the sources' own hits and the spellings offered, likewise.
        results = [r for r in results if r.player_id or r.live_scores]
        suggestions = [r for r in suggestions if r.player_id or r.live_scores]
    # Everyone with exactly the typed name is listed, however far down the rest would cut them: the
    # picker says how many people that is (Maldini's football and tennis rows are one) and shows each.
    results = [r for i, r in enumerate(results) if i < LIVE_LIMIT + LOCAL_LIMIT or r.exact]
    namesakes = len({r.qid or r.key for r in results if r.exact})
    for r in results:
        if namesakes > 1 and r.exact and r.born and str(r.born) not in r.detail:
            r.detail = " · ".join(x for x in (r.detail, f"born {r.born}") if x)
    return {
        "query": query,
        "sport": sport,
        "results": [public(r) for r in results],
        "suggestions": [public(r) for r in suggestions],
        "namesakes": namesakes if namesakes > 1 else 0,
        "partial": partial,
        "researchable": AGENT and bool(slugify(query)),
    }


def _respellings(query: str) -> list[str]:
    """Other spellings the registry may have the name meant under: Đ as "dj" (Wikidata writes Novak
    Djokovic), and "ue", "oe", "ae" as the umlauts they spell (Thomas Müller is indexed "muller")."""
    typed = " ".join(naming.words(query))
    out = [" ".join(naming.words(query.translate(naming.NATIVE)))] if re.search("[đĐÐ]", query) else []
    out.append(re.sub(r"([aou])e", r"\1", typed))
    return [x for x in dict.fromkeys(out) if x != typed]


def _registry(db: Session, query: str, sport: str | None) -> list[SearchResult]:
    """Registry athletes for a query, in the spelling typed and its respellings. An athlete found by a
    respelling is kept only if their own name or an alias matches what was typed: "michael" looked up
    as "michal" keeps no Michal."""
    out = {r.key: r for r in (from_athlete(a, query) for a in registry.find(db, query, sport, REGISTRY_LIMIT))}
    for other in _respellings(query):
        for a in registry.find(db, other, sport, REGISTRY_LIMIT):
            r = from_athlete(a, query)
            if r.matched:
                out.setdefault(r.key, r)
    return list(out.values())


def _ours(db: Session, rows: list[SearchResult], fan: str | None) -> None:
    """Fill in the rows from the registry and the live sources that are players on Sports Follow: by
    the source id a player is bound to, or the registry athlete a player holds in that sport
    (pipeline.registry_holder). Gukesh D is followed however he was found ("dommaraju gukesh" isn't an
    alias fans typed)."""
    rows = [r for r in rows if not r.player_id]
    sources = sorted({(r.system, r.athlete_id) for r in rows if r.system and r.athlete_id})
    qids = sorted({r.qid for r in rows if r.qid})
    if not sources and not qids:
        return
    wanted = [tuple_(PlayerIdentity.system, PlayerIdentity.external_id).in_(sources)] if sources else []
    if qids:
        wanted.append((PlayerIdentity.system == "wikidata") & PlayerIdentity.external_id.in_(qids))
    held = {(system, ext): pid for system, ext, pid in db.execute(select(PlayerIdentity.system, PlayerIdentity.external_id, PlayerIdentity.player_id).where(or_(*wanted))).all()}
    if not held:
        return
    theirs = []
    for r in rows:
        pid = held.get((r.system, r.athlete_id))
        if pid is None and ("wikidata", r.qid) in held:
            # One player holds a qid: in another of the athlete's sports, they aren't this row (Chahal).
            pid = pipeline.registry_holder(db, r.qid, pipeline.registry_sport(r.sport))
        if pid is not None:
            theirs.append((r, pid))
    ids = sorted({pid for _, pid in theirs})
    cards = {pid: (status, card) for pid, status, card in db.execute(select(PlayerCard.player_id, PlayerCard.status, PlayerCard.card).where(PlayerCard.player_id.in_(ids), PlayerCard.status != "failed")).all()} if ids else {}
    followers, mine = _fans(db, list(cards), fan) if cards else ({}, set())
    for r, pid in theirs:
        if pid in cards:
            status, card = cards[pid]
            r.player_id, r.following, r.followers, r.status, r.next = pid, pid in mine, int(followers.get(pid, 0)), status, next_up(card or {})


def _collect(jobs: dict[str, Any]) -> tuple[list[list[SearchResult]], list[str]]:
    """Each job's results, in the jobs' order, and the names of those that didn't answer in time."""
    answers: list[list[SearchResult]] = []
    partial: list[str] = []
    deadline = time.monotonic() + SOURCE_TIMEOUT
    for name, job in jobs.items():
        try:
            answers.append(job.result(timeout=max(0.05, deadline - time.monotonic())))
        except FutureTimeout:
            partial.append(name)
        except AdapterError as exc:
            log.info("search: %s left out: %s", name, exc)
            partial.append(name)
    return answers, partial


def _live(query: str, sport: str | None, by_word: bool = False) -> list[tuple[Provider, list[Adapter]]]:
    """The providers whose own search is asked for this query: under a sport filter their sources serve,
    or with no filter if they take unfiltered searches. by_word: asked a word at a time for "did you mean"."""
    out = []
    for provider, adapters in providers():
        live = provider.search
        if live is None or len(query) < live.min_length or (by_word and not live.by_word):
            continue
        if (sport in {a.sport for a in adapters}) if sport else live.unfiltered:
            out.append((provider, adapters))
    return out


def suggest(db: Session, query: str, mine: list[SearchResult], sport: str | None, live: Callable[[], bool] | None = None) -> list[SearchResult]:
    """Did you mean: names a few letters away from the query, from our players and the registry's
    trigram index. Only if neither has one, and live says yes (None: never), are the sources' searches
    with no typo tolerance asked (ESPN's), each longer word on its own ("catalin clark" -> "clark" finds
    Caitlin Clark), keeping hits whose whole name is close to what was typed."""
    pool: dict[str, SearchResult] = {r.key: r for r in mine}
    for a in registry.near(db, query, sport):
        r = from_athlete(a, query)
        if not sports.on(r.sport):
            continue
        held = pool.setdefault(r.key, r)
        held.popularity = max(held.popularity, r.popularity)  # a followed player keeps the registry's fame
    # Close enough spellings, the famous first: "kohly" means Virat Kohli before Marius Köhl.
    close = sorted(((closeness(query, r.name), r) for r in pool.values()), key=lambda x: -(x[0] + 0.1 * math.log10(1 + x[1].popularity)))
    words = [w for w in _key(query) if len(w) >= 3][:3]
    by_word = _live(query, sport, by_word=True) if words else []
    if any(score >= SUGGEST_MIN for score, _ in close) or not by_word or live is None or not live():
        return [r for score, r in close if score >= SUGGEST_MIN][:3]
    jobs = {f"{provider.name}:{w}": _pool.submit(budget.carry(provider.search.find), w, sport, adapters) for provider, adapters in by_word for w in words}
    answers, _ = _collect(jobs)
    for r in (r for hits in answers for r in hits):
        if (not sport or r.sport.lower() == sport) and r.live_scores and sports.on(r.sport):
            pool.setdefault(r.key, r)
    scored = sorted(((closeness(query, r.name), r) for r in pool.values()), key=lambda x: -x[0])
    return [r for score, r in scored if score >= SUGGEST_MIN][:3]


def offered(db: Session, athletes: list[Athlete], fan: str | None) -> list[SearchResult]:
    """Registry athletes as the app's result rows, not found by a query but offered (starters.py): filled
    in for those already on Sports Follow, so following one is the same call as from search. Only those a
    running source covers by id, or who have a page, so following one never needs AI research."""
    return [r for _, r in offered_pairs(db, athletes, fan)]


def offered_pairs(db: Session, athletes: list[Athlete], fan: str | None) -> list[tuple[Athlete, SearchResult]]:
    """offered, each row with the athlete it stands for."""
    rows = [from_athlete(a, "") for a in athletes]
    _ours(db, rows, fan)
    return [(a, r) for a, r in zip(athletes, rows) if r.player_id or (r.system and r.athlete_id)]


def offered_refs(db: Session, sport: str, refs: list[PlayerRef], fan: str | None) -> list[SearchResult]:
    """Players a source names (a game on now's, live.py) as result rows, in the order given: followed by
    the source's id, and filled in for those already on Sports Follow."""
    rows = [
        SearchResult(key=f"{r.system}:{r.athlete_id}", name=r.name, sport=sport, detail=" · ".join(n for n in r.team_names[:1] if n),
                     system=r.system, athlete_id=r.athlete_id, league=r.league, live_scores=True)
        for r in refs
    ]
    _ours(db, rows, fan)
    return rows


def offered_players(db: Session, players: list[Player], fan: str | None) -> list[SearchResult]:
    """Players on Sports Follow as offered result rows (starters.py), in the order given: those whose page
    is ready or being built, with what's next from their card."""
    ids = [p.id for p in players]
    cards = {pid: (status, card) for pid, status, card in db.execute(select(PlayerCard.player_id, PlayerCard.status, PlayerCard.card).where(PlayerCard.player_id.in_(ids), PlayerCard.status != "failed")).all()} if ids else {}
    return _results(db, [(p, *cards[p.id], 0.0, None) for p in players if p.id in cards], fan, "")


def public(r: SearchResult) -> dict[str, Any]:
    out = asdict(r)
    for hidden in ("_rank", "matched", "popularity"):
        out.pop(hidden, None)
    return out


# ---------------------------------------------------------------- AI research allowance


RESEARCH_PER_DAY = 3


def research_key(fan: str, now: datetime | None = None) -> str:
    day = (now or datetime.now(timezone.utc)).date().isoformat()
    return f"research:{fan}:{day}"


def research_address_key(address: str, now: datetime | None = None) -> str:
    """One client address's count for the UTC day. Not under "research:", where a fan id could name it."""
    day = (now or datetime.now(timezone.utc)).date().isoformat()
    return f"research-address:{address}:{day}"


def research_hour_key(now: datetime | None = None) -> str:
    """The count across all fans for the UTC hour."""
    return f"research-hour:{(now or datetime.now(timezone.utc)):%Y-%m-%dT%H}"


def research_status(used: int, now: datetime | None = None) -> dict[str, Any]:
    """A fan's AI research allowance today. Days are UTC days; resets_at lets the app say when in local time."""
    now = now or datetime.now(timezone.utc)
    tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    used = max(0, min(used, RESEARCH_PER_DAY))  # a count below zero is never shown as more than three left
    return {"used": used, "limit": RESEARCH_PER_DAY, "left": RESEARCH_PER_DAY - used, "resets_at": tomorrow.isoformat()}


# ---------------------------------------------------------------- preview before following


_previews: dict[str, tuple[float, dict[str, Any]]] = {}
PREVIEW_TTL = 600


def preview(system: str, athlete_id: str, league: str | None) -> dict[str, Any] | None:
    """What following this athlete would show: who they are, their next game, their last game with
    their line, and their stats. None if the source has no such athlete, or has just been turned off."""
    adapter = ADAPTERS.get(system)
    if adapter is None:
        return None
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
        "sport": structured.sport_name(adapter, ref),
        "teams": [t for t in ref.team_names if t and t != ref.name],
        "source": source_name(system),
        "source_url": ref.profile_url,
        "next": None,
        "last": None,
        "stats": [],
        "stats_note": "",
    }
    recent = []
    if adapter.preview_fixtures:
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
