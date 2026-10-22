"""Your sports (BHA-653): the sports a fan said they watch, in their order. The welcome's first step saves them, and
Following's Edit changes them (PUT /api/me/sports); Today gives each one that's on it a chip, keeps offering its
starters, and puts its games first.

They're kept per fan (FanSport), as follows are: an anonymous fan's under their cookie's id, moved into their account
on sign-in (merge, accounts.adopt) and deleted with it (accounts.FAN_ROWS). Only the sports the app offers now count
(offered: GET /api/sports' `on`). One turned off in the admin panel, or with nothing to show its players, is out of
sight but kept, and back when it's offered again, as a followed player of it is (server._following): in its place, or
after the sports the fan saved meanwhile.
Dropping a sport unfollows nobody in it.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import sports
from .adapters import served
from .models import FanSport

GIVEN_MOST = 100  # sport keys a request may name: far more than there are sports, so more is refused


def offered() -> set[str]:
    """The keys of the sports the app offers fans now (GET /api/sports' `on`): the only ones a fan's list shows or takes."""
    live = served()
    return {s.key for s in sports.ALL if sports.offered(s, s.key in live)}


def _lock(db: Session, fan: str) -> None:
    """One change to a fan's sports at a time: two devices saving at once, or a sign-in bringing a device's."""
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"fan-sport:{fan}", 0))))


def _kept(db: Session, fan: str) -> list[str]:
    """Every sport kept for the fan, in their order: the hidden ones too."""
    return list(db.scalars(select(FanSport.sport).where(FanSport.fan_id == fan).order_by(FanSport.position, FanSport.id)))


def mine(db: Session, fan: str, shown: set[str] | None = None) -> list[str]:
    """The fan's sports, in their order: those the app offers now (`shown`, else offered())."""
    shown = offered() if shown is None else shown
    return [k for k in _kept(db, fan) if k in shown]


def replace(db: Session, fan: str, keys: Iterable[str], shown: set[str] | None = None) -> list[str]:
    """The fan's sports are these now, in this order: those the app offers (`shown`, else offered()), once each. One
    the fan had that it doesn't offer now stays, after them, for when it's back; one no module knows goes. Returns
    the list as mine says it."""
    shown = offered() if shown is None else shown
    wanted = list(dict.fromkeys(k for k in keys if k in shown))
    known = {s.key for s in sports.ALL}
    _lock(db, fan)
    order = wanted + [k for k in _kept(db, fan) if k not in shown and k in known]
    db.execute(delete(FanSport).where(FanSport.fan_id == fan, FanSport.sport.not_in(order)))
    if order:
        rows = insert(FanSport).values([{"fan_id": fan, "sport": k, "position": i} for i, k in enumerate(order)])
        db.execute(rows.on_conflict_do_update(index_elements=["fan_id", "sport"], set_={"position": rows.excluded.position}))  # a sport kept keeps its date
    return wanted


def merge(db: Session, anon: str, fan: str) -> None:
    """An anonymous fan's sports into their account's (accounts.adopt): the account's first, in its order, then the
    device's it didn't have, in the device's. One at a time with either's own changes (replace's lock)."""
    _lock(db, fan)
    _lock(db, anon)
    have = set(_kept(db, fan))
    after = db.scalar(select(func.coalesce(func.max(FanSport.position) + 1, 0)).where(FanSport.fan_id == fan)) or 0
    adding = [(k, at) for k, at in db.execute(select(FanSport.sport, FanSport.created_at).where(FanSport.fan_id == anon).order_by(FanSport.position, FanSport.id)) if k not in have]
    if adding:
        db.execute(insert(FanSport).values([{"fan_id": fan, "sport": k, "position": after + i, "created_at": at} for i, (k, at) in enumerate(adding)]).on_conflict_do_nothing())
    db.execute(delete(FanSport).where(FanSport.fan_id == anon))
