"""A followed player's older results, read once and kept, a little at a time.

Refreshes store every finished game the source's current window returns (a season of team
schedules, six weeks of tennis, a month of cricket, two months of chess) with the scorecards of the
last five (structured._refresh). This lane fills in the rest, gently:

1. Scorecards (the player's line) of stored games that don't have one, newest first.
2. Older pages the refresh window doesn't reach (Adapter.history_pages): last season's schedules
   for football and basketball, weekly draws back a year for tennis, daily feeds back six months
   for cricket. One page per player per run, so every player's history moves along.

Each page and scorecard is read once, then kept. One lane for the whole system (a lock), at most
READS source requests a run, one run a minute from the scheduler tick. A scorecard the source says it
doesn't have (a 404, no data) is marked missing rather than retried, and a page it has nothing for is
passed. Any other failure (the source unreachable, a 5xx, a rejected key, data that can't be read)
leaves the scorecard, or the player's pages, to wait RETRY and read again, twice as long after each
failure in a row; one bad page never holds up the other players. A source that failed to answer or asked
us to slow down isn't asked again in the same run. A source with an hourly allowance (Sportmonks) is
skipped while it's down to the share kept for live games. Only the players of sports that run are read (a sport
turned off in the admin panel isn't, though its source runs for another: an esports game), and only the games
a running source can read, so those of a source switched off never crowd out the rest.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import exists, select, tuple_
from sqlalchemy.orm import Session

from . import bus, shutdown, sports, structured
from .adapters import ADAPTERS, AdapterError, PlayerRef
from .db import session
from .models import Event, EventPlayer, Follow, Player, SourceBinding

log = logging.getLogger("sports_follow.history")

READS = int(os.environ.get("SPORTS_FOLLOW_HISTORY_READS", 20))  # source requests per run (one run a minute)
LOCK = "history-lane"
RETRY, RETRY_MAX = 10 * 60, 6 * 3600  # a failed read waits RETRY, twice as long after each failure in a row
RESTING = "history:resting"  # scorecards whose read failed: "event_id:player_id" -> {"failures", "retry_at"}


def _rate_limited(exc: Exception) -> bool:
    return type(exc).__name__ == "RateLimited"


def _not_found(exc: Exception) -> bool:
    """The source answered that it has no such game or page (a 404, or nothing there), as opposed to not
    answering: unreachable, a 5xx, a rejected key."""
    text = str(exc)
    return isinstance(exc, AdapterError) and (type(exc).__name__ == "NotFound" or "HTTP 404" in text or " has no " in text)


def _wait(failures: int) -> int:
    return min(RETRY * 2 ** min(failures - 1, 16), RETRY_MAX)


def _off(db: Session) -> list[str]:
    """Followed players' sports that are turned off (sports.on), as their pages name them ("CS2"): the lane reads
    nothing of theirs, as the scheduler doesn't (BUG-176)."""
    return [sport for sport in db.scalars(select(Player.sport).where(Player.id.in_(select(Follow.player_id))).distinct()) if not sports.on(sport)]


def _of_sports_off(off: list[str]):
    return select(Player.id).where(Player.sport.in_(off))


def _followed_bindings(db: Session, off: list[str]) -> list[SourceBinding]:
    followed = select(Follow.player_id)
    rows = db.scalars(select(SourceBinding).where(SourceBinding.purpose == "fixtures", SourceBinding.player_id.in_(followed), SourceBinding.adapter.in_(list(ADAPTERS)),
                                                  *([SourceBinding.player_id.not_in(_of_sports_off(off))] if off else []))
                      .order_by(SourceBinding.player_id, SourceBinding.priority.desc(), SourceBinding.id.desc())).all()
    best: dict[int, SourceBinding] = {}
    for row in rows:
        best.setdefault(row.player_id, row)  # structured.binding()'s pick: highest priority, newest
    return list(best.values())


def _pending(history: dict[str, Any] | None, now: datetime) -> bool:
    """Whether a binding has older pages to read now: not all read, and not waiting after a failure."""
    state = history or {}
    retry = state.get("retry_at")
    return not state.get("done") and not (retry and datetime.fromisoformat(retry) > now)


def _missing_scorecards(waiting: list[tuple[int, int]] | None = None, off: list[str] | None = None):
    """Stored results of followed players still waiting for their scorecard, newest first, leaving out
    those waiting after a failed read. Only those the lane can read: a game whose source runs, of a player a
    running source has, in a sport that runs (off: those turned off). A source switched off keeps its games'
    rows waiting for when it runs again, but they never fill the lane's window (BUG-179)."""
    bound = select(SourceBinding.player_id).where(SourceBinding.purpose == "fixtures", SourceBinding.adapter.in_(list(ADAPTERS)))
    query = (
        select(EventPlayer, Event)
        .join(Event, Event.id == EventPlayer.event_id)
        .where(Event.status == "final", EventPlayer.result.is_not(None), EventPlayer.result["scorecard"].astext == "false", EventPlayer.player_id.in_(select(Follow.player_id)),
               Event.live_binding["adapter"].astext.in_(list(ADAPTERS)), EventPlayer.player_id.in_(bound))
        .order_by(Event.start_utc.desc().nulls_last(), Event.id.desc())
    )
    if off:
        query = query.where(EventPlayer.player_id.not_in(_of_sports_off(off)))
    return query.where(tuple_(EventPlayer.event_id, EventPlayer.player_id).not_in(waiting)) if waiting else query


def _rests() -> dict[str, dict[str, Any]]:
    """Scorecards whose last read failed: "event_id:player_id" -> {"failures", "retry_at" (epoch seconds)}.
    One not tried again for a day (its player unfollowed, or a refresh read it meanwhile) is forgotten."""
    r = bus.sync_redis()
    rests = {key: json.loads(raw) for key, raw in (r.hgetall(RESTING) or {}).items()}
    old = [key for key, rest in rests.items() if rest["retry_at"] < time.time() - 24 * 3600]
    if old:
        r.hdel(RESTING, *old)
    return {key: rest for key, rest in rests.items() if key not in old}


def _waiting(rests: dict[str, dict[str, Any]]) -> list[tuple[int, int]]:
    now = time.time()
    return [tuple(int(n) for n in key.split(":")) for key, rest in rests.items() if rest["retry_at"] > now]


def has_work() -> bool:
    if bus.is_locked(LOCK):
        return False
    waiting = _waiting(_rests())
    with session() as db:
        off = _off(db)
        if db.scalar(select(exists(_missing_scorecards(waiting, off).limit(1).subquery()))):
            return True
        now = datetime.now(timezone.utc)
        return any(_pending(b.history, now) for b in _followed_bindings(db, off))


def run() -> None:
    """One run: scorecards first, then older pages, within READS requests."""
    token = bus.try_lock(LOCK, 600)
    if not token:
        return
    budget, done = READS, {"scorecards": 0, "missing": 0, "pages": 0, "games": 0, "failed": 0}
    down: set[str] = set()  # sources that failed to answer this run, or asked us to slow down
    try:
        budget = _scorecards(budget, done, down)
        if budget > 0:
            _pages(budget, done, down)
    except Exception:
        log.exception("history lane crashed")
    finally:
        bus.release(LOCK, token)
        if any(done.values()):
            log.info("history: %(scorecards)d scorecard(s), %(missing)d missing, %(pages)d older page(s) with %(games)d game(s), %(failed)d failed", done)


def _failed(system: str, what: str, exc: Exception, down: set[str], done: dict[str, int]) -> None:
    """A read that went wrong and is tried again later. A source that didn't answer isn't asked again this
    run; data that couldn't be read is logged in full."""
    if _rate_limited(exc):
        if system not in down:
            log.info("history: %s asked us to slow down; next run continues", system)
        down.add(system)
        return
    done["failed"] += 1
    if isinstance(exc, AdapterError):
        down.add(system)
        log.warning("history: %s from %s failed: %s", what, system, exc)
    else:
        log.exception("history: %s from %s crashed", what, system)


def _scorecards(budget: int, done: dict[str, int], down: set[str]) -> int:
    rests = _rests()
    with session() as db:
        off = _off(db)
        athlete = {b.player_id: PlayerRef(**b.locator).athlete_id for b in _followed_bindings(db, off)}
        # More than a run reads, so a source that's down this run doesn't hold up the others.
        todo = [(ep.event_id, ep.player_id) for ep, _ in db.execute(_missing_scorecards(_waiting(rests), off).limit(budget * 5))]
    for event_id, player_id in todo:
        if budget <= 0 or shutdown.stopping.is_set():
            break
        if player_id not in athlete:
            continue
        with session() as db:
            event = db.get(Event, event_id)
            binding = dict(event.live_binding or {}) if event else {}
        adapter = ADAPTERS.get(binding.get("adapter", ""))
        if adapter is None or adapter.system in down or not structured.spare(adapter):
            continue
        key = f"{event_id}:{player_id}"
        budget -= 1
        try:
            snap = adapter.snapshot(binding["locator"], final=True)
            with session() as db:
                structured.add_scorecard(db, db.get(Event, event_id), player_id, athlete[player_id], snap)
            done["scorecards"] += 1
        except Exception as exc:
            if not _not_found(exc):
                _failed(adapter.system, f"scorecard of event {event_id}", exc, down, done)
                if not _rate_limited(exc):
                    failures = rests.get(key, {}).get("failures", 0) + 1
                    bus.sync_redis().hset(RESTING, key, json.dumps({"failures": failures, "retry_at": time.time() + _wait(failures)}))
                continue
            with session() as db:
                row = db.get(EventPlayer, (event_id, player_id))
                if row is not None and row.result is not None:
                    row.result = {**row.result, "scorecard": "missing"}  # the source has none; don't ask again
            done["missing"] += 1
        if key in rests:
            bus.sync_redis().hdel(RESTING, key)
    return budget


def _pages(budget: int, done: dict[str, int], down: set[str]) -> int:
    now = datetime.now(timezone.utc)
    with session() as db:
        todo = [(b.id, b.player_id) for b in _followed_bindings(db, _off(db)) if _pending(b.history, now)]
    for binding_id, player_id in todo:
        if budget <= 0 or shutdown.stopping.is_set():
            break
        with session() as db:
            binding = db.get(SourceBinding, binding_id)
            adapter = ADAPTERS.get(binding.adapter) if binding else None
            if binding is None or adapter is None:
                continue
            ref, state = PlayerRef(**binding.locator), dict(binding.history or {})
        if adapter.system in down or not structured.spare(adapter):
            continue
        try:
            fixtures, read = [], "pages" in state
            if not read:
                budget -= 1
                today = now.date()
                pages = adapter.history_pages(ref, today)
                state = {"anchor": today.isoformat(), "pages": pages, "next": 0, "done": not pages}
            else:
                page = state["pages"][state["next"]]
                budget -= int(page.get("cost", 1))
                try:
                    fixtures = adapter.history(ref, page)
                except AdapterError as exc:
                    if not _not_found(exc):
                        raise
                    log.info("history: %s has nothing for player %s's page %s: %s", adapter.system, player_id, page, exc)
                state["next"] += 1
                state["done"] = state["next"] >= len(state["pages"])
            with session() as db:
                binding, player = db.get(SourceBinding, binding_id), db.get(Player, player_id)
                if binding is None or player is None:
                    continue
                for f in fixtures:
                    event = structured.upsert_event(db, adapter, player, f)
                    structured.keep_result(db, event.id, player_id, structured.result_item(adapter, f, None, event, ref), scorecard=False)
                binding.history = {k: v for k, v in state.items() if k not in ("failures", "retry_at", "error")}
        except Exception as exc:
            _failed(adapter.system, f"older results of player {player_id}", exc, down, done)
            if not _rate_limited(exc):
                # This player's pages wait; the others carry on.
                failures = int(state.get("failures") or 0) + 1
                with session() as db:
                    binding = db.get(SourceBinding, binding_id)
                    if binding is not None:
                        retry = now + timedelta(seconds=_wait(failures))
                        binding.history = {**(binding.history or {}), "failures": failures, "retry_at": retry.isoformat(), "error": str(exc)[:300]}
            continue
        if read:
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

