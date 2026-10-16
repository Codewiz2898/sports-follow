"""Athletes from the sources' own lists (esports pros) matched to their Wikidata person, for their photos.

The registry imports most sports from Wikidata, so each of their rows has the athlete's Wikidata id and
photo file (P18). Esports pros come from their sources instead (registry.import_rosters), keyed by the
source's id ("pandascore:20401"), with neither. This step finds each pro's Wikidata person and records
them in the pro's row: the Wikidata id in its ids ("wikidata": "Q113450352"), the photo file in its image.
The photos job then fetches the photos of those followed or offered as starters, as for any other athlete
(photos.py: on the profile only, recoloured, with a credit).

Who a pro could be: every living person the sport counts as one of its people on Wikidata
(Sport.people_ids and people_occupations: anyone with a Liquipedia, HLTV or Esports Earnings id, or a
professional gamer), a few thousand, read in a few queries one at a time. A pro is matched to one of them:

- by id, where Wikidata keeps the ids of the pro's source (Adapter.wikidata_property: Liquipedia's pages);
- by handle: the handle is theirs (a label, an alias, or a name their ids give, Sport.known_as: their
  Liquipedia page), and so is the real name or the birth date, and so is the game or a team;
- by real name and birth date to the day, both theirs.

Never by a handle alone: handles collide ("Faker" is also a band; "Zeus" a god, a rapper and two pros). A
pro two people could be, or a person two pros could be, is matched to no one; birth dates known to the day
that differ rule a person out. A run that read everyone also takes back a match that no longer holds (an
edit on Wikidata), with its photo; one that didn't only adds.

    .venv/bin/python -m sports_follow.roster_wikidata                      # every roster sport, as the weekly import does after it
    .venv/bin/python -m sports_follow.roster_wikidata --dry-run --show 20  # what it would match, 20 at random, written nowhere
"""

from __future__ import annotations

import argparse
import logging
import random
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Any, Iterable
from urllib.parse import unquote

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from . import bus, logs, naming, registry, sports
from .adapters import KNOWN
from .db import session
from .models import Athlete

log = logging.getLogger("sports_follow.roster_wikidata")

BATCH = 2000  # people a query reads
KEY = "wikidata"  # an athlete's Wikidata id among their ids
QUOTED = re.compile(r"""["“”„«»]([^"“”„«»]+)["“”„«»]""")  # Tyson "TenZ" Ngo
CLOSE = 0.85  # how alike two spellings of a name's word must be: "Aleksandr", "Oleksandr"
VOWELS = re.compile(r"[aeiouwy]")
LEET = str.maketrans("013", "oie")  # a handle's digits for letters: "Suma1L" is SumaiL


@dataclass
class Person:
    """A person on Wikidata who may be one of a roster sport's athletes, as matching needs them."""

    qid: str
    label: str = ""
    handles: set[str] = field(default_factory=set)  # the names they play under, folded: "tenz"
    names: list[list[str]] = field(default_factory=list)  # their real names, as plain words (plain): ["tyson", "ngo"]
    born: date | None = None  # known to the day only
    plays: set[str] = field(default_factory=set)  # their games, as the sport's game_key writes them
    teams: list[str] = field(default_factory=list)
    ids: dict[str, set[str]] = field(default_factory=dict)  # by property: {"P10918": {"valorant/TenZ"}}
    image: str | None = None  # their photo on Commons (P18)
    sitelinks: int = 0  # Wikipedia editions with an article on them


@dataclass
class Row:
    """A registry athlete from a source's list, as matching needs them."""

    qid: str  # their registry id: "pandascore:20401"
    handle: str
    aliases: list[str]  # a real name among them
    born: date | None
    plays: str  # their game, as the sport's game_key writes it; "" for none
    teams: list[str]
    ids: list[tuple[str, str]] = field(default_factory=list)  # their source ids as Wikidata writes them: ("P10918", "pubgmobile/Jonathan")


@dataclass
class Match:
    row: Row
    person: Person
    how: str  # "id" | "handle" | "name and birth date"


# ---------------------------------------------------------------- names


def plain(text: str) -> list[list[str]]:
    """A real name as plain words, a hyphenated word apart and run together: "Lee Sang-hyeok" -> ["lee",
    "sang", "hyeok"] and ["lee", "sanghyeok"]."""
    apart, together = naming.words(text), naming.words(re.sub(r"[-‐‑]", "", text or ""))
    return [apart] if apart == together else [apart, together]


def handle(text: str) -> str:
    """A handle as compared: plain words, a digit for a letter read as the letter ("s1mple", "simple")."""
    return " ".join(naming.words(text)).translate(LEET)


def _within(short: list[str], long: list[str]) -> bool:
    return bool(short) and any(long[i : i + len(short)] == short for i in range(len(long) - len(short) + 1))


def alike(a: str, b: str) -> bool:
    """Two spellings of a name's word, four letters or more: nearly the same ("Aleksandr", "Oleksandr";
    "Ilya", "Illya"), or the same first letter and consonants, as romanisations of one Korean name differ
    ("Jeong", "Jung"; "Jihoon", "Jihun")."""
    if min(len(a), len(b)) >= 4 and SequenceMatcher(None, a, b).ratio() >= CLOSE:
        return True
    bones = VOWELS.sub("", a[1:]), VOWELS.sub("", b[1:])
    return min(len(a), len(b)) >= 4 and a[0] == b[0] and bones[0] == bones[1] and len(bones[0]) >= 2


def names_agree(a: list[str], b: list[str]) -> bool:
    """Two real names are one person's: every word of the shorter is a different word of the longer, the
    same or spelled alike (alike), and at least one word of three letters or more the same. "Aleksandr
    Kostyliev" is "Oleksandr Kostyliev", "Erick Santos" is "Erick Andrade Santos", "Jeong Jihoon" is "Jung
    Jihoon"."""
    short, long = sorted((a, b), key=len)
    if len(short) < 2:
        return False
    left, same = list(long), False
    for word in short:
        if word in left:
            left.remove(word)
            same = same or len(word) >= 3
            continue
        spelt = next((w for w in left if alike(word, w)), None)
        if spelt is None:
            return False
        left.remove(spelt)
    return same


def same_team(ours: list[str], theirs: list[str], filler: Iterable[str] = ()) -> bool:
    """Whether a team of each is one, by the words that tell teams apart (not the sport's filler: "Team"):
    "Vitality" and "Team Vitality", "T1" and "T1"."""
    filler = set(filler)
    for a in ours:
        for b in theirs:
            x, y = ([w for w in naming.words(t) if w not in filler] for t in (a, b))
            short, long = sorted((x, y), key=len)
            if _within(short, long):
                return True
    return False


# ---------------------------------------------------------------- Wikidata


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def members(kind: sports.Sport) -> list[str]:
    """Every person on Wikidata the sport counts as one of its people."""
    ways = [f"{{ ?p wdt:{prop} [] }}" for prop in kind.people_ids] + [f"{{ ?p wdt:P106 wd:{job} }}" for job in kind.people_occupations]
    return sorted({_qid(r["p"]) for r in registry.sparql(f"SELECT DISTINCT ?p WHERE {{ {' UNION '.join(ways)} ?p wdt:P31 wd:Q5 }}")})


# What's read of each person, labelled ("given": a given name's label).
LABELLED = {"discipline": "P2416", "team": "P54", "given": "P735", "family": "P734"}


def fetch(kind: sports.Sport, qids: list[str]) -> tuple[list[dict], list[dict], list[dict]]:
    """What matching needs of these people, in three queries: their photo, birth date and Wikipedia
    editions; their ids, games, teams and real names; their labels and aliases."""
    values = " ".join(f"wd:{q}" for q in qids)
    core = registry.sparql(f"""SELECT ?p ?sitelinks ?birth ?birth_precision ?death ?image WHERE {{
      VALUES ?p {{ {values} }}
      OPTIONAL {{ ?p wikibase:sitelinks ?sitelinks }}
      OPTIONAL {{ ?p p:P569 ?bst . ?bst a wikibase:BestRank ; psv:P569 [ wikibase:timeValue ?birth ; wikibase:timePrecision ?birth_precision ] }}
      OPTIONAL {{ ?p wdt:P570 ?death }}
      OPTIONAL {{ ?p wdt:P18 ?image }} }}""")
    ways = [f'{{ ?p wdt:{prop} ?v BIND("{prop}" AS ?kind) }}' for prop in kind.people_ids]
    ways += [f'{{ ?p wdt:{prop} ?x . ?x rdfs:label ?v FILTER(LANG(?v) IN ("en", "mul")) BIND("{key}" AS ?kind) }}' for key, prop in LABELLED.items()]
    ways.append('{ ?p wdt:P1477 ?v BIND("birth_name" AS ?kind) }')
    facts = registry.sparql(f"SELECT ?p ?kind ?v WHERE {{ VALUES ?p {{ {values} }} {' UNION '.join(ways)} }}")
    langs = ", ".join(f'"{lang}"' for lang in registry.LABEL_LANGS)
    names = registry.sparql(f"""SELECT ?p ?n ?kind (LANG(?n) AS ?lang) WHERE {{
      VALUES ?p {{ {values} }}
      {{ ?p rdfs:label ?n FILTER(LANG(?n) IN ({langs})) BIND("label" AS ?kind) }}
      UNION {{ ?p skos:altLabel ?n FILTER(LANG(?n) IN ("en", "mul")) BIND("alias" AS ?kind) }} }}""")
    return core, facts, names


def people(kind: sports.Sport, core: list[dict], facts: list[dict], names: list[dict]) -> list[Person]:
    """People from one batch's query rows (pure, so it's tested without the network). The dead are left out."""
    found: dict[str, Person] = {}
    dead: set[str] = set()
    births: dict[str, set[date]] = defaultdict(set)
    images: dict[str, set[str]] = defaultdict(set)
    for r in core:
        p = found.setdefault(_qid(r["p"]), Person(_qid(r["p"])))
        if r.get("death"):
            dead.add(p.qid)
        if r.get("birth") and r.get("birth_precision") == str(registry.DAY) and not r["birth"].startswith("-"):
            births[p.qid].add(date.fromisoformat(r["birth"][:10]))
        p.sitelinks = max(p.sitelinks, int(r.get("sitelinks") or 0))
        if r.get("image"):
            images[p.qid].add(unquote(r["image"].rsplit("/", 1)[-1]))  # a Special:FilePath URL: the file name
    forms: dict[str, list[str]] = defaultdict(list)  # labels, aliases, birth names
    given: dict[str, list[str]] = defaultdict(list)
    family: dict[str, list[str]] = defaultdict(list)
    disciplines: dict[str, list[str]] = defaultdict(list)
    for r in facts:
        p = found.get(_qid(r["p"]))
        what, value = r.get("kind"), r.get("v") or ""
        if p is None or not value:
            continue
        if what in kind.people_ids:
            p.ids.setdefault(what, set()).add(value)
        elif what == "team":
            p.teams.append(value)
        elif what == "discipline":
            disciplines[p.qid].append(value)
        elif what == "given":
            given[p.qid].append(value)
        elif what == "family":
            family[p.qid].append(value)
        elif what == "birth_name":
            forms[p.qid].append(value)
    labels: dict[str, dict[str, str]] = defaultdict(dict)
    for r in names:
        p = found.get(_qid(r["p"]))
        if p is not None and r.get("n"):
            forms[p.qid].append(r["n"])
            if r.get("kind") == "label":
                labels[p.qid][r.get("lang") or ""] = r["n"]
    out = []
    for qid, p in found.items():
        if qid in dead:
            continue
        p.label = next((labels[qid][lang] for lang in registry.LABEL_LANGS if lang in labels[qid]), "")
        p.born = next(iter(births[qid])) if len(births[qid]) == 1 else None  # two days: neither
        p.image = min(images[qid])[:300] if images[qid] else None  # the same one every week
        for form in forms[qid]:
            nicknames = QUOTED.findall(form)
            p.handles.update(handle(n) for n in nicknames)
            rest = QUOTED.sub(" ", form)
            p.handles.add(handle(rest))
            p.names += plain(rest)
        for g in given[qid]:
            for f in family[qid]:
                p.names += [*plain(f"{g} {f}"), *plain(f"{f} {g}")]
        ids = {k: sorted(v) for k, v in p.ids.items()}
        p.handles.update(handle(name) for name in kind.known_as(ids))
        p.handles.discard("")
        p.names = [list(n) for n in dict.fromkeys(tuple(n) for n in p.names if len(n) >= 2)]
        p.plays = kind.plays(ids, disciplines[qid])
        out.append(p)
    return out


def read_people(kind: sports.Sport) -> tuple[list[Person], bool]:
    """Everyone the sport counts as one of its people on Wikidata, and whether every batch was read."""
    qids = members(kind)
    if not qids:
        raise RuntimeError(f"Wikidata listed no one in {kind.key}; last week's matches kept")
    everyone, complete = [], True
    for i in range(0, len(qids), BATCH):
        try:
            everyone += people(kind, *fetch(kind, qids[i : i + BATCH]))
        except Exception as exc:  # one bad batch shouldn't lose the rest
            log.warning("roster wikidata %s: batch %d failed: %s", kind.key, i // BATCH + 1, exc)
            complete = False
    return everyone, complete


# ---------------------------------------------------------------- matching


def rows(db: Session, kind: sports.Sport) -> list[Row]:
    """The sport's registry athletes, as matching needs them."""
    out = []
    for a in db.scalars(select(Athlete).where(Athlete.sport == kind.key)):
        ids = []
        for system, xid in (a.ids or {}).items():
            adapter = KNOWN.get(system)
            if adapter is not None and adapter.wikidata_property:
                ids.append((adapter.wikidata_property, adapter.wikidata_id(str(xid))))
        out.append(Row(a.qid, a.name, list(a.aliases or []), a.birth_date, kind.game_key(kind.result_sport(a)), list(a.teams or []), ids))
    return out


def _id(value: str) -> str:
    return value.replace(" ", "_")


def match(rows: list[Row], everyone: list[Person], filler: Iterable[str] = ()) -> list[Match]:
    """Each row's Wikidata person, where only one can be them and they can be only that row (pure). filler:
    the words of the sport's team names that don't tell teams apart (Sport.team_filler)."""
    by_id: dict[tuple[str, str], list[Person]] = defaultdict(list)
    by_handle: dict[str, list[Person]] = defaultdict(list)
    by_name: dict[tuple[str, ...], list[Person]] = defaultdict(list)
    for p in everyone:
        for prop, values in p.ids.items():
            for value in values:
                by_id[(prop, _id(value))].append(p)
        for h in p.handles:
            by_handle[h].append(p)
        for name in p.names:
            by_name[tuple(sorted(name))].append(p)

    found: list[Match] = []
    for row in rows:
        exact = {p.qid: p for key in row.ids for p in by_id.get((key[0], _id(key[1])), [])}
        if exact:
            if len(exact) == 1:
                found.append(Match(row, next(iter(exact.values())), "id"))
            continue  # two people with the same id: neither
        names = [n for alias in row.aliases for n in plain(alias) if len(n) >= 2]
        could: dict[str, Match] = {}
        for p in by_handle.get(handle(row.handle), []):
            if row.born and p.born and row.born != p.born:
                continue
            who = (row.born is not None and row.born == p.born) or any(names_agree(n, m) for n in names for m in p.names)
            where = (row.plays and row.plays in p.plays) or same_team(row.teams, p.teams, filler)
            if who and where:
                could[p.qid] = Match(row, p, "handle")
        if not could and row.born:
            for n in names:
                for p in by_name.get(tuple(sorted(n)), []):
                    if p.born == row.born:
                        could[p.qid] = Match(row, p, "name and birth date")
        if len(could) == 1:
            found += could.values()
    # A person two rows could be is neither's, unless one row is theirs by id.
    by_person: dict[str, list[Match]] = defaultdict(list)
    for m in found:
        by_person[m.person.qid].append(m)
    out = []
    for ms in by_person.values():
        by_id_too = [m for m in ms if m.how == "id"]
        if len(by_id_too) == 1:
            out.append(by_id_too[0])
        elif not by_id_too and len(ms) == 1:
            out.append(ms[0])
    return out


# ---------------------------------------------------------------- the registry


def record(db: Session, sport: str, found: list[Match], complete: bool) -> dict[str, int]:
    """Write each match into its row (their Wikidata id, and photo file or none), and, after a complete
    read, take back the matches that no longer hold. Returns how many rows changed and were taken back."""
    now = {m.row.qid: (m.person.qid, m.person.image) for m in found}
    held = Athlete.ids[KEY].astext
    have = {qid: (wd, image) for qid, wd, image in db.execute(select(Athlete.qid, held, Athlete.image).where(Athlete.sport == sport, or_(Athlete.ids.has_key(KEY), Athlete.image.is_not(None))))}
    changed = 0
    for row, (qid, image) in now.items():
        if have.get(row) != (qid, image):
            db.execute(update(Athlete).where(Athlete.qid == row, Athlete.sport == sport).values(ids=Athlete.ids.op("||")(func.jsonb_build_object(KEY, qid)), image=image))
            changed += 1
    gone = [row for row in have if row not in now] if complete else []
    if gone:
        db.execute(update(Athlete).where(Athlete.qid.in_(gone), Athlete.sport == sport).values(ids=Athlete.ids.op("-")(KEY), image=None))
    return {"changed": changed, "taken_back": len(gone)}


def tally(found: list[Match]) -> dict[str, int]:
    """How many matched, each way, and how many with a photo, and with a Wikipedia article."""
    count = {"matched": len(found), "by_id": 0, "by_handle": 0, "by_name_and_birth": 0}
    for m in found:
        count[{"id": "by_id", "handle": "by_handle"}.get(m.how, "by_name_and_birth")] += 1
    count["with_photo"] = sum(1 for m in found if m.person.image)
    count["on_wikipedia"] = sum(1 for m in found if m.person.sitelinks)
    count["on_wikipedia_with_photo"] = sum(1 for m in found if m.person.sitelinks and m.person.image)
    return count


def match_sport(sport: str, write: bool = True) -> tuple[list[Match], dict[str, Any]]:
    """Match a roster sport's athletes to Wikidata people and, unless write is False, record them."""
    kind = sports.BY_KEY[sport]
    everyone, complete = read_people(kind)
    with session() as db:
        athletes = rows(db, kind)
    found = match(athletes, everyone, kind.team_filler)
    done: dict[str, Any] = {"people": len(everyone), "athletes": len(athletes), **tally(found), "complete": complete}
    if write:
        with session() as db:
            done |= record(db, sport, found, complete)
    return found, done


def run(sport: str) -> dict[str, Any]:
    """The weekly run for one roster sport (registry.find_people), logged."""
    _, done = match_sport(sport)
    log.info("roster wikidata %s: %s", sport, done)
    return done


def roster_sports() -> list[str]:
    return [s.key for s in sports.ENABLED if s.roster and (s.people_ids or s.people_occupations)]


def show(m: Match) -> str:
    """One match, with what it rests on, for checking by eye."""
    r, p = m.row, m.person
    ours = " · ".join(x for x in (r.handle, "/".join(r.aliases), r.plays, "/".join(r.teams), r.born.isoformat() if r.born else "") if x)
    theirs = " · ".join(x for x in (p.label, "/".join(sorted(v for vs in p.ids.values() for v in vs)), ",".join(sorted(p.plays)), "/".join(p.teams[:3]), p.born.isoformat() if p.born else "") if x)
    return f"{r.qid:<18} {ours}\n  = {p.qid:<11} {theirs}  [{m.how}; {p.sitelinks} wikis; photo: {p.image or 'none'}]"


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="python -m sports_follow.roster_wikidata", description="Match esports pros to their Wikidata person, for their photos.")
    parser.add_argument("sport", nargs="*", help="roster sports (default: every one that runs)")
    parser.add_argument("--dry-run", action="store_true", help="match, but write nothing")
    parser.add_argument("--show", type=int, default=0, metavar="N", help="print N matches at random, with what each rests on")
    args = parser.parse_args(argv)
    every = roster_sports()
    unknown = [s for s in args.sport if s not in every]
    if unknown:
        sys.exit(f"unknown roster sport(s): {', '.join(unknown)}; choose from {', '.join(every)}")
    token = None if args.dry_run else bus.try_lock(registry.LOCK, 3600)
    if not args.dry_run and token is None:
        sys.exit(f"a registry import is running (it matches the pros itself when it's done); try again later, or redis-cli del lock:{registry.LOCK} if none is")
    try:
        for sport in args.sport or every:
            found, done = match_sport(sport, write=not args.dry_run)
            print(sport, done)
            for m in random.sample(found, min(args.show, len(found))):
                print(show(m))
    finally:
        if token is not None:
            bus.release(registry.LOCK, token)


if __name__ == "__main__":
    logs.setup()
    main(sys.argv[1:])
