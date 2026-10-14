"""One rate-limit layer for every source: what each may spend, and for what, shared by every process.

A source's client declares its allowance (a Budget: requests a window, a margin never spent, a reserve
kept for live games, whether a fan's request may reach it) and, before each request it sends, takes one
(take). It tells what the source said back: the allowance left where the source reports it (observe),
and "slow down" (pause). Counts are kept in Redis, so the API and the worker spend from one allowance;
if Redis can't be reached, each process counts its own until it can.

Every request is made for something, its lane, set once where the work starts (a worker job, an API
request) and carried into the threads it uses (carry):

    live     a game on now: its polls come first
    fan      a fan waiting: search, a preview, following
    refresh  a followed player's fixtures and stats
    history  older results (history.py)
    import   the weekly registry import

The background lanes (refresh, history, import) stop while a window's allowance is down to the source's
reserve, which is kept for live games and fans. A scarce source gives fans a share of their own (fan: at
most that many fan requests a window): one fan following a pro is a request, but searches or previews in
a loop would spend what live games need, so past the share they're refused, and logged.

    .venv/bin/python -m sports_follow.usage     # each source's spend this hour and today, by lane
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, TypeVar

from .base import AdapterError

log = logging.getLogger("sports_follow.budget")

LANES = ("live", "fan", "refresh", "history", "import")
BACKGROUND = ("refresh", "history", "import")
_lane: contextvars.ContextVar[str] = contextvars.ContextVar("source_lane", default="refresh")
T = TypeVar("T")


class RateLimited(AdapterError):
    """The source's allowance (or the share of it this lane may use) is spent, or it asked us to slow down."""


@dataclass(frozen=True)
class Window:
    seconds: int  # 60, 3600, 86400: counted in windows of this many seconds, from the epoch
    requests: int  # the plan's allowance a window
    margin: int = 0  # never spent: another process may count a moment late


@dataclass(frozen=True)
class Budget:
    """What one source (one API key) may spend."""

    name: str  # its Redis keys: "sportmonks"
    windows: tuple[Window, ...] = ()  # none: no published limit (counted, never refused)
    reserve: int = 0  # requests of each window kept for live games and fans: background lanes stop there
    fan: int | None = None  # the most fan requests a (first) window may take, for a scarce source; None: no cap of its own
    pause: int = 60  # seconds every lane waits after the source says slow down (429)


# ---------------------------------------------------------------- lanes


def lane() -> str:
    """The lane this work is in (refresh if nothing said)."""
    return _lane.get()


@contextmanager
def in_lane(name: str) -> Iterator[None]:
    """Requests made inside are for this lane."""
    assert name in LANES, name
    token = _lane.set(name)
    try:
        yield
    finally:
        _lane.reset(token)


def carry(fn: Callable[..., T]) -> Callable[..., T]:
    """fn, run in the lane of the code that made it: for work handed to a thread pool, which doesn't take
    the submitting thread's context with it."""
    context = contextvars.copy_context()
    return lambda *args, **kwargs: context.copy().run(fn, *args, **kwargs)


# ---------------------------------------------------------------- the counts


class MemoryStore:
    """Counts in this process: for tests, and while Redis can't be reached."""

    def __init__(self) -> None:
        self.data: dict[str, tuple[int, float]] = {}
        self.lock = threading.Lock()

    def incr(self, key: str, ttl: int, by: int = 1) -> int:
        with self.lock:
            value, until = self.data.get(key, (0, 0.0))
            if until and until < time.time():
                value = 0
            value += by
            self.data[key] = (value, until if until and until >= time.time() else time.time() + ttl)
            return value

    def get(self, key: str) -> int | None:
        value, until = self.data.get(key, (0, 0.0))
        return value if key in self.data and until >= time.time() else None

    def put(self, key: str, value: int, ttl: int) -> None:
        with self.lock:
            self.data[key] = (value, time.time() + ttl)


class RedisStore:
    """Counts every process shares."""

    def incr(self, key: str, ttl: int, by: int = 1) -> int:
        # One transaction: the count and its end (set once, when the window's first request makes the key).
        pipe = _redis().pipeline()
        pipe.incrby(key, by)
        pipe.expire(key, ttl, nx=True)
        value, _ = pipe.execute()
        return int(value)

    def get(self, key: str) -> int | None:
        raw = _redis().get(key)
        return int(raw) if raw is not None else None

    def put(self, key: str, value: int, ttl: int) -> None:
        _redis().set(key, value, ex=ttl)


def _redis() -> Any:
    from .. import bus

    return bus.sync_redis()


STORE: Any = RedisStore()
_fallback = MemoryStore()
_warned = {"at": 0.0}


def _store_call(method: str, *args: Any) -> Any:
    """A store call; while Redis can't be reached, this process's own count stands in."""
    try:
        return getattr(STORE, method)(*args)
    except Exception as exc:
        if not isinstance(STORE, RedisStore):
            raise
        if time.time() - _warned["at"] > 60:
            log.warning("source budgets: Redis unreachable (%s); counting in this process until it's back", exc)
            _warned["at"] = time.time()
        return getattr(_fallback, method)(*args)


def _window_key(budget: Budget, bucket: str, w: Window, now: float) -> str:
    return f"budget:{budget.name}:{bucket}:{w.seconds}:{int(now // w.seconds)}"


def _left_key(budget: Budget, bucket: str) -> str:
    return f"budget:{budget.name}:{bucket}:left"


def _paused_key(budget: Budget) -> str:
    return f"budget:{budget.name}:paused"


def _use_key(budget: Budget, lane_name: str, what: str, span: str, now: float) -> str:
    n = int(now // (3600 if span == "h" else 86400))
    return f"budget:use:{budget.name}:{lane_name}:{what}:{span}{n}"


def _note(budget: Budget, lane_name: str, what: str, now: float) -> None:
    for span, ttl in (("h", 2 * 3600), ("d", 2 * 86400)):
        _store_call("incr", _use_key(budget, lane_name, what, span, now), ttl)


# ---------------------------------------------------------------- asking


def _headroom(budget: Budget, lane_name: str) -> int:
    """Requests of a window this lane must leave: the margin always, the reserve too in the background."""
    return budget.reserve if lane_name in BACKGROUND else 0


def take(budget: Budget, bucket: str = "", lane_name: str | None = None) -> None:
    """Count one request this lane is about to send, or refuse it (RateLimited): the source asked us to
    slow down, what it says is left is down to the margin (or, for a background lane, the reserve), a
    window's allowance less the same is spent, or so is the fans' share of a scarce source. A refused
    request isn't counted."""
    lane_name = lane_name or lane()
    now = time.time()
    paused = _store_call("get", _paused_key(budget))
    if paused and paused > now:
        _note(budget, lane_name, "refused", now)
        raise RateLimited(f"{budget.name} asked us to slow down; paused until it's over")
    keep = _headroom(budget, lane_name)
    left = _store_call("get", _left_key(budget, bucket))
    if budget.windows and left is not None and left <= min(w.margin for w in budget.windows) + keep:
        _note(budget, lane_name, "refused", now)
        raise RateLimited(f"{budget.name} says {left} requests are left{f' ({bucket})' if bucket else ''}: kept for {'live games' if keep else 'the margin'}")
    # (key, ttl, limit, why): the fans' share of a scarce source first, then each window less what this lane leaves.
    counts = []
    if lane_name == "fan" and budget.fan is not None and budget.windows:
        w = budget.windows[0]
        why = f"{budget.name}: fans' share ({budget.fan} {per(w.seconds)}) is spent"
        counts.append((f"budget:{budget.name}:{bucket}:fan:{w.seconds}:{int(now // w.seconds)}", w.seconds + 60, budget.fan, why))
    for w in budget.windows:
        where = f"{budget.name}{f' ({bucket})' if bucket else ''}"
        why = f"{where}: the {lane_name} share of its {w.requests} requests {per(w.seconds)} is spent" if keep else f"{where}: its {w.requests} requests {per(w.seconds)} are spent"
        counts.append((_window_key(budget, bucket, w, now), w.seconds + 60, w.requests - w.margin - keep, why))
    taken: list[tuple[str, int]] = []
    for key, ttl, limit, why in counts:
        taken.append((key, ttl))
        if _store_call("incr", key, ttl) > limit:
            for k, t in taken:
                _store_call("incr", k, t, -1)
            _note(budget, lane_name, "refused", now)
            if lane_name == "fan":
                log.warning("source budgets: a fan request refused: %s", why)
            raise RateLimited(why)
    _note(budget, lane_name, "sent", now)


def spare(budget: Budget, bucket: str = "", lane_name: str = "history") -> bool:
    """Whether a background read (a refresh, older results, the import) has room now, before starting a
    batch of them: the source isn't pausing us, and every window has more left than its reserve."""
    now = time.time()
    paused = _store_call("get", _paused_key(budget))
    if paused and paused > now:
        return False
    keep = _headroom(budget, lane_name)
    left = _store_call("get", _left_key(budget, bucket))
    if budget.windows and left is not None and left <= min(w.margin for w in budget.windows) + keep:
        return False
    return all((_store_call("get", _window_key(budget, bucket, w, now)) or 0) < w.requests - w.margin - keep for w in budget.windows)


def observe(budget: Budget, bucket: str, left: int, resets_in: float) -> None:
    """What the source says is left of its allowance (its headers or body), until it resets: it counts
    every key's requests, including ones from before this process started."""
    _store_call("put", _left_key(budget, bucket), max(0, int(left)), max(1, int(resets_in)))


def left(budget: Budget, bucket: str = "") -> int | None:
    """What the source last said is left of its allowance, while that holds; None if it hasn't said."""
    return _store_call("get", _left_key(budget, bucket))


def pause(budget: Budget) -> None:
    """The source said slow down (429): no lane asks it for budget.pause seconds."""
    _store_call("put", _paused_key(budget), int(time.time() + budget.pause), budget.pause + 5)


def _span(seconds: int) -> str:
    return {60: "minute", 3600: "hour", 86400: "day"}.get(seconds, f"{seconds} s")


def per(seconds: int) -> str:
    """ "an hour", "a minute", "a day"."""
    span = _span(seconds)
    return f"{'an' if span == 'hour' else 'a'} {span}"


# ---------------------------------------------------------------- what was spent


def usage(budgets: list[Budget], now: float | None = None) -> list[dict[str, Any]]:
    """Each source's requests this hour and today, by lane: sent, and refused."""
    now = now or time.time()
    rows = []
    for b in budgets:
        for lane_name in LANES:
            row = {"source": b.name, "lane": lane_name}
            for what in ("sent", "refused"):
                for span in ("h", "d"):
                    row[f"{what}_{span}"] = _store_call("get", _use_key(b, lane_name, what, span, now)) or 0
            if any(row[k] for k in ("sent_h", "sent_d", "refused_h", "refused_d")):
                rows.append(row)
    return rows


def known() -> list[Budget]:
    """Every source's budget (each client module's BUDGET)."""
    from . import espn, liquipedia, opendota, pandascore, pubg, sportmonks
    from . import lichess_chess

    return [m.BUDGET for m in (sportmonks, espn, lichess_chess, pandascore, liquipedia, pubg, opendota)]
