"""What the data sources answered, kept in Redis: shared by the API and the worker, and kept across deploys.

Each source's client reads through a Cache of its own, named after its budget (read): this process's copy
first, so a live poll's few seconds of cache cost no round trip; then Redis; then the source, and only
then is a request counted (budget.py). A response is kept as the source sent it (its document, not what a
parser made of it, so a parser change needs nothing flushed) with when it was fetched, and each caller
judges freshness by its own ttl against that time: the same match is read with 5 s while it's live and
24 h once it's over. A ttl of 0 is fetched every time and never kept. Threads of one process asking for
the same thing at once send one request between them: the first fetches, the others take what it kept.

The Redis is SPORTS_FOLLOW_CACHE_REDIS_URL (config.CACHE_REDIS_URL): in production one of its own, capped
and evicting the least recently used key, so a full cache never pushes out a job, a lock or a budget
(deploy/compose.yaml). A read never fails because of it: while it can't be reached, each process keeps its
own copy, as before, and tries Redis again every RETRY seconds.

Keys: src:<budget name>:<sha1 of the request>, holding zlib-compressed JSON [fetched at, document], kept
KEEP seconds after they're written, or the ttl if that's longer.
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
T = TypeVar("T")


class RedisStore:
    """The cache Redis, connected on first use. Its timeouts are short: a cache that's slow to answer is skipped."""

    def __init__(self) -> None:
        self._client: Any = None

    def _redis(self) -> Any:
        if self._client is None:
            import redis

            from ..config import CACHE_REDIS_URL

            self._client = redis.Redis.from_url(CACHE_REDIS_URL, socket_timeout=1, socket_connect_timeout=1)
        return self._client

    def get(self, key: str) -> bytes | None:
        return self._redis().get(key)

    def set(self, key: str, value: bytes, ex: int) -> None:
        self._redis().set(key, value, ex=ex)


class MemoryStore:
    """Redis's part, in memory: for tests. Two Caches of one name over one store are two processes."""

    def __init__(self) -> None:
        self.data: dict[str, tuple[bytes, float]] = {}  # key -> (value, when it expires)
        self.expiry: dict[str, int] = {}  # key -> the seconds it was last kept for

    def get(self, key: str) -> bytes | None:
        hit = self.data.get(key)
        return hit[0] if hit and hit[1] > time.time() else None

    def set(self, key: str, value: bytes, ex: int) -> None:
        self.data[key] = (value, time.time() + ex)
        self.expiry[key] = ex


STORE: Any = RedisStore()
_down = {"until": 0.0, "warned": 0.0}


def _store(method: str, *args: Any, **kwargs: Any) -> Any:
    """A call to Redis; None while it can't be reached, and for RETRY seconds after: the cache goes on in
    each process alone."""
    now = time.time()
    if _down["until"] > now:
        return None
    try:
        return getattr(STORE, method)(*args, **kwargs)
    except Exception as exc:
        _down["until"] = now + RETRY
        if now - _down["warned"] > 60:
            log.warning("source cache: Redis unreachable (%s); each process keeps its own copy until it's back", exc)
            _down["warned"] = now
        return None


_caches: weakref.WeakSet[Cache] = weakref.WeakSet()


class Cache:
    """One source's responses: this process's copy (at most `size` of them), then Redis under src:<namespace>:."""

    def __init__(self, namespace: str, size: int = 2000) -> None:
        self.namespace = namespace
        self.size = size
        self.entries: dict[str, tuple[float, Any]] = {}  # this process's copy: key -> (fetched at, document)
        self._guard = threading.Lock()
        self._flights: dict[str, list[Any]] = {}  # key -> [its lock, threads holding or waiting for it]
        _caches.add(self)

    def read(self, key: str, ttl: float, fetch: Callable[[], T]) -> T:
        """The document for key: a copy younger than ttl seconds, from this process or Redis, else fetch().
        A failed fetch keeps nothing. With ttl 0 (or less), fetched every time and never kept."""
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
            data = fetch()
            self._keep(key, time.time(), data, ttl)
            return data

    def peek(self, key: str, ttl: float) -> Any:
        """A kept document younger than ttl seconds, or None: never fetched, nor waited for."""
        hit = self._local(key, ttl) or self._shared(key, ttl)
        return hit[1] if hit is not None else None

    def _local(self, key: str, ttl: float) -> tuple[float, Any] | None:
        hit = self.entries.get(key)
        return hit if hit is not None and time.time() - hit[0] < ttl else None

    def _shared(self, key: str, ttl: float) -> tuple[float, Any] | None:
        raw = _store("get", self._key(key))
        if not raw:
            return None
        try:
            at, data = json.loads(zlib.decompress(raw))
        except (zlib.error, ValueError, TypeError):  # not one of ours: as good as missing
            return None
        if time.time() - at >= ttl:
            return None
        self._put(key, (at, data))  # judged by when it was fetched, not when this process read it
        return at, data

    def _keep(self, key: str, at: float, data: Any, ttl: float) -> None:
        self._put(key, (at, data))
        try:
            raw = zlib.compress(json.dumps([at, data], separators=(",", ":")).encode())
        except (TypeError, ValueError):  # not JSON: kept in this process only
            return
        _store("set", self._key(key), raw, ex=max(math.ceil(ttl), KEEP))

    def _put(self, key: str, entry: tuple[float, Any]) -> None:
        with self._guard:
            self.entries[key] = entry
            if len(self.entries) > self.size:  # the oldest quarter go
                for old, _ in sorted(self.entries.items(), key=lambda kv: kv[1][0])[: self.size // 4]:
                    self.entries.pop(old, None)

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
    _down.update(until=0.0, warned=0.0)
