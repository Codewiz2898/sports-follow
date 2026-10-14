"""The source budget (adapters/budget.py): one rate-limit layer for every source, by lane, shared by every process."""

from __future__ import annotations

import ast
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sports_follow import search, worker
from sports_follow.adapters import budget, liquipedia, pubg

from .test_api import api  # noqa: F401  (the fixture)

ADAPTERS_DIR = Path(__file__).resolve().parent.parent / "sports_follow" / "adapters"
SMALL = budget.Budget("small", windows=(budget.Window(3600, 20, margin=2),), reserve=8)


def test_work_is_in_the_lane_it_started_in_even_on_a_pool_thread():
    assert budget.lane() == "refresh"  # nothing said: a background lane
    with budget.in_lane("live"), ThreadPoolExecutor(1) as pool:
        assert budget.lane() == "live"
        assert pool.submit(budget.lane).result() == "refresh"  # a pool thread doesn't carry it by itself
        assert pool.submit(budget.carry(budget.lane)).result() == "live"
    assert budget.lane() == "refresh"


def test_background_lanes_stop_at_the_reserve_and_live_games_spend_it():
    for _ in range(10):  # 20 an hour, 2 never spent, 8 kept: 10 for the background
        budget.take(SMALL, lane_name="refresh")
    assert not budget.spare(SMALL)
    with pytest.raises(budget.RateLimited, match="the history share of its 20 requests an hour is spent"):
        budget.take(SMALL, lane_name="history")
    for _ in range(8):
        budget.take(SMALL, lane_name="live")
    with pytest.raises(budget.RateLimited):
        budget.take(SMALL, lane_name="live")  # the margin, never spent


def test_a_scarce_sources_fans_get_their_share_and_no_more(caplog):
    # A fan's search, preview or follow can't spend what live lobbies need: past their share, refused.
    for scarce in (liquipedia.BUDGET, pubg.BUDGET):
        assert scarce.fan is not None and scarce.fan < scarce.windows[0].requests - scarce.reserve
        for _ in range(scarce.fan):
            budget.take(scarce, lane_name="fan")
        with caplog.at_level(logging.WARNING), pytest.raises(budget.RateLimited, match=f"{scarce.name}: fans' share"):
            budget.take(scarce, lane_name="fan")
        budget.take(scarce, lane_name="live")  # live games still go
    assert "a fan request refused" in caplog.text


def test_a_source_saying_slow_down_pauses_every_lane():
    budget.pause(SMALL)
    assert not budget.spare(SMALL)
    for lane in budget.LANES:
        with pytest.raises(budget.RateLimited, match="slow down"):
            budget.take(SMALL, lane_name=lane)


def test_what_the_source_says_is_left_decides_over_our_count():
    budget.observe(SMALL, "x", 9, 600)  # another key's user, or a process before a restart, spent the rest
    assert budget.left(SMALL, "x") == 9 and not budget.spare(SMALL, "x")  # 9 left: within the margin and reserve
    with pytest.raises(budget.RateLimited, match="9 requests are left"):
        budget.take(SMALL, "x", "refresh")
    budget.take(SMALL, "x", "live")
    budget.take(SMALL, "y", "refresh")  # another bucket is counted apart


def test_a_request_refused_by_one_window_isnt_counted_in_another():
    two = budget.Budget("two", windows=(budget.Window(60, 100), budget.Window(86400, 3)))
    for _ in range(3):
        budget.take(two, lane_name="live")
    for _ in range(5):
        with pytest.raises(budget.RateLimited, match="two: its 3 requests a day are spent"):
            budget.take(two, lane_name="live")
    assert budget.spare(budget.Budget("two", windows=(budget.Window(60, 4),)), lane_name="live")  # the minute holds 3, not 8


def test_spend_is_counted_by_lane():
    budget.take(SMALL, lane_name="fan")
    budget.take(SMALL, lane_name="live")
    budget.pause(SMALL)
    with pytest.raises(budget.RateLimited):
        budget.take(SMALL, lane_name="live")
    rows = {r["lane"]: r for r in budget.usage([SMALL])}
    assert (rows["fan"]["sent_h"], rows["live"]["sent_d"], rows["live"]["refused_h"]) == (1, 1, 1) and "history" not in rows


def test_every_client_asks_the_budget_before_it_sends():
    # Each module that makes an HTTP client declares a BUDGET and takes from it.
    for path in sorted(ADAPTERS_DIR.glob("*.py")):
        text = path.read_text()
        if "httpx.Client(" in text:
            names = {n.targets[0].id for n in ast.parse(text).body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
            assert "BUDGET" in names and "budget.take(" in text, path.name


def test_whatever_the_api_asks_of_a_source_is_in_the_fan_lane(api, monkeypatch):
    seen = []
    monkeypatch.setattr(search, "run", lambda *args, **kwargs: seen.append(budget.lane()) or {"results": [], "suggestions": []})
    api.call("GET", "/api/search?q=virat", fan="fan")
    assert seen == ["fan"]


def test_each_worker_job_names_its_lane():
    assert asyncio.run(worker.in_lane("live", budget.lane)) == "live"
    assert asyncio.run(worker.in_lane("history", budget.lane)) == "history"

