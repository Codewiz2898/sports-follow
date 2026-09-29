"""Redis: the hot copy of each card and the per-player channels fans subscribe to (design section 7)."""

from __future__ import annotations

import json
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


def try_lock(name: str, seconds: int) -> bool:
    """A short lock so two workers never run the same player's job at once."""
    return bool(sync_redis().set(f"lock:{name}", "1", nx=True, ex=seconds))


def release(name: str) -> None:
    sync_redis().delete(f"lock:{name}")


def is_locked(name: str) -> bool:
    return bool(sync_redis().exists(f"lock:{name}"))


def extend(name: str, seconds: int) -> None:
    """Keep a lock held by a long-running job (the live poller renews it every poll)."""
    sync_redis().expire(f"lock:{name}", seconds)
