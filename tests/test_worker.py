"""The worker (worker.py): every job slot gets a thread, and the tick has its own. After a sleep, arq's
missed cron runs don't all run (BUG-022); cron times are UTC (BUG-024). A job logs under its own id, or
the request's that queued it, and ends with one line (BHA-192)."""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from arq.worker import Worker, create_worker

import pytest

from sports_follow import db, engine, history, logs, notify, pipeline, worker


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


# ---------------------------------------------------------------- cron runs (BUG-022, BUG-024)


class Queue:
    """arq's pool as its scheduler uses it: the cron runs it queues, by job id."""

    def __init__(self):
        self.job_ids: list[str] = []

    async def enqueue_job(self, name, _job_id=None, _queue_name=None, _defer_until=None):
        self.job_ids.append(_job_id)


class Redis:
    """SET NX EX, the one call a late run makes; nothing expires within a test."""

    def __init__(self):
        self.data: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True


def _cron_job(name: str):
    return copy.copy(next(j for j in worker.WorkerSettings.cron_jobs if j.name == name))


def test_a_sleep_doesnt_replay_every_missed_tick(monkeypatch):
    ticks = []
    monkeypatch.setattr(pipeline, "due_work", lambda: ticks.append(1) or pipeline.Due())
    monkeypatch.setattr(engine, "needs_lane", lambda: False)
    monkeypatch.setattr(notify, "pending", lambda: [])
    monkeypatch.setattr(history, "has_work", lambda: False)
    # The laptop slept from 11:50:05 to 12:00:20. arq's next tick is still 11:51, and each heartbeat
    # queues one missed run: 11:51, 11:52 ... 12:00, then it's caught up.
    woke = datetime(2026, 10, 13, 12, 0, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(worker, "time", SimpleNamespace(time=woke.timestamp), raising=False)
    tick = _cron_job("cron:tick")
    tick.calculate_next(datetime(2026, 10, 13, 11, 50, 5, tzinfo=timezone.utc))
    queue = Queue()
    scheduler = SimpleNamespace(cron_jobs=[tick], pool=queue, queue_name="arq:queue", timezone=timezone.utc)

    async def wake_up() -> None:
        for _ in range(15):  # heartbeats, half a second apart
            await Worker.run_cron(scheduler, woke, 0.5)
        redis = Redis()
        for job_id in queue.job_ids:
            await worker.tick({"redis": redis, "job_id": job_id})

    asyncio.run(wake_up())
    assert len(queue.job_ids) == 10  # arq still queues every missed minute
    assert len(ticks) == 2  # the first late one, and 12:00's, 20 seconds after its time


def test_a_late_tick_still_runs_once_in_a_while(monkeypatch):
    """A queue so busy that every tick starts late mustn't stop the ticks: one gets through every LATE seconds."""
    monkeypatch.setattr(pipeline, "due_work", lambda: pipeline.Due())
    monkeypatch.setattr(engine, "needs_lane", lambda: False)
    monkeypatch.setattr(notify, "pending", lambda: [])
    monkeypatch.setattr(history, "has_work", lambda: False)
    now = datetime(2026, 10, 13, 12, 2, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(worker, "time", SimpleNamespace(time=now.timestamp), raising=False)
    late = f"cron:tick:{int(datetime(2026, 10, 13, 12, 1, 0, 123456, tzinfo=timezone.utc).timestamp() * 1000)}"
    redis = Redis()
    assert asyncio.run(worker._missed({"redis": redis, "job_id": late}, "tick")) is False
    assert asyncio.run(worker._missed({"redis": redis, "job_id": late}, "tick")) is True
    on_time = f"cron:tick:{int(datetime(2026, 10, 13, 12, 2, 0, 123456, tzinfo=timezone.utc).timestamp() * 1000)}"
    assert asyncio.run(worker._missed({"redis": redis, "job_id": on_time}, "tick")) is False
    assert asyncio.run(worker._missed({"redis": None}, "tick")) is False  # called by hand: no job id


def test_cron_times_are_utc_whatever_the_machines_zone(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Kolkata")  # the laptop the weekly import ran at 22:00 UTC on
    time.tzset()
    try:
        arq_worker = create_worker(worker.WorkerSettings, handle_signals=False)
        weekly = _cron_job("cron:import_registry")
        weekly.calculate_next(datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc).astimezone(arq_worker.timezone))
        assert weekly.next_run.astimezone(timezone.utc) == datetime(2026, 10, 19, 3, 30, 0, 123456, tzinfo=timezone.utc)  # Monday 03:30 UTC
        daily = _cron_job("cron:prune")
        daily.calculate_next(datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc).astimezone(arq_worker.timezone))
        assert daily.next_run.astimezone(timezone.utc) == datetime(2026, 10, 14, 2, 15, 0, 123456, tzinfo=timezone.utc)
    finally:
        monkeypatch.undo()
        time.tzset()


# ---------------------------------------------------------------- job logs (BHA-192)


def _ran(caplog, job, *args, ctx=None, **kwargs) -> list[logging.LogRecord]:
    """The sports_follow lines a job logged, run as arq runs it (by default queued a second ago)."""
    caplog.set_level(logging.INFO, "sports_follow")
    ctx = {"job_id": "0f8e2a6c", "job_try": 1, "enqueue_time": datetime.now(timezone.utc) - timedelta(seconds=1)} if ctx is None else ctx
    asyncio.run(job(ctx, *args, **kwargs))
    return [r for r in caplog.records if r.name.startswith("sports_follow")]


def test_a_job_logs_under_the_id_of_the_request_that_queued_it(caplog):
    async def build_card(ctx, player_id: int) -> None:
        logging.getLogger("sports_follow.pipeline").info("build %s: bound", player_id)
        await asyncio.to_thread(logging.getLogger("sports_follow.structured").info, "bind %s: espn", player_id)

    built, bound, done = _ran(caplog, worker._logged(build_card), 7, trace={"rid": "ab12cd34ef56ab78"})
    assert built.context == bound.context == done.context == {"rid": "ab12cd34ef56ab78", "job": "build_card", "player_id": 7}
    assert done.getMessage().startswith("build_card done in ") and done.outcome == "ok" and done.job_id == "0f8e2a6c"
    assert done.waited_ms >= 1000 and done.duration_ms >= 0


def test_a_job_nobody_queued_from_a_request_has_an_id_of_its_own(caplog):
    async def poll_event(ctx, event_id: int) -> None:
        pass

    first, = _ran(caplog, worker._logged(poll_event), 5)
    second, = _ran(caplog, worker._logged(poll_event), 5)[1:]
    assert first.context["event_id"] == 5 and logs.given_id(first.context["rid"]) and first.context["rid"] != second.context["rid"]


def test_a_failed_job_says_so_and_still_fails(caplog):
    async def poll_lane(ctx, system: str) -> None:
        raise ValueError("no such round")

    with pytest.raises(ValueError):
        _ran(caplog, worker._logged(poll_lane), "lichess_chess")
    [failed] = [r for r in caplog.records if r.name == "sports_follow.worker"]
    assert failed.levelno == logging.WARNING and failed.outcome == "failed" and failed.context["system"] == "lichess_chess"
    assert "ValueError: no such round" in failed.getMessage()


def test_a_quiet_job_logs_only_when_it_runs_long(caplog, monkeypatch):
    async def tick(ctx) -> None:
        pass

    assert _ran(caplog, worker._logged(tick, quiet=True), ctx={}) == []
    clock = iter([0.0, worker.QUIET_UNDER + 1])
    monkeypatch.setattr(worker, "perf_counter", lambda: next(clock))
    [slow] = _ran(caplog, worker._logged(tick, quiet=True), ctx={})
    assert slow.getMessage() == f"tick done in {worker.QUIET_UNDER + 1:.1f}s" and slow.waited_ms is None  # called by hand: no queue


def test_every_job_and_cron_run_is_logged():
    assert all(hasattr(f, "__wrapped__") for f in worker.WorkerSettings.functions)
    assert all(hasattr(j.coroutine, "__wrapped__") for j in worker.WorkerSettings.cron_jobs)
    assert {j.name for j in worker.WorkerSettings.cron_jobs} >= {"cron:tick", "cron:prune"}  # arq still names them after the job
