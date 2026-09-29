"""The pipeline worker: arq jobs for card builds and live checks, plus the one-minute scheduler tick.

Run it with:  .venv/bin/arq sports_follow.worker.WorkerSettings
"""

from __future__ import annotations

import asyncio
import logging

from arq import cron
from arq.connections import RedisSettings

from . import pipeline
from .config import REDIS_URL

log = logging.getLogger("sports_follow.worker")


async def build_card(ctx, player_id: int) -> None:
    await asyncio.to_thread(pipeline.build_card, player_id)


async def refresh_live(ctx, player_id: int) -> None:
    await asyncio.to_thread(pipeline.refresh_live, player_id)


async def tick(ctx) -> None:
    builds, lives = await asyncio.to_thread(pipeline.due_work)
    for player_id in builds:
        await ctx["redis"].enqueue_job("build_card", player_id)
    for player_id in lives:
        await ctx["redis"].enqueue_job("refresh_live", player_id)
    if builds or lives:
        log.info("tick: %d builds, %d live checks", len(builds), len(lives))


class WorkerSettings:
    functions = [build_card, refresh_live]
    cron_jobs = [cron(tick, second=0, run_at_startup=True)]
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    max_jobs = 8
    job_timeout = 900
    keep_result = 0
