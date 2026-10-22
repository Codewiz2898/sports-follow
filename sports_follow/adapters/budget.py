"""One rate-limit layer for every source: what each may spend, and for what, shared by every process.

A source's client declares its allowance (a Budget: requests a window, a margin never spent, a reserve
kept for live games, whether a fan's request may reach it) and, before each request it sends, takes one
(take). It tells what the source said back: the allowance left where the source reports it (observe),
and "slow down" (pause). Counts are kept in Redis, so the API and the worker spend from one allowance;
if Redis can't be reached, each process counts its own until it can. A window under an hour (a rate limit, PUBG's
10 a minute) is counted over the last minute, whenever it began; an hour or a day from its start, as the source
resets it.

Every request is made for something, its lane, set once where the work starts (a worker job, an API
request) and carried into the threads it uses (carry):

    live     a game on now: its polls come first
    fan      a fan waiting: search, a preview, following
    refresh  a followed player's fixtures and stats
    history  older results (history.py)
    import   the weekly registry import
    feed     videos and posts about players, for the feed (curator.py)

The background lanes (refresh, history, import, feed) stop while a window's allowance is down to the source's
reserve, which is kept for live games and fans. A source with a reserve, or a scarce one, gives fans a share of
their own (fan: at most that many fan requests a window, fewer than the reserve): one fan following a pro is a
request, but searches or previews in a loop would spend what live games need, so past the share they're
refused, and logged. The fan lane leaves nothing else, so live games keep at least the reserve less that share.

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
from datetime import datetime
from typing import Any, Callable, Iterator, TypeVar
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    seconds: int  # 60, 3600, 86400: counted in windows of this many seconds, from the epoch; under an hour, rolling (_keys)
    requests: int  # the plan's allowance a window
    margin: int = 0  # never spent: another process may count a moment late
    zone: str | None = None  # the time zone its windows start in, where the source's day isn't UTC's: "America/Los_Angeles"


def _offset(zone: str | None, now: float) -> float:
    """Seconds the zone is ahead of UTC at that moment (0 for UTC, or a zone this machine doesn't know)."""
    if not zone:
        return 0.0
    try:
        offset = datetime.fromtimestamp(now, ZoneInfo(zone)).utcoffset()
    except (ZoneInfoNotFoundError, ValueError):
        if time.time() - _warned["zone"] > 3600:
            log.warning("source budgets: no time zone %r here; its windows are counted in UTC", zone)
            _warned["zone"] = time.time()
        return 0.0
    return offset.total_seconds() if offset else 0.0


def _start(w: Window, now: float) -> int:
    """Which of its windows now falls in: the epoch's, or for a zoned one, counted from the zone's midnight."""
    return int((now + _offset(w.zone, now)) // w.seconds)


def _ttl(w: Window) -> int:
    """How long a window's count is kept: past its end, and a zoned day may run 25 hours (the clocks go back)."""
    return w.seconds + (3660 if w.zone else 60)


SLOTS = 12  # a rolling window is counted in this many slots (five seconds of a minute)


def _rolling(w: Window) -> bool:
    """A window under an hour is a source's rate limit (PUBG's 10 a minute), which it holds over any span that long,
    wherever it starts: counted from the epoch, a minute's allowance spent just before a minute began would be there
    again just after. An hour or a day is a plan's allowance, which the source resets on the hour or at its midnight."""
    return w.seconds < 3600


@dataclass(frozen=True)
class Budget:
    """What one source (one API key) may spend."""

    name: str  # its Redis keys: "sportmonks"
    windows: tuple[Window, ...] = ()  # none: no published limit (counted, never refused)
    reserve: int = 0  # requests of each window kept for live games and fans: background lanes stop there
    fan: int | None = None  # the most fan requests a (first) window may take, fewer than the reserve, or for a scarce source; None: no cap of its own
    pause: int = 60  # seconds every lane waits after the source says slow down (429)
    urls: tuple[str, ...] = ()  # where its requests go: a page the research agent fetches there spends it too


def effective(budget: Budget) -> Budget:
    """The budget as the admin panel left it (controls.set_budget): its windows (each one's allowance and
    margin), reserve, fans' share and pause where the panel changed them, the rest as its source declares.
    A window keeps the time zone its source declares for its span (the panel doesn't set one). A change of the
    wrong shape (written by hand) is left out."""
    change = controls.current().budgets.get(budget.name)
    if not change:
        return budget
    fields: dict[str, Any] = {}
    zones = {w.seconds: w.zone for w in budget.windows}
    try:
        if isinstance(change.get("windows"), list):
            fields["windows"] = tuple(Window(int(w["seconds"]), int(w["requests"]), int(w.get("margin", 0)), zones.get(int(w["seconds"])))
                                      for w in change["windows"])
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

    def get_many(self, keys: list[str]) -> list[int | None]:
        return [self.get(k) for k in keys]

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

    def get_many(self, keys: list[str]) -> list[int | None]:
        return [int(raw) if raw is not None else None for raw in _redis().mget(keys)]

    def put(self, key: str, value: int, ttl: int) -> None:
        _redis().set(key, value, ex=ttl)

    def keys(self, pattern: str) -> list[str]:
        return [k.decode() if isinstance(k, bytes) else k for k in _redis().scan_iter(match=pattern, count=500)]


def _redis() -> Any:
    from .. import bus

    return bus.sync_redis()


STORE: Any = RedisStore()
_fallback = MemoryStore()
_warned = {"at": 0.0, "zone": 0.0}


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


# The fans' share is counted under its bucket's name and this (budget:pubg::fan:60:<n>): not a bucket of its own.
FAN = ":fan"


def _keys(budget: Budget, bucket: str, w: Window, now: float) -> list[str]:
    """The keys whose counts are what a window has spent now, the one a request now is counted under first: its
    window's; for a rolling one, the slot now and the SLOTS before it, so no span of w.seconds holds more than its
    allowance (one counted a slot longer, which takes no more than a slot's worth of room)."""
    if not _rolling(w):
        return [f"budget:{budget.name}:{bucket}:{w.seconds}:{_start(w, now)}"]
    slot = int(now // max(1, w.seconds // SLOTS))
    return [f"budget:{budget.name}:{bucket}:{w.seconds}:s{n}" for n in range(slot, slot - SLOTS - 1, -1)]


def _window_key(budget: Budget, bucket: str, w: Window, now: float) -> str:
    """The key a request now is counted under."""
    return _keys(budget, bucket, w, now)[0]


def _used(budget: Budget, bucket: str, w: Window, now: float) -> int:
    """What a window has spent now."""
    return sum(n or 0 for n in _store_call("get_many", _keys(budget, bucket, w, now)))


def _left_key(budget: Budget, bucket: str) -> str:
    return f"budget:{budget.name}:{bucket}:left"


def _paused_key(budget: Budget) -> str:
    return f"budget:{budget.name}:paused"


def _use_key(budget: Budget, lane_name: str, what: str, span: str, now: float) -> str:
    """Where a lane's requests this hour ("h") or today ("d") are counted: today from the midnight its source's day
    starts at, where it says (YouTube's, Pacific), so it matches what its day window has spent; else UTC's."""
    zone = next((w.zone for w in budget.windows if w.seconds == 86400 and w.zone), None)
    n = int(now // 3600) if span == "h" else _start(Window(86400, 0, zone=zone), now)
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
    (a search worth a hundred reads, where a source counts them in one allowance)."""
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
    # (key, ttl, limit, why, the keys of a rolling window's slots before): the fans' share of a scarce source first,
    # then each window less what this lane leaves.
    counts: list[tuple[str, int, int, str, list[str]]] = []
    if lane_name == "fan" and budget.fan is not None and budget.windows:
        w = budget.windows[0]
        why = f"{budget.name}: fans' share ({budget.fan} {per(w.seconds)}) is spent"
        counts.append((f"budget:{budget.name}:{bucket}{FAN}:{w.seconds}:{_start(w, now)}", _ttl(w), budget.fan, why, []))
    for w in budget.windows:
        where = f"{budget.name}{f' ({bucket})' if bucket else ''}"
        why = f"{where}: the {lane_name} share of its {w.requests} requests {per(w.seconds)} is spent" if keep else f"{where}: its {w.requests} requests {per(w.seconds)} are spent"
        key, *before = _keys(budget, bucket, w, now)
        counts.append((key, _ttl(w), w.requests - w.margin - keep, why, before))
    taken: list[tuple[str, int]] = []
    for key, ttl, limit, why, before in counts:
        taken.append((key, ttl))
        if _store_call("incr", key, ttl, cost) + (sum(n or 0 for n in _store_call("get_many", before)) if before else 0) > limit:
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
    return all(_used(budget, bucket, w, now) < w.requests - w.margin - keep for w in budget.windows)


def room(budget: Budget, bucket: str = "", lane_name: str = "feed") -> tuple[int, int] | None:
    """What this lane may still spend of the source's tightest window now, and that window's allowance: (left, of).
    What the source says is left counts too, against its first window. None for a source with no published limit."""
    budget = effective(budget)
    if not budget.windows:
        return None
    now = time.time()
    keep = _headroom(budget, lane_name)
    rooms = [(w.requests - w.margin - keep - _used(budget, bucket, w, now), w.requests) for w in budget.windows]
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
    """Each source's requests this hour and today (its own day, _use_key), by lane: sent, and refused."""
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
        counted = {key.rsplit(":", 1)[1] for key in _keys(budget, "", w, now)}
        for key in _store_call("keys", f"{prefix}*:{w.seconds}:*"):
            parts = key[len(prefix):].rsplit(":", 2)  # its bucket, the window's seconds, and which window or slot
            if len(parts) == 3 and parts[2] in counted and not parts[0].endswith(FAN):  # the fans' share is counted in the window too
                buckets.add(parts[0])
    windows = [{"bucket": b, "window": WINDOW_NAMES.get(w.seconds, f"{w.seconds}s"), "used": _used(budget, b, w, now), "allowance": w.requests}
               for b in sorted(buckets) for w in budget.windows]
    left_now = {b: v for b in sorted(buckets) if (v := left(budget, b)) is not None}
    paused = _store_call("get", _paused_key(budget))
    return {"windows": [w for w in windows if w["used"] or not w["bucket"]], "left": left_now, "paused": bool(paused and paused > now)}


def known() -> list[Budget]:
    """Every budget a known source spends (Adapter.budgets, VideoSource.budgets, NewsSource.budgets), once each, in
    adapters.KNOWN's order, then adapters.VIDEOS', then adapters.NEWS'."""
    from . import KNOWN, NEWS, VIDEOS

    found: dict[str, Budget] = {}
    for adapter in [*KNOWN.values(), *VIDEOS.values(), *NEWS.values()]:
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
