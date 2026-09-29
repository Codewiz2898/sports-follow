"""The pipeline worker: arq jobs for card builds, source refreshes, live polls and agent live
checks, plus the one-minute scheduler tick.

Run it with:  .venv/bin/arq sports_follow.worker.WorkerSettings
"""

from __future__ import annotations

import asyncio
import logging

from arq import cron
from arq.connections import RedisSettings

from . import pipeline, structured
from .config import REDIS_URL

log = logging.getLogger("sports_follow.worker")

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


async def tick(ctx) -> None:
    due = await asyncio.to_thread(pipeline.due_work)
    for player_id in due.builds:
        await ctx["redis"].enqueue_job("build_card", player_id)
    for player_id in due.refreshes:
        await ctx["redis"].enqueue_job("refresh_structured", player_id)
    for event_id in due.polls:
        await ctx["redis"].enqueue_job("poll_event", event_id)
    for player_id in due.lives:
        await ctx["redis"].enqueue_job("refresh_live", player_id)
    if due.builds or due.refreshes or due.polls or due.lives:
        log.info("tick: %d builds, %d refreshes, %d polls, %d agent live checks", len(due.builds), len(due.refreshes), len(due.polls), len(due.lives))


class WorkerSettings:
    functions = [build_card, refresh_live, refresh_structured, poll_event]
    cron_jobs = [cron(tick, second=0, run_at_startup=True)]
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    # A poll job holds its slot for up to POLL_SESSION while a game is on; builds hold one for minutes.
    max_jobs = 24
    job_timeout = 900
    keep_result = 0
