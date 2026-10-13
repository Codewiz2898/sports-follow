"""The worker's threads (worker.py): every job slot gets one, and the tick has its own."""

from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from sports_follow import db, engine, history, notify, pipeline, worker


def test_every_job_slot_gets_a_thread(monkeypatch):
    # The 2 vCPU server, where asyncio's own pool has six threads.
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    if hasattr(os, "process_cpu_count"):
        monkeypatch.setattr(os, "process_cpu_count", lambda: 2)
    slots = worker.WorkerSettings.max_jobs
    together = threading.Barrier(slots, timeout=2)  # opens only once every job is in a thread at the same time

    async def full_house() -> None:
        await worker.WorkerSettings.on_startup({})
        await asyncio.gather(*(asyncio.to_thread(together.wait) for _ in range(slots)))

    asyncio.run(full_house())


def test_the_tick_runs_while_long_jobs_hold_every_thread(monkeypatch):
    monkeypatch.setattr(pipeline, "due_work", lambda: pipeline.Due())
    monkeypatch.setattr(engine, "needs_lane", lambda: False)
    monkeypatch.setattr(notify, "pending", lambda: [])
    monkeypatch.setattr(history, "has_work", lambda: False)
    session_over = threading.Event()

    async def busy_minute() -> None:
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        poll = asyncio.ensure_future(asyncio.to_thread(session_over.wait))  # a ten-minute poll session
        try:
            await asyncio.wait_for(worker.tick({"redis": None}), 2)
        finally:
            session_over.set()
            await poll

    asyncio.run(busy_minute())


def test_every_thread_that_reaches_the_database_gets_a_connection():
    # Checkouts give up after 5 s (db.py), so the worker's threads mustn't queue for the API's 15.
    try:
        asyncio.run(worker.WorkerSettings.on_startup({}))
        pool = db.engine.pool
        assert pool.size() + pool._max_overflow >= worker.MAX_JOBS + 4 + worker._tick_pool._max_workers + notify._pool._max_workers
    finally:
        db.size_pool(15)
