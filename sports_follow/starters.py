"""Starter players: the few athletes a new fan, who follows nobody yet, is offered in each sport, in the
welcome's picker and on an empty Today (web/src/components/Starters.tsx), and as the search page's
"Try" names.

They're data, not code: the operator sets them with `python -m sports_follow.players starter add|remove|list`
(players.py), and a sport that's turned off offers none. Each is a registry athlete, pinned so no import
drops them; removing the athlete (`players remove`) takes the starter row with them. The app is offered
only those a running source covers by id, or who already have a page (search.offered), so following a
starter never costs a fan one of their AI researches.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import search, sports
from .models import Athlete, Starter

PER_SPORT = 8  # the most a sport offers; the picker shows them as tiles, two to a row


def add(db: Session, athlete: Athlete) -> bool:
    """Offer a registry athlete to new fans, after the sport's other starters. False if they already were."""
    last = db.scalar(select(func.max(Starter.position)).where(Starter.sport == athlete.sport))
    stmt = insert(Starter).values(qid=athlete.qid, sport=athlete.sport, position=(last or 0) + 1)
    return bool(db.execute(stmt.on_conflict_do_nothing(index_elements=["qid", "sport"]).returning(Starter.id)).first())


def remove(db: Session, athletes: list[Athlete]) -> int:
    """Stop offering these athletes. Returns how many starters that took out."""
    if not athletes:
        return 0
    pairs = [(a.qid, a.sport) for a in athletes]
    return len(db.execute(delete(Starter).where(tuple_(Starter.qid, Starter.sport).in_(pairs)).returning(Starter.id)).all())


def listed(db: Session) -> list[tuple[Starter, Athlete | None]]:
    """Every starter by sport and order, with their registry row (None if it has gone)."""
    on_row = (Athlete.qid == Starter.qid) & (Athlete.sport == Starter.sport)
    return [(s, a) for s, a in db.execute(select(Starter, Athlete).outerjoin(Athlete, on_row).order_by(Starter.sport, Starter.position)).all()]


def offer(db: Session, fan: str | None) -> list[dict[str, Any]]:
    """What a new fan is offered: each running sport with starters, in the order the app lists sports, and
    its starters as result rows ({"key", "label", "players"})."""
    keys = [s.key for s in sports.ENABLED]
    rows = db.execute(
        select(Athlete)
        .join(Starter, (Athlete.qid == Starter.qid) & (Athlete.sport == Starter.sport))
        .where(Starter.sport.in_(keys))
        .order_by(Starter.position)
    ).scalars().all()
    out = []
    for sport in sports.ENABLED:
        players = search.offered(db, [a for a in rows if a.sport == sport.key], fan)[:PER_SPORT]
        if players:
            out.append({"key": sport.key, "label": sport.label, "players": players})
    return out
