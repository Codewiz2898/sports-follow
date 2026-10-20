"""The curator: the feed's own process beside the worker, which fetches what's shared about followed players
(candidates.py: trusted channels; searches.py: searches for big moments), then has the judge read it and ranks it
(rank.py). It's an arq worker of its own on a queue of
its own, so its jobs never take the worker's slots and a slow source or model never holds up a live poll; its
source requests are in the feed lane, a background lane (adapters/budget.py). The feed's endpoint only reads
what it stored: nothing a fan does waits on it.

Its jobs are logged, traced and measured as the worker's are (worker._logged), it reports as its own service,
and it stops as the worker does: running jobs end at their next step and free their locks (worker.wind_down).
Cron times are UTC.

Run it with:  .venv/bin/python -m sports_follow.curator
Work every score out again from what's stored, without fetching (and with --rejudge, ask the judge again first):
              .venv/bin/python -m sports_follow.curator rescore [--rejudge]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
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

from . import bus, candidates, db, rank, searches  # noqa: E402
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


def search_moments() -> dict[str, int]:
    """After a poll: queue a search for each big moment of a followed player's, and run the ones due, within the
    day's cap (searches.py). A failure leaves them for the next poll."""
    done: dict[str, int] = {}
    for fn in (searches.queue, searches.run):
        try:
            done |= {f"search {k}": v for k, v in fn().items() if v}
        except Exception:
            log.exception("feed: moment searches failed; the rest of the run goes on")
            break
    return done


def rank_new() -> dict[str, int]:
    """After a poll: read the youngest videos again, have the judge read what's new, and rank everything again.
    Each step stands alone: a judge that can't answer (the model off, the gateway down) leaves its rows for
    next time, and the rest are still ranked."""
    done: dict[str, int] = {}
    for step, fn in (("fresh", candidates.fresh_reads), ("judge", rank.judge), ("rank", rank.rescore)):
        try:
            done |= {f"{step} {k}": v for k, v in fn().items() if v}
        except Exception:
            log.exception("feed: %s failed; the rest of the run goes on", step)
    return done


async def poll_channels(ctx) -> None:
    """Every half hour: each trusted channel's latest uploads, kept where they name a followed player, and the
    searches for big moments that are due; then what's new is judged and everything ranked."""
    if await _missed(ctx, "feed_poll"):
        return
    done = await in_lane("feed", _locked("feed-poll", lambda: (candidates.poll() or {}) | search_moments() | rank_new()))
    if done:
        log.info("feed poll: %s", ", ".join(f"{v} {k}" for k, v in done.items()), extra={k.replace(" ", "_"): v for k, v in done.items()})


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sports_follow.curator", description="The feed's curator: run it, or rank what's stored again.")
    sub = parser.add_subparsers(dest="command")
    again = sub.add_parser("rescore", help="work every stored candidate's score out again, fetching nothing")
    again.add_argument("--rejudge", action="store_true", help="ask the judge about every candidate again first (a model call each the rules don't settle)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command != "rescore":
        run_worker(CuratorSettings)  # type: ignore[arg-type]  # arq types its settings as a dict; it reads a class's attributes too
        return 0
    if args.rejudge:
        judged = rank.judge(limit=None, again=True)
        print(f"judged {judged['judged']}, {judged['unjudged']} left unjudged (the model couldn't answer)")
    done = rank.rescore()
    print(f"{done['scored']} judged candidates: {done['served']} served, {done['changed']} changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
