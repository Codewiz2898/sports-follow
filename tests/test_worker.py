"""The worker (worker.py): every job slot gets a thread, and the tick has its own, and needs no slot
(BUG-173). After a sleep, arq's missed cron runs don't all run (BUG-022); cron times are UTC (BUG-024). A
job logs under its own id, or the request's that queued it, and ends with one line (BHA-192)."""

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
from typing import Any

from arq.connections import ArqRedis
from arq.worker import Worker, create_worker

import pytest

from sports_follow import db, engine, history, livecards, logs, notify, pipeline, predictions_push, shutdown, structured, worker


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


# ---------------------------------------------------------------- the tick beside the jobs (BUG-173)


class MemoryArq:
    """Just enough of Redis, in memory, for a real arq Worker to queue, start and finish jobs and queue its cron
    runs: no test reaches a Redis server. Nothing expires within a test."""

    default_queue_name = "arq:queue"
    expires_extra_ms = 86_400_000
    job_serializer = job_deserializer = None
    enqueue_job = ArqRedis.enqueue_job

    def __init__(self):
        self.data: dict[str, Any] = {}
        self.sorted: dict[str, dict[str, float]] = {}

    def pipeline(self, transaction: bool = True) -> "_Pipeline":
        return _Pipeline(self)

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, nx=False, ex=None, px=None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def psetex(self, key, ms, value):
        return await self.set(key, value)

    async def setex(self, key, seconds, value):
        return await self.set(key, value)

    async def exists(self, *keys):
        return sum(key in self.data for key in keys)

    async def delete(self, *keys):
        return sum(self.data.pop(key, None) is not None for key in keys)

    async def incr(self, key):
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]

    async def expire(self, key, seconds):
        return key in self.data

    async def pexpire(self, key, ms):
        return key in self.data

    async def zadd(self, name, mapping):
        self.sorted.setdefault(name, {}).update(mapping)
        return len(mapping)

    async def zscore(self, name, member):
        return self.sorted.get(name, {}).get(member)

    async def zrem(self, name, *members):
        return sum(self.sorted.get(name, {}).pop(m, None) is not None for m in members)

    async def zincrby(self, name, amount, member):
        scores = self.sorted.setdefault(name, {})
        scores[member] = scores.get(member, 0) + amount
        return scores[member]

    async def zcard(self, name):
        return len(self.sorted.get(name, {}))

    async def zrangebyscore(self, name, min, max, start=0, num=None):
        members = [m for m, s in sorted(self.sorted.get(name, {}).items(), key=lambda ms: ms[1]) if min <= s <= max]
        return [m.encode() for m in members[start:None if num is None else start + num]]

    async def info(self, section=None):
        return {}

    async def dbsize(self):
        return len(self.data)

    async def close(self, close_connection_pool=None):
        pass


class _Pipeline:
    """A pipeline as arq uses one: a command awaited (after WATCH) runs at once, the rest at execute()."""

    def __init__(self, redis: MemoryArq):
        self.redis, self.queued = redis, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def watch(self, *keys):
        return None

    def multi(self):
        return None

    async def reset(self):
        self.queued = []

    def __getattr__(self, name):
        command = getattr(self.redis, name)

        def queue(*args, **kwargs):
            self.queued.append(call := _Command(command, args, kwargs))
            return call

        return queue

    async def execute(self):
        calls, self.queued = [c for c in self.queued if not c.ran], []
        return [await c.run() for c in calls]


class _Command:
    def __init__(self, fn, args, kwargs):
        self.fn, self.args, self.kwargs, self.ran = fn, args, kwargs, False

    def run(self):
        self.ran = True
        return self.fn(*self.args, **self.kwargs)

    def __await__(self):
        return self.run().__await__()


def test_the_tick_keeps_its_minute_while_long_jobs_hold_every_slot(monkeypatch):
    """arq runs a cron job in a job slot, like any job: with MAX_JOBS poll sessions running (about 20 followed chess
    players live in a broadcast, each board its own ten-minute poll) the tick waited for one to end, and nothing was
    armed or swept meanwhile. Here every slot of a real arq Worker holds a board's poll session, and the tick still
    runs each time it's due; once the worker stops, it doesn't."""
    monkeypatch.setattr(worker, "TICK_EVERY", 0.5, raising=False)  # a minute, made short
    monkeypatch.setattr(structured, "poll_event", lambda event_id: shutdown.sleep(600))  # polls till the worker stops
    arq_worker: Worker | None = None
    ticks: list[tuple[float, int]] = []  # when each tick began, and how many slots were taken then
    monkeypatch.setattr(pipeline, "due_work", lambda: ticks.append((time.time(), arq_worker.job_counter if arq_worker else -1)) or pipeline.Due())
    monkeypatch.setattr(engine, "needs_lane", lambda: False)
    monkeypatch.setattr(notify, "pending", lambda: [])
    monkeypatch.setattr(notify, "held_due", lambda: [])
    monkeypatch.setattr(history, "has_work", lambda: False)
    redis = MemoryArq()
    redis.data[worker.STARTERS_DONE] = redis.data[worker.TEAMS_DONE] = b"1"  # no startup refresh or import: they'd read sources

    async def broadcast() -> int:
        nonlocal arq_worker
        for board in range(worker.MAX_JOBS):
            await redis.enqueue_job("poll_event", board)
        arq_worker = create_worker(worker.WorkerSettings, redis_pool=redis, handle_signals=False, poll_delay=0.05)
        running = asyncio.ensure_future(arq_worker.main())
        await asyncio.sleep(2.3)
        running.cancel()
        await asyncio.wait([running])
        await arq_worker.close()  # the worker stops: its jobs are cancelled, then its shutdown hook runs
        stopped = len(ticks)
        await asyncio.sleep(1)
        return stopped

    try:
        stopped = asyncio.run(broadcast())
    finally:
        db.size_pool(15)
    full = [at for at, taken in ticks[1:] if taken == worker.MAX_JOBS]  # after the one at startup
    assert len(full) >= 3, f"ticks while every slot was held: {full}"
    assert all(abs(at - round(at / 0.5) * 0.5) < 0.2 for at in full), full  # each on its half-second
    assert len(ticks) == stopped  # none once the worker stopped


def test_a_tick_that_fails_or_finds_redis_down_doesnt_stop_the_next(monkeypatch, caplog):
    monkeypatch.setattr(worker, "TICK_EVERY", 0.2)
    monkeypatch.setattr(engine, "needs_lane", lambda: False)
    monkeypatch.setattr(notify, "pending", lambda: [])
    monkeypatch.setattr(notify, "held_due", lambda: [])
    monkeypatch.setattr(history, "has_work", lambda: False)
    ticks = []

    def due_work():
        ticks.append(time.time())
        if len(ticks) == 1:
            raise RuntimeError("the database went away")
        return pipeline.Due()

    monkeypatch.setattr(pipeline, "due_work", due_work)

    class Flaky(MemoryArq):
        answered = 0

        async def set(self, key, value, nx=False, ex=None, px=None):
            self.answered += 1
            if self.answered == 2:
                raise ConnectionError("Redis is restarting")
            return await super().set(key, value, nx=nx, ex=ex, px=px)

    async def a_second() -> None:
        ticker = asyncio.create_task(worker._ticker({"redis": Flaky()}))
        await asyncio.sleep(1.1)
        ticker.cancel()
        await asyncio.wait([ticker])

    caplog.set_level(logging.INFO, "sports_follow")
    asyncio.run(a_second())
    assert len(ticks) >= 4  # at startup (failed), none for the one Redis missed, then each on time
    assert "tick failed (RuntimeError: the database went away)" in caplog.text
    assert "tick: Redis didn't answer" in caplog.text


def test_every_thread_that_reaches_the_database_gets_a_connection():
    # Checkouts give up after 5 s (db.py), so the worker's threads mustn't queue for the API's 15.
    try:
        asyncio.run(worker.WorkerSettings.on_startup({}))
        pool = db.engine.pool
        assert pool.size() + pool._max_overflow >= worker.MAX_JOBS + 4 + worker._tick_pool._max_workers + notify._pool._max_workers + livecards._pool._max_workers
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


def test_a_sleep_doesnt_replay_every_missed_cron_run(monkeypatch):
    runs = []
    monkeypatch.setattr(predictions_push, "run", lambda: runs.append(1))
    # The laptop slept from 11:50:05 to 12:00:20. arq's next run of the every-minute job at :50 is still
    # 11:50:50, and each heartbeat queues one missed run: 11:50:50, 11:51:50 ... 11:59:50, then it's caught up.
    woke = datetime(2026, 10, 13, 12, 0, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(worker, "time", SimpleNamespace(time=woke.timestamp), raising=False)
    pushes = _cron_job("cron:prediction_pushes")
    pushes.calculate_next(datetime(2026, 10, 13, 11, 50, 5, tzinfo=timezone.utc))
    queue = Queue()
    scheduler = SimpleNamespace(cron_jobs=[pushes], pool=queue, queue_name="arq:queue", timezone=timezone.utc)

    async def wake_up() -> None:
        for _ in range(15):  # heartbeats, half a second apart
            await Worker.run_cron(scheduler, woke, 0.5)
        redis = Redis()
        for job_id in queue.job_ids:
            await worker.prediction_pushes({"redis": redis, "job_id": job_id})

    asyncio.run(wake_up())
    assert len(queue.job_ids) == 10  # arq still queues every missed minute
    assert len(runs) == 2  # the first late one, and 11:59:50's, 30 seconds after its time


def test_after_a_sleep_or_a_long_tick_the_next_is_at_the_next_minute():
    """The tick isn't arq's (_ticker), so arq never queues its missed minutes: it runs once, at the next one."""
    noon = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc).timestamp()
    assert worker._next_tick(noon, noon + 0.3) == noon + 60
    assert worker._next_tick(noon + 17.5, noon + 17.6) == noon + 60  # the one at startup: then on the minute
    assert worker._next_tick(noon, noon + 610) == noon + 660  # slept ten minutes, or a tick ran that long
    assert worker._next_tick(noon, noon - 0.001) == noon + 60  # woken a moment early: not 12:00 again


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
    assert all(hasattr(getattr(f, "coroutine", f), "__wrapped__") for f in worker.WorkerSettings.functions)  # or arq's func(), for a timeout
    assert all(hasattr(j.coroutine, "__wrapped__") for j in worker.WorkerSettings.cron_jobs)
    names = {j.name for j in worker.WorkerSettings.cron_jobs}
    assert names >= {"cron:prune", "cron:prediction_pushes"}  # arq still names them after the job
    assert "cron:tick" not in names  # it would wait for a free slot: it runs beside the jobs (_ticker)


def test_a_worker_starting_without_a_recent_starters_refresh_or_team_import_queues_one(monkeypatch):
    """A fresh server, or one whose last refresh or import failed, needn't wait for 04:40 or Monday; a deploy's
    restart after them doesn't spend the sources again."""
    monkeypatch.setattr(worker, "_ticker", lambda ctx: asyncio.sleep(0))  # no tick: it would read the database
    queued = []

    class Redis:
        def __init__(self, done: bool):
            self.done = done

        async def exists(self, key):
            return self.done

        async def enqueue_job(self, name, **options):
            queued.append((name, options.get("_job_id")))

    asyncio.run(worker.WorkerSettings.on_startup({"redis": Redis(done=False)}))
    asyncio.run(worker.WorkerSettings.on_startup({"redis": Redis(done=True)}))
    assert queued == [("refresh_starters", "refresh_starters:startup"), ("import_teams", "import_teams:startup")]
