"""Redis: the hot copy of each card and the per-player channels fans subscribe to (design section 7), and a
channel per game fans follow by itself (matches.py): its board and its own moments."""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

import redis

from . import shutdown
from .config import REDIS_URL

_sync: redis.Redis | None = None


def sync_redis() -> redis.Redis:
    global _sync
    if _sync is None:
        _sync = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _sync


def card_key(player_id: int) -> str:
    return f"card:{player_id}"


def channel(player_id: int) -> str:
    return f"player:{player_id}"


def publish(player_id: int, event: dict[str, Any]) -> None:
    sync_redis().publish(channel(player_id), json.dumps(event, default=str))


def match_channel(event_id: int) -> str:
    return f"match:{event_id}"


def publish_match(event_id: int, event: dict[str, Any]) -> None:
    """Tell the fans following a game by itself: its board as polled, a moment of its own."""
    sync_redis().publish(match_channel(event_id), json.dumps(event, default=str))


def store_card(player_id: int, card: dict[str, Any]) -> None:
    """Write the hot copy, then tell subscribers. Nothing durable lives only here."""
    r = sync_redis()
    r.set(card_key(player_id), json.dumps(card, default=str))
    publish(player_id, {"type": "card", "player_id": player_id, "card": card})


# Release and extend a lock only while it is still the caller's: a job that outlived its lock must not
# delete or prolong the lock of the job that took over.
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"
_EXTEND = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) end return 0"


# The locks this process holds and hasn't released, by name: what a stopping worker frees (release_held).
_held: dict[str, str] = {}
_held_guard = threading.Lock()


def try_lock(name: str, seconds: int) -> str | None:
    """A lock so two workers never run the same player's job at once. Returns the holder's token, or
    None if someone else holds it, or if this worker is stopping (its locks may already have been freed,
    and one taken now would outlive it); release and extend with the token. For a mark that only spaces
    something out and is never released, use cooldown."""
    if shutdown.stopping.is_set():
        return None
    token = uuid.uuid4().hex
    if not sync_redis().set(f"lock:{name}", token, nx=True, ex=seconds):
        return None
    with _held_guard:
        _held[name] = token
    if shutdown.stopping.is_set():  # it began stopping meanwhile, and may have freed its locks already
        release(name, token)
        return None
    return token


def cooldown(name: str, seconds: int) -> bool:
    """True at most once every `seconds` for `name`, across processes: a mark that expires by itself and
    isn't released, not even when the worker stops. is_locked(name) sees it as a lock."""
    return bool(sync_redis().set(f"lock:{name}", uuid.uuid4().hex, nx=True, ex=seconds))


def release(name: str, token: str | None = None) -> None:
    """Free a lock: with its token, only if the caller still holds it."""
    with _held_guard:
        if token is None or _held.get(name) == token:
            _held.pop(name, None)
    if token is None:
        sync_redis().delete(f"lock:{name}")
    else:
        sync_redis().eval(_RELEASE, 1, f"lock:{name}", token)


def held() -> list[str]:
    """The locks this process holds."""
    with _held_guard:
        return list(_held)


def release_held() -> list[str]:
    """Free every lock this process still holds (each only if it's still this process's), for a worker
    that's stopping: the next one can take the work at once. Returns their names."""
    with _held_guard:
        locks = list(_held.items())
        _held.clear()
    for name, token in locks:
        sync_redis().eval(_RELEASE, 1, f"lock:{name}", token)
    return [name for name, _ in locks]


def is_locked(name: str) -> bool:
    return bool(sync_redis().exists(f"lock:{name}"))


def extend(name: str, seconds: int, token: str | None = None) -> bool:
    """Keep a lock held by a long-running job (the live poller renews it every poll, a build as the agent
    works). With its token, only if the caller still holds it. Returns whether it's held seconds more."""
    if token is None:
        return bool(sync_redis().expire(f"lock:{name}", seconds))
    return bool(sync_redis().eval(_EXTEND, 1, f"lock:{name}", token, seconds))
