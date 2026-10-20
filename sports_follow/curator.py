"""The curator: the feed's own process beside the worker, which fetches what's shared about followed players
(candidates.py: trusted channels; searches.py: searches for big moments; news.py: publishers' feeds), then has the
judge read it and ranks it (rank.py). It's an arq worker of its own on a queue of
its own, so its jobs never take the worker's slots and a slow source or model never holds up a live poll; its
source requests are in the feed lane, a background lane (adapters/budget.py). The feed's endpoint only reads
what it stored: nothing a fan does waits on it.

Its jobs are logged, traced and measured as the worker's are (worker._logged), it reports as its own service,
and it stops as the worker does: running jobs end at their next step and free their locks (worker.wind_down).
Cron times are UTC.

Run it with:  .venv/bin/python -m sports_follow.curator
Work every score out again from what's stored, without fetching (and with --rejudge, ask the judge again first):
              .venv/bin/python -m sports_follow.curator rescore [--rejudge]
Read the publishers' feeds once, judge and rank what's new, and say what it did for each sport:
              .venv/bin/python -m sports_follow.curator news
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

from . import bus, candidates, db, news, rank, searches  # noqa: E402
from .config import REDIS_URL  # noqa: E402
from .worker import _logged, _missed, in_lane, wind_down  # noqa: E402

log = logging.getLogger("sports_follow.curator")

QUEUE = "arq:curator"
MAX_JOBS = 3  # the channel poll, the news poll and the check (or the prune); each works through its sources one request at a time
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


def rank_new(videos: bool = True) -> dict[str, int]:
    """After a poll: read the youngest videos again (after a channel poll), have the judge read what's new, and rank
    everything again. Each step stands alone: a judge that can't answer (the model off, the gateway down) leaves
    its rows for next time, and the rest are still ranked. Judging and ranking are one run at a time across the
    curator's polls: one that finds the other running leaves its new rows for that run's next turn."""
    done: dict[str, int] = {}
    if videos:
        done |= _step("fresh", candidates.fresh_reads)
    token = bus.try_lock("feed-rank", POLL_LOCK)
    if token is None:
        log.info("feed: the judge and ranking are running elsewhere; this poll's rows wait for the next run")
        return done
    try:
        for step, fn in (("judge", rank.judge), ("rank", rank.rescore), ("cards", rank.cards)):
            done |= _step(step, fn)
    finally:
        bus.release("feed-rank", token)
    return done


def _step(step: str, fn) -> dict[str, int]:
    try:
        return {f"{step} {k}": v for k, v in fn().items() if v}
    except Exception:
        log.exception("feed: %s failed; the rest of the run goes on", step)
        return {}


def poll_stories() -> dict[str, int]:
    """The publishers' feeds read once (news.py), their counts summed over the sports."""
    done: dict[str, int] = {}
    for counts in news.poll().values():
        for k, v in counts.items():
            done[f"news {k}"] = done.get(f"news {k}", 0) + v
    return {k: v for k, v in done.items() if v}


async def poll_channels(ctx) -> None:
    """Every half hour: each trusted channel's latest uploads, kept where they name a followed player, and the
    searches for big moments that are due; then what's new is judged and everything ranked."""
    if await _missed(ctx, "feed_poll"):
        return
    done = await in_lane("feed", _locked("feed-poll", lambda: (candidates.poll() or {}) | search_moments() | rank_new()))
    if done:
        log.info("feed poll: %s", ", ".join(f"{v} {k}" for k, v in done.items()), extra={k.replace(" ", "_"): v for k, v in done.items()})


async def poll_news(ctx) -> None:
    """Every 20 minutes: each publisher's feed a running sport reads, its new stories kept where they name a followed
    player; then what's new is judged and everything ranked."""
    if await _missed(ctx, "news_poll"):
        return
    done = await in_lane("feed", _locked("news-poll", lambda: poll_stories() | rank_new(videos=False)))
    if done:
        log.info("news poll: %s", ", ".join(f"{v} {k}" for k, v in done.items()), extra={k.replace(" ", "_"): v for k, v in done.items()})


async def prune_news(ctx) -> None:
    """Weekly: stories older than a month go, but an admin's evergreen ones (news.prune)."""
    if await _missed(ctx, "news_prune"):
        return
    done = await in_lane("feed", _locked("news-prune", news.prune))
    if done:
        log.info("news prune: %s", ", ".join(f"{v} {k}" for k, v in done.items()), extra=done)


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
        cron(_logged(poll_news), minute={0, 20, 40}, second=40, run_at_startup=True, timeout=POLL_LOCK),
        cron(_logged(check_videos), hour=3, minute=50, second=0, timeout=3600),
        cron(_logged(prune_news), weekday=0, hour=4, minute=20, second=0, timeout=3600),
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
    sub.add_parser("news", help="read the publishers' feeds once, judge and rank what's new, and say what it did for each sport")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "news":
        return _news_once()
    if args.command != "rescore":
        run_worker(CuratorSettings)  # type: ignore[arg-type]  # arq types its settings as a dict; it reads a class's attributes too
        return 0
    if args.rejudge:
        judged = rank.judge(limit=None, again=True)
        print(f"judged {judged['judged']}, {judged['unjudged']} left unjudged (the model couldn't answer)")
    done = rank.rescore()
    print(f"{done['scored']} judged candidates: {done['served']} served, {done['changed']} changed")
    return 0


def _news_once() -> int:
    """One news poll by hand, in the feed lane, then the judge and the ranking; prints each sport's counts."""
    from sqlalchemy import func, select

    from .adapters import NEWS, budget
    from .models import FeedCandidate, Player

    with budget.in_lane("feed"):
        polled = news.poll()
        judged = rank.judge(limit=None)
        ranked = rank.rescore()
        rank.cards()
    print(f"judge: {judged['judged']} judged, {judged['unjudged']} left unjudged; rank: {ranked['served']} served of {ranked['scored']} judged rows")
    with db.session() as session:
        rows = session.execute(select(Player.sport, func.count(), func.count(FeedCandidate.verdict), func.count(FeedCandidate.score))
                               .join(Player, Player.id == FeedCandidate.player_id).where(FeedCandidate.source.in_(list(NEWS)))
                               .group_by(Player.sport)).all()  # fmt: skip
    stored: dict[str, list[int]] = {}
    for sport, n, j, s in rows:
        key = news.sports.of(sport).group or news.sports.family(sport)
        stored[key] = [a + b for a, b in zip(stored.get(key, [0, 0, 0]), (n, j, s))]
    print(f"{'sport':<12}{'feeds':>6}{'unread':>7}{'read':>6}{'matched':>8}{'kept':>6}{'stored':>8}{'judged':>8}{'served':>8}")
    for key in sorted(set(polled) | set(stored)):
        c, (n, j, s) = polled.get(key, {}), stored.get(key, [0, 0, 0])
        print(f"{key:<12}{c.get('feeds', 0):>6}{c.get('unread', 0):>7}{c.get('read', 0):>6}{c.get('matched', 0):>8}{c.get('kept', 0):>6}{n:>8}{j:>8}{s:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
