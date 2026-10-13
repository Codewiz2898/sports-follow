"""Redis: the hot copy of each card and the per-player channels fans subscribe to (design section 7)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import redis

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


def store_card(player_id: int, card: dict[str, Any]) -> None:
    """Write the hot copy, then tell subscribers. Nothing durable lives only here."""
    r = sync_redis()
    r.set(card_key(player_id), json.dumps(card, default=str))
    publish(player_id, {"type": "card", "player_id": player_id, "card": card})


# Release and extend a lock only while it is still the caller's: a job that outlived its lock must not
# delete or prolong the lock of the job that took over.
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"
_EXTEND = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) end return 0"


def try_lock(name: str, seconds: int) -> str | None:
    """A lock so two workers never run the same player's job at once. Returns the holder's token, or
    None if someone else holds it; release and extend with the token."""
    token = uuid.uuid4().hex
    return token if sync_redis().set(f"lock:{name}", token, nx=True, ex=seconds) else None


def release(name: str, token: str | None = None) -> None:
    """Free a lock: with its token, only if the caller still holds it."""
    if token is None:
        sync_redis().delete(f"lock:{name}")
    else:
        sync_redis().eval(_RELEASE, 1, f"lock:{name}", token)


def is_locked(name: str) -> bool:
    return bool(sync_redis().exists(f"lock:{name}"))


def extend(name: str, seconds: int, token: str | None = None) -> bool:
    """Keep a lock held by a long-running job (the live poller renews it every poll, a build as the agent
    works). With its token, only if the caller still holds it. Returns whether it's held seconds more."""
    if token is None:
        return bool(sync_redis().expire(f"lock:{name}", seconds))
    return bool(sync_redis().eval(_EXTEND, 1, f"lock:{name}", token, seconds))
