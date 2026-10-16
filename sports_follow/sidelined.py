"""Who's out: the injuries and bans a source lists league-wide, whoever follows the players, for the facts on
Today's Wire (week.py). Sportmonks football has them (its teams' "sidelined"); a sport whose sources don't
simply has no facts.

A source that lists every player out of its competitions' games (an adapter's sidelined()) is read by the
worker twice a day (worker.read_sidelined), and each read is compared with the last one's, kept here by the
source's own id for each spell (models.Sidelined):

- a spell the last read didn't have, or one it had as over that is on again (back on its team's list, or
  its end date put back), is news that the player is out (out_at), when it started in the last NEW_DAYS:
  one already under way when reads began, or when its team was first read, isn't;
- a spell that's over, gone from its team's list or past the date the player was expected back, is news
  that they're back (back_at), unless another spell still has them out.

A read that fails changes nothing, and a spell of a team the read didn't list (a team relegated out of the
plan's leagues) is left as it was. The player's sport words each fact (Sport.out_fact, back_fact).

Spells and players are kept by the source's ids, so a fan's player is found by the identity they're bound by
(PlayerIdentity), never by name. How well known each player is (the registry's Wikipedia editions, the
Wire's order for players nobody follows) is looked up at each read: by the source's id where the registry
has it, else by the registry's own test that a namesake is the same person (registry.same_person: the birth
date, else a team). A spell no read has listed for KEEP_DAYS goes (retention.py), and its facts with it.

    .venv/bin/python -m sports_follow.sidelined     # read every source now
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable
from datetime import date, datetime, timezone
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import logs, registry
from .adapters import ADAPTERS, Absence, Adapter
from .db import session
from .models import Athlete, Sidelined

log = logging.getLogger(__name__)

NEW_DAYS = 7  # a spell first listed this long after it started is still news: sources are a few days behind
KEEP_DAYS = 14  # a spell no read has listed for this long goes, and its facts with it (retention.py)


def sources() -> list[Adapter]:
    """The running sources that list who's out."""
    return [a for a in ADAPTERS.values() if callable(getattr(a, "sidelined", None))]


def _lately(since: date | None, today: date, first: bool) -> bool:
    """Whether a spell new to a read started lately enough to be news. One with no start date is news,
    except on a source's first read, when every spell is new."""
    return (today - since).days <= NEW_DAYS if since else not first


def _fame(db: Session, sport: str, system: str, listed: Iterable[Absence]) -> dict[str, int]:
    """How well known each player out is, by the source's id for them: the registry's Wikipedia editions for
    the athlete holding that id, else for a namesake the registry takes for the same person (birth date, else
    a team: registry.same_person) who holds no other id in the source. 0 for anyone the registry doesn't know."""
    refs = {a.ref.athlete_id: a.ref for a in listed}
    if not refs:
        return {}
    held = Athlete.ids[system].astext
    by_id = dict(db.execute(select(held, func.max(Athlete.sitelinks)).where(Athlete.sport == sport, Athlete.ids.has_key(system), held.in_(list(refs))).group_by(held)).all())
    namesakes: dict[str, list[Athlete]] = {}
    names = {r.name.lower() for r in refs.values() if r.name}
    for a in db.scalars(select(Athlete).where(Athlete.sport == sport, Athlete.current, func.lower(Athlete.name).in_(list(names)))) if names else ():
        namesakes.setdefault(a.name.lower(), []).append(a)
    fame: dict[str, int] = {}
    for athlete_id, ref in refs.items():
        known = by_id.get(athlete_id)
        if known is None:
            alike = [a for a in namesakes.get(ref.name.lower(), []) if (a.ids or {}).get(system) in (None, athlete_id) and registry.same_person(registry.Person.of(a), ref)]
            known = max((a.sitelinks or 0 for a in alike), default=0)
        fame[athlete_id] = int(known or 0)
    return fame


def read(system: str, now: datetime | None = None) -> dict[str, int]:
    """Read who a source has out, compare it with its last read and keep the news (the module's docstring).
    Returns how many spells it listed, how many it found news that a player is out, and how many players
    back: nothing from a source turned off since the read was asked for (the admin panel)."""
    adapter = ADAPTERS.get(system)
    if adapter is None or not callable(getattr(adapter, "sidelined", None)):
        return {}
    teams: dict[str, list[Absence]] = cast(Any, adapter).sidelined()
    now = now or datetime.now(timezone.utc)
    today = now.date()
    listed = [a for spells in teams.values() for a in spells]
    with session() as db:
        stored = {row.source_id: row for row in db.scalars(select(Sidelined).where(Sidelined.system == system))}
        first = not stored
        fame = _fame(db, adapter.sport, system, listed)
        ended: list[Sidelined] = []
        out = 0
        for a in listed:
            row = stored.get(a.source_id)
            over = a.until is not None and a.until < today
            if row is None:
                row = stored[a.source_id] = Sidelined(system=system, source_id=a.source_id, ended_at=now if over else None)
                if not over and _lately(a.since, today, first):
                    row.out_at, out = now, out + 1
                db.add(row)
            elif over and row.ended_at is None:
                row.ended_at = now
                ended.append(row)
            elif not over and row.ended_at is not None:
                # On again: news if they were told back, or if it started lately.
                if row.back_at is not None or _lately(a.since, today, first):
                    row.out_at, out = now, out + 1
                row.ended_at = row.back_at = None
            row.athlete_id, row.team_id = a.ref.athlete_id, (a.ref.team_ids or [""])[0]
            row.sport, row.name, row.team = adapter.sport, a.ref.name, (a.ref.team_names or [""])[0]
            row.kind, row.what, row.since, row.until = a.kind, a.what, a.since, a.until
            row.fame, row.seen_at = fame.get(a.ref.athlete_id, 0), now
        on_list = {a.source_id for a in listed}
        for row in stored.values():
            if row.ended_at is None and row.source_id not in on_list and row.team_id in teams:
                row.ended_at = now  # off the list of a team the read has: over
                ended.append(row)
        still_out = {row.athlete_id for row in stored.values() if row.ended_at is None}
        back = {row.athlete_id for row in ended if row.athlete_id not in still_out}
        for row in ended:
            if row.athlete_id in back:
                row.back_at = now
        db.commit()
    return {"listed": len(listed), "out": out, "back": len(back)}


def read_all() -> None:
    """Every source that lists who's out, one after another (worker.read_sidelined); one that fails is logged
    and changes nothing."""
    for adapter in sources():
        try:
            done = read(adapter.system)
            log.info("sidelined %s: %d spells listed, %d news of a player out, %d players back", adapter.system, done.get("listed", 0), done.get("out", 0), done.get("back", 0))
        except Exception:
            log.exception("sidelined %s: the read failed; nothing changed", adapter.system)


if __name__ == "__main__":
    logs.setup()
    if sys.argv[1:]:
        sys.exit("usage: python -m sports_follow.sidelined")
    read_all()
