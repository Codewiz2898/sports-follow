"""What the admin panel changes on the running app (admin.py): the sports and sources turned off, and what
each source may spend (its allowance a window, the reserve kept for live games, the fans' share).

The server's settings stay the ceiling: SPORTS_FOLLOW_SPORTS and SPORTS_FOLLOW_SOURCES say what this
build may run, and a licensed source still needs its token. The panel turns things off within that, and
back on; it can't turn on a source the settings leave out (ESPN's unofficial endpoints in a public build).

They're rows in Postgres (models.Control). Every process (the API, the worker, a command) keeps a copy,
read again every REFRESH seconds by a thread of its own, so a change reaches all of them within that,
without a restart, and nothing waits on the database to read one. sports.ENABLED and adapters.ADAPTERS
follow them, and budget.take spends what the panel allows.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("sports_follow.controls")

REFRESH = 10  # seconds between reads of the rows, in each process
SPORTS_OFF = "sports_off"  # [sport key, …]
SOURCES_OFF = "sources_off"  # [adapter system, …]
BUDGET = "budget:"  # + a budget's name: what the panel changed of it (budget.effective)


@dataclass(frozen=True)
class Controls:
    sports_off: frozenset[str] = frozenset()
    sources_off: frozenset[str] = frozenset()
    budgets: dict[str, dict[str, Any]] = field(default_factory=dict)  # by budget name


def parse(rows: dict[str, Any]) -> Controls:
    """The rows as the app reads them; a row of the wrong shape (written by hand) counts as unset."""

    def names(value: Any) -> frozenset[str]:
        return frozenset(v for v in value if isinstance(v, str)) if isinstance(value, list) else frozenset()

    budgets = {k[len(BUDGET):]: v for k, v in rows.items() if k.startswith(BUDGET) and isinstance(v, dict)}
    return Controls(sports_off=names(rows.get(SPORTS_OFF)), sources_off=names(rows.get(SOURCES_OFF)), budgets=budgets)


# ---------------------------------------------------------------- where they're kept


class DbStore:
    """The rows in Postgres, which every process reads."""

    def read(self) -> dict[str, Any]:
        from sqlalchemy import select

        from .db import session
        from .models import Control

        with session() as db:
            return {key: value for key, value in db.execute(select(Control.key, Control.value)).all()}

    def change(self, key: str, fn: Callable[[Any], Any]) -> None:
        """Write fn(the row's value, None if unset) as the row, or remove it if that's None: in one
        transaction, holding the row, so two changes at once don't lose one."""
        from sqlalchemy import delete, func, select
        from sqlalchemy.dialects.postgresql import insert

        from .db import session
        from .models import Control

        with session() as db:
            db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"control:{key}", 0))))
            value = fn(db.scalar(select(Control.value).where(Control.key == key)))
            if value is None:
                db.execute(delete(Control).where(Control.key == key))
            else:
                stmt = insert(Control).values(key=key, value=value)
                db.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"value": stmt.excluded.value, "updated_at": func.now()}))


class MemoryStore:
    """The rows in this process: for tests."""

    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}

    def read(self) -> dict[str, Any]:
        return dict(self.rows)

    def change(self, key: str, fn: Callable[[Any], Any]) -> None:
        value = fn(self.rows.get(key))
        if value is None:
            self.rows.pop(key, None)
        else:
            self.rows[key] = value


STORE: Any = DbStore()


# ---------------------------------------------------------------- this process's copy


class _Copy:
    def __init__(self) -> None:
        self.now = Controls()
        self.version = 0  # goes up each time what's read differs from the copy
        self.loaded = False
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None


_copy = _Copy()


def load() -> Controls:
    """Read the rows now (a change made here is seen at once; other processes within REFRESH)."""
    rows = STORE.read()
    fresh = parse(rows)
    with _copy.lock:
        if fresh != _copy.now:
            _copy.now, _copy.version = fresh, _copy.version + 1
        _copy.loaded = True
    return fresh


def _keep_reading() -> None:
    failing = False
    while True:
        time.sleep(REFRESH)
        try:
            load()
            if failing:
                log.info("admin controls: read again")
            failing = False
        except Exception as exc:
            if not failing:
                log.warning("admin controls: can't read them (%s); the last copy stands until they can be", exc)
            failing = True


def _first_read() -> None:
    with _copy.lock:
        if _copy.loaded:
            return
        _copy.loaded = True  # once: a database that's down isn't asked again on every read
        start = isinstance(STORE, DbStore) and _copy.thread is None
        if start:
            _copy.thread = threading.Thread(target=_keep_reading, name="controls", daemon=True)
    try:
        load()
    except Exception as exc:
        log.warning("admin controls: can't read them (%s); nothing is turned off until they can be", exc)
    if start and _copy.thread is not None:
        _copy.thread.start()


def current() -> Controls:
    """What the panel has changed, as this process last read it."""
    if not _copy.loaded:
        _first_read()
    return _copy.now


def version() -> int:
    """A number that changes whenever what the panel has changed does: what's built from it is built again."""
    if not _copy.loaded:
        _first_read()
    return _copy.version


def reset(store: Any) -> None:
    """Start over with these rows (tests)."""
    global STORE
    STORE = store
    with _copy.lock:
        _copy.now, _copy.version, _copy.loaded = Controls(), _copy.version + 1, False


# ---------------------------------------------------------------- changing them


def _switch(key: str, name: str, on: bool) -> None:
    def fn(value: Any) -> list[str] | None:
        off = set(value) if isinstance(value, list) else set()
        off = off - {name} if on else off | {name}
        return sorted(off) or None

    STORE.change(key, fn)
    load()


def turn_sport(key: str, on: bool) -> None:
    _switch(SPORTS_OFF, key, on)


def turn_source(system: str, on: bool) -> None:
    _switch(SOURCES_OFF, system, on)


def set_budget(name: str, change: dict[str, Any] | None) -> None:
    """What the panel changes of a budget (budget.effective says what each field means); None goes back to
    what its source declares."""
    STORE.change(f"{BUDGET}{name}", lambda _: change or None)
    load()
