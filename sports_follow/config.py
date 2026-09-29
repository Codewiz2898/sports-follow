"""Runtime settings, all from the environment with local-dev defaults matching docker-compose.yml."""

from __future__ import annotations

import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow"
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6380/0")

# How long a full card stays fresh before the scheduler rebuilds it (seconds).
CARD_MAX_AGE = int(os.environ.get("SPORTS_FOLLOW_CARD_MAX_AGE", 6 * 3600))
# How often a live (or about-to-start) player gets a live check (seconds).
LIVE_CHECK_INTERVAL = int(os.environ.get("SPORTS_FOLLOW_LIVE_INTERVAL", 60))
# An event is "armed" this long before its start (seconds).
ARM_BEFORE_START = int(os.environ.get("SPORTS_FOLLOW_ARM_BEFORE", 30 * 60))

# How often a bound player's fixtures and stats are re-read from their structured source (seconds).
FIXTURES_MAX_AGE = int(os.environ.get("SPORTS_FOLLOW_FIXTURES_MAX_AGE", 30 * 60))
# How long one live-poll job keeps polling before handing over to the next tick's job (seconds).
POLL_SESSION = int(os.environ.get("SPORTS_FOLLOW_POLL_SESSION", 10 * 60))

FAN_COOKIE = "sf_fan"
