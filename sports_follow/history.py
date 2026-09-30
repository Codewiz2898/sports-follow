"""A followed player's older results, read once and kept, a little at a time.

Refreshes store every finished game the source's current window returns (a season of team
schedules, six weeks of tennis, a month of cricket, two months of chess) with the scorecards of the
last five (structured._refresh). This lane fills in the rest, gently:

1. Scorecards (the player's line) of stored games that don't have one, newest first.
2. Older pages the refresh window doesn't reach (Adapter.history_pages): last season's schedules
   for football and basketball, weekly draws back a year for tennis, daily feeds back six months
   for cricket. One page per player per run, so every player's history moves along.

Each page and scorecard is read once, then kept. One lane for the whole system (a lock), at most
READS source requests a run, one run a minute from the scheduler tick. A scorecard the source
doesn't have is marked missing rather than retried; a rate limit ends the run and is retried next time.
A source with an hourly allowance (Sportmonks) is skipped while it's down to the share kept for live games.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from . import bus, structured
from .adapters import ADAPTERS, AdapterError, PlayerRef
from .db import session
from .models import Event, EventPlayer, Follow, Player, SourceBinding

log = logging.getLogger("sports_follow.history")

READS = int(os.environ.get("SPORTS_FOLLOW_HISTORY_READS", 20))  # source requests per run (one run a minute)
LOCK = "history-lane"


def _rate_limited(exc: Exception) -> bool:
    return type(exc).__name__ == "RateLimited"


def _spare(adapter: Any) -> bool:
    """A source with an hourly allowance (Sportmonks) says when it's down to what live games need."""
    check = getattr(adapter, "spare", None)
    return check() if check else True


def _followed_bindings(db: Session) -> list[SourceBinding]:
    followed = select(Follow.player_id)
    rows = db.scalars(select(SourceBinding).where(SourceBinding.purpose == "fixtures", SourceBinding.player_id.in_(followed), SourceBinding.adapter.in_(list(ADAPTERS))).order_by(SourceBinding.player_id, SourceBinding.priority.desc(), SourceBinding.id.desc())).all()
    best: dict[int, SourceBinding] = {}
    for row in rows:
        best.setdefault(row.player_id, row)  # structured.binding()'s pick: highest priority, newest
    return list(best.values())


def _missing_scorecards():
    """Stored results of followed players still waiting for their scorecard, newest first."""
    return (
        select(EventPlayer, Event)
        .join(Event, Event.id == EventPlayer.event_id)
        .where(Event.status == "final", EventPlayer.result.is_not(None), EventPlayer.result["scorecard"].astext == "false", EventPlayer.player_id.in_(select(Follow.player_id)))
        .order_by(Event.start_utc.desc().nulls_last(), Event.id.desc())
    )


def has_work() -> bool:
    if bus.is_locked(LOCK):
        return False
    with session() as db:
        if db.scalar(select(exists(_missing_scorecards().limit(1).subquery()))):
            return True
        return any(not (b.history or {}).get("done") for b in _followed_bindings(db))


def run() -> None:
    """One run: scorecards first, then older pages, within READS requests."""
    if not bus.try_lock(LOCK, 600):
        return
    budget, done = READS, {"scorecards": 0, "missing": 0, "pages": 0, "games": 0}
    try:
        budget = _scorecards(budget, done)
        if budget > 0:
            _pages(budget, done)
    except AdapterError as exc:
        if not _rate_limited(exc):
            raise
        log.info("history: the source asked us to slow down; next run continues")
    except Exception:
        log.exception("history lane crashed")
    finally:
        bus.release(LOCK)
        if any(done.values()):
            log.info("history: %(scorecards)d scorecard(s), %(missing)d missing, %(pages)d older page(s) with %(games)d game(s)", done)


def _scorecards(budget: int, done: dict[str, int]) -> int:
    with session() as db:
        athlete = {b.player_id: PlayerRef(**b.locator).athlete_id for b in _followed_bindings(db)}
        todo = [(ep.event_id, ep.player_id) for ep, _ in db.execute(_missing_scorecards().limit(budget))]
    for event_id, player_id in todo:
        if budget <= 0 or player_id not in athlete:
            continue
        with session() as db:
            event = db.get(Event, event_id)
            binding = event.live_binding if event else None
            adapter = ADAPTERS.get((binding or {}).get("adapter", ""))
            if event is None or adapter is None or not _spare(adapter):
                continue
            budget -= 1
            try:
                snap = adapter.snapshot(binding["locator"], final=True)
            except AdapterError as exc:
                if _rate_limited(exc):
                    raise
                row = db.get(EventPlayer, (event_id, player_id))
                if row is not None and row.result is not None:
                    row.result = {**row.result, "scorecard": "missing"}  # the source has none; don't ask again
                done["missing"] += 1
                continue
            structured.add_scorecard(db, event, player_id, athlete[player_id], snap)
            done["scorecards"] += 1
    return budget


def _pages(budget: int, done: dict[str, int]) -> int:
    with session() as db:
        todo = [(b.id, b.player_id) for b in _followed_bindings(db) if not (b.history or {}).get("done")]
    for binding_id, player_id in todo:
        if budget <= 0:
            break
        with session() as db:
            binding, player = db.get(SourceBinding, binding_id), db.get(Player, player_id)
            adapter = ADAPTERS.get(binding.adapter) if binding else None
            if binding is None or player is None or adapter is None or not _spare(adapter):
                continue
            ref = PlayerRef(**binding.locator)
            state: dict[str, Any] = dict(binding.history or {})
            if "pages" not in state:
                budget -= 1
                today = datetime.now(timezone.utc).date()
                pages = adapter.history_pages(ref, today)
                binding.history = state = {"anchor": today.isoformat(), "pages": pages, "next": 0, "done": not pages}
                continue
            page = state["pages"][state["next"]]
            budget -= int(page.get("cost", 1))
            fixtures = adapter.history(ref, page)
            for f in fixtures:
                event = structured.upsert_event(db, adapter, player, f)
                structured.keep_result(db, event.id, player_id, structured.result_item(adapter, f, None, event, ref), scorecard=False)
            state["next"] += 1
            state["done"] = state["next"] >= len(state["pages"])
            binding.history = {**state}
            done["pages"] += 1
            done["games"] += len(fixtures)
    return budget


def progress(db: Session, player_id: int) -> dict[str, Any]:
    """How complete a player's history is: older pages left, scorecards still to read."""
    binding = structured.binding(db, player_id)
    state = (binding.history or {}) if binding else {}
    pages_left = len(state.get("pages", [])) - int(state.get("next", 0)) if "pages" in state else None
    waiting = db.scalar(select(exists(_missing_scorecards().where(EventPlayer.player_id == player_id).limit(1).subquery())))
    return {
        "filling": bool(binding and binding.adapter in ADAPTERS and (not state.get("done") or waiting)),
        "pages_left": pages_left,
        "since": state.get("anchor"),
    }

