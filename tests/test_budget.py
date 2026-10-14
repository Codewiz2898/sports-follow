"""The source budget (adapters/budget.py): one rate-limit layer for every source, by lane, shared by every process."""

from __future__ import annotations

import ast
import asyncio
import importlib
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from sports_follow import history, pipeline, registry, search, server, structured, tools, worker
from sports_follow.adapters import ADAPTERS, PlayerRef, budget, lichess_chess, liquipedia, pubg

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


def test_every_budget_a_source_declares_is_known():
    # Each source's client says what it spends (its Provider's budgets, Adapter.budgets), so usage shows
    # all of it and removing a source takes its budget with it.
    declared = set()
    for path in sorted(p for p in ADAPTERS_DIR.glob("*.py") if p.stem != "__init__"):
        module = importlib.import_module(f"sports_follow.adapters.{path.stem}")
        declared |= {v.name for v in vars(module).values() if isinstance(v, budget.Budget)}
    assert declared == {b.name for b in budget.known()}


def test_whatever_the_api_asks_of_a_source_is_in_the_fan_lane(api, monkeypatch):
    seen = []
    monkeypatch.setattr(search, "run", lambda *args, **kwargs: seen.append(("search", budget.lane())) or {"results": [], "suggestions": []})
    monkeypatch.setattr(server.player_search, "preview", lambda system, athlete_id, league: seen.append(("preview", budget.lane())) or {"name": "x"})
    adapter = ADAPTERS["espn_soccer"]
    monkeypatch.setattr(adapter, "player", lambda athlete_id, league=None: seen.append(("player", budget.lane())) or PlayerRef(adapter.system, athlete_id, "x", [], []))
    monkeypatch.setattr(pipeline, "start_from_pick", lambda adapter, ref: seen.append(("start", budget.lane())) or (5, False))
    api.call("GET", "/api/search?q=virat", fan="fan")
    api.call("GET", "/api/athletes/espn_soccer/231388", fan="fan")
    api.call("POST", "/api/follows", fan="fan", body={"system": "espn_soccer", "athlete_id": "231388"})
    assert seen == [("search", "fan"), ("preview", "fan"), ("player", "fan"), ("start", "fan")]


@pytest.mark.parametrize("job, module, target, args, lane_name", [
    ("poll_event", structured, "poll_event", (1,), "live"),
    ("poll_lane", structured, "poll_lane", ("a_source",), "live"),
    ("refresh_live", pipeline, "refresh_live", (1,), "live"),
    ("refresh_structured", structured, "refresh", (1,), "refresh"),
    ("build_card", pipeline, "build_card", (1,), "refresh"),
    ("history_lane", history, "run", (), "history"),
    ("import_registry", registry, "import_locked", (), "import"),
    ("warm_sources", worker, "_warm_sources", (), "refresh"),
])
def test_each_worker_job_runs_in_its_lane(monkeypatch, job, module, target, args, lane_name):
    # A live poll outside the live lane would stop at the reserve kept for it.
    seen = []
    monkeypatch.setattr(module, target, lambda *a: seen.append(budget.lane()))
    with budget.in_lane("fan"):  # a job that set no lane would show this, not the default
        asyncio.run(getattr(worker, job)({}, *args))
    assert seen == [lane_name]


def test_the_research_agents_lookups_stay_in_the_lane_that_started_it(monkeypatch):
    from sports_follow import agent

    seen = []
    monkeypatch.setattr(agent, "run_tool", lambda name, args: seen.append(budget.lane()) or "ok")
    lookups = [SimpleNamespace(id=str(n), name="fetch_url", args={"url": "https://example.com"}) for n in range(3)]
    turns = [SimpleNamespace(finish="tool_use", message={"role": "assistant", "content": ""}, tool_calls=lookups)]

    class Model:
        def complete(self, **request):
            if not turns:
                raise RuntimeError("enough")
            return turns.pop(0)

    with budget.in_lane("live"), pytest.raises(RuntimeError, match="enough"):
        list(agent.live_update(Model(), "Player One", "chess", []))  # its lookups run on a pool of threads
    assert seen == ["live", "live", "live"]


def test_the_research_agent_spends_a_sources_budget_on_its_api(monkeypatch):
    answers = []
    monkeypatch.setattr(tools._http, "get", lambda url: answers.pop(0) if answers else httpx.Response(200, json={}, request=httpx.Request("GET", url)))
    # Its requests carry no token: with one, they're counted apart from ours (lichess-anonymous), so a 429
    # they get doesn't pause our polls; without one, they and ours share Lichess's one limit.
    lichess = budget.for_url("https://lichess.org/api/broadcast/top")
    assert lichess is lichess_chess.ANONYMOUS
    assert (lichess is lichess_chess.BUDGET) == (lichess_chess.token() is None)
    assert budget.for_url("https://lichess.org/@/someone") is None  # a page, not the API
    assert budget.for_url("http://LICHESS.org/api/x") is lichess
    assert budget.for_url("https://api.pubg.com.example.net/x") is None  # another host that starts the same
    assert budget.for_url("https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard").name == "espn"
    with budget.in_lane("refresh"):
        tools.fetch_url("https://lichess.org/api/broadcast/top")
        answers.append(httpx.Response(429, request=httpx.Request("GET", "https://lichess.org/api/broadcast/top")))
        with pytest.raises(tools.ToolError, match="HTTP 429"):
            tools.fetch_url("https://lichess.org/api/broadcast/top")
        with pytest.raises(tools.ToolError, match="kept for live games"):
            tools.fetch_url("https://lichess.org/api/broadcast/top")  # the 429 paused Lichess for everyone
    assert {(r["lane"], r["sent_h"], r["refused_h"]) for r in budget.usage([lichess])} == {("refresh", 2, 1)}

