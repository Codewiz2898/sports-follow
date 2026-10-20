"""The pipeline worker: arq jobs for card builds, source refreshes, live polls and agent live
checks, plus the one-minute scheduler tick (which also sends moments no sender claimed). Cron times
are UTC, whatever the machine's zone.

Run it with:  .venv/bin/arq sports_follow.worker.WorkerSettings
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from arq import cron, func
from arq.connections import RedisSettings

from . import accounts, bus, campaigns, db, engine, history, league, live, livecards, logs, match, metrics, notify, photos, pipeline, predictions, registry, retention, shutdown, sidelined, starters, structured, teams, tracing
from .adapters import budget
from .config import REDIS_URL

log = logging.getLogger("sports_follow.worker")

# A job holds a thread for as long as it runs: a poll or a lane up to POLL_SESSION (ten minutes), the
# registry import about two hours. asyncio's default pool has min(32, cpus + 4) threads, six on the
# 2 vCPU server, so six long jobs used to leave every other job, and the tick, waiting for one.
MAX_JOBS = 24
_tick_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tick")
# After a sleep or a paused VM, arq queues every run a cron job missed, one per heartbeat, from the first
# it missed: 384 ticks in four minutes once. A run this many seconds past its time is skipped, unless no
# other late run has gone for as long, so a busy queue that delays every tick can't stop them all.
LATE = 50

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


def _logged(fn, quiet: bool = False, deep: bool = True):
    """A job whose lines carry its own id, or that of the request that queued it (server.enqueue), its
    name and its player, game or source, and which ends with one line: how long it waited in the queue,
    how long it ran and how it ended (a failed job's line has the exception's stack, unless a line of the
    job's own logged it). It's a span too, in the trace of the request that queued it, or of its own; a
    job that isn't deep has only that span, none for the requests and queries it makes."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    async def job(ctx, *args, trace: dict | None = None, **kwargs):
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


async def in_lane(lane: str, fn, *args):
    """fn(*args) on a worker thread, its source requests in this lane (adapters/budget.py)."""

    def run():
        with budget.in_lane(lane):
            return fn(*args)

    return await asyncio.to_thread(run)


async def build_card(ctx, player_id: int) -> None:
    await in_lane('refresh', pipeline.build_card, player_id)


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
    """Moments a poll or a rebuild found but no sender claimed: the worker restarted in between."""
    await asyncio.to_thread(notify.send, moment_ids)


async def engine_lane(ctx) -> None:
    """Chess engine evaluations for live games, within its CPU budget (engine.py)."""
    await asyncio.to_thread(engine.run_lane)


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

    for adapter in list(ADAPTERS.values()):
        warm = getattr(adapter, "warm", None)
        if warm is None:
            continue
        try:
            log.info("warm %s: %s lobbies indexed", adapter.system, warm())
        except Exception:
            log.exception("warm %s failed", adapter.system)
    for adapter in list(ADAPTERS.values()):
        if getattr(adapter, "rekey", None) is None:
            continue
        try:
            if moved := structured.rekey_bound(adapter):
                log.info("rekey %s: %d players moved to the ids it gives now", adapter.system, moved)
        except Exception:
            log.exception("rekey %s failed", adapter.system)
    for adapter in list(ADAPTERS.values()):
        every = getattr(adapter, "roster_every", None)
        if every and bus.cooldown(f"registry-{adapter.system}", every):  # at most every `every` seconds
            registry.import_roster(adapter)


async def warm_sources(ctx) -> None:
    """Shared source indexes, every ten minutes (_warm_sources)."""
    if await _missed(ctx, "warm_sources"):
        return
    await in_lane('refresh', _warm_sources)


async def adopt_stragglers(ctx, anon: str, account_id: int, session_id: int) -> None:
    """A minute after a sign-in: whatever a request already under way wrote to the device's anonymous
    fan after its things moved into the account (accounts.adopt_later)."""
    moved = await asyncio.to_thread(accounts.adopt_later, anon, account_id, session_id)
    if moved:
        log.info("accounts: %d more player(s) moved into account %d after sign-in", moved, account_id)


STARTERS_DONE = "starters:refreshed"  # set once a refresh finishes; a worker starting without it queues one


async def refresh_starters(ctx) -> None:
    """Each sport's top players for new fans, found in their sources with their next games: once a day,
    and when a worker starts with no refresh in the last day (startup), say on a fresh server. One at a
    time: a restart's refresh and 04:40's don't both ask the sources."""
    if not await ctx["redis"].set("starters:refreshing", "1", nx=True, ex=2400):
        return
    try:
        await in_lane('refresh', starters.refresh)
        await ctx["redis"].set(STARTERS_DONE, "1", ex=23 * 3600)
    finally:
        await ctx["redis"].delete("starters:refreshing")


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
    await asyncio.to_thread(predictions.made, system, game)  # the questions its board asks, if it's to come


async def prepare_questions(ctx) -> None:
    """The questions of fans' games coming up within a day, made ahead from a read of each (predictions.prepare):
    every ten minutes, so a fan can call a game, and be asked to, before its poller arms it."""
    if await _missed(ctx, "prepare_questions"):
        return
    for system, game in await asyncio.to_thread(predictions.prepare):
        await ctx["redis"].enqueue_job("read_match", system, game, _job_id=f"read_match:{system}:{game}")


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
    ended = await asyncio.to_thread(accounts.purge_expired)
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
    import lane; one at a time."""
    if not await ctx["redis"].set("teams:importing", "1", nx=True, ex=1800):
        return
    try:
        await in_lane('import', teams.import_all)
        await ctx["redis"].set(TEAMS_DONE, "1", ex=7 * 24 * 3600)
    finally:
        await ctx["redis"].delete("teams:importing")


async def prune(ctx) -> None:
    """Rows and hot copies nothing reads any more, once a day (retention.py)."""
    await asyncio.to_thread(retention.prune)


async def fetch_photos(ctx) -> None:
    """Followed and starter players' photos from Wikimedia Commons, every 15 minutes (photos.py)."""
    if await _missed(ctx, "fetch_photos"):
        return
    await asyncio.to_thread(photos.run)


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
    # A connection for each of those threads, the tick's, notify's and the live cards': checkouts give up after 5 s.
    db.size_pool(MAX_JOBS + 4 + _tick_pool._max_workers + notify._pool._max_workers + livecards._pool._max_workers)
    metrics.watch()  # the live games, the queue, cards and source allowances, read each minute here alone
    metrics.seed()
    redis = ctx.get("redis")
    if redis is not None and not await redis.exists(STARTERS_DONE):
        # A deploy restarts the worker several times a day: only one that finds no recent refresh queues one.
        await redis.enqueue_job("refresh_starters", _job_id="refresh_starters:startup")
    if redis is not None and not await redis.exists(TEAMS_DONE):
        await redis.enqueue_job("import_teams", _job_id="import_teams:startup")


async def wind_down(ctx) -> None:
    """The worker is stopping (a deploy, a restart). arq has cancelled the running jobs and queued them
    again, but their threads run on: they end at their next step (shutdown.stopping) and free their locks.
    Whatever is still held after STOP_WAIT, say a build waiting on the model, is freed here, so the next
    worker starts that work at once instead of finding it locked for up to twenty minutes (BHA-51)."""
    shutdown.stopping.set()
    deadline = time.monotonic() + STOP_WAIT
    while bus.held() and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    freed = bus.release_held()
    if freed:
        log.warning("stopping: freed %d lock(s) jobs still held: %s", len(freed), ", ".join(sorted(freed)), extra={"locks": len(freed)})
    else:
        log.info("stopping: every job ended and freed its locks")


async def _in_tick_pool(fn):
    """The tick works on threads of its own: whatever the jobs are doing, the scheduler keeps its minute."""
    return await asyncio.get_running_loop().run_in_executor(_tick_pool, logs.carried(fn))


async def tick(ctx) -> None:
    if await _missed(ctx, "tick"):
        return
    due = await _in_tick_pool(pipeline.due_work)
    for player_id in due.builds:
        await ctx["redis"].enqueue_job("build_card", player_id)
    for player_id in due.refreshes:
        await ctx["redis"].enqueue_job("refresh_structured", player_id)
    for team_id in due.teams:
        await ctx["redis"].enqueue_job("refresh_team", team_id)
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
    if due.builds or due.refreshes or due.teams or due.polls or due.lanes or due.lives:
        log.info("tick: %d builds, %d refreshes, %d team refreshes, %d polls, %d poll lanes, %d agent live checks",
                 len(due.builds), len(due.refreshes), len(due.teams), len(due.polls), len(due.lanes), len(due.lives))


class WorkerSettings:
    functions: list[Any] = [_logged(f) for f in (build_card, refresh_live, refresh_structured, refresh_team, poll_event, poll_lane, send_moments, engine_lane, history_lane, adopt_stragglers, read_live, read_match, read_league)]
    functions.append(func(_logged(refresh_starters), timeout=2400))  # also queued by a worker starting without a recent refresh
    functions.append(func(_logged(import_teams), timeout=1800))  # and the team import, likewise
    cron_jobs = [
        cron(_logged(tick, quiet=True), second=0, run_at_startup=True),
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
