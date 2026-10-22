"""The source budget (adapters/budget.py): one rate-limit layer for every source, by lane, shared by every process."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from sports_follow import controls, history, live, pipeline, registry, search, server, structured, tools, worker
from sports_follow.adapters import ADAPTERS, KNOWN, PlayerRef, budget, lichess_chess, liquipedia, pandascore, pubg, sportmonks

from .test_api import api  # noqa: F401  (the fixture)
from .test_new_adapters import load

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


def test_every_source_with_a_live_reserve_gives_fans_a_share_smaller_than_it():
    # BUG-128: the fan lane leaves no reserve of its own, so without a share a loop of previews of made-up ids
    # spent what live series, matches and boards are kept (PandaScore, Sportmonks, Lichess's chess searches).
    for b in budget.known():
        if b.reserve:
            assert b.fan is not None and b.fan < b.reserve, b.name


@pytest.mark.parametrize("scarce", [pandascore.BUDGET, sportmonks.BUDGET, lichess_chess.BUDGET], ids=lambda b: b.name)
def test_fans_in_a_loop_never_spend_what_live_games_keep(scarce):
    # The background spends all it may (down to the reserve), then a fan loop: refused past the fans' share, and
    # live games still get the rest of what was kept.
    while budget.spare(scarce, "x", lane_name="refresh"):
        budget.take(scarce, "x", lane_name="refresh")
    fans = 0
    with pytest.raises(budget.RateLimited, match="fans' share"):
        for _ in range(scarce.reserve + 1):
            budget.take(scarce, "x", lane_name="fan")
            fans += 1
    assert fans == scarce.fan
    for _ in range(scarce.reserve - scarce.fan):
        budget.take(scarce, "x", lane_name="live")
    with pytest.raises(budget.RateLimited):
        budget.take(scarce, "x", lane_name="live")  # the margin, never spent


@pytest.mark.parametrize("with_token", [True, False], ids=["token", "no-token"])
def test_a_cold_chess_preview_and_its_follow_fit_in_lichesss_fans_share(api, monkeypatch, with_token):
    # Lichess's fans' share holds what a fan's preview of a chess player nobody has previewed costs, and the follow
    # after it: their FIDE record, read once (a chess preview reads no games: preview_fixtures), with or without the token.
    kept = lichess_chess._budget(with_token)
    monkeypatch.setattr(lichess_chess, "BUDGET", kept)
    monkeypatch.setattr(lichess_chess, "SPACING", 0.0)
    monkeypatch.setattr(search, "_previews", {})
    monkeypatch.setitem(ADAPTERS, "lichess_chess", KNOWN["lichess_chess"])
    monkeypatch.setattr(pipeline, "start_from_pick", lambda adapter, ref: (5, True))
    asked = []

    def lichess(request):
        asked.append(str(request.url))
        assert str(request.url) == "https://lichess.org/api/fide/player?q=46616543", request.url
        return httpx.Response(200, json=load("lichess/fide-search-id-gukesh"))

    monkeypatch.setattr(lichess_chess, "_client", httpx.Client(transport=httpx.MockTransport(lichess)))
    shown = api.call("GET", "/api/athletes/lichess_chess/46616543", fan="fan")
    assert shown.status_code == 200 and shown.json()["name"] == "Gukesh D"
    assert api.call("POST", "/api/follows", fan="fan", body={"system": "lichess_chess", "athlete_id": "46616543"}).status_code == 200
    assert asked == ["https://lichess.org/api/fide/player?q=46616543"]
    [fans] = [r for r in budget.usage([kept, lichess_chess.FIDE]) if r["lane"] == "fan"]
    assert (fans["source"], fans["sent_h"], fans["refused_h"]) == ("lichess", 1, 0) and 1 <= kept.fan < kept.reserve


def test_work_nobody_waits_on_can_leave_a_share_of_every_window_to_the_rest():
    """A source with no reserve (OpenDota's) still puts pages and polls first: what nobody waits on stops while any
    window is down to its share, by our count or by what the source says is left of its longest window."""
    two = budget.Budget("two", windows=(budget.Window(60, 30, margin=3), budget.Window(86400, 300, margin=30)))
    for _ in range(16):
        budget.take(two, lane_name="live")
    assert budget.spare(two, lane_name="refresh", leave=1 / 3)  # 30 a minute, 3 never spent, 10 left to the rest: 17
    budget.take(two, lane_name="live")
    assert not budget.spare(two, lane_name="refresh", leave=1 / 3) and budget.spare(two, lane_name="refresh")
    day = budget.Budget("day", windows=(budget.Window(60, 30, margin=3), budget.Window(86400, 300, margin=30)))
    budget.observe(day, "", 103, 3600)  # of the day: within the smaller margin and a third of 300
    assert not budget.spare(day, lane_name="refresh", leave=1 / 3) and budget.spare(day, lane_name="refresh")
    budget.observe(day, "", 104, 3600)
    assert budget.spare(day, lane_name="refresh", leave=1 / 3)


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


PACIFIC = "America/Los_Angeles"


def _at(year: int, month: int, day: int, hour: int, minute: int) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


def test_a_zoned_day_starts_at_the_zones_midnight_not_utcs():
    day = budget.Window(86400, 100, zone=PACIFIC)
    assert budget._start(day, _at(2026, 10, 20, 23, 59)) == budget._start(day, _at(2026, 10, 21, 0, 1))  # UTC midnight: the same Pacific day
    assert budget._start(day, _at(2026, 10, 21, 6, 59)) + 1 == budget._start(day, _at(2026, 10, 21, 7, 1))  # midnight PDT
    assert budget._start(day, _at(2026, 12, 21, 7, 59)) + 1 == budget._start(day, _at(2026, 12, 21, 8, 1))  # midnight PST
    utc = budget.Window(86400, 100)
    assert budget._start(utc, _at(2026, 10, 20, 23, 59)) + 1 == budget._start(utc, _at(2026, 10, 21, 0, 1))
    assert budget._start(budget.Window(86400, 100, zone="Nowhere/At_All"), _at(2026, 10, 21, 0, 1)) == budget._start(utc, _at(2026, 10, 21, 0, 1))


def test_a_zoned_days_allowance_holds_across_utcs_midnight(monkeypatch):
    clock = [_at(2026, 10, 20, 23, 0)]  # 16:00 PDT
    monkeypatch.setattr(budget, "time", SimpleNamespace(time=lambda: clock[0]))
    pacific = budget.Budget("pacific", windows=(budget.Window(86400, 10, margin=1, zone=PACIFIC),))
    budget.take(pacific, cost=5)
    clock[0] = _at(2026, 10, 21, 1, 0)  # a new UTC day, the same Pacific one
    budget.take(pacific, cost=4)
    with pytest.raises(budget.RateLimited):
        budget.take(pacific)
    assert budget.standing(pacific)["windows"] == [{"bucket": "", "window": "day", "used": 9, "allowance": 10}]
    clock[0] = _at(2026, 10, 21, 7, 30)  # 00:30 PDT: YouTube's new day
    budget.take(pacific)


def test_the_admin_panels_allowance_keeps_the_zone_its_source_declares():
    pacific = budget.Budget("pacific", windows=(budget.Window(86400, 10, margin=1, zone=PACIFIC),))
    controls.set_budget("pacific", {"windows": [{"seconds": 86400, "requests": 50, "margin": 2}, {"seconds": 3600, "requests": 5}]})
    assert budget.effective(pacific).windows == (budget.Window(86400, 50, 2, PACIFIC), budget.Window(3600, 5, 0, None))


def test_a_zoned_sources_spend_today_is_counted_from_its_own_midnight(monkeypatch):
    """BUG-263: the usage table's "Today" was UTC's day while YouTube's allowance is counted from midnight Pacific, so
    for most of an Indian day what it said was sent today and "used X of 10,000" disagreed."""
    clock = [_at(2026, 10, 20, 23, 0)]  # 16:00 PDT
    monkeypatch.setattr(budget, "time", SimpleNamespace(time=lambda: clock[0]))
    pacific = budget.Budget("pacific", windows=(budget.Window(86400, 100, zone=PACIFIC),))
    budget.take(pacific, lane_name="feed")
    clock[0] = _at(2026, 10, 21, 1, 0)  # a new UTC day, the same Pacific one
    budget.take(pacific, lane_name="feed")
    [row] = budget.usage([pacific])
    assert row["sent_d"] == 2 == budget.standing(pacific)["windows"][0]["used"]
    clock[0] = _at(2026, 10, 21, 7, 30)  # 00:30 PDT: its new day
    assert budget.usage([pacific]) == []


def test_a_minutes_allowance_holds_across_the_edge_of_a_minute(monkeypatch):
    """BUG-134: windows were counted from the epoch, so a minute's allowance spent just before a minute began was
    there again just after: PUBG let 18 requests through in two seconds (it allows 10 a minute), and the 429 that
    brings pauses its live lobbies. A minute is counted over the last minute, wherever it started; spread out, a
    source still gets about its allowance a minute."""
    edge = _at(2026, 10, 22, 10, 0)
    clock = [edge - 1]
    monkeypatch.setattr(budget, "time", SimpleNamespace(time=lambda: clock[0]))

    def burst() -> int:
        sent = 0
        with contextlib.suppress(budget.RateLimited):
            while True:
                budget.take(pubg.BUDGET, lane_name="live")
                sent += 1
        return sent

    allowance = pubg.PER_MINUTE - pubg.MARGIN
    assert burst() == allowance
    clock[0] = edge + 1
    assert burst() == 0  # two seconds on, across the minute's edge
    clock[0] = edge + 45
    assert burst() == 0 and not budget.spare(pubg.BUDGET, lane_name="live")
    assert budget.standing(pubg.BUDGET)["windows"] == [{"bucket": "", "window": "minute", "used": allowance, "allowance": pubg.PER_MINUTE}]
    clock[0] = edge + 61  # a minute after the burst
    assert burst() == allowance
    # One every 7.5 s (8 a minute) for five minutes: none refused.
    for i in range(40):
        clock[0] = edge + 300 + i * 7.5
        budget.take(pubg.BUDGET, lane_name="live")


def test_the_fans_share_of_a_minute_holds_across_the_edge_of_a_minute_too(monkeypatch):
    """BUG-275: BUG-134 made a minute's window rolling, but the fans' share of it was still counted in minutes from the
    epoch: PUBG's fans got their 3 just before a minute began and 3 more just after, twice their share in two seconds,
    and the live lobbies were left 3 of the 9 that minute. The share is counted over the last minute, as its window."""
    edge = _at(2026, 10, 22, 10, 0)
    clock = [edge - 1]
    monkeypatch.setattr(budget, "time", SimpleNamespace(time=lambda: clock[0]))

    def burst(lane: str) -> int:
        sent = 0
        with contextlib.suppress(budget.RateLimited):
            while True:
                budget.take(pubg.BUDGET, lane_name=lane)
                sent += 1
        return sent

    assert burst("fan") == pubg.BUDGET.fan
    clock[0] = edge + 1
    assert burst("fan") == 0  # two seconds on, across the minute's edge
    assert burst("live") == pubg.PER_MINUTE - pubg.MARGIN - pubg.BUDGET.fan
    clock[0] = edge + 61  # a minute after the fans' burst
    assert burst("fan") == pubg.BUDGET.fan
    assert budget.standing(pubg.BUDGET)["windows"][0]["bucket"] == ""  # still counted in the window, not a bucket


def test_the_fans_share_is_counted_in_the_window_not_shown_as_a_bucket_of_its_own():
    """BUG-135: the fans' share is counted under keys of its own, which the panel and the metrics took for a bucket
    (":fan"). A fan's request counts in the window it's sent in; a real bucket (Sportmonks' entities) is still shown."""
    for scarce in (liquipedia.BUDGET, pubg.BUDGET):
        budget.take(scarce, lane_name="fan")
        budget.take(scarce, lane_name="live")
        assert [(w["bucket"], w["used"]) for w in budget.standing(scarce)["windows"]] == [("", 2)], scarce.name
    budget.take(sportmonks.BUDGET, "Fixture", lane_name="fan")
    assert [(w["bucket"], w["used"]) for w in budget.standing(sportmonks.BUDGET)["windows"]] == [("", 0), ("Fixture", 1)]


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
    ("keep_up_live", live, "keep_up", (), "refresh"),
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


def test_a_url_spends_the_budget_of_the_longest_prefix_it_is_under():
    # Lichess's FIDE pages and player endpoint sit under its API but behind a limit of their own, however
    # its budgets are listed, and with or without a token.
    assert budget.for_url("https://lichess.org/fide/1503014/Carlsen_Magnus") is lichess_chess.FIDE
    assert budget.for_url("https://lichess.org/api/fide/player/1503014") is lichess_chess.FIDE
    assert budget.for_url("https://lichess.org/api/fide/player?q=1503014") is lichess_chess.ANONYMOUS  # search has no such limit
    assert budget.for_url("https://lichess.org/fide") is None


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

