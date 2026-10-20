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
    feed     videos and posts about players, for the feed (curator.py)

The background lanes (refresh, history, import, feed) stop while a window's allowance is down to the source's
reserve, which is kept for live games and fans. A scarce source gives fans a share of their own (fan: at
most that many fan requests a window): one fan following a pro is a request, but searches or previews in
a loop would spend what live games need, so past the share they're refused, and logged.

    .venv/bin/python -m sports_follow.usage     # each source's spend this hour and today, by lane

What a source declares can be changed while the app runs, from the admin panel (controls.py): a new
plan's allowance, a bigger reserve, a smaller fans' share. Every count reads the budget as the panel
left it (effective).
"""

from __future__ import annotations

import contextvars
import fnmatch
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterator, TypeVar
from urllib.parse import urlparse

from .. import controls
from .base import AdapterError

log = logging.getLogger("sports_follow.budget")

LANES = ("live", "fan", "refresh", "history", "import", "feed")
BACKGROUND = ("refresh", "history", "import", "feed")
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
    urls: tuple[str, ...] = ()  # where its requests go: a page the research agent fetches there spends it too


def effective(budget: Budget) -> Budget:
    """The budget as the admin panel left it (controls.set_budget): its windows (each one's allowance and
    margin), reserve, fans' share and pause where the panel changed them, the rest as its source declares.
    A change of the wrong shape (written by hand) is left out."""
    change = controls.current().budgets.get(budget.name)
    if not change:
        return budget
    fields: dict[str, Any] = {}
    try:
        if isinstance(change.get("windows"), list):
            fields["windows"] = tuple(Window(int(w["seconds"]), int(w["requests"]), int(w.get("margin", 0))) for w in change["windows"])
    except (KeyError, TypeError, ValueError):
        log.warning("source budgets: %s's windows from the admin panel aren't readable; its own stand", budget.name)
    for name in ("reserve", "pause"):
        if type(change.get(name)) is int:
            fields[name] = change[name]
    if "fan" in change and (change["fan"] is None or type(change["fan"]) is int):
        fields["fan"] = change["fan"]
    return replace(budget, **fields)


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

    def keys(self, pattern: str) -> list[str]:
        return [k for k, (_, until) in list(self.data.items()) if fnmatch.fnmatchcase(k, pattern) and until >= time.time()]


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

    def keys(self, pattern: str) -> list[str]:
        return [k.decode() if isinstance(k, bytes) else k for k in _redis().scan_iter(match=pattern, count=500)]


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


def take(budget: Budget, bucket: str = "", lane_name: str | None = None, cost: int = 1) -> None:
    """Count one request this lane is about to send, or refuse it (RateLimited): the source asked us to
    slow down, what it says is left is down to the margin (or, for a background lane, the reserve), a
    window's allowance less the same is spent, or so is the fans' share of a scarce source. A refused
    request isn't counted. cost: what the request spends of each window, where a source prices its calls apart
    (a search worth a hundred reads)."""
    budget = effective(budget)
    lane_name = lane_name or lane()
    now = time.time()
    paused = _store_call("get", _paused_key(budget))
    if paused and paused > now:
        _note(budget, lane_name, "refused", now)
        raise RateLimited(f"{budget.name} asked us to slow down; paused until it's over")
    keep = _headroom(budget, lane_name)
    left = _store_call("get", _left_key(budget, bucket))
    if budget.windows and left is not None and left - cost < min(w.margin for w in budget.windows) + keep:
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
        if _store_call("incr", key, ttl, cost) > limit:
            for k, t in taken:
                _store_call("incr", k, t, -cost)
            _note(budget, lane_name, "refused", now)
            if lane_name == "fan":
                log.warning("source budgets: a fan request refused: %s", why)
            raise RateLimited(why)
    _note(budget, lane_name, "sent", now)


def spare(budget: Budget, bucket: str = "", lane_name: str = "history") -> bool:
    """Whether a background read (a refresh, older results, the import) has room now, before starting a
    batch of them: the source isn't pausing us, and every window has more left than its reserve."""
    budget = effective(budget)
    now = time.time()
    paused = _store_call("get", _paused_key(budget))
    if paused and paused > now:
        return False
    keep = _headroom(budget, lane_name)
    left = _store_call("get", _left_key(budget, bucket))
    if budget.windows and left is not None and left <= min(w.margin for w in budget.windows) + keep:
        return False
    return all((_store_call("get", _window_key(budget, bucket, w, now)) or 0) < w.requests - w.margin - keep for w in budget.windows)


def room(budget: Budget, bucket: str = "", lane_name: str = "feed") -> tuple[int, int] | None:
    """What this lane may still spend of the source's tightest window now, and that window's allowance: (left, of).
    What the source says is left counts too, against its first window. None for a source with no published limit."""
    budget = effective(budget)
    if not budget.windows:
        return None
    now = time.time()
    keep = _headroom(budget, lane_name)
    rooms = [(w.requests - w.margin - keep - (_store_call("get", _window_key(budget, bucket, w, now)) or 0), w.requests) for w in budget.windows]
    said = _store_call("get", _left_key(budget, bucket))
    if said is not None:
        rooms.append((said - min(w.margin for w in budget.windows) - keep, budget.windows[0].requests))
    return min(rooms, key=lambda r: r[0])


def observe(budget: Budget, bucket: str, left: int, resets_in: float) -> None:
    """What the source says is left of its allowance (its headers or body), until it resets: it counts
    every key's requests, including ones from before this process started."""
    _store_call("put", _left_key(budget, bucket), max(0, int(left)), max(1, int(resets_in)))


def left(budget: Budget, bucket: str = "") -> int | None:
    """What the source last said is left of its allowance, while that holds; None if it hasn't said."""
    return _store_call("get", _left_key(budget, bucket))


def pause(budget: Budget) -> None:
    """The source said slow down (429): no lane asks it for budget.pause seconds."""
    budget = effective(budget)
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
            row: dict[str, Any] = {"source": b.name, "lane": lane_name}
            for what in ("sent", "refused"):
                for span in ("h", "d"):
                    row[f"{what}_{span}"] = _store_call("get", _use_key(b, lane_name, what, span, now)) or 0
            if any(row[k] for k in ("sent_h", "sent_d", "refused_h", "refused_d")):
                rows.append(row)
    return rows


WINDOW_NAMES = {60: "minute", 3600: "hour", 86400: "day"}


def standing(budget: Budget, now: float | None = None) -> dict[str, Any]:
    """Where one source stands now (metrics.py reads it every minute): each window's requests so far
    against its allowance, for each bucket that has counted any; what the source says is left, by
    bucket; and whether it's pausing us."""
    budget = effective(budget)
    now = now or time.time()
    prefix = f"budget:{budget.name}:"
    buckets = {""}
    for w in budget.windows:
        buckets |= {key[len(prefix):].rsplit(":", 2)[0] for key in _store_call("keys", f"{prefix}*:{w.seconds}:{int(now // w.seconds)}")}
    windows = [{"bucket": b, "window": WINDOW_NAMES.get(w.seconds, f"{w.seconds}s"), "used": _store_call("get", _window_key(budget, b, w, now)) or 0, "allowance": w.requests}
               for b in sorted(buckets) for w in budget.windows]
    left_now = {b: v for b in sorted(buckets) if (v := left(budget, b)) is not None}
    paused = _store_call("get", _paused_key(budget))
    return {"windows": [w for w in windows if w["used"] or not w["bucket"]], "left": left_now, "paused": bool(paused and paused > now)}


def known() -> list[Budget]:
    """Every budget a known source spends (Adapter.budgets, VideoSource.budgets), once each, in adapters.KNOWN's
    order, then adapters.VIDEOS'."""
    from . import KNOWN, VIDEOS

    found: dict[str, Budget] = {}
    for adapter in [*KNOWN.values(), *VIDEOS.values()]:
        for b in adapter.budgets():
            found.setdefault(b.name, b)
    return list(found.values())


def for_url(url: str) -> Budget | None:
    """The budget a request to this URL spends, if a known source's requests go there. Budget.urls are
    https:// prefixes ending at a "/", matched against the URL's host and path; the longest wins (part
    of an API may sit behind a limit of its own)."""
    parts = urlparse(url)
    where = f"https://{parts.hostname or ''}{parts.path or '/'}"
    found = [(len(u), b) for b in known() for u in b.urls if where.startswith(u)]
    return max(found, key=lambda match: match[0])[1] if found else None
