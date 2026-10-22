"""The history lane (history.py): a bad page or a source outage never stalls it, and only a source's
"there's no such game" marks a scorecard missing. The database and Redis are fakes."""

from __future__ import annotations

import json
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sports_follow import bus, history, structured
from sports_follow.adapters import ADAPTERS, AdapterError, Fixture, Snapshot
from sports_follow.models import Event, EventPlayer, Player, SourceBinding

from .test_pipeline import FakeRedis, Rows

NOW = datetime.now(timezone.utc)


class HashRedis(FakeRedis):
    def __init__(self):
        super().__init__()
        self.hashes: dict[str, dict[str, str]] = {}

    def hgetall(self, name):
        return dict(self.hashes.get(name, {}))

    def hset(self, name, key, value):
        self.hashes.setdefault(name, {})[key] = value

    def hdel(self, name, *keys):
        for key in keys:
            self.hashes.get(name, {}).pop(key, None)


class Waiting:
    """history._missing_scorecards(waiting).limit(n), as FakeSession answers it."""

    def __init__(self, waiting=None):
        self.waiting, self.n = set(waiting or ()), None

    def limit(self, n):
        self.n = n
        return self


class FakeSession:
    """Bindings, players, events and results by key; execute() answers the missing-scorecards query:
    final results without a scorecard, newest first, minus those waiting after a failed read."""

    def __init__(self):
        self.bindings: dict[int, SourceBinding] = {}
        self.players: dict[int, Player] = {}
        self.events: dict[int, Event] = {}
        self.results: dict[tuple[int, int], EventPlayer] = {}

    def get(self, model, key):
        return {SourceBinding: self.bindings, Player: self.players, Event: self.events, EventPlayer: self.results}[model].get(key)

    def scalars(self, stmt):
        return Rows(self.bindings.values())  # only _followed_bindings asks

    def execute(self, query: Waiting):
        newest = sorted(self.results.items(), key=lambda kv: self.events[kv[0][0]].start_utc, reverse=True)
        rows = [(ep, self.events[key[0]]) for key, ep in newest if ep.result["scorecard"] is False and key not in query.waiting]
        return rows[:query.n]


class FakeSource:
    """Three older pages per player; pages and scorecards fail as told, and every request is counted."""

    system = "fake_src"
    sport = "hurling"
    live_cadence = 10

    def __init__(self, page_error=None, scorecard_error=None):
        self.page_error, self.scorecard_error = page_error, scorecard_error
        self.pages_read: list[tuple[str, int]] = []
        self.scorecards_read: list[int] = []

    def history_pages(self, ref, anchor):
        return [{"n": i} for i in range(3)]

    def sport_of(self, ref):
        return self.sport

    def history(self, ref, page):
        self.pages_read.append((ref.athlete_id, page["n"]))
        if self.page_error and ref.athlete_id == "A1":
            raise self.page_error  # one bad page of one player
        return [Fixture(source_id=f"{ref.athlete_id}-{page['n']}", title="A vs B", competition="L", start_utc=NOW, venue=None, status="final", team_ids=["T"], locator={})]

    def snapshot(self, locator, final=False):
        self.scorecards_read.append(locator["id"])
        if self.scorecard_error:
            raise self.scorecard_error
        return Snapshot(status="final", score_label="1 – 0", clock_label="FT", state={})

    def result_label(self, fixture, team_ids):
        return "Won 1–0"


@pytest.fixture
def lane(monkeypatch):
    """Players 1-3 followed and bound to a FakeSource; returns the fake session, Redis and stored games."""
    db, redis, games = FakeSession(), HashRedis(), []
    for pid in (1, 2, 3):
        db.players[pid] = Player(id=pid, slug=f"p{pid}", name=f"P{pid}", sport="Hurling", teams=[])
        db.bindings[pid] = SourceBinding(id=pid, player_id=pid, adapter="fake_src", purpose="fixtures", priority=0, history=None,
                                         locator={"system": "fake_src", "athlete_id": f"A{pid}", "name": f"P{pid}", "team_ids": ["T"], "team_names": ["T"]})

    def add_scorecard(db_, event, player_id, athlete_id, snap):
        db.results[(event.id, player_id)].result["scorecard"] = True

    monkeypatch.setattr(bus, "_sync", redis)
    monkeypatch.setattr(history, "session", lambda: nullcontext(db))
    monkeypatch.setattr(history, "_missing_scorecards", lambda waiting=None, off=None: Waiting(waiting))
    monkeypatch.setattr(history, "_off", lambda db_: [])  # every sport runs (tests/test_history_db.py turns one off)
    monkeypatch.setattr(structured, "upsert_event", lambda db_, adapter, player, f: games.append((player.id, f.source_id)) or SimpleNamespace(id=len(games), key=f"{adapter.system}:{f.source_id}"))
    monkeypatch.setattr(structured, "keep_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(structured, "add_scorecard", add_scorecard)
    return SimpleNamespace(db=db, redis=redis, games=games)


def use(monkeypatch, source: FakeSource) -> FakeSource:
    monkeypatch.setitem(ADAPTERS, source.system, source)
    return source


def test_a_page_the_source_has_nothing_for_is_passed(monkeypatch, lane):
    # BUG-021 (H6 exp4): one 404 page made every later run fail, and players 2 and 3 never moved on.
    source = use(monkeypatch, FakeSource(page_error=AdapterError("Fake returned HTTP 404 for /old-season")))
    for _ in range(4):
        history.run()
    assert all(b.history["done"] for b in lane.db.bindings.values())
    assert sorted(source.pages_read) == [(f"A{p}", n) for p in (1, 2, 3) for n in range(3)]  # each page read once
    assert sorted(p for p, _ in lane.games) == [2, 2, 2, 3, 3, 3]  # player 1 had no games there


def test_a_failing_page_holds_up_only_its_own_player(monkeypatch, lane):
    # BUG-021: an "unreachable" (or a 5xx, a rejected key) escaped run() and stalled the whole lane.
    source = use(monkeypatch, FakeSource(page_error=AdapterError("Fake unreachable: [Errno 8] nodename nor servname provided")))
    for _ in range(5):
        history.run()
    first, *others = lane.db.bindings.values()
    assert all(b.history["done"] for b in others)
    assert first.history["next"] == 0 and first.history["failures"] == 1 and "unreachable" in first.history["error"]
    retry = datetime.fromisoformat(first.history["retry_at"])
    assert timedelta(minutes=9) < retry - datetime.now(timezone.utc) <= timedelta(minutes=10)
    assert source.pages_read.count(("A1", 0)) == 1  # waiting, not asked again every minute

    # Its wait over and the source back, player 1 carries on from the page that failed.
    first.history["retry_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    source.page_error = None
    for _ in range(3):
        history.run()
    assert first.history["done"] and "failures" not in first.history and "retry_at" not in first.history


def scorecards_waiting(lane, player_id: int = 2) -> None:
    for i in range(3):
        lane.db.events[i] = Event(id=i, key=f"fake_src:{i}", sport="Hurling", title=f"G{i}", status="final", start_utc=NOW - timedelta(days=i + 1),
                                  live_binding={"adapter": "fake_src", "locator": {"id": i}})
        lane.db.results[(i, player_id)] = EventPlayer(event_id=i, player_id=player_id, result={"title": f"G{i}", "result": "Won 1–0", "scorecard": False})
    for b in lane.db.bindings.values():
        b.history = {"done": True}


def test_an_outage_never_marks_scorecards_missing(monkeypatch, lane):
    # BUG-021 (H6 exp4): one run with the source unreachable marked three scorecards "missing" for good.
    scorecards_waiting(lane)
    source = use(monkeypatch, FakeSource(scorecard_error=AdapterError("Sportmonks unreachable: [Errno 8] nodename nor servname provided")))
    history.run()
    assert [r.result["scorecard"] for r in lane.db.results.values()] == [False, False, False]
    assert source.scorecards_read == [0]  # the source is down: one request, not one per game

    # Back up: the games are read; the one that failed waits its turn.
    source.scorecard_error = None
    history.run()
    assert [lane.db.results[(i, 2)].result["scorecard"] for i in range(3)] == [False, True, True]
    assert json.loads(lane.redis.hashes[history.RESTING]["0:2"])["failures"] == 1
    lane.redis.hashes[history.RESTING]["0:2"] = json.dumps({"failures": 1, "retry_at": time.time() - 1})  # its wait is over
    history.run()
    assert lane.db.results[(0, 2)].result["scorecard"] is True and lane.redis.hashes[history.RESTING] == {}


def test_only_a_source_without_the_game_marks_its_scorecard_missing(monkeypatch, lane):
    scorecards_waiting(lane)
    use(monkeypatch, FakeSource(scorecard_error=AdapterError("Sportmonks has no fixture 19427")))
    history.run()
    assert [r.result["scorecard"] for r in lane.db.results.values()] == ["missing"] * 3


def test_a_scorecard_that_cant_be_read_doesnt_hold_up_the_rest(monkeypatch, lane):
    # BUG-021: data one game's scorecard can't be read from (a parser's KeyError) ended every run at that
    # game, the newest, so no older one was ever read. It waits now, and the others are read.
    class Garbled(FakeSource):
        def snapshot(self, locator, final=False):
            if locator["id"] == 0:
                raise KeyError("innings")
            return super().snapshot(locator, final)

    scorecards_waiting(lane)
    use(monkeypatch, Garbled())
    history.run()
    assert [lane.db.results[(i, 2)].result["scorecard"] for i in range(3)] == [False, True, True]
    assert list(lane.redis.hashes[history.RESTING]) == ["0:2"]


def test_a_run_reads_nothing_more_once_the_worker_stops(monkeypatch, lane, stopping):
    # BHA-51: a deploy during a run. It ends after the read under way, frees its lock, and the next
    # worker's run carries on with the rest.
    scorecards_waiting(lane)
    source = use(monkeypatch, FakeSource())
    read = source.snapshot

    def read_then_stop(locator, final=False):
        stopping.set()
        return read(locator, final)

    source.snapshot = read_then_stop
    history.run()
    assert source.scorecards_read == [0] and source.pages_read == []
    assert not bus.is_locked(history.LOCK)


def test_a_scorecard_cached_while_the_game_was_on_is_read_again(monkeypatch, lane):
    # BHA-450: a fan watched the game live, and its poll's copy is kept a day; the lane's scorecard, read after
    # full time, is the result, not the board as it stood in play.
    from sports_follow.adapters import espn, espn_soccer

    from .test_final_reads import in_play, load

    final = load("espn/soccer-summary-final")
    now = {"doc": in_play(final), "t": 1_800_000_000.0}
    monkeypatch.setattr(time, "time", lambda: now["t"])
    monkeypatch.setattr(espn, "_fetch", lambda url: now["doc"])
    soccer, locator = espn_soccer.EspnSoccer(), {"league": "eng.1", "event": "1"}
    monkeypatch.setitem(ADAPTERS, soccer.system, soccer)
    assert soccer.snapshot(locator).status == "live"
    now["doc"], now["t"] = final, now["t"] + 1800

    lane.db.events[0] = Event(id=0, key=f"{soccer.system}:1", sport="Football", title="Norway v Portugal", status="final", start_utc=NOW - timedelta(days=1),
                              live_binding={"adapter": soccer.system, "locator": locator})
    lane.db.results[(0, 2)] = EventPlayer(event_id=0, player_id=2, result={"title": "Norway v Portugal", "result": "Lost 1–2", "scorecard": False})
    for b in lane.db.bindings.values():
        b.history = {"done": True}
    stored: list[Snapshot] = []
    monkeypatch.setattr(structured, "add_scorecard", lambda db_, event, player_id, athlete_id, snap: stored.append(snap))
    history.run()
    assert [s.status for s in stored] == ["final"]
