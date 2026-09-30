"""The player registry, imported from Wikidata (design canvas: "Player registry").

Wikidata (public domain, CC0) knows most professional athletes in the covered sports and their ids
in other databases: ESPNcricinfo for cricket, FIDE for chess, ESPN for NBA and tennis players,
Transfermarkt (and sometimes ESPN) for footballers. The importer copies every one of them into the
`athlete` table once a week, and search answers from there before asking any live source.

An athlete whose live-source id Wikidata lacks (most footballers have only a Transfermarkt id) is
looked up in the live source by name when someone follows or previews them, and accepted only if the
birth date agrees (resolve); the id found is kept, so it's a lookup by id from then on.

    .venv/bin/python -m sports_follow.registry            # import every sport, smallest first
    .venv/bin/python -m sports_follow.registry chess      # one sport
    .venv/bin/python -m sports_follow.registry --new      # only people not imported yet

The query service allows a client about a minute of query time a minute, so batches run one at a
time: chess, basketball and tennis take a few minutes each, cricket ten, football about an hour.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

import httpx
from sqlalchemy import case, delete, func, literal, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .adapters import ADAPTERS, Adapter, AdapterError, PlayerRef, espn, for_sport
from .db import session
from .models import Athlete, PlayerIdentity

log = logging.getLogger("sports_follow.registry")

WDQS = "https://query.wikidata.org/sparql"
BATCH = 2000
LABEL_LANGS = ("en", "mul", "es", "pt", "fr", "de", "it", "nl")
MAX_ALIASES = 8

# Who counts as an athlete of each sport (any of these ids), and which ids to keep. The key of each id
# is the adapter that reads it, where one does ("espn_cricket"), or the database's name.
SPORTS: dict[str, dict[str, str]] = {
    "chess": {"lichess_chess": "P1440"},
    "basketball": {"espn_basketball": "P3685", "nba": "P3647", "wnba": "P3588", "bref_nba": "P2685", "bref_wnba": "P4561"},
    "tennis": {"espn_tennis": "P11585", "atp": "P536", "wta": "P597"},
    "cricket": {"espn_cricket": "P2697"},
    # Women's players are on Soccerdonna (Transfermarkt's women's site) and FBref, rarely Transfermarkt.
    "football": {"espn_soccer": "P3681", "transfermarkt": "P2446", "soccerdonna": "P4381", "fbref": "P5750"},
}
# ESPN's search keeps each sport's athletes under one uid prefix.
ESPN_UIDS = {"espn_soccer": "s:600", "espn_cricket": "s:200", "espn_basketball": "s:40", "espn_tennis": "s:850"}
TEAM_SPORTS = ("basketball", "cricket", "football")
CHESS_TITLES = {
    "Grandmaster": "GM", "International Master": "IM", "FIDE Master": "FM", "Candidate Master": "CM",
    "Woman Grandmaster": "WGM", "Woman International Master": "WIM", "Woman FIDE Master": "WFM", "Woman Candidate Master": "WCM",
}
TITLE_ORDER = ["GM", "IM", "WGM", "FM", "WIM", "CM", "WFM", "WCM"]  # highest first
# Country names as fans say them.
COUNTRIES = {"People's Republic of China": "China", "United States of America": "United States", "Kingdom of the Netherlands": "Netherlands", "Republic of Ireland": "Ireland", "Czech Republic": "Czechia"}
# Youth and age-group sides aren't who a fan follows a player for.
YOUTH = re.compile(r"\b(under-?\d+|u-?\d{2}|youth|olympic|academy|reserves?|b team|ii)\b", re.I)
# College sides ("Baylor Bears women's basketball"): college players are hidden, and a pro's college
# team isn't their team.
COLLEGE = re.compile(r"(men's|women's) (basketball|soccer|football|cricket)$|\b(college|university|ncaa)\b", re.I)
NATIONAL = re.compile(r"\s+(men's |women's )?national (association )?(football|cricket|basketball|soccer) team$", re.I)

_client = httpx.Client(headers={"user-agent": espn.USER_AGENT, "accept": "application/sparql-results+json"}, timeout=90)


# ---------------------------------------------------------------- names as search sees them


def words(text: str) -> list[str]:
    """Plain lower-case ASCII words: "A'ja Wilson" -> ["aja", "wilson"], "Jokić" -> ["jokic"]."""
    return re.sub(r"[^a-z0-9]+", " ", espn.fold(re.sub(r"['’`]", "", text or ""))).split()


def search_text(name: str, aliases: Iterable[str]) -> str:
    """ " virat kohli | king kohli |": every word starts after a space and every name ends in " |",
    so "% koh%" finds a word start and "% virat kohli |%" a whole name."""
    names = [" ".join(words(n)) for n in [name, *aliases]]
    return " " + " | ".join(dict.fromkeys(n for n in names if n)) + " |"


# ---------------------------------------------------------------- the query service


def sparql(query: str) -> list[dict[str, str]]:
    """Run a query, one at a time, backing off as the service asks."""
    for attempt in range(4):
        try:
            res = _client.post(WDQS, data={"query": query})
        except httpx.HTTPError as exc:
            log.warning("wikidata query failed (%s); retrying", exc)
            time.sleep(10 * (attempt + 1))
            continue
        if res.status_code == 429:
            wait = int(res.headers.get("retry-after") or 60)
            log.warning("wikidata asked us to slow down; waiting %ss", wait)
            time.sleep(min(wait, 300))
            continue
        if res.status_code >= 500:
            time.sleep(10 * (attempt + 1))
            continue
        res.raise_for_status()
        return [{k: v["value"] for k, v in row.items()} for row in res.json()["results"]["bindings"]]
    raise RuntimeError("wikidata query service kept failing")


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def members(sport: str) -> list[str]:
    union = " UNION ".join(f"{{ ?p wdt:{prop} [] }}" for prop in SPORTS[sport].values())
    return sorted({_qid(r["p"]) for r in sparql(f"SELECT DISTINCT ?p WHERE {{ {union} }}")})


def _values(qids: list[str]) -> str:
    return " ".join(f"wd:{q}" for q in qids)


def fetch(sport: str, qids: list[str]) -> tuple[list[dict], list[dict], list[dict]]:
    values = _values(qids)
    ids = " ".join(f"OPTIONAL {{ ?p wdt:{prop} ?id_{key} }}" for key, prop in SPORTS[sport].items())
    title = 'OPTIONAL { ?p wdt:P2962 ?t . ?t rdfs:label ?title FILTER(LANG(?title) = "en") }' if sport == "chess" else ""
    core = sparql(f"""SELECT * WHERE {{
      VALUES ?p {{ {values} }}
      OPTIONAL {{ ?p wikibase:sitelinks ?sitelinks }}
      OPTIONAL {{ ?p wdt:P569 ?birth }}
      OPTIONAL {{ ?p wdt:P570 ?death }}
      OPTIONAL {{ ?p wdt:P18 ?image }}
      OPTIONAL {{ ?p wdt:P1532 ?c1 . ?c1 rdfs:label ?sport_country FILTER(LANG(?sport_country) = "en") }}
      OPTIONAL {{ ?p wdt:P27 ?c2 . ?c2 rdfs:label ?citizenship FILTER(LANG(?citizenship) = "en") }}
      {title} {ids} }}""")
    langs = ", ".join(f'"{lang}"' for lang in LABEL_LANGS)
    names = sparql(f"""SELECT ?p ?n ?kind (LANG(?n) AS ?lang) WHERE {{
      VALUES ?p {{ {values} }}
      {{ ?p rdfs:label ?n FILTER(LANG(?n) IN ({langs})) BIND("label" AS ?kind) }}
      UNION {{ ?p skos:altLabel ?n FILTER(LANG(?n) = "en") BIND("alias" AS ?kind) }} }}""")
    teams: list[dict] = []
    if sport in TEAM_SPORTS:
        teams = sparql(f"""SELECT ?p ?team ?teamLabel ?start WHERE {{
          VALUES ?p {{ {values} }}
          ?p p:P54 ?st . ?st ps:P54 ?team . FILTER NOT EXISTS {{ ?st pq:P582 [] }}
          OPTIONAL {{ ?st pq:P580 ?start }}
          ?team rdfs:label ?teamLabel FILTER(LANG(?teamLabel) = "en") }}""")
    return core, names, teams


# ---------------------------------------------------------------- query rows -> athlete rows


def _date(value: str | None) -> date | None:
    if not value or value.startswith("-"):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def team_name(label: str) -> str:
    """"India national cricket team" -> "India"; club names stay as they are."""
    return NATIONAL.sub("", label).strip()


def build(sport: str, core: list[dict], names: list[dict], teams: list[dict], today: date | None = None) -> list[dict[str, Any]]:
    """Athlete rows from one batch's query rows. Pure, so it's tested without the network.

    The dead are left out, as is anyone with no name in a Latin-script language we read.
    """
    today = today or datetime.now(timezone.utc).date()
    people: dict[str, dict[str, Any]] = {}
    for r in core:
        p = people.setdefault(_qid(r["p"]), {"ids": {}, "countries": [], "dead": False})
        p["dead"] = p["dead"] or bool(r.get("death"))
        p.setdefault("birth", _date(r.get("birth")))
        p["sitelinks"] = max(p.get("sitelinks", 0), int(r.get("sitelinks") or 0))
        if r.get("image") and not p.get("image"):
            p["image"] = r["image"].rsplit("/", 1)[-1].replace("%20", " ")
        for key in ("sport_country", "citizenship"):
            country = COUNTRIES.get(r.get(key) or "", r.get(key))
            if country and country not in p["countries"]:
                # The country they play for first, then citizenship.
                p["countries"].insert(0 if key == "sport_country" else len(p["countries"]), country)
        title = CHESS_TITLES.get(r.get("title") or "")
        if title and (not p.get("title") or TITLE_ORDER.index(title) < TITLE_ORDER.index(p["title"])):
            p["title"] = title  # a player holds every title they've earned; show the highest
        for key in SPORTS[sport]:
            if r.get(f"id_{key}"):
                p["ids"].setdefault(key, r[f"id_{key}"])
    labels: dict[str, dict[str, str]] = {}
    aliases: dict[str, list[str]] = {}
    for r in names:
        qid = _qid(r["p"])
        if r["kind"] == "label":
            labels.setdefault(qid, {})[r.get("lang", "")] = r["n"]
        else:
            aliases.setdefault(qid, []).append(r["n"])
    current_teams: dict[str, list[tuple[str, str, bool]]] = {}
    for r in teams:
        label = r.get("teamLabel") or ""
        if not label or YOUTH.search(label) or COLLEGE.search(label) or (sport == "basketball" and label.lower().endswith(" football")):
            continue
        national = bool(NATIONAL.search(label))
        current_teams.setdefault(_qid(r["p"]), []).append((r.get("start") or "", team_name(label), national))

    rows = []
    for qid, p in people.items():
        by_lang = labels.get(qid, {})
        name = next((by_lang[lang] for lang in LABEL_LANGS if lang in by_lang), None)
        if not name or p["dead"] or not p["ids"] or not words(name):
            continue
        folded = " ".join(words(name))
        extra = [a for a in dict.fromkeys(aliases.get(qid, [])) if " ".join(words(a)) not in ("", folded)][:MAX_ALIASES]
        stints = sorted(current_teams.get(qid, []), key=lambda t: t[0], reverse=True)
        clubs = [t[1] for t in stints if not t[2]]
        nations = [t[1] for t in stints if t[2]]
        team_list = list(dict.fromkeys([*clubs[:1], *nations[:1]] if sport != "cricket" else [*nations[:1], *clubs[:1]]))
        birth: date | None = p.get("birth")
        age = (today - birth).days // 365 if birth else None
        latest_start = _date(stints[0][0]) if stints and stints[0][0] else None
        if sport in TEAM_SPORTS:
            current = bool(clubs or nations) and ((latest_start is not None and latest_start.year >= today.year - 10) or (age is not None and age <= 38))
            current = current or (age is not None and age <= 30)
        elif sport == "tennis":
            current = age is None or age <= 40
        else:
            current = age is None or age <= 75
        league = None
        if sport == "basketball":
            league = "wnba" if ("wnba" in p["ids"] or "bref_wnba" in p["ids"]) else "nba"
        elif sport == "tennis":
            league = "wta" if "wta" in p["ids"] else "atp"
        rows.append({
            "qid": qid,
            "sport": sport,
            "name": name[:200],
            "aliases": extra,
            "search_text": search_text(name, extra),
            "birth_date": birth,
            "country": (p["countries"] or [None])[0],
            "teams": team_list,
            "league": league,
            "title": p.get("title"),
            "ids": p["ids"],
            "image": (p.get("image") or "")[:300] or None,
            "sitelinks": p.get("sitelinks", 0),
            "current": current,
        })
    return rows


# ---------------------------------------------------------------- import


def upsert(db: Session, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    stmt = insert(Athlete).values(rows)
    ex = stmt.excluded
    db.execute(stmt.on_conflict_do_update(
        index_elements=["qid", "sport"],
        set_={
            "name": ex.name, "aliases": ex.aliases, "search_text": ex.search_text, "birth_date": ex.birth_date,
            "country": ex.country, "teams": ex.teams, "league": ex.league, "title": ex.title,
            # Ids learned from the live sources (resolve) are kept; Wikidata's own overwrite theirs.
            "ids": Athlete.ids.op("||")(ex.ids),
            "image": ex.image, "sitelinks": ex.sitelinks, "current": ex.current, "updated_at": func.now(),
        },
    ))


def import_sport(sport: str, new_only: bool = False) -> int:
    """Import one sport. new_only reads just the people not in the table yet (after adding an id
    property, say), in minutes instead of an hour; the weekly run refreshes everyone."""
    started = datetime.now(timezone.utc)
    qids = members(sport)
    log.info("registry %s: %d people on Wikidata", sport, len(qids))
    if new_only:
        with session() as db:
            have = set(db.scalars(select(Athlete.qid).where(Athlete.sport == sport)))
        qids = [q for q in qids if q not in have]
        log.info("registry %s: %d of them new", sport, len(qids))
    complete, total = True, 0
    for i in range(0, len(qids), BATCH):
        batch = qids[i : i + BATCH]
        try:
            rows = build(sport, *fetch(sport, batch))
        except Exception as exc:  # one bad batch shouldn't lose the rest of the run
            log.warning("registry %s: batch %d failed: %s", sport, i // BATCH + 1, exc)
            complete = False
            continue
        with session() as db:
            upsert(db, rows)
        total += len(rows)
        log.info("registry %s: %d/%d people read, %d athletes kept", sport, min(i + BATCH, len(qids)), len(qids), total)
    if complete and not new_only:
        # Merged, deleted or no-longer-matching items drop out; a partial run keeps everything.
        with session() as db:
            gone = db.execute(delete(Athlete).where(Athlete.sport == sport, Athlete.updated_at < started)).rowcount
        if gone:
            log.info("registry %s: %d athletes no longer on Wikidata removed", sport, gone)
    link_players(sport)
    return total


def import_all(sports: Iterable[str] = SPORTS, new_only: bool = False) -> dict[str, int]:
    return {sport: import_sport(sport, new_only) for sport in sports}


# ---------------------------------------------------------------- search


def score():
    """Popularity for ranking: Wikipedia editions, doubled for players of today, so a star still
    playing beats a retired one and a famous name still beats an obscure current one."""
    return Athlete.sitelinks * case((Athlete.current, 2), else_=1)


def find(db: Session, query: str, sport: str | None = None, limit: int = 20) -> list[Athlete]:
    """Athletes whose name or an alias has a word starting with every typed word: whole-name matches
    first, then the rest by popularity (score)."""
    typed = words(query)
    if not typed or len(" ".join(typed)) < 2:
        return []
    starts = [Athlete.search_text.like(f"% {w}%") for w in typed]
    base = select(Athlete).where(*starts)
    if sport:
        base = base.where(Athlete.sport == sport)
    exact = db.scalars(base.where(Athlete.search_text.like(f"% {' '.join(typed)} |%")).order_by(Athlete.sitelinks.desc()).limit(10)).all()
    popular = db.scalars(base.order_by(score().desc()).limit(limit)).all()
    return list({a.id: a for a in [*exact, *popular]}.values())


def near(db: Session, query: str, sport: str | None = None, limit: int = 10) -> list[Athlete]:
    """Athletes whose name is a few letters away from the query ("catalin clark" -> Caitlin Clark)."""
    folded = " ".join(words(query))
    if len(folded) < 4:
        return []
    db.execute(text("SET LOCAL pg_trgm.word_similarity_threshold = 0.5"))
    stmt = select(Athlete).where(literal(folded).op("<%")(Athlete.search_text))
    if sport:
        stmt = stmt.where(Athlete.sport == sport)
    return list(db.scalars(stmt.order_by(func.word_similarity(folded, Athlete.search_text).desc(), Athlete.sitelinks.desc()).limit(limit)))


def get(db: Session, qid: str, sport: str | None = None) -> Athlete | None:
    stmt = select(Athlete).where(Athlete.qid == qid)
    if sport:
        stmt = stmt.where(Athlete.sport == sport)
    return db.scalars(stmt.order_by(Athlete.sitelinks.desc()).limit(1)).first()


# ---------------------------------------------------------------- the registry and the live sources


@dataclass
class Person:
    """What resolve needs of an athlete row, detached from any session."""

    qid: str
    sport: str
    name: str
    aliases: list[str]
    birth_date: date | None
    teams: list[str]
    league: str | None
    ids: dict[str, str]

    @classmethod
    def of(cls, a: Athlete) -> "Person":
        return cls(a.qid, a.sport, a.name, list(a.aliases or []), a.birth_date, list(a.teams or []), a.league, dict(a.ids or {}))


def same_day(born: date | None, source: str | None) -> bool | None:
    """Whether a Wikidata birth date and a source's agree; None if either is missing. ESPN writes
    day/month/year, but a day of 12 or less could be either way round, so both readings count."""
    if born is None or not source:
        return None
    try:
        y, m, d = (int(x) for x in source.split("-"))
    except ValueError:
        return None
    return (y, m, d) == (born.year, born.month, born.day) or (y, d, m) == (born.year, born.month, born.day)


def same_person(person: Person, ref: PlayerRef) -> bool:
    """Is this source athlete the registry's person? The birth date decides when both know it;
    otherwise the whole name must match and, in team sports, a team too."""
    agree = same_day(person.birth_date, ref.born)
    if agree is not None:
        return agree
    if sorted(words(ref.name)) not in [sorted(words(n)) for n in [person.name, *person.aliases]]:
        return False
    if person.sport in TEAM_SPORTS and person.teams:
        return any(espn.team_key(a) in espn.team_key(b) or espn.team_key(b) in espn.team_key(a) for a in person.teams for b in ref.team_names if a and b)
    return True


def live_system(sport: str | None) -> str | None:
    """The source this build reads a sport's live data from ("sportmonks_football", "espn_cricket"),
    or None when the sport has none running."""
    adapter = for_sport(sport)
    return adapter.system if adapter else None


def sport_of(system: str) -> str | None:
    """The registry sport an adapter serves ("football" for both football sources)."""
    adapter = ADAPTERS.get(system)
    return next((s for s in SPORTS if adapter and s in adapter.sports), None)


def _candidates(adapter: Adapter, name: str, league: str | None, tried: set[str]) -> Iterable[PlayerRef]:
    """Source athletes a name could mean, surname first: the source's own search where it has one
    (Sportmonks), else ESPN's search read back through the adapter."""
    search = getattr(adapter, "search", None)
    if search is not None:
        for ref in search(name)[:6]:
            if ref.athlete_id not in tried and espn.names_match(name, ref.name):
                tried.add(ref.athlete_id)
                yield ref
        return
    uid = ESPN_UIDS.get(adapter.system)
    if uid is None:
        return  # Lichess is found by the FIDE id Wikidata has
    for hit in espn.search_athletes(name, uid)[:6]:
        if hit["athlete_id"] in tried or not espn.names_match(name, hit["name"]):
            continue
        tried.add(hit["athlete_id"])
        try:
            ref = adapter.player(hit["athlete_id"], league)
        except AdapterError:
            continue
        if ref:
            yield ref


def resolve(person: Person) -> tuple[Adapter, PlayerRef] | None:
    """The person in their sport's live source: by the id Wikidata has, or by name and birth date.
    A found id is written back to the registry, so the next lookup is by id."""
    adapter = for_sport(person.sport)
    if adapter is None:
        return None
    live = adapter.system
    if person.ids.get(live):
        ref = adapter.player(person.ids[live], person.league)
        return (adapter, ref) if ref else None
    tried: set[str] = set()
    for name in [person.name, *person.aliases[:2]]:
        for ref in _candidates(adapter, name, person.league, tried):
            if same_person(person, ref):
                learn(person.qid, person.sport, live, ref.athlete_id)
                return adapter, ref
    return None


def learn(qid: str, sport: str, system: str, athlete_id: str) -> None:
    with session() as db:
        db.execute(update(Athlete).where(Athlete.qid == qid, Athlete.sport == sport).values(ids=Athlete.ids.op("||")(func.jsonb_build_object(system, athlete_id))))


def link(db: Session, player_id: int, qid: str) -> None:
    """Record that a player is this registry athlete (a "wikidata" identity), unless either is taken."""
    taken = db.scalar(select(PlayerIdentity.id).where(or_(
        (PlayerIdentity.system == "wikidata") & (PlayerIdentity.external_id == qid),
        (PlayerIdentity.system == "wikidata") & (PlayerIdentity.player_id == player_id),
    )).limit(1))
    if taken is None:
        db.add(PlayerIdentity(player_id=player_id, system="wikidata", external_id=qid, confidence=1.0, evidence_url=f"https://www.wikidata.org/wiki/{qid}"))
        db.flush()


def link_players(sport: str | None = None) -> int:
    """Tie players already followed to their registry athlete by a source id both know, or, for a
    bound player the registry has no id for, by name and a matching birth date from the source."""
    linked = 0
    systems = [s for s in ([live_system(sport)] if sport else list(ADAPTERS)) if s]
    with session() as db:
        bound = db.execute(
            select(PlayerIdentity.player_id, PlayerIdentity.system, PlayerIdentity.external_id)
            .where(PlayerIdentity.system.in_(systems))
            .where(~PlayerIdentity.player_id.in_(select(PlayerIdentity.player_id).where(PlayerIdentity.system == "wikidata")))
        ).all()
    for player_id, system, external_id in bound:
        sport = sport_of(system)
        if sport is None:
            continue
        with session() as db:
            athlete = db.scalars(select(Athlete).where(Athlete.sport == sport, Athlete.ids.contains({system: external_id})).limit(1)).first()
            if athlete is None:
                ref = _source_ref(system, external_id)
                if ref is None:
                    continue
                key = " ".join(words(ref.name))
                named = db.scalars(select(Athlete).where(Athlete.sport == sport, Athlete.search_text.like(f"% {key} |%")).limit(5)).all()
                matches = [a for a in named if same_day(a.birth_date, ref.born)]
                if len(matches) != 1:
                    continue
                athlete = matches[0]
                athlete.ids = {**(athlete.ids or {}), system: external_id}
            link(db, player_id, athlete.qid)
            linked += 1
    if linked:
        log.info("registry: %d followed players linked to their Wikidata athlete", linked)
    return linked


def _source_ref(system: str, athlete_id: str) -> PlayerRef | None:
    try:
        return ADAPTERS[system].player(athlete_id)
    except AdapterError:
        return None


def describe(player_id: int) -> str | None:
    """Who a player linked to the registry is, for the research agent when no live source knows them."""
    with session() as db:
        qid = db.scalar(select(PlayerIdentity.external_id).where(PlayerIdentity.player_id == player_id, PlayerIdentity.system == "wikidata"))
        a = get(db, qid) if qid else None
        if a is None:
            return None
        facts = [a.sport, *(a.teams or []), f"born {a.birth_date.isoformat()}" if a.birth_date else "", a.country or ""]
        return f"{a.name}, {', '.join(f for f in facts if f)}. Wikidata: https://www.wikidata.org/wiki/{a.qid}. Other athletes may share the name; report only on this one."


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    new_only = "--new" in sys.argv
    wanted = [a for a in sys.argv[1:] if a != "--new"] or list(SPORTS)
    unknown = [s for s in wanted if s not in SPORTS]
    if unknown:
        sys.exit(f"unknown sport(s): {', '.join(unknown)}; choose from {', '.join(SPORTS)}")
    print(import_all(wanted, new_only))
