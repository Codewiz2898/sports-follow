"""The curator: the feed's own process beside the worker, which fetches what's shared about followed players
(candidates.py). It's an arq worker of its own on a queue of its own, so its jobs never take the worker's
slots and a slow source never holds up a live poll; its source requests are in the feed lane, a background
lane (adapters/budget.py). The feed's endpoint only reads what it stored: nothing a fan does waits on it.

Its jobs are logged, traced and measured as the worker's are (worker._logged), it reports as its own service,
and it stops as the worker does: running jobs end at their next step and free their locks (worker.wind_down).
Cron times are UTC.

Run it with:  .venv/bin/python -m sports_follow.curator
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone
from typing import Any

from . import logs, tracing

# Before the worker's module is read: a process's telemetry starts once, under the first name it's given.
logs.setup()
tracing.setup("sports-follow-curator")

from arq import cron  # noqa: E402
from arq.connections import RedisSettings  # noqa: E402
from arq.worker import run_worker  # noqa: E402

from . import bus, candidates, db  # noqa: E402
from .config import REDIS_URL  # noqa: E402
from .worker import _logged, _missed, in_lane, wind_down  # noqa: E402

log = logging.getLogger("sports_follow.curator")

QUEUE = "arq:curator"
MAX_JOBS = 2  # the poll and the check; each works through its channels and videos one request at a time
POLL_LOCK = 25 * 60  # seconds: a poll that hasn't ended by the next one's time is still running, or died holding it


def _locked(name: str, fn):
    """fn, unless another curator is running it (a deploy's new one starting while the old one stops)."""

    def run():
        token = bus.try_lock(name, POLL_LOCK)
        if token is None:
            log.info("feed: %s is running elsewhere; skipped", name)
            return None
        try:
            return fn()
        finally:
            bus.release(name, token)

    return run


async def poll_channels(ctx) -> None:
    """Every half hour: each trusted channel's latest uploads, kept where they name a followed player."""
    if await _missed(ctx, "feed_poll"):
        return
    done = await in_lane("feed", _locked("feed-poll", candidates.poll))
    if done:
        log.info("feed poll: %s", ", ".join(f"{v} {k}" for k, v in done.items()), extra=done)


async def check_videos(ctx) -> None:
    """Daily: stored videos not read for a week are read again, and dropped once gone."""
    if await _missed(ctx, "feed_check"):
        return
    done = await in_lane("feed", _locked("feed-check", candidates.check))
    if done:
        log.info("feed check: %s", ", ".join(f"{v} {k}" for k, v in done.items()), extra=done)


async def startup(ctx) -> None:
    logs.setup()
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=MAX_JOBS + 2, thread_name_prefix="curator"))
    db.size_pool(MAX_JOBS + 7)  # the pool's least is five


class CuratorSettings:
    functions: list[Any] = []
    cron_jobs = [
        cron(_logged(poll_channels), minute={5, 35}, second=20, run_at_startup=True, timeout=POLL_LOCK),
        cron(_logged(check_videos), hour=3, minute=50, second=0, timeout=3600),
    ]
    queue_name = QUEUE
    timezone = timezone.utc
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    on_startup = startup
    on_shutdown = wind_down
    max_jobs = MAX_JOBS
    job_timeout = 3600
    keep_result = 0


def main() -> None:
    run_worker(CuratorSettings)  # type: ignore[arg-type]  # arq types its settings as a dict; it reads a class's attributes too


if __name__ == "__main__":
    main()
