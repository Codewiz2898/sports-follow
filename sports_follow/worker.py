"""The pipeline worker: arq jobs for card builds, source refreshes, live polls and agent live
checks, plus the one-minute scheduler tick (which also sends moments no sender claimed).

Run it with:  .venv/bin/arq sports_follow.worker.WorkerSettings
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from arq import cron
from arq.connections import RedisSettings

from . import bus, db, engine, history, notify, pipeline, registry, structured
from .config import REDIS_URL

log = logging.getLogger("sports_follow.worker")

# A job holds a thread for as long as it runs: a poll or a lane up to POLL_SESSION (ten minutes), the
# registry import about two hours. asyncio's default pool has min(32, cpus + 4) threads, six on the
# 2 vCPU server, so six long jobs used to leave every other job, and the tick, waiting for one.
MAX_JOBS = 24
_tick_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tick")

# arq's CLI configures only its own logger; show the pipeline's too (binds, polls, refresh failures).
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s: %(name)s %(levelname)s %(message)s", "%H:%M:%S"))
logging.getLogger("sports_follow").addHandler(_handler)
logging.getLogger("sports_follow").setLevel(logging.INFO)


async def build_card(ctx, player_id: int) -> None:
    await asyncio.to_thread(pipeline.build_card, player_id)


async def refresh_live(ctx, player_id: int) -> None:
    await asyncio.to_thread(pipeline.refresh_live, player_id)


async def refresh_structured(ctx, player_id: int) -> None:
    await asyncio.to_thread(structured.refresh, player_id)


async def poll_event(ctx, event_id: int) -> None:
    await asyncio.to_thread(structured.poll_event, event_id)


async def poll_lane(ctx, system: str) -> None:
    """Every armed and live event of one source, one request a round (structured.poll_lane)."""
    await asyncio.to_thread(structured.poll_lane, system)


async def send_moments(ctx, moment_ids: list[int]) -> None:
    """Moments a poll or a rebuild found but no sender claimed: the worker restarted in between."""
    await asyncio.to_thread(notify.send, moment_ids)


async def engine_lane(ctx) -> None:
    """Chess engine evaluations for live games, within its CPU budget (engine.py)."""
    await asyncio.to_thread(engine.run_lane)


async def history_lane(ctx) -> None:
    """Older results and missing scorecards, a few source requests a minute (history.py)."""
    await asyncio.to_thread(history.run)


def _import_registry() -> None:
    if not bus.try_lock("registry-import", 6 * 3600):
        return  # the last week's run (or one started by hand) is still going
    try:
        log.info("registry import: %s", registry.import_all())
    finally:
        bus.release("registry-import")


def _warm_sources() -> None:
    """Sources that keep a shared index (PUBG: recent tournaments and their matches) bring it up to date;
    PUBG's players join the registry every six hours from it."""
    from .adapters import ADAPTERS

    for adapter in list(ADAPTERS.values()):
        warm = getattr(adapter, "warm", None)
        if warm is None:
            continue
        try:
            log.info("warm %s: %s lobbies indexed", adapter.system, warm())
        except Exception:
            log.exception("warm %s failed", adapter.system)
    if "pubg_esports" in ADAPTERS and bus.try_lock("registry-pubg", 6 * 3600):  # held, not released: at most every six hours
        registry.import_pubg()


async def warm_sources(ctx) -> None:
    """Shared source indexes, every ten minutes (_warm_sources)."""
    await asyncio.to_thread(_warm_sources)


async def import_registry(ctx) -> None:
    """The weekly player-registry refresh from Wikidata, one query at a time, then the esports pros from
    PandaScore; about two hours."""
    await asyncio.to_thread(_import_registry)


async def startup(ctx) -> None:
    """A thread for every job slot, so no job waits for one, and a few for the event loop's own use
    (it looks up hosts in one)."""
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=MAX_JOBS + 4, thread_name_prefix="job"))
    # A connection for each of those threads, the tick's and notify's: checkouts give up after 5 s.
    db.size_pool(MAX_JOBS + 4 + _tick_pool._max_workers + notify._pool._max_workers)


async def _in_tick_pool(fn):
    """The tick works on threads of its own: whatever the jobs are doing, the scheduler keeps its minute."""
    return await asyncio.get_running_loop().run_in_executor(_tick_pool, fn)


async def tick(ctx) -> None:
    due = await _in_tick_pool(pipeline.due_work)
    for player_id in due.builds:
        await ctx["redis"].enqueue_job("build_card", player_id)
    for player_id in due.refreshes:
        await ctx["redis"].enqueue_job("refresh_structured", player_id)
    for event_id in due.polls:
        await ctx["redis"].enqueue_job("poll_event", event_id)
    for system in due.lanes:
        await ctx["redis"].enqueue_job("poll_lane", system, _job_id=f"poll-lane:{system}")
    for player_id in due.lives:
        await ctx["redis"].enqueue_job("refresh_live", player_id)
    if await _in_tick_pool(engine.needs_lane):
        await ctx["redis"].enqueue_job("engine_lane", _job_id="engine-lane")
    unsent = await _in_tick_pool(notify.pending)
    if unsent:
        log.info("tick: sending %d moment(s) no sender claimed", len(unsent))
        await ctx["redis"].enqueue_job("send_moments", unsent, _job_id="send-moments")
    if await _in_tick_pool(history.has_work):
        await ctx["redis"].enqueue_job("history_lane", _job_id="history-lane")
    if due.builds or due.refreshes or due.polls or due.lanes or due.lives:
        log.info("tick: %d builds, %d refreshes, %d polls, %d poll lanes, %d agent live checks", len(due.builds), len(due.refreshes), len(due.polls), len(due.lanes), len(due.lives))


class WorkerSettings:
    functions = [build_card, refresh_live, refresh_structured, poll_event, poll_lane, send_moments, engine_lane, history_lane]
    cron_jobs = [
        cron(tick, second=0, run_at_startup=True),
        cron(warm_sources, minute=set(range(0, 60, 10)), second=30, run_at_startup=True, timeout=900),
        cron(import_registry, weekday="mon", hour=3, minute=30, second=0, timeout=6 * 3600),
    ]
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    on_startup = startup
    # A poll job or lane holds its slot for up to POLL_SESSION while games are on; builds hold one for minutes.
    max_jobs = MAX_JOBS
    job_timeout = 900
    keep_result = 0
