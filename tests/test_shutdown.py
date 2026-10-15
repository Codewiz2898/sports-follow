"""A stopping worker (BHA-51): its long jobs end at their next step and free their locks, and the locks
still held after a few seconds are freed for them, so the next worker takes their work at once instead
of finding it locked for up to twenty minutes. Marks that only space work out (cooldowns) are kept."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import nullcontext
from datetime import timedelta

import pytest

from sports_follow import bus, config, pipeline, structured, worker
from sports_follow.models import Player, PlayerCard

from .test_pipeline import NOW, FakeDb, FakeRedis


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(bus, "_sync", fake)
    monkeypatch.setattr(bus, "_held", {})
    return fake


def in_thread(fn, *args) -> threading.Thread:
    job = threading.Thread(target=fn, args=args, daemon=True)
    job.start()
    return job


# ---------------------------------------------------------------- which locks a stopping worker frees


def test_a_stopping_worker_frees_its_locks_but_keeps_cooldowns(redis):
    bus.try_lock("build:1", 1200)
    bus.cooldown("bindtry:1", 6 * 3600)  # no new lookup for six hours, whatever restarts meanwhile
    assert bus.held() == ["build:1"]
    assert bus.release_held() == ["build:1"]
    assert not bus.is_locked("build:1") and bus.is_locked("bindtry:1")
    assert bus.held() == []


def test_a_lock_another_worker_took_over_stays_theirs(redis):
    bus.try_lock("poll:7", 120)
    redis.data["lock:poll:7"] = "the-next-workers-token"  # this one lapsed, and another worker took it
    bus.release_held()
    assert redis.data["lock:poll:7"] == "the-next-workers-token"


def test_a_released_lock_is_no_longer_held(redis):
    token = bus.try_lock("structured:3", 1200)
    bus.release("structured:3", token)
    bus.try_lock("engine", 120)
    bus.release("engine")  # without its token, as the engine lane does
    assert bus.held() == []


def test_a_stopping_worker_takes_no_new_locks(redis, stopping):
    # A job that gets to a lock after the shutdown hook freed the rest (a first build's lookup that ran
    # long, then its refresh) would hold that lock past the worker, for the next one to wait out.
    stopping.set()
    assert bus.try_lock("structured:4", 1200) is None
    assert not bus.is_locked("structured:4") and bus.held() == []


def test_the_roster_import_still_waits_six_hours_after_a_restart(monkeypatch, redis):
    # A deploy mustn't cost a roster import (a source's requests): the mark outlives the worker.
    imported = []
    source = type("Source", (), {"system": "pubg_esports", "roster_every": 6 * 3600})()
    monkeypatch.setattr("sports_follow.adapters.ADAPTERS", {"pubg_esports": source})
    monkeypatch.setattr(worker.registry, "import_roster", imported.append)
    worker._warm_sources()
    bus.release_held()
    worker._warm_sources()
    assert imported == [source]


def test_the_warm_pass_moves_the_players_of_a_source_that_renamed_them(monkeypatch, redis):
    # BHA-216: every ten minutes, so every player bound to it moves, followed or not (structured.rekey_bound).
    moved = []
    renamed = type("Source", (), {"system": "renamed", "rekey": lambda self, ref: None})()
    plain = type("Source", (), {"system": "plain"})()  # never renamed anyone: not asked
    monkeypatch.setattr("sports_follow.adapters.ADAPTERS", {"renamed": renamed, "plain": plain})
    monkeypatch.setattr(worker.structured, "rekey_bound", lambda adapter: moved.append(adapter.system) or 0)
    worker._warm_sources()
    assert moved == ["renamed"]


# ---------------------------------------------------------------- long jobs end at their next step


def test_a_poll_session_ends_when_the_worker_stops(monkeypatch, redis, stopping):
    polls = []
    monkeypatch.setattr(structured, "poll_once", lambda event_id: polls.append(event_id) or ("live", 30, True))
    job = in_thread(structured.poll_event, 5)
    time.sleep(0.2)  # it has polled once and waits 30 s for the next round
    started = time.monotonic()
    stopping.set()
    job.join(5)
    assert not job.is_alive() and time.monotonic() - started < 1  # woken, not after the 30 s
    assert polls == [5]
    assert not bus.is_locked("poll:5") and bus.held() == []


def test_a_poll_lane_ends_when_the_worker_stops(monkeypatch, redis, stopping):
    monkeypatch.setitem(structured.ADAPTERS, "lane_source", object())
    monkeypatch.setattr(structured, "lane_round", lambda adapter, moved, missing: 30)
    job = in_thread(structured.poll_lane, "lane_source")
    time.sleep(0.2)
    stopping.set()
    job.join(5)
    assert not job.is_alive()
    assert not bus.is_locked("lane:lane_source") and bus.held() == []


def test_a_build_stops_without_writing_once_the_worker_stops(monkeypatch, redis, stopping):
    # The model answered after the worker began stopping: the lock may already be the next worker's,
    # so this build writes nothing; arq has queued it again, and the next worker builds the card.
    player = Player(id=1, slug="one", name="Player One", sport="Hurling", status="active", teams=[])
    card = PlayerCard(player_id=1, status="ready", card={"status": "ready"}, version=3, built_at=NOW - timedelta(hours=7))
    applied, failed = [], []

    def agent(llm, name, known):
        yield {"type": "progress", "message": "Searching the web…"}
        stopping.set()  # a deploy, while the model works
        yield {"type": "result", "data": {"name": "Player One"}}

    monkeypatch.setattr(config, "CARD_MAX_AGE", 6 * 3600)
    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(FakeDb(player, card)))
    monkeypatch.setattr(pipeline, "_now", lambda: NOW)
    monkeypatch.setattr(pipeline, "llm", lambda: None)
    monkeypatch.setattr(pipeline, "follow_player", agent)
    monkeypatch.setattr(pipeline, "apply_report", lambda player_id, report: applied.append(player_id))
    monkeypatch.setattr(pipeline, "mark_failed", lambda player_id, message: failed.append(player_id))
    monkeypatch.setattr(structured, "describe", lambda player_id: None)
    pipeline.build_card(1)
    assert applied == [] and failed == []  # not a failure: the card keeps what it had
    assert not bus.is_locked("build:1")


def test_a_live_check_stops_without_writing_once_the_worker_stops(monkeypatch, redis, stopping):
    player = Player(id=1, slug="one", name="Player One", sport="Hurling", status="active", teams=[])
    applied = []

    def check(llm, name, sport, teams):
        stopping.set()  # a deploy, while the model looks for the score
        yield {"type": "result", "data": {"is_live": True}}

    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(FakeDb(player)))
    monkeypatch.setattr(pipeline, "llm", lambda: None)
    monkeypatch.setattr(pipeline, "live_update", check)
    monkeypatch.setattr(pipeline, "apply_live", lambda player_id, data: applied.append(player_id))
    pipeline.refresh_live(1)
    assert applied == [] and not bus.is_locked("live:1")


# ---------------------------------------------------------------- the worker's shutdown hook


def test_the_shutdown_hook_frees_only_what_jobs_still_hold(monkeypatch, redis, stopping, caplog):
    monkeypatch.setattr(worker, "STOP_WAIT", 0.5)
    polling = bus.try_lock("poll:1", 120)
    bus.try_lock("build:2", 1200)  # a build waiting on the model: it won't notice for a while

    def poll_session():  # ends at its next step and frees its own lock
        stopping.wait()
        bus.release("poll:1", polling)

    in_thread(poll_session)
    caplog.set_level(logging.INFO, logger="sports_follow.worker")
    asyncio.run(worker.wind_down({}))
    assert stopping.is_set()
    assert not bus.is_locked("poll:1") and not bus.is_locked("build:2")
    assert "freed 1 lock(s) jobs still held: build:2" in caplog.text


def test_the_shutdown_hook_doesnt_wait_when_every_job_has_ended(monkeypatch, redis, stopping, caplog):
    monkeypatch.setattr(worker, "STOP_WAIT", 30)
    caplog.set_level(logging.INFO, logger="sports_follow.worker")
    started = time.monotonic()
    asyncio.run(worker.wind_down({}))
    assert time.monotonic() - started < 1
    assert "every job ended and freed its locks" in caplog.text


def test_the_worker_runs_the_shutdown_hook():
    assert worker.WorkerSettings.on_shutdown is worker.wind_down
