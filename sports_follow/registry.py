"""The player registry, imported from Wikidata (design canvas: "Player registry").

Wikidata (public domain, CC0) knows most professional athletes in the covered sports and their ids
in other databases: ESPNcricinfo for cricket, FIDE for chess, ESPN for NBA and tennis players,
Transfermarkt (and sometimes ESPN) for footballers. The importer copies every one of them into the
`athlete` table once a week, and search answers from there before asking any live source.

An athlete whose live-source id Wikidata lacks (most footballers have only a Transfermarkt id) is
looked up in the live source by name when someone follows or previews them, and accepted only if the
names and the birth date agree (resolve); the id found is kept, so it's a lookup by id from then on.

    .venv/bin/python -m sports_follow.registry            # import every sport, smallest first
    .venv/bin/python -m sports_follow.registry chess      # one sport
    .venv/bin/python -m sports_follow.registry --new      # only people not imported yet
    .venv/bin/python -m sports_follow.registry esports    # every game's pros, from their sources, then their photos
    .venv/bin/python -m sports_follow.registry valorant   # one game's

The query service allows a client about a minute of query time a minute, so batches run one at a
time: chess, basketball and tennis take a few minutes each, cricket ten, football about an hour. A
run, by hand or the worker's weekly one, holds the "registry-import" lock, so two never overlap; a
sport (or esports source) that fails keeps last week's rows, and the rest still run.

Esports games come from their sources instead (import_rosters): Wikidata has a Liquipedia id for only
2,844 people. Each esports source with a roster lists its pros (team titles from PandaScore, battle royale
from Liquipedia and PUBG), each under their game's sport ("cs2", "bgmi"), keyed by the source
("pandascore:<id>", "lp:<wiki>:<page id>") where the others hold a Wikidata id. Each carries its source's
id, so following one binds that source by id like any other. A source is read once for every game of it
the run imports. After each import, roster_wikidata.py finds each pro's Wikidata person, where it can tell
for sure, for their photo; a source's roster never overwrites the photo it found.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable
from urllib.parse import unquote

import httpx
from sqlalchemy import case, delete, func, literal, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import bus, logs, naming, sports
from .adapters import ADAPTERS, ALL, KNOWN, Adapter, AdapterError, PlayerRef, Rostered, budget, for_sport, pick
from .config import SOURCES, USER_AGENT
from .db import session
from .models import Athlete, AthleteOverride, Player, PlayerIdentity

log = logging.getLogger("sports_follow.registry")

WDQS = "https://query.wikidata.org/sparql"
BATCH = 2000
NAMESAKES = 40  # athletes with exactly the typed name that search lists, all for the picker (the most is 30)
LABEL_LANGS = ("en", "mul", "es", "pt", "fr", "de", "it", "nl")
# Aliases and the names of teams and other items: English, then "mul", the name every language shares, where
# Wikidata keeps most people's other names now ("Leo Messi", "MSD") and many clubs' names.
NAME_LANGS = ("en", "mul")
# A star has many (MS Dhoni 18, 14 that search tells apart): room for every name fans type ("MSD" is Wikidata's
# tenth of his). search_text's only index is a trigram one (GIN), with no limit on a row's length.
MAX_ALIASES = 16

# The sports this build runs that are imported from Wikidata (sports.Sport.wikidata: who counts as one of their athletes, and the
# ids kept), smallest first, so a first import has most sports searchable long before football is done;
# then those whose athletes come from their sources' own lists (sports.Sport.roster: the esports games).
def importable() -> list[str]:
    on = [s for s in sports.ENABLED if s.wikidata or s.roster]
    return [s.key for s in sorted(on, key=lambda s: (s.roster, s.athletes))]


# Country names as fans say them.
COUNTRIES = {"People's Republic of China": "China", "United States of America": "United States", "Kingdom of the Netherlands": "Netherlands", "Republic of Ireland": "Ireland", "Czech Republic": "Czechia"}
# Youth and age-group sides aren't who a fan follows a player for.
YOUTH = re.compile(r"\b(under-?\d+|u-?\d{2}|youth|olympic|academy|reserves?|b team|ii)\b", re.I)
# College sides ("Baylor Bears women's basketball"): college players are hidden, and a pro's college
# team isn't their team.
COLLEGE = re.compile(r"(men's|women's) (basketball|soccer|football|cricket)$|\b(college|university|ncaa)\b", re.I)
NATIONAL = re.compile(r"\s+(men's |women's )?national (men's |women's )?(association )?(football|cricket|basketball|soccer) team$", re.I)
# The ICC's members, whose sides Wikidata often names without "national": "Ireland cricket team", "West
# Indies women's cricket team". A domestic side named the same way ("Tasmania cricket team") is a club.
ICC_MEMBERS = (
    "Afghanistan", "Australia", "Bangladesh", "England", "India", "Ireland", "New Zealand", "Pakistan", "South Africa", "Sri Lanka", "West Indies", "Zimbabwe",
    "Argentina", "Austria", "Bahamas", "Bahrain", "Belgium", "Belize", "Bermuda", "Bhutan", "Botswana", "Brazil", "Bulgaria", "Cambodia", "Cameroon",
    "Canada", "Cayman Islands", "Chile", "China", "Cook Islands", "Costa Rica", "Croatia", "Cyprus", "Czech Republic", "Czechia", "Denmark", "Estonia",
    "Eswatini", "Fiji", "Finland", "France", "Gambia", "Germany", "Ghana", "Gibraltar", "Greece", "Guernsey", "Hong Kong", "Hungary", "Indonesia", "Iran",
    "Isle of Man", "Israel", "Italy", "Ivory Coast", "Japan", "Jersey", "Kenya", "Kuwait", "Lesotho", "Luxembourg", "Malawi", "Malaysia", "Maldives",
    "Mali", "Malta", "Mexico", "Mongolia", "Mozambique", "Myanmar", "Namibia", "Nepal", "Netherlands", "Nigeria", "Norway", "Oman", "Panama",
    "Papua New Guinea", "Peru", "Philippines", "Portugal", "Qatar", "Romania", "Rwanda", "Saint Helena", "Samoa", "Saudi Arabia", "Scotland", "Serbia",
    "Seychelles", "Sierra Leone", "Singapore", "Slovenia", "South Korea", "Spain", "Suriname", "Sweden", "Switzerland", "Tajikistan", "Tanzania",
    "Thailand", "Turkey", "Turks and Caicos Islands", "Uganda", "United Arab Emirates", "United States", "Uzbekistan", "Vanuatu",
)
ICC_SIDE = re.compile(rf"^({'|'.join(map(re.escape, ICC_MEMBERS))})\s+(men's |women's )?cricket team$", re.I)
# Wikidata's date precisions (wikibase:timePrecision): a date known to the year is stored as 1 January.
DAY, MONTH, YEAR = 11, 10, 9

_client = httpx.Client(headers={"user-agent": USER_AGENT, "accept": "application/sparql-results+json"}, timeout=90)


# ---------------------------------------------------------------- names as search sees them


def words(text: str) -> list[str]:
    """Plain lower-case ASCII words: "A'ja Wilson" -> ["aja", "wilson"], "Jokić" -> ["jokic"]."""
    return naming.words(text)


def search_text(name: str, aliases: Iterable[str]) -> str:
    """ " virat kohli | king kohli |": every word starts after a space and every name ends in " |",
    so "% koh%" finds a word start and named("virat kohli") a whole name."""
    names = [" ".join(words(n)) for n in [name, *aliases]]
    return " " + " | ".join(dict.fromkeys(n for n in names if n)) + " |"


def named(key: str):
    """Athletes with this whole name (folded words), as their name or an alias: " ronaldo |" and
    "| ronaldo |", never " cristiano ronaldo |"."""
    return or_(Athlete.search_text.like(f" {key} |%"), Athlete.search_text.like(f"%| {key} |%"))


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
    union = " UNION ".join(f"{{ ?p wdt:{prop} [] }}" for prop in sports.BY_KEY[sport].wikidata.values())
    return sorted({_qid(r["p"]) for r in sparql(f"SELECT DISTINCT ?p WHERE {{ {union} }}")})


def _values(qids: list[str]) -> str:
    return " ".join(f"wd:{q}" for q in qids)


def fetch(sport: str, qids: list[str]) -> tuple[list[dict], list[dict], list[dict]]:
    values = _values(qids)
    kind = sports.BY_KEY[sport]
    ids = " ".join(f"OPTIONAL {{ ?p wdt:{prop} ?id_{key} }}" for key, prop in kind.wikidata.items())
    title = f'OPTIONAL {{ ?p wdt:{kind.wikidata_title} ?t . ?t rdfs:label ?title FILTER(LANG(?title) = "en") }}' if kind.wikidata_title else ""
    core = sparql(f"""SELECT * WHERE {{
      VALUES ?p {{ {values} }}
      OPTIONAL {{ ?p wikibase:sitelinks ?sitelinks }}
      OPTIONAL {{ ?p p:P569 ?bst . ?bst a wikibase:BestRank ; psv:P569 [ wikibase:timeValue ?birth ; wikibase:timePrecision ?birth_precision ] }}
      OPTIONAL {{ ?p wdt:P570 ?death }}
      OPTIONAL {{ ?p wdt:P18 ?image }}
      OPTIONAL {{ ?p wdt:P1532 ?c1 . ?c1 rdfs:label ?sport_country FILTER(LANG(?sport_country) = "en") }}
      OPTIONAL {{ ?p wdt:P27 ?c2 . ?c2 rdfs:label ?citizenship FILTER(LANG(?citizenship) = "en") }}
      {title} {ids} }}""")
    langs = ", ".join(f'"{lang}"' for lang in LABEL_LANGS)
    named_in = ", ".join(f'"{lang}"' for lang in NAME_LANGS)
    names = sparql(f"""SELECT ?p ?n ?kind (LANG(?n) AS ?lang) WHERE {{
      VALUES ?p {{ {values} }}
      {{ ?p rdfs:label ?n FILTER(LANG(?n) IN ({langs})) BIND("label" AS ?kind) }}
      UNION {{ ?p skos:altLabel ?n FILTER(LANG(?n) IN ({named_in})) BIND("alias" AS ?kind) }} }}""")
    teams: list[dict] = []
    if kind.team:
        teams = sparql(f"""SELECT ?p ?team ?teamLabel ?start ?teamSport (LANG(?teamLabel) AS ?lang) WHERE {{
          VALUES ?p {{ {values} }}
          ?p p:P54 ?st . ?st ps:P54 ?team . FILTER NOT EXISTS {{ ?st pq:P582 [] }}
          OPTIONAL {{ ?st pq:P580 ?start }}
          OPTIONAL {{ ?team wdt:P641 ?teamSport }}
          ?team rdfs:label ?teamLabel FILTER(LANG(?teamLabel) IN ({named_in})) }}""")
    return core, names, teams


# ---------------------------------------------------------------- query rows -> athlete rows


def _date(value: str | None) -> date | None:
    if not value or value.startswith("-"):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def is_national(label: str) -> bool:
    return bool(NATIONAL.search(label) or ICC_SIDE.match(label))


def team_name(label: str) -> str:
    """"India national cricket team" and "Ireland cricket team" -> the country; club names stay as they are."""
    side = ICC_SIDE.match(label)
    return side.group(1) if side else NATIONAL.sub("", label).strip()


def keeps_team(kind: sports.Sport, label: str, plays: Iterable[str] = ()) -> bool:
    """Whether a team Wikidata lists for one of a sport's athletes is one the app shows: not a youth, reserve
    or college side, and one of the sport's: by its name (Sport.team_ok), and not one Wikidata says plays another
    of the app's sports and not this one, by the sports it names for the team (P641: `plays`, their items)."""
    plays = set(plays)
    others = {item for s in sports.ALL if s is not kind for item in s.wikidata_sport}
    elsewhere = bool(plays & others) and not plays & set(kind.wikidata_sport)
    return bool(label) and not YOUTH.search(label) and not COLLEGE.search(label) and kind.team_ok(label) and not elsewhere


def team_list(kind: sports.Sport, stints: list[tuple[str, str, bool]]) -> list[str]:
    """An athlete's teams as the app shows them, from their current stints (start, name, national): the newest
    club and the newest national side, the country first in a sport that puts it first (cricket)."""
    stints = sorted(stints, key=lambda t: t[0], reverse=True)
    clubs = [t[1] for t in stints if not t[2]]
    nations = [t[1] for t in stints if t[2]]
    return list(dict.fromkeys([*nations[:1], *clubs[:1]] if kind.nation_first else [*clubs[:1], *nations[:1]]))


def build(sport: str, core: list[dict], names: list[dict], teams: list[dict], today: date | None = None) -> list[dict[str, Any]]:
    """Athlete rows from one batch's query rows. Pure, so it's tested without the network.

    The dead are left out, as is anyone with no name in a Latin-script language we read.
    """
    today = today or datetime.now(timezone.utc).date()
    kind = sports.BY_KEY[sport]
    people: dict[str, dict[str, Any]] = {}
    for r in core:
        p = people.setdefault(_qid(r["p"]), {"ids": {}, "countries": [], "dead": False})
        p["dead"] = p["dead"] or bool(r.get("death"))
        birth, known = _date(r.get("birth")), int(r["birth_precision"]) if r.get("birth_precision") else None
        if birth and known is not None and known < YEAR:
            birth = known = None  # a decade or a century says nothing about which person this is
        if birth and (p.get("birth") is None or (known or 0) > (p.get("birth_precision") or 0)):
            # Two dates for one person (Pau Sans: "2004", and 24 November 2004): the more precise one.
            p["birth"], p["birth_precision"] = birth, known
        p["sitelinks"] = max(p.get("sitelinks", 0), int(r.get("sitelinks") or 0))
        if r.get("image") and not p.get("image"):
            # The query service gives a Special:FilePath URL: the file name, percent-encoded.
            p["image"] = unquote(r["image"].rsplit("/", 1)[-1])
        for key in ("sport_country", "citizenship"):
            country = COUNTRIES.get(r.get(key) or "", r.get(key))
            if country and country not in p["countries"]:
                # The country they play for first, then citizenship.
                p["countries"].insert(0 if key == "sport_country" else len(p["countries"]), country)
        title = kind.title_of(r.get("title") or "")
        if title and (not p.get("title") or kind.better_title(title, p["title"])):
            p["title"] = title
        for key in kind.wikidata:
            if r.get(f"id_{key}"):
                p["ids"].setdefault(key, r[f"id_{key}"])
    labels: dict[str, dict[str, str]] = {}
    aliases: dict[str, list[tuple[str, str]]] = {}
    for r in names:
        qid = _qid(r["p"])
        if r["kind"] == "label":
            labels.setdefault(qid, {})[r.get("lang", "")] = r["n"]
        else:
            aliases.setdefault(qid, []).append((r.get("lang") or "en", r["n"]))
    # A team named in English and in "mul" is one team, by its English name; with each sport Wikidata says it plays.
    team_labels: dict[tuple[str, str, str], dict[str, str]] = {}
    team_sports: dict[tuple[str, str, str], set[str]] = {}
    for r in teams:
        stint = (_qid(r["p"]), r.get("team") or r.get("teamLabel") or "", r.get("start") or "")
        team_labels.setdefault(stint, {})[r.get("lang") or "en"] = r.get("teamLabel") or ""
        if r.get("teamSport"):
            team_sports.setdefault(stint, set()).add(_qid(r["teamSport"]))
    current_teams: dict[str, list[tuple[str, str, bool]]] = {}
    for (qid, team, start), by_lang in team_labels.items():
        label = next((by_lang[lang] for lang in NAME_LANGS if lang in by_lang), "")
        if not keeps_team(kind, label, team_sports.get((qid, team, start), ())):
            continue
        current_teams.setdefault(qid, []).append((start, team_name(label), is_national(label)))

    rows = []
    for qid, p in people.items():
        by_lang = labels.get(qid, {})
        name = next((by_lang[lang] for lang in LABEL_LANGS if lang in by_lang), None)
        if not name or p["dead"] or not p["ids"] or not words(name):
            continue
        folded = " ".join(words(name))
        # English aliases first, then "mul"'s; one that search can't tell from one already kept ("M. S. Dhoni"
        # after "M.S. Dhoni") takes no place.
        spelled: dict[str, str] = {}
        for _, alias in sorted(aliases.get(qid, []), key=lambda la: NAME_LANGS.index(la[0]) if la[0] in NAME_LANGS else len(NAME_LANGS)):
            spelled.setdefault(" ".join(words(alias)), alias)
        extra = [a for f, a in spelled.items() if f not in ("", folded)][:MAX_ALIASES]
        stints = sorted(current_teams.get(qid, []), key=lambda t: t[0], reverse=True)
        birth: date | None = p.get("birth")
        age = (today - birth).days // 365 if birth else None
        latest_start = _date(stints[0][0]) if stints and stints[0][0] else None
        current = kind.current(age, bool(stints), latest_start, today)
        league = kind.league_of(p["ids"])
        rows.append({
            "qid": qid,
            "sport": sport,
            "name": name[:200],
            "aliases": extra,
            "search_text": search_text(name, extra),
            "birth_date": birth,
            "birth_precision": p.get("birth_precision") if birth else None,
            "country": (p["countries"] or [None])[0],
            "teams": team_list(kind, stints),
            "league": league,
            "title": p.get("title"),
            "ids": p["ids"],
            "image": (p.get("image") or "")[:300] or None,
            "sitelinks": p.get("sitelinks", 0),
            "current": current,
        })
    return rows


# ---------------------------------------------------------------- import


def upsert(db: Session, rows: list[dict[str, Any]], photos: bool = True) -> None:
    """Write athlete rows, new or refreshed. An athlete an operator removed (players.py) is never written again.
    photos=False: the rows don't say who has a photo (a source's roster), so a refreshed row keeps the one
    it has (roster_wikidata.py found it)."""
    if not rows:
        return
    wanted = {r["sport"] for r in rows}
    wanted |= {sports.of(k).group for k in wanted} - {""}  # removed under a group ("esports"): from every game
    removed = set(db.execute(select(AthleteOverride.qid, AthleteOverride.sport).where(AthleteOverride.action == "remove", AthleteOverride.sport.in_(wanted))).all())
    rows = [r for r in rows if (r["qid"], r["sport"]) not in removed and (r["qid"], sports.of(r["sport"]).group) not in removed]
    if not rows:
        return
    stmt = insert(Athlete).values(rows)
    ex = stmt.excluded
    db.execute(stmt.on_conflict_do_update(
        index_elements=["qid", "sport"],
        set_={
            "name": ex.name, "aliases": ex.aliases, "search_text": ex.search_text, "birth_date": ex.birth_date, "birth_precision": ex.birth_precision,
            "country": ex.country, "teams": ex.teams, "league": ex.league, "title": ex.title,
            # Ids learned from the live sources (resolve) are kept; Wikidata's own overwrite theirs.
            "ids": Athlete.ids.op("||")(ex.ids),
            "image": ex.image if photos else Athlete.image, "sitelinks": ex.sitelinks, "current": ex.current, "updated_at": func.now(),
        },
    ))


def blocked(db: Session) -> set[tuple[str, str]]:
    """The source ids of every athlete an operator removed (players.py), as (system, id): search leaves
    them out and following refuses them, so a source's own search can't bring them back. Removing one finds
    them in every source of their sport this build may run (everywhere), not only the one followed."""
    return {(system, str(xid)) for ids in db.scalars(select(AthleteOverride.ids).where(AthleteOverride.action == "remove")) for system, xid in (ids or {}).items()}


def removed(db: Session, sport: str | None, ref: PlayerRef) -> bool:
    """Whether a source's athlete, in this sport, is one an operator removed (players.py): by their id in that
    source (blocked), or, in a source that had no id for them then (one switched to or added since, or one that
    didn't answer), by the same name and birth date: as far as the registry knew the date (a real New Year's Day
    is that day), and a date it knew only to the month or year by their whole name, in any order ("Ahmed Hassan",
    not every Ahmed Hassan Mahgoub born that year, nor the one-name "Ali" for Mohammad Ali). A pick with no birth
    date isn't taken for anyone: a namesake can still be followed."""
    if (ref.system, str(ref.athlete_id)) in blocked(db):
        return True
    if not ref.born:
        return False
    key = sports.family(sport)
    within = [key, *([group] if (group := sports.of(key).group) else [])]  # removed as "esports": every game's
    gone = db.execute(select(AthleteOverride.name, AthleteOverride.birth_date, AthleteOverride.birth_precision).where(
        AthleteOverride.action == "remove", AthleteOverride.sport.in_(within), AthleteOverride.birth_date.is_not(None))).all()
    picked = sorted(words(ref.name))
    return any(same_day(born, ref.born, known) and (naming.names_match(name, ref.name) if date_precision(born, known) == DAY else sorted(words(name)) == picked)
               for name, born, known in gone)  # fmt: skip


def db_now() -> datetime:
    """Now by the database's clock, which stamps every row's updated_at: a run's start compared with them
    must be on the same clock (the app's may be a little ahead of the database's in another VM or host)."""
    with session() as db:
        return db.scalar(select(func.now()))


def operator_kept(*keys: str) -> Any:
    """The athletes of these sports an operator keeps (players.py: added by hand, or pinned), or of their
    group (kept as "esports", from before each game was a sport): never dropped by an import."""
    within = {*keys, *(sports.of(k).group for k in keys)} - {""}
    return select(AthleteOverride.qid).where(AthleteOverride.sport.in_(within), AthleteOverride.action == "keep")


def too_many_gone(leaving: int, kept: int) -> bool:
    """Whether a weekly run would remove more of a sport than it kept: athletes retire or merge a few at a
    time, so that is an answer cut short."""
    return leaving > kept


def import_sport(sport: str, new_only: bool = False) -> int:
    """Import one sport. new_only reads just the people not in the table yet (after adding an id
    property, say), in minutes instead of an hour; the weekly run refreshes everyone."""
    qids = members(sport)
    log.info("registry %s: %d people on Wikidata", sport, len(qids))
    if not qids:
        # No sport has nobody: an empty answer is a failed query, and the run below would delete the sport.
        raise RuntimeError(f"Wikidata listed no one in {sport}; last week's rows are kept")
    started = db_now()  # before the first row is written: every row this run writes is newer
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
        # Merged, deleted or no-longer-matching items drop out; a partial run keeps everything, and so does
        # one that would remove more of the sport than it kept (a cut-short answer, not a week's churn).
        with session() as db:
            stale = (Athlete.sport == sport, Athlete.updated_at < started, Athlete.qid.not_in(operator_kept(sport)))
            leaving = db.scalar(select(func.count()).select_from(Athlete).where(*stale)) or 0
            if too_many_gone(leaving, total):
                log.warning("registry %s: this run kept %d and would remove %d; nothing removed", sport, total, leaving)
                leaving = 0
            elif leaving:
                db.execute(delete(Athlete).where(*stale))
        if leaving:
            log.info("registry %s: %d athletes no longer on Wikidata removed", sport, leaving)
    link_players(sport)
    return total


def import_all(keys: Iterable[str] | None = None, new_only: bool = False) -> dict[str, int | None]:
    """Import each sport in turn (all of importable() unless given), then the sports whose athletes come from
    their sources' own lists all at once (each source read once for all of its sports): athletes kept per
    sport, None for one that failed. A sport that fails keeps last week's rows, and the others still run."""
    done: dict[str, int | None] = {}
    wanted = importable() if keys is None else list(keys)
    for sport in [k for k in wanted if not sports.BY_KEY[k].roster]:
        try:
            done[sport] = import_sport(sport, new_only)
        except Exception:
            log.exception("registry %s: import failed; last week's rows kept", sport)
            done[sport] = None
    rostered = [k for k in wanted if sports.BY_KEY[k].roster]
    if rostered:
        try:
            done |= import_rosters(rostered)
        except Exception:
            log.exception("registry %s: import failed; last week's rows kept", ", ".join(rostered))
            done |= dict.fromkeys(rostered, None)
        find_people(rostered)  # last week's rows too, when a source failed
    failed = [s for s, n in done.items() if n is None]
    if failed:
        log.warning("registry import: %s; %s failed", done, ", ".join(failed))
    else:
        log.info("registry import: %s", done)
    return done


def find_people(keys: list[str]) -> None:
    """Each of these roster sports' athletes' Wikidata person and photo, where it can tell for sure
    (roster_wikidata.py), a group's sports together (every esports game: one read of Wikidata). Wikidata not
    answering keeps last week's, and the import still counts."""
    from . import roster_wikidata  # it reads Wikidata through this module

    for kind, within in roster_wikidata.by_group(keys):
        try:
            roster_wikidata.run(kind, within)
        except Exception:
            log.exception("registry %s: matching athletes to Wikidata failed; last week's matches kept", kind.key)


LOCK = "registry-import"


def import_locked(keys: Iterable[str] | None = None, new_only: bool = False) -> dict[str, int | None] | None:
    """import_all under the lock every run takes, so a run by hand and the weekly one never overlap.
    None if another run holds it."""
    token = bus.try_lock(LOCK, 6 * 3600)
    if token is None:
        return None
    try:
        return import_all(keys, new_only)
    finally:
        bus.release(LOCK, token)


# ---------------------------------------------------------------- athletes from the sources' own lists (esports)


def roster_rows(adapter: Adapter, entries: list[Rostered], wanted: set[str] | None = None) -> list[dict[str, Any]]:
    """Athlete rows from a source's roster (pure), each in the sport the source has them in (an esports pro's
    game: Adapter.sport_of), of the sports wanted (all by default). Sources have no popularity signal, so
    sitelinks stay 0."""
    rows = []
    for e in entries:
        ref = e.ref
        sport = adapter.sport_of(ref)
        if not words(ref.name) or (wanted is not None and sport not in wanted):
            continue
        aliases = [n for n in dict.fromkeys(e.aliases) if n and words(n) and words(n) != words(ref.name)][:MAX_ALIASES]
        rows.append({
            "qid": e.key,
            "sport": sport,
            "name": ref.name[:200],
            "aliases": aliases,
            "search_text": search_text(ref.name, aliases),
            "birth_date": _date(ref.born),
            "country": e.country,
            "teams": ref.team_names,
            "league": ref.league,
            "title": None,
            "ids": {adapter.system: ref.athlete_id},
            "image": None,
            "sitelinks": 0,
            "current": e.current,
        })
    return rows


def import_roster(adapter: Adapter, wanted: Iterable[str] | None = None) -> dict[str, int]:
    """One source's athletes of these of its sports (those that run, by default) into the registry
    (adapter.roster): how many kept, by sport. After a complete run, its rows it didn't see again are
    dropped, in the sports and leagues it read; rows of leagues it didn't read are left alone, so a run of a
    few games is safe. A row not seen again goes after the run's grace, or at once if it holds an id of a
    shape the source no longer gives (id_pattern): it names the athlete as the source used to, and they're
    among this run's rows as it names them now."""
    within = set(adapter.running() if wanted is None else wanted) & set(adapter.sports)
    if not within:
        return {}
    started = db_now()
    kept: dict[str, int] = dict.fromkeys(sorted(within), 0)

    def keep(entries: list[Rostered]) -> int:
        rows = roster_rows(adapter, entries, within)
        with session() as db:
            upsert(db, rows, photos=False)
        for r in rows:
            kept[r["sport"]] += 1
        return sum(kept.values())

    run = adapter.roster(keep, within)
    named = ", ".join(sorted(within))
    prefix: str = getattr(adapter, "roster_prefix", "")
    if run.complete:
        unseen = [Athlete.sport.in_(within), Athlete.qid.like(f"{prefix}%"), Athlete.updated_at < started, Athlete.qid.not_in(operator_kept(*within))]
        if run.leagues is not None:
            unseen.append(Athlete.league.in_(run.leagues))
        with session() as db:
            renamed = [qid for qid, ids in db.execute(select(Athlete.qid, Athlete.ids).where(*unseen)) if (ids or {}).get(adapter.system) and not adapter.id_pattern.fullmatch(str(ids[adapter.system]))]
            gone = len(db.execute(delete(Athlete).where(*unseen, or_(Athlete.updated_at < started - run.grace, Athlete.qid.in_(renamed))).returning(Athlete.id)).all())
        if gone:
            log.info("registry %s: %d %s athletes no longer listed removed", named, gone, adapter.provider.name)
    log.info("registry %s: %s %s athletes kept", named, kept, adapter.provider.name)
    return kept


def import_rosters(keys: list[str]) -> dict[str, int | None]:
    """These sports' athletes from each of their sources with a roster (adapters.base.Adapter) that runs (the
    esports games' pros), each source read once for all of its sports: athletes kept by sport. A source that
    fails keeps its rows and the others still run; its sports are reported as failed (None)."""
    done: dict[str, int | None] = dict.fromkeys(keys, 0)
    for adapter in KNOWN.values():
        mine = [k for k in keys if k in adapter.sports]
        if not mine or not hasattr(adapter, "roster"):
            continue
        if adapter.system not in ADAPTERS:
            log.info("registry %s: %s isn't running (no key); skipped", ", ".join(mine), adapter.provider.name)
            continue
        try:
            for sport, n in import_roster(adapter, mine).items():
                if (before := done[sport]) is not None:
                    done[sport] = before + n
        except Exception:
            log.exception("registry %s: %s failed; its rows kept", ", ".join(mine), adapter.provider.name)
            done |= dict.fromkeys(mine, None)
    return done


# ---------------------------------------------------------------- search


def score():
    """Popularity for ranking: Wikipedia editions, doubled for players of today, so a star still
    playing beats a retired one and a famous name still beats an obscure current one."""
    return Athlete.sitelinks * case((Athlete.current, 2), else_=1)


def running(stmt: Any) -> Any:
    """Only athletes of the sports this build runs (every sport, unless SPORTS_FOLLOW_SPORTS names some), and
    of a group any of them is in (an esports pro whose game can't be told is "esports")."""
    if len(sports.ENABLED) == len(sports.ALL):
        return stmt
    return stmt.where(Athlete.sport.in_(sports.running_keys()))


def find(db: Session, query: str, sport: str | None = None, limit: int = 20) -> list[Athlete]:
    """Athletes whose name or an alias has a word starting with every typed word: every one with the
    whole name typed (up to NAMESAKES), then the rest by popularity (score), players of today first
    where that ties (esports rows have no popularity: a pro on a team comes before a teamless one with
    the same handle)."""
    typed = words(query)
    if not typed or len(" ".join(typed)) < 2:
        return []
    starts = [Athlete.search_text.like(f"% {w}%") for w in typed]
    base = running(select(Athlete).where(*starts))
    if sport:
        base = base.where(Athlete.sport.in_(sports.within(sport)))  # a group: any of its sports
    exact = db.scalars(base.where(named(" ".join(typed))).order_by(Athlete.sitelinks.desc(), Athlete.current.desc()).limit(NAMESAKES)).all()
    popular = db.scalars(base.order_by(score().desc(), Athlete.current.desc()).limit(limit)).all()
    return list({a.id: a for a in [*exact, *popular]}.values())


def near(db: Session, query: str, sport: str | None = None, limit: int = 10) -> list[Athlete]:
    """Athletes whose name is a few letters away from the query ("catalin clark" -> Caitlin Clark)."""
    folded = " ".join(words(query))
    if len(folded) < 4:
        return []
    db.execute(text("SET LOCAL pg_trgm.word_similarity_threshold = 0.5"))
    stmt = running(select(Athlete).where(literal(folded).op("<%")(Athlete.search_text)))
    if sport:
        stmt = stmt.where(Athlete.sport.in_(sports.within(sport)))  # a group: any of its sports
    return list(db.scalars(stmt.order_by(func.word_similarity(folded, Athlete.search_text).desc(), Athlete.sitelinks.desc()).limit(limit)))


def get(db: Session, qid: str, sport: str | None = None, only_running: bool = False) -> Athlete | None:
    """An athlete by registry id: in this sport, or the best known of their rows (a person in two sports is two);
    only_running: of the sports that run (running)."""
    stmt = select(Athlete).where(Athlete.qid == qid)
    if sport:
        stmt = stmt.where(Athlete.sport == sport)
    if only_running:
        stmt = running(stmt)
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
    birth_precision: int | None = None

    @classmethod
    def of(cls, a: Athlete) -> "Person":
        return cls(a.qid, a.sport, a.name, list(a.aliases or []), a.birth_date, list(a.teams or []), a.league, dict(a.ids or {}), a.birth_precision)


def date_precision(born: date | None, stored: int | None) -> int:
    """How much of a registry birth date to trust. Rows imported before the precision was read hold a
    year-only date as 1 January, so until the next import a 1 January counts as the year alone: a real
    New Year's birthday then matches anyone of that name born that year, where otherwise every
    footballer Wikidata knows only to the year couldn't be found at all."""
    if stored is not None:
        return stored
    return YEAR if born is not None and (born.month, born.day) == (1, 1) else DAY


def same_day(born: date | None, source: str | None, stored: int | None = None) -> bool | None:
    """Whether a Wikidata birth date and a source's agree, as far as Wikidata knows it (stored: its
    precision); None if either is missing. A source's date is ISO, its own order read by its adapter
    (ESPN's day/month/year: espn.birth_date), so a day and month the other way round is another day."""
    if born is None or not source:
        return None
    try:
        y, m, d = (int(x) for x in source.split("-"))
    except ValueError:
        return None
    known = date_precision(born, stored)
    if known <= YEAR:
        return y == born.year
    if known == MONTH:
        return (y, m) == (born.year, born.month)
    return (y, m, d) == (born.year, born.month, born.day)


def same_person(person: Person, ref: PlayerRef) -> bool:
    """Is this source athlete the registry's person? When both know the birth date, it must agree as far as
    Wikidata knows it, and the names match too: someone else born the same day isn't them. Otherwise the
    whole name must match and, in team sports, a team too."""
    agree = same_day(person.birth_date, ref.born, person.birth_precision)
    names = [person.name, *person.aliases]
    if agree is not None:
        return agree and any(naming.names_match(n, ref.name) for n in names)
    if sorted(words(ref.name)) not in [sorted(words(n)) for n in names]:
        return False
    if sports.of(person.sport).team and person.teams:
        return any(naming.team_key(a) in naming.team_key(b) or naming.team_key(b) in naming.team_key(a) for a in person.teams for b in ref.team_names if a and b)
    return True


def live_system(sport: str | None) -> str | None:
    """The source this build reads a sport's live data from ("sportmonks_football", "espn_cricket"),
    or None when the sport has none running."""
    adapter = for_sport(sport)
    return adapter.system if adapter else None


def follow_system(sport: str | None, ids: dict[str, str]) -> str | None:
    """The source an athlete with these source ids is followed through: their sport's live source, or,
    where a sport's sources sit side by side, each for its own games (esports), the one their row came from."""
    if sports.of(sport).side_by_side:
        return next((s for s in ids if s in ADAPTERS), None)
    return live_system(sport)


def find_in_source(person: Person) -> tuple[Adapter, PlayerRef] | None:
    """The person in the source they're followed through (follow_system): their sport's live source, by the
    id Wikidata has or by name and birth date (resolve, which writes a found id back), or a source side by
    side with it, by the id their row came with. None when no running source has them."""
    system = follow_system(person.sport, person.ids)
    if system is None:
        return None
    if system == live_system(person.sport):
        return resolve(person)
    adapter = ADAPTERS.get(system)  # None: turned off since follow_system looked (the admin panel)
    ref = adapter.player(person.ids[system], person.league) if adapter else None
    return (adapter, ref) if adapter and ref else None


def sport_of(system: str) -> str | None:
    """The Wikidata sport a running adapter serves ("football" for both football sources)."""
    adapter = ADAPTERS.get(system)
    wikidata = [s for s in adapter.sports if sports.of(s).wikidata] if adapter else []
    return wikidata[0] if len(wikidata) == 1 else None


def _candidates(adapter: Adapter, name: str, league: str | None, tried: set[str]) -> Iterable[PlayerRef]:
    """Source athletes a name could mean, from the source's own search by name (Sportmonks', or ESPN's
    read back through the adapter). A source without one (Lichess) is found by the id Wikidata has."""
    search = getattr(adapter, "search", None)
    if search is None:
        return
    for ref in search(name, league)[:6]:
        if ref.athlete_id not in tried and naming.names_match(name, ref.name):
            tried.add(ref.athlete_id)
            yield ref


def resolve(person: Person) -> tuple[Adapter, PlayerRef] | None:
    """The person in their sport's live source: by the id Wikidata has, or by name and birth date.
    A found id is written back to the registry, so the next lookup is by id."""
    adapter = for_sport(person.sport)
    if adapter is None:
        return None
    ref = _resolve_in(adapter, person)
    if ref is None:
        return None
    if not person.ids.get(adapter.system):
        learn(person.qid, person.sport, adapter.system, ref.athlete_id)
    return adapter, ref


def _resolve_in(adapter: Adapter, person: Person) -> PlayerRef | None:
    """The person in one source: by the id they have there, or by name and birth date (same_person)."""
    if person.ids.get(adapter.system):
        return adapter.player(person.ids[adapter.system], person.league)
    tried: set[str] = set()
    for name in [person.name, *person.aliases[:2]]:
        for ref in _candidates(adapter, name, person.league, tried):
            if same_person(person, ref):
                return ref
    return None


def everywhere(person: Person) -> dict[str, str]:
    """The person's ids in every source of their sport this build may run, running now or not (the admin panel
    turns one back on, and a sport's source can be switched): the ids they have, and in each source they have
    none in, the one its search finds by name and birth date. A source that doesn't answer is left out. Learns
    nothing, so it can run in the transaction that takes them out (players.remove)."""
    ids = dict(person.ids)
    for adapter in pick(ALL, SOURCES, sports.ALLOWED).values():
        if person.sport not in adapter.sports or adapter.system in ids:
            continue
        try:
            ref = _resolve_in(adapter, person)
        except Exception:  # one source's failure (down, its allowance spent, an answer it changed) never stops a removal
            log.warning("registry: %s (%s) couldn't be looked up in %s", person.name, person.sport, adapter.system, exc_info=True)
            continue
        if ref is not None:
            ids[adapter.system] = ref.athlete_id
    return ids


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
        # Two fans following the athlete at once both get here: the second's insert does nothing.
        db.execute(insert(PlayerIdentity).values(player_id=player_id, system="wikidata", external_id=qid, confidence=1.0, evidence_url=f"https://www.wikidata.org/wiki/{qid}").on_conflict_do_nothing())


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
                namesakes = db.scalars(select(Athlete).where(Athlete.sport == sport, named(key)).limit(5)).all()
                matches = [a for a in namesakes if same_day(a.birth_date, ref.born, a.birth_precision)]
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
    adapter = ADAPTERS.get(system)
    try:
        return adapter.player(athlete_id) if adapter else None
    except AdapterError:
        return None


def describe(player_id: int) -> str | None:
    """Who a player linked to the registry is, for the research agent when no live source knows them."""
    with session() as db:
        qid = db.scalar(select(PlayerIdentity.external_id).where(PlayerIdentity.player_id == player_id, PlayerIdentity.system == "wikidata"))
        sport = (db.scalar(select(Player.sport).where(Player.id == player_id)) or "").lower()
        # The row in the player's sport: Chahal the cricketer isn't to be researched as a chess player.
        a = (get(db, qid, sport) or get(db, qid)) if qid else None
        if a is None:
            return None
        facts = [a.sport, *(a.teams or []), f"born {a.birth_date.isoformat()}" if a.birth_date else "", a.country or ""]
        return f"{a.name}, {', '.join(f for f in facts if f)}. Wikidata: https://www.wikidata.org/wiki/{a.qid}. Other athletes may share the name; report only on this one."


def main(argv: list[str]) -> None:
    new_only = "--new" in argv
    every = importable()
    asked = [a for a in argv if a != "--new"]
    wanted = sports.expand(asked, among=every) or every  # "esports": every game that runs
    if asked and not sports.expand(asked, among=every):
        sys.exit(f"none of {', '.join(asked)} runs here; choose from {', '.join(every)}")
    unknown = [s for s in wanted if s not in every]
    if unknown:
        sys.exit(f"unknown sport(s): {', '.join(unknown)}; choose from {', '.join(every)}")
    with budget.in_lane("import"):
        done = import_locked(wanted, new_only)
    if done is None:
        sys.exit(f"another registry import is running (the worker's weekly one, or one started by hand); try again when it's done. "
                 f"If none is, the lock is left from one that was killed: it expires within six hours, or redis-cli del lock:{LOCK}")
    print(done)
    if None in done.values():
        sys.exit(1)


if __name__ == "__main__":
    logs.setup()
    main(sys.argv[1:])
