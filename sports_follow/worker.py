"""The pipeline worker: arq jobs for card builds, source refreshes, live polls and agent live
checks, plus the one-minute scheduler tick (which also sends moments no sender claimed), which runs
beside the jobs rather than in one of their slots, as the prediction pushes do (ON_THE_CLOCK). Cron
times are UTC, whatever the machine's zone.

Run it with:  .venv/bin/arq sports_follow.worker.WorkerSettings
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from arq import cron, func
from arq.connections import RedisSettings
from arq.constants import in_progress_key_prefix

from . import accounts, bus, campaigns, db, engine, history, league, live, livecards, logs, match, metrics, notify, photos, pipeline, predictions, predictions_push, registry, retention, shutdown, sidelined, starters, structured, teams, tracing
from .adapters import budget
from .config import REDIS_URL

log = logging.getLogger("sports_follow.worker")

# A job holds a thread for as long as it runs: a poll or a lane up to POLL_SESSION (ten minutes), the
# registry import about two hours. asyncio's default pool has min(32, cpus + 4) threads, six on the
# 2 vCPU server, so six long jobs used to leave every other job, and the tick, waiting for one.
MAX_JOBS = 24
_tick_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tick")
# The prediction pushes' own thread, likewise: a job arq timed out keeps its thread, so the jobs' can all be taken.
# One run at a time.
_pushes_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pushes")
# After a sleep or a paused VM, arq queues every run a cron job missed, one per heartbeat, from the first
# it missed: 384 ticks in four minutes once. A run this many seconds past its time is skipped, unless no
# other late run has gone for as long, so a busy queue that delays every tick can't stop them all.
LATE = 50
# A minute, as the jobs ON_THE_CLOCK keep it: tests make it shorter.
MINUTE = 60

# Again at startup: arq's CLI replaces the logging config after importing this module.
logs.setup()
tracing.setup("sports-follow-worker")
# A job's arguments its log lines are tagged with.
FIELDS = ("player_id", "event_id", "system", "game")
# A quiet job's closing line (the tick's, every minute) is logged only past this many seconds, or on failure.
QUIET_UNDER = 10
# How long a stopping worker gives its jobs to end and free their own locks before it frees the rest
# itself: well inside the ten seconds Docker waits between asking a container to stop and killing it.
STOP_WAIT = 5
# The job threads still running, by what they run: arq cancels a stopping worker's jobs, but their threads run on
# until they end (wind_down waits for them, and says which didn't).
_running: Counter[str] = Counter()
_running_guard = threading.Lock()


def _logged(fn, quiet: bool = False, deep: bool = True):
    """A job whose lines carry its own id, or that of the request that queued it (server.enqueue), its
    name and its player, game or source, and which ends with one line: how long it waited in the queue,
    how long it ran and how it ended (a failed job's line has the exception's stack, unless a line of the
    job's own logged it). It's a span too, in the trace of the request that queued it, or of its own; a
    job that isn't deep has only that span, none for the requests and queries it makes."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    async def job(ctx, *args, trace: dict | None = None, **kwargs):
        await _in_progress_for(ctx, fn.__name__)
        given = signature.bind_partial(ctx, *args, **kwargs).arguments
        fields = {k: given[k] for k in FIELDS if k in given}
        logs.begin(rid=(trace or {}).get("rid") or logs.new_id(), job=fn.__name__, **fields)
        started = perf_counter()
        queued = ctx.get("enqueue_time")
        waited = (datetime.now(timezone.utc) - queued).total_seconds() if isinstance(queued, datetime) else None

        def ended(outcome: str, how: str, failure: Exception | None = None) -> None:
            took = perf_counter() - started
            metrics.job(fn.__name__, outcome, took, waited)
            if quiet and outcome == "ok" and took < QUIET_UNDER:
                return
            queue = "" if waited is None else f", after {waited:.1f}s in the queue"
            log.log(logging.INFO if outcome == "ok" else logging.WARNING, "%s %s in %.1fs%s", fn.__name__, how, took, queue, exc_info=failure, extra={
                "outcome": outcome, "duration_ms": round(took * 1000, 1), "waited_ms": None if waited is None else round(waited * 1000),
                "job_id": ctx.get("job_id"), "attempt": ctx.get("job_try"),
            })

        attributes = {"job.id": ctx.get("job_id"), "job.attempt": ctx.get("job_try"), "job.waited_ms": None if waited is None else round(waited * 1000), **fields}
        with tracing.job_span(fn.__name__, trace, attributes) as span, (nullcontext() if deep else tracing.shallow()):
            try:
                result = await fn(ctx, *args, **kwargs)
            except asyncio.CancelledError:
                ended("cancelled", "cancelled (timed out, or the worker stopped)")
                tracing.failed(span, "cancelled")
                raise
            except Exception as exc:
                ended("failed", f"failed ({type(exc).__name__}: {exc})", exc)
                raise
            ended("ok", "done")
            return result

    return job


def _timeout(name: str) -> float:
    """The seconds arq lets a job of this name run before cancelling it: the timeout it was given (func() or cron()),
    or the worker's job_timeout. The longest, where it's given more than one."""
    jobs: list[Any] = [*WorkerSettings.functions, *WorkerSettings.cron_jobs]  # a job, or arq's Function or CronJob of one
    given = [getattr(job, "timeout_s", None) or WorkerSettings.job_timeout for job in jobs if getattr(job, "coroutine", job).__name__ == name]
    return max(given, default=WorkerSettings.job_timeout)


async def _in_progress_for(ctx, name: str) -> None:
    """arq marks a job it starts as in progress, so no worker starts it again and its id can't be queued again, for
    the longest timeout of any job (the weekly import's six hours); only a worker that ends the job takes the mark
    off. After a worker died mid-job (an OOM kill, a host reboot), a job queued under a fixed id (a source's poll
    lane, the engine lane, the moment sweep, the history lane, a player's refresh) was stuck for six hours: no live
    scores from that source meanwhile (BUG-272). The mark lasts as long as this job may run instead, with arq's own
    ten seconds over, and then the next worker runs it again."""
    redis, job_id = ctx.get("redis"), ctx.get("job_id")
    if redis is None or not job_id:
        return
    with contextlib.suppress(Exception):  # Redis not answering fails the job's own steps, not this
        await redis.pexpire(in_progress_key_prefix + job_id, int((_timeout(name) + 10) * 1000))


def _counted(fn, *args):
    """fn(*args), counted among the job threads still running for as long as it runs (running)."""
    name = f"{getattr(fn, '__module__', '').rpartition('.')[2]}.{getattr(fn, '__name__', 'job')}"
    with _running_guard:
        _running[name] += 1
    try:
        return fn(*args)
    finally:
        with _running_guard:
            _running[name] -= 1
            if _running[name] <= 0:
                del _running[name]


def running() -> list[str]:
    """What the job threads still running run ("starters.refresh"), one entry each."""
    with _running_guard:
        return sorted(_running.elements())


async def on_thread(fn, *args):
    """fn(*args) on a worker thread, counted while it runs (running)."""
    return await asyncio.to_thread(_counted, fn, *args)


async def in_lane(lane: str, fn, *args):
    """fn(*args) on a worker thread, its source requests in this lane (adapters/budget.py)."""

    def run():
        with budget.in_lane(lane):
            return _counted(fn, *args)

    return await asyncio.to_thread(run)


def _guarded(name: str, seconds: int, fn, done: str | None = None, keep: int = 0):
    """fn, unless another worker's thread is running it (a restart's job beside the one before it), under a lock
    its own thread holds until it ends: arq cancelling the job, as a stopping worker does, doesn't free it while
    the thread runs on, and a stopping worker sees it (bus.held). Once it ends with the worker still running, the
    mark `done` is set for `keep` seconds."""

    def run():
        token = bus.try_lock(name, seconds)
        if token is None:
            log.info("%s: running elsewhere; skipped", name)
            return
        try:
            fn()
            if done and not shutdown.stopping.is_set():  # one cut short by a stop is done by the next worker
                bus.sync_redis().set(done, "1", ex=keep)
        finally:
            bus.release(name, token)

    run.__name__ = getattr(fn, "__name__", name)
    run.__module__ = getattr(fn, "__module__", __name__)
    return run


async def build_card(ctx, player_id: int) -> None:
    await in_lane('refresh', pipeline.build_card, player_id)


async def reread_profile(ctx, player_id: int) -> None:
    """A player's profile from Wikidata and Wikipedia, read again a month after the last (wiki.py)."""
    await in_lane('refresh', pipeline.reread_profile, player_id)


async def refresh_live(ctx, player_id: int) -> None:
    await in_lane('live', pipeline.refresh_live, player_id)


async def refresh_structured(ctx, player_id: int) -> None:
    await in_lane('refresh', structured.refresh, player_id)


async def refresh_team(ctx, team_id: int) -> None:
    """A followed team's games, read again from its source (teams.refresh); or a team page's nobody follows it from."""
    await in_lane('refresh', teams.refresh, team_id)


async def poll_event(ctx, event_id: int) -> None:
    await in_lane('live', structured.poll_event, event_id)


async def poll_lane(ctx, system: str) -> None:
    """Every armed and live event of one source, one request a round (structured.poll_lane)."""
    await in_lane('live', structured.poll_lane, system)


async def send_moments(ctx, moment_ids: list[int]) -> None:
    """Moments a poll or a rebuild found but no sender claimed: the worker restarted in between. One that can't be
    planned holds back no other game's (notify.sweep)."""
    await on_thread(notify.sweep, moment_ids)


async def send_held(ctx, moment_ids: list[int]) -> None:
    """Moments a fan of both sides of a board was left to hear from the other side, late where it never said them
    (notify.send_held)."""
    await on_thread(notify.send_held, moment_ids)


async def engine_lane(ctx) -> None:
    """Chess engine evaluations for live games, within its CPU budget (engine.py)."""
    await on_thread(engine.run_lane)


async def history_lane(ctx) -> None:
    """Older results and missing scorecards, a few source requests a minute (history.py)."""
    await in_lane('history', history.run)


def _import_registry() -> None:
    if registry.import_locked() is None:
        log.info("registry import: the last week's run, or one started by hand, is still going; skipped")


def _warm_sources() -> None:
    """Sources that keep a shared index (PUBG: recent tournaments and their matches) bring it up to date,
    a source that has changed how it names its athletes has the players bound to it moved to the new ids
    (structured.rekey_bound), and a source whose roster is read more often than the weekly import (PUBG's,
    from that index, every six hours: roster_every) adds its players to the registry."""
    from .adapters import ADAPTERS

    # A stopping worker reads no more sources: the next one's pass, ten minutes on at most, does the rest.
    for adapter in list(ADAPTERS.values()):
        warm = getattr(adapter, "warm", None)
        if warm is None or shutdown.stopping.is_set():
            continue
        try:
            log.info("warm %s: %s lobbies indexed", adapter.system, warm())
        except Exception:
            log.exception("warm %s failed", adapter.system)
    for adapter in list(ADAPTERS.values()):
        if getattr(adapter, "rekey", None) is None or shutdown.stopping.is_set():
            continue
        try:
            if moved := structured.rekey_bound(adapter):
                log.info("rekey %s: %d players moved to the ids it gives now", adapter.system, moved)
        except Exception:
            log.exception("rekey %s failed", adapter.system)
    for adapter in list(ADAPTERS.values()):
        every = getattr(adapter, "roster_every", None)
        if every and not shutdown.stopping.is_set() and bus.cooldown(f"registry-{adapter.system}", every):  # at most every `every` seconds
            registry.import_roster(adapter)


async def warm_sources(ctx) -> None:
    """Shared source indexes, every ten minutes (_warm_sources)."""
    if await _missed(ctx, "warm_sources"):
        return
    await in_lane('refresh', _warm_sources)


async def adopt_stragglers(ctx, anon: str, account_id: int, session_id: int) -> None:
    """A minute after a sign-in: whatever a request already under way wrote to the device's anonymous
    fan after its things moved into the account (accounts.adopt_later). Then a word on the account's own
    channel, as a follow says: its apps open on its devices send for their list again and show it (BUG-165)."""
    moved = await on_thread(accounts.adopt_later, anon, account_id, session_id)
    if moved:
        log.info("accounts: %d more player(s) moved into account %d after sign-in", moved, account_id)
    fan = await on_thread(_fan_of, account_id)
    try:
        if fan:
            await ctx["redis"].publish(bus.follows_channel(fan), "{}")
    except Exception as exc:  # noqa: BLE001 — what moved stays moved; the apps catch up as their streams connect
        log.warning("accounts: couldn't tell account %d's open apps what moved in: %s", account_id, exc)


def _fan_of(account_id: int) -> str | None:
    """The account's fan id (accounts.fan_of), or None if it's gone."""
    with db.session() as s:
        return accounts.fan_of(s, account_id)


STARTERS_DONE = "starters:refreshed"  # set once a refresh finishes; a worker starting without it queues one


async def refresh_starters(ctx) -> None:
    """Each sport's top players for new fans, found in their sources with their next games: once a day,
    and when a worker starts with no refresh in the last day (startup), say on a fresh server. One at a
    time: a restart's refresh and 04:40's don't both ask the sources (_guarded)."""
    await in_lane('refresh', _guarded("starters-refresh", 2400, starters.refresh, STARTERS_DONE, 23 * 3600))


async def read_live(ctx, system: str) -> None:
    """What a source has on now, league-wide, for "Live now" (live.read): queued by the API when a fan looks
    and it last asked over a minute ago. In the refresh lane: no fan waits on it, so it stops short of the
    allowance kept for followed players' live games."""
    await in_lane('refresh', live.read, system)


async def read_match(ctx, system: str, game: str) -> None:
    """One game from its source, for its match page (match.read): queued by the API while a fan has the page
    open and the last read is over a minute old. In the refresh lane: no fan waits on it, so it stops short of
    the allowance kept for followed players' live games."""
    await in_lane('refresh', match.read, system, game)
    await on_thread(predictions.made, system, game)  # the questions its board asks, if it's to come


async def prepare_questions(ctx) -> None:
    """The questions of fans' games and Today's big games coming up within a day, made ahead from a read of each
    (predictions.prepare): every ten minutes, so a fan can call a game, and be asked to, before its poller arms it."""
    if await _missed(ctx, "prepare_questions"):
        return
    for system, game in await on_thread(predictions.prepare):
        await ctx["redis"].enqueue_job("read_match", system, game, _job_id=f"read_match:{system}:{game}")


async def prediction_pushes(ctx) -> None:
    """"Make your pick" before a campaign's games, and how each pick went once settled (predictions_push.py): every
    minute, at :50 (ON_THE_CLOCK)."""
    if await _missed(ctx, "prediction_pushes"):
        return
    await asyncio.get_running_loop().run_in_executor(_pushes_pool, logs.carried(functools.partial(_counted, predictions_push.run)))


async def read_league(ctx, system: str) -> None:
    """A source's competitions' games, three days back and a fortnight on, for Today's big results and big games
    coming up (league.read): queued by the API when a fan looks and the last read is a few hours old. In the
    refresh lane: no fan waits on it, so it stops short of the allowance kept for live games."""
    await in_lane('refresh', league.read, system)


async def refresh_campaigns(ctx) -> None:
    """Each campaign page's teams' rosters, read again from their sources (campaigns.refresh): once a day, so
    a change of line-up reaches the page's whole-team follow. A few requests a campaign, in the refresh lane."""
    if await _missed(ctx, "refresh_campaigns"):
        return
    await in_lane('refresh', campaigns.refresh)


async def read_sidelined(ctx) -> None:
    """Who's out of the running sources' competitions, injured or suspended, for the Wire's facts
    (sidelined.read_all): twice a day, whoever is looking, as each read is told against the last. In the
    refresh lane: it stops short of the allowance kept for live games."""
    if await _missed(ctx, "read_sidelined"):
        return
    await in_lane('refresh', sidelined.read_all)


async def purge_sessions(ctx) -> None:
    """Sessions unused for a year end, and their devices' alerts with them."""
    ended = await on_thread(accounts.purge_expired)
    if ended:
        log.info("accounts: %d expired session(s) ended", ended)


async def import_registry(ctx) -> None:
    """The weekly player-registry refresh from Wikidata, one query at a time, then the esports pros from
    their sources and each one's Wikidata person, for their photo (roster_wikidata.py); about two hours."""
    await in_lane('import', _import_registry)


TEAMS_DONE = "teams:imported"  # set once an import finishes; a worker starting without it queues one


async def import_teams(ctx) -> None:
    """The team registry from the running sources' lists of teams (teams.import_all): weekly, and when a worker
    starts with no import in the last week (startup), say on a fresh server. A few requests a source, in the
    import lane; one at a time (_guarded)."""
    await in_lane('import', _guarded("teams-import", 1800, teams.import_all, TEAMS_DONE, 7 * 24 * 3600))


async def prune(ctx) -> None:
    """Rows and hot copies nothing reads any more, once a day (retention.py)."""
    await on_thread(retention.prune)


async def fetch_photos(ctx) -> None:
    """Followed and starter players' photos from Wikimedia Commons, every 15 minutes (photos.py)."""
    if await _missed(ctx, "fetch_photos"):
        return
    await on_thread(photos.run)


def _late(ctx) -> bool:
    """Whether this cron run starts more than LATE seconds after its time. arq names a run by its time
    in milliseconds ("cron:tick:1760342400123"); a job called any other way isn't late."""
    scheduled = str(ctx.get("job_id") or "").rpartition(":")[2]
    return scheduled.isdigit() and time.time() - int(scheduled) / 1000 > LATE


async def _missed(ctx, name: str) -> bool:
    """A late run of a cron job to skip: another late run of it went less than LATE seconds ago."""
    return _late(ctx) and not await ctx["redis"].set(f"late:{name}", "1", nx=True, ex=LATE)


async def startup(ctx) -> None:
    """A thread for every job slot, so no job waits for one, and a few for the event loop's own use
    (it looks up hosts in one)."""
    logs.setup()
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=MAX_JOBS + 4, thread_name_prefix="job"))
    # A connection for each of those threads, the tick's, the prediction pushes', notify's and the live cards':
    # checkouts give up after 5 s.
    db.size_pool(MAX_JOBS + 4 + _tick_pool._max_workers + _pushes_pool._max_workers + notify._pool._max_workers + livecards._pool._max_workers)
    metrics.watch()  # the live games, the queue, cards and source allowances, read each minute here alone
    metrics.seed()
    redis = ctx.get("redis")
    if redis is not None and not await redis.exists(STARTERS_DONE):
        # A deploy restarts the worker several times a day: only one that finds no recent refresh queues one.
        await redis.enqueue_job("refresh_starters", _job_id="refresh_starters:startup")
    if redis is not None and not await redis.exists(TEAMS_DONE):
        await redis.enqueue_job("import_teams", _job_id="import_teams:startup")
    if redis is not None:
        ctx["on_the_clock"] = [asyncio.create_task(_on_the_clock(ctx, *job)) for job in ON_THE_CLOCK]  # stopped by wind_down


async def wind_down(ctx) -> None:
    """The worker is stopping (a deploy, a restart). The jobs ON_THE_CLOCK stop first, so the tick queues
    nothing more. arq has cancelled the running jobs and queued them again, but their threads run on: they
    end at their next step (shutdown.stopping) and free their locks. Whatever is still held after STOP_WAIT,
    say a build waiting on the model, is freed here, so the next worker starts that work at once instead of
    finding it locked for up to twenty minutes (BHA-51). A job thread still running then is named: the process
    ends with it, or when Docker stops it."""
    clocked = ctx.pop("on_the_clock", [])
    for task in clocked:
        task.cancel()
    if clocked:
        await asyncio.wait(clocked)
    shutdown.stopping.set()
    deadline = time.monotonic() + STOP_WAIT
    while (bus.held() or running()) and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    freed = bus.release_held()
    still = running()
    if freed:
        log.warning("stopping: freed %d lock(s) jobs still held: %s", len(freed), ", ".join(sorted(freed)), extra={"locks": len(freed)})
    if still:
        log.warning("stopping: %d job thread(s) still running: %s", len(still), ", ".join(still), extra={"threads": len(still)})
    if not freed and not still:
        log.info("stopping: every job ended and freed its locks")


async def _in_tick_pool(fn):
    """The tick works on threads of its own: whatever the jobs are doing, the scheduler keeps its minute."""
    return await asyncio.get_running_loop().run_in_executor(_tick_pool, logs.carried(functools.partial(_counted, fn)))


async def tick(ctx) -> None:
    if await _missed(ctx, "tick"):
        return
    due = await _in_tick_pool(pipeline.due_work)
    for player_id in due.builds:
        await ctx["redis"].enqueue_job("build_card", player_id)
    # One refresh of a player or a team waits at a time, as the API queues them too: while the queue was behind, each
    # tick queued another, and each read the source again once it ran (BUG-175).
    for player_id in due.refreshes:
        await ctx["redis"].enqueue_job("refresh_structured", player_id, _job_id=f"refresh-structured:{player_id}")
    for player_id in due.profiles:
        await ctx["redis"].enqueue_job("reread_profile", player_id, _job_id=f"reread-profile:{player_id}")
    for team_id in due.teams:
        await ctx["redis"].enqueue_job("refresh_team", team_id, _job_id=f"refresh-team:{team_id}")
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
    held = await _in_tick_pool(notify.held_due)
    if held:
        log.info("tick: %d moment(s) a board's other side was to tell, to send if it never did", len(held))
        await ctx["redis"].enqueue_job("send_held", held, _job_id="send-held")
    if await _in_tick_pool(history.has_work):
        await ctx["redis"].enqueue_job("history_lane", _job_id="history-lane")
    if due.builds or due.refreshes or due.teams or due.polls or due.lanes or due.lives or due.profiles:
        log.info("tick: %d builds, %d refreshes, %d team refreshes, %d polls, %d poll lanes, %d agent live checks, %d profiles",
                 len(due.builds), len(due.refreshes), len(due.teams), len(due.polls), len(due.lanes), len(due.lives), len(due.profiles))


# Every-minute jobs that keep their minute however full the job slots are: arq runs a cron job in a job slot like any
# other, and with MAX_JOBS poll sessions running (about 20 followed chess players live in a broadcast, each board its
# own ten-minute poll) no game was armed, no poll handed over, no moment swept and no "Make your pick" sent for up to
# ten minutes (BUG-173). Each runs beside the jobs instead (_on_the_clock). An entry is the job, its second of the
# minute (UTC), the seconds after which a run is cancelled (its timeout as a cron job), and whether it also runs as
# the worker starts.
ON_THE_CLOCK = [
    (tick, 0, 900, True),
    (prediction_pushes, 50, 120, False),
]


def _next_run(last: float, now: float, at: float = 0) -> float:
    """When a job ON_THE_CLOCK that last ran, or was due, at `last` runs next, it being `now`: `at` seconds past
    the next whole MINUTE after both. A run that went long, or a laptop that slept, skips the minutes it missed
    instead of running them all, and one woken a moment early doesn't run its minute twice."""
    return max((last - at) // MINUTE, (now - at) // MINUTE) * MINUTE + MINUTE + at


async def _on_the_clock(ctx, fn, at: float, timeout: float, at_startup: bool) -> None:
    """An every-minute job (ON_THE_CLOCK) as a task of the worker's own, `at` seconds past each minute, and at once
    if `at_startup`: it takes no job slot, and its work keeps to threads of its own (_tick_pool, _pushes_pool). Each run
    is a task of its own, logged as a quiet job is, under an id like arq's for a cron run ("cron:tick:<ms>", which
    _missed reads); it's cancelled after `timeout` seconds, and runs once a minute however many workers share the
    queue (a "tick:<ms>" key), as arq's cron did. A run that fails, or Redis not answering, doesn't stop the next."""
    name, run = fn.__name__, _logged(fn, quiet=True)
    due = time.time() if at_startup else _next_run(time.time(), time.time(), at)
    while True:
        await asyncio.sleep(due - time.time())
        ms = int(due * 1000)
        try:
            first = await ctx["redis"].set(f"{name}:{ms}", "1", nx=True, px=int(MINUTE * 2000))
        except Exception:
            log.warning("%s: Redis didn't answer; the next run tries again", name, exc_info=True)
            first = False
        if first:
            job = {"redis": ctx["redis"], "job_id": f"cron:{name}:{ms}", "enqueue_time": datetime.fromtimestamp(due, timezone.utc)}
            with contextlib.suppress(Exception):  # a run that failed or timed out has logged how it ended (_logged)
                await asyncio.wait_for(asyncio.create_task(run(job)), timeout)
        due = _next_run(due, time.time(), at)


class WorkerSettings:
    functions: list[Any] = [_logged(f) for f in (build_card, reread_profile, refresh_live, refresh_structured, refresh_team, poll_event, poll_lane, send_moments, send_held, engine_lane, history_lane, adopt_stragglers, read_live, read_match, read_league)]
    functions.append(func(_logged(refresh_starters), timeout=2400))  # also queued by a worker starting without a recent refresh
    functions.append(func(_logged(import_teams), timeout=1800))  # and the team import, likewise
    # Nor the every-minute jobs ON_THE_CLOCK: a cron run waits for a free job slot.
    cron_jobs = [
        cron(_logged(purge_sessions), hour=4, minute=10, second=0),
        cron(_logged(refresh_starters), hour=4, minute=40, second=0, timeout=2400),
        cron(_logged(warm_sources), minute=set(range(0, 60, 10)), second=30, run_at_startup=True, timeout=900),
        cron(_logged(import_registry, deep=False), weekday="mon", hour=3, minute=30, second=0, timeout=6 * 3600),
        cron(_logged(import_teams), weekday="mon", hour=2, minute=50, second=0, timeout=1800),
        cron(_logged(prune, deep=False), hour=2, minute=15, second=0, timeout=3 * 3600),
        cron(_logged(fetch_photos, quiet=True), minute={7, 22, 37, 52}, second=40, run_at_startup=True, timeout=900),
        # Before Europe's matchdays get going, and after most of the day's news is in.
        cron(_logged(read_sidelined), hour={6, 16}, minute=30, second=0, timeout=600),
        cron(_logged(refresh_campaigns), hour=5, minute=10, second=0, timeout=600),
        cron(_logged(prepare_questions, quiet=True), minute=set(range(4, 60, 10)), second=20, run_at_startup=True, timeout=300),
    ]
    # Without a zone arq reads cron times in the machine's own: the weekly import ran at 22:00 UTC on a
    # laptop in India, Sunday evening's football.
    timezone = timezone.utc
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    on_startup = startup
    on_shutdown = wind_down
    # A poll job or lane holds its slot for up to POLL_SESSION while games are on; builds hold one for minutes.
    max_jobs = MAX_JOBS
    job_timeout = 900
    keep_result = 0
