"""Operator tooling: add and remove players with a command.

    .venv/bin/python -m sports_follow.players list
    .venv/bin/python -m sports_follow.players add Q1234 [--sport cricket]   # pin a registry athlete: no import drops them
    .venv/bin/python -m sports_follow.players add --sport cs2 --name amster --id pandascore_esports=34470 --team 6666 --league cs-go
    .venv/bin/python -m sports_follow.players remove 17                    # a player's page, by its id
    .venv/bin/python -m sports_follow.players remove Q1234                 # or a registry id, or a source id (espn_cricket:253802)
    .venv/bin/python -m sports_follow.players restore Q1234                # take the decision back
    .venv/bin/python -m sports_follow.players unmatch pandascore:20401     # a pro's Wikidata match and photo taken back, never matched again
    .venv/bin/python -m sports_follow.players unmatch 17 Q113450352        # or never again to that person
    .venv/bin/python -m sports_follow.players rematch pandascore:20401     # take the unmatch back
    .venv/bin/python -m sports_follow.players starter add Q1234            # pin them for new fans (starters.py)
    .venv/bin/python -m sports_follow.players starter remove Q1234
    .venv/bin/python -m sports_follow.players starter list                 # the pins, and each sport's top players
    .venv/bin/python -m sports_follow.players starter refresh              # find the top players now (daily anyway)
    .venv/bin/python -m sports_follow.players socials 17 x=imVkohli instagram=virat.kohli  # their own accounts (curate.py)
    .venv/bin/python -m sports_follow.players socials 17                   # show them; --clear takes them all off

What's decided is kept in athlete_override, which every registry import respects (registry.upsert and the
stale and unseen deletes), so it holds on a running server without a deploy and the weekly import doesn't
undo it. Adding puts an athlete no source lists in the registry, keyed "op:…", or pins one an import would
drop: search finds them, and following binds the source ids given. Removing deletes the player's page with
everything that hangs off it (follows, moments, results, bindings: the database cascades) and its hot card
in Redis, deletes their registry rows and blocks them: imports never write them again, and search and
following leave out their ids in the sources. A name typed for research isn't blocked; the agent decides
who that is. Removing shows what it will delete and asks first, unless given --yes.

Unmatching is for a pro matched to the wrong Wikidata person, and so shown with someone else's face
(roster_wikidata.py): it takes the match and photo back at once, off every card showing it and the
Credits page too, and kept in athlete_unmatch, no run matches them to that person again, or to anyone. It's apart from keeping and
removing: a kept pro can be unmatched, and removing one leaves it, so restoring them doesn't bring the face
back. Rematching takes it back; the next weekly run (or roster_wikidata by hand) may match them again.

Starters are the athletes a new fan is offered in each sport (starters.py): who's playing now, the most
followed, and the best known, found by themselves; pinned starters come first, in the order they were
added. Pinning one keeps them in the registry too, so no import drops them; removing the athlete takes their
starter row with them, and takes them off every campaign page (campaigns.py).

Socials are a followed player's own accounts (player.socials), which the Feed's judge reads to know a share is
theirs (curate.py). Setting them replaces what the page had; removing the page takes them with it.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence, get_args

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import bus, campaigns, curate, logs, photos, registry, roster_wikidata, search, sports, starters
from .adapters import KNOWN, AdapterError
from .db import session
from .models import Athlete, AthleteOverride, AthleteUnmatch, EventPlayer, Follow, Moment, Player, PlayerIdentity

QID = re.compile(r"Q[1-9][0-9]*")  # a Wikidata person


class Refused(ValueError):
    """What an operator asked for can't be done as asked; the message says why."""


@dataclass
class Found:
    """What a reference means: players' pages and registry rows."""

    players: list[Player] = field(default_factory=list)
    athletes: list[Athlete] = field(default_factory=list)


def source_ref(ref: str) -> tuple[str, str] | None:
    """("espn_cricket", "253802") for "espn_cricket:253802": a source's system and its id, or None."""
    system, sep, xid = ref.partition(":")
    return (system, xid) if sep and system in KNOWN and xid else None


def find(db: Session, ref: str, sport: str | None = None) -> Found:
    """The pages and registry rows a reference means: a player's id ("17"), a registry id ("Q1234",
    "pandascore:34470", "op:…"), or a source's id ("espn_cricket:253802"). sport narrows to one sport."""
    ref = ref.strip()
    found = Found()
    if ref.isdigit():
        player = db.get(Player, int(ref))
        if player:
            found.players = [player]
            idents = db.execute(select(PlayerIdentity.system, PlayerIdentity.external_id).where(PlayerIdentity.player_id == player.id)).all()
            qids = {xid for system, xid in idents if system == "wikidata"} | ({player.wikidata_qid} if player.wikidata_qid else set())
            by_id = [Athlete.ids.contains({system: xid}) for system, xid in idents if system != "wikidata"]
            if qids or by_id:
                rows = db.scalars(select(Athlete).where(or_(Athlete.qid.in_(qids), *by_id))).all()
                found.athletes = [a for a in rows if a.sport == sports.family(player.sport)]
    elif (src := source_ref(ref)) is not None:
        system, xid = src
        found.players = list(db.scalars(select(Player).join(PlayerIdentity, PlayerIdentity.player_id == Player.id).where(PlayerIdentity.system == system, PlayerIdentity.external_id == xid)))
        found.athletes = list(db.scalars(select(Athlete).where(Athlete.ids.contains({system: xid}))))
    else:
        found.athletes = list(db.scalars(select(Athlete).where(Athlete.qid == ref)))
        holders = select(PlayerIdentity.player_id).where(PlayerIdentity.system == "wikidata", PlayerIdentity.external_id == ref)
        found.players = list(db.scalars(select(Player).where(or_(Player.id.in_(holders), Player.wikidata_qid == ref))))
    if sport:
        within = {sport, *sports.expand([sport])}  # "esports": any game's
        found.athletes = [a for a in found.athletes if a.sport in within]
        found.players = [p for p in found.players if sports.family(p.sport) in within]
    return found


def describe(db: Session, found: Found) -> list[str]:
    """What removing would take out, a line each."""
    lines = []
    for p in found.players:
        follows = db.scalar(select(func.count()).select_from(Follow).where(Follow.player_id == p.id)) or 0
        games = db.scalar(select(func.count()).select_from(EventPlayer).where(EventPlayer.player_id == p.id)) or 0
        moments = db.scalar(select(func.count()).select_from(Moment).where(Moment.player_id == p.id)) or 0
        lines.append(f"page {p.id}: {p.name} ({p.sport or 'sport not known'}), followed by {follows}, {games} games, {moments} moments")
    for a in found.athletes:
        lines.append(f"registry {a.qid} ({a.sport}): {a.name}{f', ids {a.ids}' if a.ids else ''}")
    return lines


def _decide(db: Session, qid: str, sport: str, action: str, name: str, ids: dict[str, str], note: str | None) -> None:
    stmt = insert(AthleteOverride).values(qid=qid, sport=sport, action=action, name=name[:200], ids=ids, note=note)
    db.execute(stmt.on_conflict_do_update(index_elements=["qid", "sport"], set_={"action": action, "name": name[:200], "ids": ids, "note": note, "created_at": func.now()}))


def remove(db: Session, found: Found, note: str | None = None) -> list[int]:
    """Take out what a reference found: delete the pages (the database cascades their follows, moments,
    results and bindings) and the registry rows, and block each athlete. Returns the deleted pages' ids,
    whose hot cards the caller clears once this commits."""
    if not found.players and not found.athletes:
        raise Refused("nothing to remove: no page or registry athlete has that id")
    covered: set[int] = set()
    for a in found.athletes:
        ids = dict(a.ids or {})
        for p in found.players:
            if sports.family(p.sport) == a.sport:
                ids |= _source_ids(db, p.id)
                covered.add(p.id)
        _decide(db, a.qid, a.sport, "remove", a.name, ids, note)
        db.execute(delete(Athlete).where(Athlete.id == a.id))
    starters.remove(db, found.athletes)
    campaigns.forget(db, found.athletes)
    for p in found.players:
        if p.id not in covered:
            # A page no registry row stands for (an agent-built one): its source ids are still blocked.
            _decide(db, f"player:{p.id}", sports.family(p.sport) or "other", "remove", p.name, _source_ids(db, p.id), note)
    gone = [p.id for p in found.players]
    if gone:
        db.execute(delete(Player).where(Player.id.in_(gone)))
    return gone


def _source_ids(db: Session, player_id: int) -> dict[str, str]:
    rows = db.execute(select(PlayerIdentity.system, PlayerIdentity.external_id).where(PlayerIdentity.player_id == player_id, PlayerIdentity.system != "wikidata")).all()
    return {system: xid for system, xid in rows}


def pin(db: Session, qid: str, sport: str | None = None, note: str | None = None) -> list[Athlete]:
    """Keep registry athletes an import would otherwise drop (a pro a source stops listing)."""
    rows = list(db.scalars(select(Athlete).where(Athlete.qid == qid, *([Athlete.sport == sport] if sport else []))))
    if not rows:
        raise Refused(f"no registry athlete {qid}{f' in {sport}' if sport else ''}")
    for a in rows:
        _refuse_removed(db, a.qid, a.sport)
        _decide(db, a.qid, a.sport, "keep", a.name, dict(a.ids or {}), note)
    return rows


def add(db: Session, sport: str, name: str, *, aliases: Sequence[str] = (), teams: Sequence[str] = (), country: str | None = None,
        league: str | None = None, born: date | None = None, ids: dict[str, str] | None = None, note: str | None = None) -> tuple[Athlete, bool]:
    """Put an athlete no source lists in the registry, kept there, or pin the one already there with one of
    these source ids. Returns the athlete and whether they were new."""
    if sport not in {s.key for s in sports.ALL}:
        raise Refused(f"unknown sport {sport!r}; choose from {', '.join(s.key for s in sports.ALL)}")
    if not registry.words(name):
        raise Refused("a name needs letters or digits in Latin script")
    ids = dict(ids or {})
    for system, xid in ids.items():
        adapter = KNOWN.get(system)
        if adapter is not None and (sport not in adapter.sports or not adapter.id_pattern.fullmatch(xid)):
            raise Refused(f"{system}:{xid} isn't one of that source's {sport} athlete ids")
    there = db.scalars(select(Athlete).where(Athlete.sport == sport, or_(*[Athlete.ids.contains({s: x}) for s, x in ids.items()]))).first() if ids else None
    if there is not None:
        pin(db, there.qid, sport, note)
        return there, False
    row = {
        "qid": f"op:{secrets.token_hex(6)}", "sport": sport, "name": name[:200], "aliases": [a for a in aliases if a][: registry.MAX_ALIASES],
        "search_text": registry.search_text(name, list(aliases)), "birth_date": born, "birth_precision": registry.DAY if born else None,
        "country": country, "teams": [t for t in teams if t], "league": league, "title": None, "ids": ids, "image": None, "sitelinks": 0, "current": True,
    }
    registry.upsert(db, [row])
    _decide(db, row["qid"], sport, "keep", name, ids, note)
    return db.scalars(select(Athlete).where(Athlete.qid == row["qid"], Athlete.sport == sport)).one(), True


def _refuse_removed(db: Session, qid: str, sport: str) -> None:
    within = [sport, *([group] if (group := sports.of(sport).group) else [])]  # removed as "esports": every game's
    if db.scalar(select(AthleteOverride.id).where(AthleteOverride.qid == qid, AthleteOverride.sport.in_(within), AthleteOverride.action == "remove")):
        raise Refused(f"{qid} ({sport}) was removed; restore it first")


def restore(db: Session, ref: str, sport: str | None = None) -> list[AthleteOverride]:
    """Take decisions back. A removed athlete returns with the next import that lists them; a kept one is an
    ordinary row again, which the next import drops if no source lists them."""
    where = [AthleteOverride.qid == ref.strip(), *([AthleteOverride.sport == sport] if sport else [])]
    rows = list(db.scalars(select(AthleteOverride).where(*where)))
    if not rows:
        raise Refused(f"nothing was decided about {ref}")
    db.execute(delete(AthleteOverride).where(*where))
    return rows


def unmatch(db: Session, found: Found, person: str | None = None, note: str | None = None) -> list[tuple[Athlete, bool, str | None]]:
    """Say the athletes a reference found aren't this Wikidata person, or with none anyone, so no run
    matches them again (roster_wikidata.py), and take back a match to them now: their Wikidata id and photo.
    Returns each athlete, whether a match was taken back, and the photo file taken back with it."""
    if person is not None and not QID.fullmatch(person):
        raise Refused(f"{person!r} isn't a Wikidata id, like Q1234")
    if not found.athletes:
        raise Refused("only a registry athlete is matched to a Wikidata person, and no registry athlete has that id")
    athletes = [a for a in found.athletes if (kind := sports.BY_KEY.get(a.sport)) is not None and roster_wikidata.matches(kind)]
    if not athletes:
        names = ", ".join(f"{a.name} ({a.sport}, {a.qid})" for a in found.athletes)
        raise Refused(f"{names} came from Wikidata itself, not a source's own list, so isn't matched to anyone here")
    out = []
    for a in athletes:
        # The decision first: a weekly run writing its matches holds it back until that run is done, so what
        # it wrote is taken back below.
        stmt = insert(AthleteUnmatch).values(qid=a.qid, sport=a.sport, person=person, name=a.name[:200], note=note)
        db.execute(stmt.on_conflict_do_update(index_elements=["qid", "sport", "person"], set_={"name": a.name[:200], "note": note, "created_at": func.now()}))
        image = a.image
        taken = bool(roster_wikidata.forget(db, [a.sport], [a.qid], person))
        out.append((a, taken, image if taken else None))
    return out


def rematch(db: Session, ref: str, sport: str | None = None, person: str | None = None) -> list[AthleteUnmatch]:
    """Take unmatches back: of the athletes a reference finds, or of the registry id itself (an athlete since
    removed); given a person, only that one. The next run may match them again."""
    keys = {(a.qid, a.sport) for a in find(db, ref, sport).athletes}
    which = or_(AthleteUnmatch.qid == ref.strip(), *[(AthleteUnmatch.qid == q) & (AthleteUnmatch.sport == s) for q, s in keys])
    where = [which, *([AthleteUnmatch.sport == sport] if sport else []), *([AthleteUnmatch.person == person] if person else [])]
    rows = list(db.scalars(select(AthleteUnmatch).where(*where)))
    if not rows:
        raise Refused(f"no match of {ref}{f' to {person}' if person else ''} was taken back")
    db.execute(delete(AthleteUnmatch).where(AthleteUnmatch.id.in_([u.id for u in rows])))
    return rows


def unmatches(db: Session) -> list[AthleteUnmatch]:
    return list(db.scalars(select(AthleteUnmatch).order_by(AthleteUnmatch.created_at.desc())))


def take_photos_off(files: list[str]) -> int:
    """Take photos taken back off the Credits page, unless an athlete's is the same file, and off every card
    that shows one its athlete no longer has, followed or not: now, not at the photos job's next run.
    Returns how many cards changed."""
    with session() as db:
        photos.forget(db, files)
        return photos.stamp(db)


def starter_athletes(found: Found, sport: str | None = None) -> list[Athlete]:
    """The registry athletes a reference means as starters: one sport's, unless the id is only in one."""
    if not found.athletes:
        raise Refused("starters come from the registry, and no registry athlete has that id")
    if len(found.athletes) > 1 and not sport:
        raise Refused(f"that id is in more than one sport ({', '.join(a.sport for a in found.athletes)}); say which with --sport")
    return found.athletes


def check_source(person: registry.Person) -> str:
    """The source a starter is followed through, or refused when it doesn't have them: a new fan would
    follow an empty card, or spend an AI research. In the sport's live source, their id is found by name
    and birth date when Wikidata didn't give it and written to the registry (registry.resolve), so
    following them is by id; a source side by side with it (esports) is asked for the id their row has."""
    system = registry.follow_system(person.sport, person.ids)
    if system is None:
        raise Refused(f"no live source runs for {person.name} ({person.sport}) in this build, so new fans can't follow them live")
    try:
        found = registry.find_in_source(person)
    except AdapterError as exc:
        raise Refused(f"couldn't reach {system} to check {person.name}: {exc}") from exc
    if found is None:
        raise Refused(f"{system} doesn't have {person.name} ({person.qid}); a starter must be someone new fans can follow live")
    return system


def make_starters(db: Session, athletes: list[Athlete]) -> list[tuple[Athlete, bool]]:
    """Offer these athletes to new fans, pinned so no import drops them. Returns each athlete and whether
    they're a new starter."""
    out = []
    for a in athletes:
        pin(db, a.qid, a.sport)
        out.append((a, starters.add(db, a)))
    return out


def decisions(db: Session) -> list[AthleteOverride]:
    return list(db.scalars(select(AthleteOverride).order_by(AthleteOverride.created_at.desc())))


def forget_cards(player_ids: list[int]) -> None:
    """Clear deleted pages' hot cards in Redis, so the API can't serve them."""
    if player_ids:
        bus.sync_redis().delete(*[bus.card_key(pid) for pid in player_ids])


def set_socials(found: Found, handles: Sequence[tuple[str, str]]) -> list[Player]:
    """Give the pages a reference found their player's own accounts (platform, handle), in place of what they
    had; none clears them."""
    if not found.players:
        raise Refused("only a followed player's page has accounts, and no page has that id")
    platforms = get_args(curate.Platform)
    for platform, handle in handles:
        if platform not in platforms:
            raise Refused(f"unknown platform {platform!r}; choose from {', '.join(platforms)}")
        if not re.fullmatch(r"@?[\w.\-]{1,100}", handle):
            raise Refused(f"{handle!r} isn't a handle")
    socials = [{"platform": p, "handle": h.lstrip("@")} for p, h in dict.fromkeys(handles)]
    for player in found.players:
        player.socials = socials
    return found.players


# ---------------------------------------------------------------- the command


def _ids(values: list[str]) -> dict[str, str]:
    out = {}
    for v in values:
        system, sep, xid = v.partition("=")
        if not sep or not system or not xid:
            raise Refused(f"--id takes system=id, a source's system ({', '.join(KNOWN)}) and the athlete's id there, not {v!r}")
        out[system.strip()] = xid.strip()
    return out


def _social(value: str) -> tuple[str, str]:
    platform, sep, handle = value.partition("=")
    if not sep or not platform.strip() or not handle.strip():
        raise Refused(f"a handle is platform=handle, like x=imVkohli, not {value!r}")
    return platform.strip().lower(), handle.strip()


def main(argv: list[str], ask: Any = input) -> int:
    parser = argparse.ArgumentParser(prog="python -m sports_follow.players", description="Add and remove players (athlete_override).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="what's been added and removed")
    a = sub.add_parser("add", help="pin a registry athlete (by id), or add one no source lists (--sport and --name)")
    # A registry id is a Wikidata id, or the id of a source that lists its own athletes (roster_prefix).
    prefixes = [f"{p}…" for p in dict.fromkeys(getattr(x, "roster_prefix", None) for x in KNOWN.values()) if p]
    a.add_argument("qid", nargs="?", help=f"a registry id to pin: {', '.join(['Q1234', *prefixes])}")
    a.add_argument("--sport")
    a.add_argument("--name")
    a.add_argument("--alias", action="append", default=[])
    a.add_argument("--team", action="append", default=[])
    a.add_argument("--country")
    a.add_argument("--league")
    a.add_argument("--born", type=date.fromisoformat, help="YYYY-MM-DD")
    a.add_argument("--id", action="append", default=[], help="system=id: their id in a source (repeatable)")
    a.add_argument("--note")
    r = sub.add_parser("remove", help="remove a page and block its athlete: a player id, registry id or system:id")
    r.add_argument("ref")
    r.add_argument("--sport")
    r.add_argument("--note")
    r.add_argument("--yes", action="store_true", help="don't ask")
    b = sub.add_parser("restore", help="take back what was decided about a registry id")
    b.add_argument("ref")
    b.add_argument("--sport")
    u = sub.add_parser("unmatch", help="take back a pro's Wikidata match and photo, and never match them again (or to that person)")
    u.add_argument("ref", help=f"a player id, registry id ({', '.join(prefixes)}) or system:id")
    u.add_argument("person", nargs="?", help="the Wikidata person they aren't, Q1234 (default: anyone)")
    u.add_argument("--sport")
    u.add_argument("--note")
    m = sub.add_parser("rematch", help="take back an unmatch: the next weekly run may match them again")
    m.add_argument("ref")
    m.add_argument("person", nargs="?", help="only the unmatch from this Wikidata person")
    m.add_argument("--sport")
    s = sub.add_parser("starter", help="the athletes a new fan is offered in each sport").add_subparsers(dest="action", required=True)
    s.add_parser("list", help="the pinned starters, by sport and order, and each sport's top players")
    s.add_parser("refresh", help="find each sport's top players and their next games now (the worker does it daily)")
    for action, text in (("add", "pin a registry athlete for new fans, after the sport's other pins"), ("remove", "unpin them")):
        x = s.add_parser(action, help=text)
        x.add_argument("ref", help="a registry id (Q1234), a player id or system:id")
        x.add_argument("--sport")
    o = sub.add_parser("socials", help="show or set a followed player's own accounts, for the Feed's judge")
    o.add_argument("ref", help="a player id, registry id or system:id")
    o.add_argument("handles", nargs="*", help="platform=handle, e.g. x=imVkohli; replaces what they had")
    o.add_argument("--sport")
    o.add_argument("--clear", action="store_true", help="take every account off")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            with session() as db:
                rows = decisions(db)
                for o in rows:
                    print(f"{o.action:6}  {o.sport:10}  {o.qid:20}  {o.name}{f'  {o.ids}' if o.ids else ''}{f'  ({o.note})' if o.note else ''}  {o.created_at:%Y-%m-%d}")
                unmatched = unmatches(db)
                if not rows and not unmatched:
                    print("Nothing added, removed or unmatched yet.")
                for u in unmatched:
                    print(f"{'unmatch':7} {u.sport:10}  {u.qid:20}  {u.name}  not {u.person or 'anyone'}{f'  ({u.note})' if u.note else ''}  {u.created_at:%Y-%m-%d}")
        elif args.command == "add":
            with session() as db:
                if args.qid:
                    for athlete in pin(db, args.qid, args.sport, args.note):
                        print(f"Kept {athlete.name} ({athlete.sport}, {athlete.qid}): no import drops them.")
                else:
                    if not (args.sport and args.name):
                        raise Refused("add takes a registry id to pin, or --sport and --name for a new athlete")
                    athlete, new = add(db, args.sport, args.name, aliases=args.alias, teams=args.team, country=args.country, league=args.league, born=args.born, ids=_ids(args.id), note=args.note)
                    print(f"{'Added' if new else 'Already in the registry, now kept:'} {athlete.name} ({athlete.sport}, {athlete.qid}).")
        elif args.command == "remove":
            with session() as db:
                found = find(db, args.ref, args.sport)
                lines = describe(db, found)
                if not lines:
                    raise Refused(f"nothing has the id {args.ref}")
                print("This removes:\n  " + "\n  ".join(lines))
                if not args.yes and ask("Remove all of this? [y/N] ").strip().lower() not in ("y", "yes"):
                    print("Nothing removed.")
                    return 1
                gone = remove(db, found, args.note)
            forget_cards(gone)
            print(f"Removed {len(gone)} page(s) and {len(found.athletes)} registry athlete(s); they're blocked from coming back.")
        elif args.command == "restore":
            with session() as db:
                for o in restore(db, args.ref, args.sport):
                    print(f"No longer {'removed' if o.action == 'remove' else 'kept'}: {o.name} ({o.sport}, {o.qid}).")
        elif args.command == "unmatch":
            with session() as db:
                done = unmatch(db, find(db, args.ref, args.sport), args.person, args.note)
            cards = take_photos_off([image for _, _, image in done if image]) if any(now for _, now, _ in done) else 0
            for a, now, _ in done:
                print(f"{'Took back' if now else 'Nothing to take back now for'} {a.name} ({a.sport}, {a.qid}){f' (not matched to {args.person})' if args.person and not now else ''}; "
                      f"no run matches them to {args.person or 'anyone'} again.")
            if cards:
                print(f"Took the photo off {cards} card(s).")
        elif args.command == "rematch":
            with session() as db:
                for u in rematch(db, args.ref, args.sport, args.person):
                    print(f"No longer unmatched from {u.person or 'anyone'}: {u.name} ({u.sport}, {u.qid}); the next weekly run may match them again.")
        elif args.command == "starter" and args.action == "list":
            with session() as db:
                rows = starters.listed(db)
                for s, a in rows:
                    why = " (not offered: its registry row is gone)" if a is None else "" if search.offered(db, [a], None) else " (not offered: no running source has their id)"
                    print(f"{s.sport:10}  {s.position:3}  {s.qid:20}  {a.name if a else '?'}{why}")
                if not rows:
                    print("Nothing pinned: new fans are offered who's playing, the most followed and the best known.")
                for sport in sports.ENABLED:
                    top = starters.top_pool(sport.key)
                    names: dict[str, str] = {q: n for q, n in db.execute(select(Athlete.qid, Athlete.name).where(Athlete.sport == sport.key, Athlete.qid.in_([e["qid"] for e in top])))} if top else {}
                    print(f"{sport.key:10}  top: {', '.join(str(names.get(e['qid']) or e['qid']) for e in top) or 'none (refresh, or no running source)'}")
        elif args.command == "starter" and args.action == "refresh":
            for sport, n in starters.refresh().items():
                print(f"{sport:10}  {n} top players playing lately or soon")
        elif args.command == "socials":
            with session() as db:
                found = find(db, args.ref, args.sport)
                if args.handles or args.clear:
                    if args.handles and args.clear:
                        raise Refused("--clear takes every account off; give handles or --clear, not both")
                    set_socials(found, [_social(h) for h in args.handles])
                if not found.players:
                    raise Refused("only a followed player's page has accounts, and no page has that id")
                for p in found.players:
                    accounts = ", ".join(f"{x['platform']}=@{x['handle']}" for x in p.socials or [])
                    print(f"{p.name} ({p.sport}, page {p.id}): {accounts or 'no accounts'}")
        elif args.command == "starter" and args.action == "add":
            with session() as db:
                people = [registry.Person.of(a) for a in starter_athletes(find(db, args.ref, args.sport), args.sport)]
            # Outside a transaction: it asks the source, and may learn their id.
            through = {(p.qid, p.sport): check_source(p) for p in people}
            with session() as db:
                for a, new in make_starters(db, starter_athletes(find(db, args.ref, args.sport), args.sport)):
                    print(f"{'Starter' if new else 'Already a starter'}: {a.name} ({a.sport}, {a.qid}), followed live through {through.get((a.qid, a.sport))}.")
        elif args.command == "starter":
            with session() as db:
                found = find(db, args.ref, args.sport)
                if not starters.remove(db, found.athletes):
                    raise Refused(f"{args.ref} isn't a starter")
                print(f"No longer offered to new fans: {', '.join(f'{a.name} ({a.sport})' for a in found.athletes)}.")
    except Refused as exc:
        print(f"Can't: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    logs.setup()
    sys.exit(main(sys.argv[1:]))
