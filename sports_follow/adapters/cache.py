"""What the data sources answered, kept in Redis: shared by the API and the worker, and kept across deploys.

Each source's client reads through a Cache of its own, named after its budget (read): this process's copy
first, so a live poll's few seconds of cache cost no round trip; then Redis; then the source, and only
then is a request counted (budget.py). A response is kept as the source sent it (its document, not what a
parser made of it, so a parser change needs nothing flushed) with when it was fetched, and each caller
judges freshness by its own ttl against that time: the same match is read with 5 s while it's live and
24 h once it's over (where a copy kept while it was on is read again: Adapter.read_snapshot). A ttl of 0
is fetched every time and never kept. Threads of one process asking for the same thing at once send one
request between them: the first fetches, the others take what it kept. This process's copy holds at most
ROOM bytes of each source's documents (and `size` of them), letting go of those used longest ago: Redis
still has them.

"When it was fetched" is when the request went out, not when its answer came back, and Redis keeps only
the latest of those: an answer that took longer, sent before another process's, never replaces the
newer one (a match page's in-play read, overtaken by a live poll that saw full time).

The Redis is SPORTS_FOLLOW_CACHE_REDIS_URL (config.CACHE_REDIS_URL): in production one of its own, capped
and evicting the least recently used key, so a full cache never pushes out a job, a lock or a budget
(deploy/compose.yaml). A read never fails because of it: while it can't be reached, each process keeps its
own copy, as before, and tries Redis again every RETRY seconds.

Keys: src:<budget name>:<sha1 of the request>, a hash of `at` (when its request went out, epoch seconds)
and `doc` (zlib-compressed JSON), kept KEEP seconds after it's written, or the ttl if that's longer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
import weakref
import zlib
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

log = logging.getLogger("sports_follow.cache")

# A response stays in Redis at least this long: the same request is read with different ttls, and a caller
# with a longer one may take a copy its writer no longer would (a live poll's, once the match is over).
KEEP = 24 * 3600
RETRY = 30  # seconds between tries while Redis can't be reached
# A process's copy of one source's documents, in bytes of their JSON; read, they take about four times that
# (an ESPN cricket scorecard is some 370 kB of JSON, 1.7 MB read). A history run reads them by the hundred.
ROOM = 4 * 2**20
T = TypeVar("T")

# Write a copy only over an older one (or none), in one step, so two processes' writes can't interleave.
_PUT = """
local at = redis.call('HGET', KEYS[1], 'at')
if at and tonumber(at) >= tonumber(ARGV[1]) then return 0 end
redis.call('HSET', KEYS[1], 'at', ARGV[1], 'doc', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
"""


class RedisStore:
    """The cache Redis, connected on first use. Its timeouts are short: a cache that's slow to answer is skipped."""

    def __init__(self) -> None:
        self._client: Any = None
        self._put: Any = None

    def _redis(self) -> Any:
        if self._client is None:
            import redis

            from ..config import CACHE_REDIS_URL

            self._client = redis.Redis.from_url(CACHE_REDIS_URL, socket_timeout=1, socket_connect_timeout=1)
            self._put = self._client.register_script(_PUT)
        return self._client

    def get(self, key: str) -> tuple[float, bytes] | None:
        at, doc = self._redis().hmget(key, "at", "doc")
        return (float(at), doc) if at is not None and doc is not None else None

    def put(self, key: str, at: float, doc: bytes, ex: int) -> bool:
        self._redis()
        return bool(self._put(keys=[key], args=[repr(at), doc, ex]))


class MemoryStore:
    """Redis's part, in memory: for tests. Two Caches of one name over one store are two processes."""

    def __init__(self) -> None:
        self.data: dict[str, tuple[float, bytes, float]] = {}  # key -> (at, doc, when it expires)
        self.expiry: dict[str, int] = {}  # key -> the seconds it was last kept for
        self._guard = threading.Lock()

    def get(self, key: str) -> tuple[float, bytes] | None:
        hit = self.data.get(key)
        return (hit[0], hit[1]) if hit and hit[2] > time.time() else None

    def put(self, key: str, at: float, doc: bytes, ex: int) -> bool:
        with self._guard:
            kept = self.get(key)
            if kept and kept[0] >= at:
                return False
            self.data[key] = (at, doc, time.time() + ex)
            self.expiry[key] = ex
            return True


STORE: Any = RedisStore()
_down = {"until": 0.0, "warned": 0.0}


def _store(method: str, *args: Any) -> Any:
    """A call to Redis; None while it can't be reached, and for RETRY seconds after: the cache goes on in
    each process alone."""
    now = time.time()
    if _down["until"] > now:
        return None
    try:
        return getattr(STORE, method)(*args)
    except Exception as exc:
        _down["until"] = now + RETRY
        if now - _down["warned"] > 60:
            log.warning("source cache: Redis unreachable (%s); each process keeps its own copy until it's back", exc)
            _down["warned"] = now
        return None


# This thread's fetches under way, innermost last: [when its request went out] each (sending).
_fetching = threading.local()


def sending() -> None:
    """Said by a fetch that may wait before its request goes out (a source taking one request at a time):
    its answer is as old as this moment, not as old as the wait."""
    stack = getattr(_fetching, "stack", None)
    if stack:
        stack[-1][0] = time.time()


class NotKept(Exception):
    """A read under kept_only that only the source could answer."""


def kept_only(fn: Callable[..., T]) -> Callable[..., T]:
    """fn, run so that each read it makes through a Cache is answered from what's kept (this process's copy, then
    Redis), and one that would ask the source, or only looks (peek) and finds nothing, raises NotKept instead: to
    tell whether a call would send anything before counting it against a limit (search.run). Only reads through a
    Cache are held back."""

    def kept(*args: Any, **kwargs: Any) -> T:
        before = getattr(_fetching, "kept_only", False)
        _fetching.kept_only = True
        try:
            return fn(*args, **kwargs)
        finally:
            _fetching.kept_only = before

    return kept


_caches: weakref.WeakSet[Cache] = weakref.WeakSet()


class Cache:
    """One source's responses: this process's copy (at most `size` of them, `room` bytes of JSON between them),
    then Redis under src:<namespace>:."""

    def __init__(self, namespace: str, size: int = 2000, room: int = ROOM) -> None:
        self.namespace = namespace
        self.size = size
        self.room = room
        # This process's copy: key -> (fetched at, document, its size as JSON), the one used longest ago first.
        self.entries: dict[str, tuple[float, Any, int]] = {}
        self.held = 0  # the sizes of everything in it
        self._guard = threading.Lock()
        self._flights: dict[str, list[Any]] = {}  # key -> [its lock, threads holding or waiting for it]
        _caches.add(self)

    def read(self, key: str, ttl: float, fetch: Callable[[], T], keep: float = 0) -> T:
        """The document for key: a copy younger than ttl seconds, from this process or Redis, else fetch().
        A failed fetch keeps nothing. With ttl 0 (or less), fetched every time and never kept. What's fetched
        stays in Redis for keep seconds where that's longer than ttl: for a caller whose next read may take a
        longer ttl (OpenDota's match, asked again every few minutes till it's parsed, then never)."""
        if getattr(_fetching, "kept_only", False):
            return self._kept(key, ttl)
        if ttl <= 0:
            return fetch()
        hit = self._local(key, ttl)
        if hit is not None:
            return hit[1]
        with self._flight(key):
            # Another thread may have fetched it while this one waited, or another process before.
            hit = self._local(key, ttl) or self._shared(key, ttl)
            if hit is not None:
                return hit[1]
            sent = [time.time()]  # when its request goes out, unless the fetch says later (sending)
            stack = _fetching.__dict__.setdefault("stack", [])
            stack.append(sent)
            try:
                data = fetch()
            finally:
                stack.pop()
            self._keep(key, sent[0], data, max(ttl, keep))
            return data

    def peek(self, key: str, ttl: float) -> Any:
        """A kept document younger than ttl seconds, or None: never fetched, nor waited for."""
        if getattr(_fetching, "kept_only", False):
            return self._kept(key, ttl)
        hit = self._local(key, ttl) or self._shared(key, ttl)
        return hit[1] if hit is not None else None

    def _kept(self, key: str, ttl: float) -> Any:
        """Under kept_only: the kept document, or NotKept."""
        hit = (self._local(key, ttl) or self._shared(key, ttl)) if ttl > 0 else None
        if hit is None:
            raise NotKept(key)
        return hit[1]

    def _local(self, key: str, ttl: float) -> tuple[float, Any] | None:
        hit = self.entries.get(key)
        if hit is None or time.time() - hit[0] >= ttl:
            return None
        with self._guard:
            if self.entries.get(key) is hit:
                self.entries[key] = self.entries.pop(key)  # used now: the last to go
        return hit[0], hit[1]

    def _shared(self, key: str, ttl: float) -> tuple[float, Any] | None:
        kept = _store("get", self._key(key))
        if not kept or time.time() - kept[0] >= ttl:
            return None
        try:
            raw = zlib.decompress(kept[1])
            data = json.loads(raw)
        except (zlib.error, ValueError, TypeError):  # not one of ours: as good as missing
            return None
        self._put(key, kept[0], data, len(raw))  # judged by when it was fetched, not when this process read it
        return kept[0], data

    def _keep(self, key: str, at: float, data: Any, ttl: float) -> None:
        try:
            raw = json.dumps(data, separators=(",", ":")).encode()
        except (TypeError, ValueError):  # not JSON: kept in this process only
            self._put(key, at, data, len(repr(data)))
            return
        self._put(key, at, data, len(raw))
        if _store("put", self._key(key), at, zlib.compress(raw), max(math.ceil(ttl), KEEP)) is False:
            # Redis has a copy whose request went out later: this process's next read takes that one.
            with self._guard:
                if self.entries.get(key, (None,))[0] == at:
                    self.held -= self.entries.pop(key)[2]

    def _put(self, key: str, at: float, data: Any, size: int) -> None:
        with self._guard:
            old = self.entries.pop(key, None)
            self.held += size - (old[2] if old else 0)
            self.entries[key] = (at, data, size)
            # Past its room or its count, what was used longest ago goes, never the one just kept: a document
            # bigger than the room is kept alone, so threads waiting for it take this one.
            while len(self.entries) > 1 and (self.held > self.room or len(self.entries) > self.size):
                self.held -= self.entries.pop(next(iter(self.entries)))[2]

    def _key(self, key: str) -> str:
        return f"src:{self.namespace}:{hashlib.sha1(key.encode()).hexdigest()}"

    @contextmanager
    def _flight(self, key: str) -> Iterator[None]:
        """Held by the one thread fetching key; the others asking for it wait here, not for other keys."""
        with self._guard:
            flight = self._flights.setdefault(key, [threading.Lock(), 0])
            flight[1] += 1
        try:
            with flight[0]:
                yield
        finally:
            with self._guard:
                flight[1] -= 1
                if not flight[1]:
                    del self._flights[key]


def forget() -> None:
    """Empty every process copy, and try Redis again at once: for tests."""
    for cache in list(_caches):
        with cache._guard:
            cache.entries.clear()
            cache.held = 0
    _down.update(until=0.0, warned=0.0)
