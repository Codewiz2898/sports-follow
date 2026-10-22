"""The chess engine's queue and budget (engine.py): newest position per game, longest wait first,
at most one first look per game per interval, the position a game stays on deepened in turns, and the
engine itself when it's installed."""

from __future__ import annotations

import json
import subprocess
import threading
import time

import pytest

from sports_follow import bus, engine, sports, structured
from sports_follow.adapters import Snapshot
from sports_follow.moments import Seen
from sports_follow.sports import chess


class FakeRedis:
    """The few Redis calls the queue makes, in memory."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value, ex=None):
        self.kv[key] = value

    def hset(self, name, key, value):
        self.hashes.setdefault(name, {})[key] = value

    def hget(self, name, key):
        return self.hashes.get(name, {}).get(key)

    def hdel(self, name, key):
        self.hashes.get(name, {}).pop(key, None)

    def hgetall(self, name):
        return dict(self.hashes.get(name, {}))

    def delete(self, *names):
        for name in names:
            self.kv.pop(name, None)
            self.hashes.pop(name, None)

    def expire(self, name, seconds):
        pass

    def exists(self, name):
        return int(name in self.kv or name in self.hashes)

    def eval(self, script, numkeys, key, *args):
        assert script == engine.SETTLE  # the one script: set it if it's still what it was settled from
        was, new, seconds = args
        if (self.kv.get(key) or "") == was:
            self.kv[key] = new
            return new
        return self.kv.get(key)

    def zadd(self, name, mapping, nx=False):
        z = self.zsets.setdefault(name, {})
        for member, score in mapping.items():
            if not (nx and member in z):
                z[member] = score

    def zrangebyscore(self, name, low, high, start=0, num=None):
        items = sorted((s, m) for m, s in self.zsets.get(name, {}).items() if s <= high)
        return [m for _, m in items][start:start + num if num else None]

    def zrem(self, name, member):
        self.zsets.get(name, {}).pop(member, None)

    def zcard(self, name):
        return len(self.zsets.get(name, {}))


def took(r: FakeRedis, now: float) -> tuple[str, str] | None:
    """The game and position take() gives, without when it was asked for."""
    job = engine.take(r, now)
    return job[:2] if job else None


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
E4E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"


def evaluated(r: FakeRedis, game: str, fen: str, at: float) -> None:
    r.set(f"engine:latest:{game}", json.dumps({"cp": 20, "mate": None, "depth": 18, "fen": fen, "at": at}))


def test_newest_position_per_game_and_longest_wait_first():
    r = FakeRedis()
    engine.request("A", START, r, now=100)
    engine.request("B", START, r, now=101)
    engine.request("A", E4, r, now=102)  # replaces A's waiting position, keeps A's place
    assert took(r, now=103) == ("A", E4)
    assert took(r, now=103) == ("B", START)
    assert took(r, now=103) is None


def test_each_game_at_most_once_per_interval():
    r = FakeRedis()
    evaluated(r, "A", E4, at=100)
    engine.request("A", E4, r, now=105)  # already evaluated: nothing to do
    assert took(r, now=105) is None
    engine.request("A", E4E5, r, now=106)
    assert took(r, now=100 + engine.GAME_INTERVAL - 1) is None
    assert took(r, now=100 + engine.GAME_INTERVAL) == ("A", E4E5)


def test_a_new_move_waits_the_interval_from_its_positions_first_look():
    # Deepening the old position doesn't push the next one back: alerts come as fast as before.
    r = FakeRedis()
    r.set("engine:latest:A", json.dumps({"cp": 20, "mate": None, "depth": 26, "fen": E4, "at": 115, "first": 100}))
    engine.request("A", E4E5, r, now=116)
    assert took(r, now=100 + engine.GAME_INTERVAL - 1) is None
    assert took(r, now=100 + engine.GAME_INTERVAL) == ("A", E4E5)


def test_new_positions_go_before_deepening_and_games_take_turns_at_it():
    r = FakeRedis()
    for game, at in (("A", 100), ("B", 101)):
        evaluated(r, game, E4, at)
        engine.request(game, E4, r, now=at + 1)  # still on it: polled, nothing new waiting
        r.zadd(engine.DEEPEN, {game: at})
    engine.request("C", START, r, now=102)
    assert took(r, now=103) == ("C", START)
    game, shown = engine.take_deeper(r, now=103) or ("", {})
    assert (game, shown["fen"]) == ("A", E4)  # the one that has waited longest
    r.zadd(engine.DEEPEN, {"A": 103})  # A had its turn
    assert (engine.take_deeper(r, now=104) or ("",))[0] == "B"


def test_a_new_move_stops_the_old_position_being_deepened_at_once():
    r = FakeRedis()
    evaluated(r, "A", E4, at=100)
    engine.request("A", E4, r, now=101)
    r.zadd(engine.DEEPEN, {"A": 100})
    engine.request("A", E4E5, r, now=102)
    assert engine.take_deeper(r, now=103) is None


def test_deepening_ends_once_deep_enough_or_nobody_polls_the_game():
    r = FakeRedis()
    evaluated(r, "A", E4, at=100)
    r.zadd(engine.DEEPEN, {"A": 100})
    assert engine.take_deeper(r, now=101) is None and r.zcard(engine.DEEPEN) == 0  # not polled for STALE
    engine.request("A", E4, r, now=101)
    r.zadd(engine.DEEPEN, {"A": 101})
    engine.keep(r, E4, {"cp": 20, "mate": None, "depth": engine.DEPTH, "nodes": engine.NODES})
    assert engine.take_deeper(r, now=102) is None and r.zcard(engine.DEEPEN) == 0


def test_a_position_dropped_after_a_gap_in_polling_is_deepened_again():
    r = FakeRedis()
    evaluated(r, "A", E4, at=100)
    r.zadd(engine.DEEPEN, {"A": 100})
    assert engine.take_deeper(r, now=101) is None  # not polled: out of the queue
    engine.request("A", E4, r, now=500)  # polled again, on the same position
    assert (engine.take_deeper(r, now=501) or ("",))[0] == "A"
    engine.keep(r, E4, {"cp": 20, "mate": None, "depth": engine.DEPTH, "nodes": engine.NODES})
    r.zrem(engine.DEEPEN, "A")
    engine.request("A", E4, r, now=502)
    assert r.zcard(engine.DEEPEN) == 0  # deep enough: not again


def test_the_cache_keeps_the_deepest_look_and_counts_all_the_work():
    r = FakeRedis()
    look = {"cp": 30, "mate": None, "depth": 20}
    assert engine.keep(r, E4, {**look, "nodes": 500_000}, look=True) == {**look, "nodes": 500_000, "look": look}
    assert engine.keep(r, E4, {"cp": 90, "mate": None, "depth": 18, "nodes": 500_000}) == {**look, "nodes": 1_000_000, "look": look}  # a cold hash table
    deeper = {"cp": 40, "mate": None, "depth": 24}
    assert engine.keep(r, E4, {**deeper, "nodes": 500_000}) == {**deeper, "nodes": 1_500_000, "look": look}
    assert engine.cached(r, E4.replace(" 0 1", " 2 5")) == {**deeper, "nodes": 1_500_000, "look": look}  # the same position
    assert engine.keep(r, E4, {"cp": 99, "mate": None, "depth": 22, "nodes": 1}, look=True)["look"] == look  # a position's first look stays its own
    assert not engine.deep_enough({"depth": 24, "nodes": 1_500_000})
    assert engine.deep_enough({"depth": engine.DEPTH, "nodes": 1}) and engine.deep_enough({"depth": 12, "nodes": engine.POSITION_NODES})


def test_the_lane_is_needed_while_a_position_is_being_deepened(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(engine, "enabled", lambda: True)
    monkeypatch.setattr(bus, "sync_redis", lambda: r)
    monkeypatch.setattr(bus, "is_locked", lambda name: False)
    assert not engine.needs_lane()
    r.zadd(engine.DEEPEN, {"A": 100})
    assert engine.needs_lane()


def run_lane(monkeypatch, stopping, r: FakeRedis, depths: list[int], during=None) -> tuple[list[str], list[str]]:
    """The lane on r till it has nothing to do, its engine finding these depths in turn, a look's or a deep search's
    slice NODES each, and stopping the lane after the last; during(n) runs while the nth is made, as a poll would
    meanwhile. Returns the positions each was of, and those deep searches were started on."""
    looked: list[str] = []
    started: list[str] = []

    class Stockfish:
        def __init__(self, *args, **kwargs):
            pass

        def next(self, fen: str) -> int:
            looked.append(fen)
            if during:
                during(len(looked))
            if len(looked) == len(depths):
                stopping.set()
            return depths[len(looked) - 1]

        def evaluate(self, fen):
            return {"cp": 20, "mate": None, "depth": self.next(fen), "nodes": engine.NODES}

        def search(self, fen, depth, nodes):
            started.append(fen)
            self.fen, self.limits, self.reading = fen, (depth, nodes), {"cp": 20, "mate": None, "depth": 0, "nodes": 0}

        def run(self, seconds):
            depth = self.next(self.fen)
            self.reading = {"cp": 20, "mate": None, "depth": depth, "nodes": self.reading["nodes"] + engine.NODES}
            return dict(self.reading), depth >= self.limits[0] or self.reading["nodes"] >= self.limits[1]

        def halt(self):
            return dict(self.reading)

        def close(self):
            pass

    monkeypatch.setattr(engine, "enabled", lambda: True)
    monkeypatch.setattr(engine, "Engine", Stockfish)
    monkeypatch.setattr(engine, "IDLE_EXIT", 0)
    monkeypatch.setattr(bus, "sync_redis", lambda: r)
    monkeypatch.setattr(bus, "try_lock", lambda name, seconds: "token")
    monkeypatch.setattr(bus, "extend", lambda name, seconds, token=None: True)
    monkeypatch.setattr(bus, "release", lambda name, token=None: None)
    engine.run_lane()
    return looked, started


def test_the_lane_deepens_the_position_a_game_stays_on(monkeypatch, stopping):
    r = FakeRedis()
    engine.request("A", E4, r)
    # A look, then one search of the position a slice at a time, till it's DEPTH deep: no sixth.
    assert run_lane(monkeypatch, stopping, r, [18, 21, 20, 24, engine.DEPTH, 99]) == ([E4] * 5, [E4])
    shown = engine.latest("A", r) or {}
    # Each deeper reading shown, the shallower one (20 after 21) not; beside the first look, which alerts go by.
    assert (shown["fen"], shown["depth"], shown["nodes"], shown["look"]["depth"]) == (E4, engine.DEPTH, 5 * engine.NODES, 18)
    assert shown["first"] < shown["at"] and r.zcard(engine.DEEPEN) == 0


def test_a_cached_shallow_look_is_deepened_and_never_replaces_a_deeper_one(monkeypatch, stopping):
    r = FakeRedis()
    engine.keep(r, E4, {"cp": 15, "mate": None, "depth": 18, "nodes": engine.NODES}, look=True)  # another game's, or an earlier lane's
    engine.request("A", E4, r)
    assert run_lane(monkeypatch, stopping, r, [16, 22]) == ([E4] * 2, [E4])  # the look from the cache, then two slices
    shown = engine.latest("A", r) or {}
    assert (shown["depth"], shown["look"]["depth"], (engine.cached(r, E4) or {})["nodes"]) == (22, 18, 3 * engine.NODES)
    assert r.zcard(engine.DEEPEN) == 1  # the lane ended (the worker stopping) before it was deep enough: the next goes on


def test_a_new_move_outranks_deepening_and_drops_the_old_position(monkeypatch, stopping):
    monkeypatch.setattr(engine, "GAME_INTERVAL", 0)
    r, shown = FakeRedis(), []

    def poll(n: int) -> None:
        shown.append((engine.latest("A", r) or {}).get("depth"))
        if n == 2:
            engine.request("B", START, r)  # another game's look goes before A's next slice
        if n == 4:
            engine.request("A", E4E5, r)  # A moves while its old position is searched

    engine.request("A", E4, r)
    looked, started = run_lane(monkeypatch, stopping, r, [18, 22, 18, 25, 19, 20], poll)
    # B's look comes first, on the looks' engine, while A's search waits paused; then A's search goes on where it was,
    # and B's starts once A has moved (B has waited longer than A's new position).
    assert (looked, started) == ([E4, E4, START, E4, E4E5, START], [E4, START])
    # The slice of A's old position under way when it moved is kept, but not shown.
    assert shown == [None, 18, 22, 22, 22, 19] and (engine.cached(r, E4) or {})["depth"] == 25
    assert ((engine.latest("A", r) or {})["fen"], (engine.latest("A", r) or {})["depth"]) == (E4E5, 19)


def test_deep_searches_take_turns(monkeypatch, stopping):
    monkeypatch.setattr(engine, "TURN", 2 * engine.NODES)
    r = FakeRedis()
    engine.request("A", E4, r)
    engine.request("B", START, r)
    # Looks at both; then A's search has its turn (TURN: two slices), B's, and A's again.
    looked, started = run_lane(monkeypatch, stopping, r, [18, 18, 20, 21, 20, 22, 23])
    assert (looked, started) == ([E4, START, E4, E4, START, START, E4], [E4, START, E4])
    assert ((engine.latest("A", r) or {})["depth"], (engine.latest("B", r) or {})["depth"]) == (23, 22)


def test_a_look_elsewhere_neither_restarts_the_deep_search_nor_gives_it_a_new_turn(monkeypatch, stopping):
    monkeypatch.setattr(engine, "TURN", 2 * engine.NODES)
    r = FakeRedis()
    engine.request("A", E4, r)
    # B's first position comes while A's search has had one slice: after B's look, A's search goes on, the same
    # search, for the rest of its turn (one more slice), then B's turn.
    looked, started = run_lane(monkeypatch, stopping, r, [18, 20, 18, 21, 20, 22, 22], lambda n: n == 2 and engine.request("B", START, r))
    assert (looked, started) == ([E4, E4, START, E4, START, START, E4], [E4, START, E4])


def test_a_deeper_reading_keeps_when_the_polls_asked(monkeypatch, stopping):
    # Asked for ten seconds before the lane got to it; a deeper reading then lands before the game's next move: that
    # move is still due GAME_INTERVAL after the ask, as an unwatched game's is, not after the lane's look.
    r = FakeRedis()
    asked = time.time() - 10
    engine.request("A", E4, r, now=asked)
    assert run_lane(monkeypatch, stopping, r, [18, 24]) == ([E4] * 2, [E4])
    shown = engine.latest("A", r) or {}
    assert (shown["depth"], shown["asked"]) == (24, asked)
    engine.request("A", E4E5, r, now=asked + 11)
    assert r.zsets[engine.QUEUE]["A"] == asked + engine.GAME_INTERVAL


def test_an_unwatched_game_gets_its_first_look_and_no_more(monkeypatch, stopping):
    r = FakeRedis()
    engine.request("A", E4, r, deepen=False)
    assert run_lane(monkeypatch, stopping, r, [18, 99]) == ([E4], [])
    assert ((engine.latest("A", r) or {})["depth"], r.zcard(engine.DEEPEN)) == (18, 0)


def test_deepening_stops_when_the_last_viewer_leaves(monkeypatch, stopping):
    r = FakeRedis()
    engine.request("A", E4, r, by=1)
    engine.request("A", E4, r, by=2)  # open on both players' events of the board

    def leave(n: int) -> None:
        if n in (2, 3):
            engine.request("A", E4, r, by=n - 1, deepen=False)  # one closes it, then the other

    # The look and two slices: one viewer left during the first, the other during the second, the last; that
    # slice's reading is kept, but not shown, and there's no third.
    assert run_lane(monkeypatch, stopping, r, [18, 20, 22, 24, 99], leave) == ([E4] * 3, [E4])
    assert ((engine.latest("A", r) or {})["depth"], (engine.cached(r, E4) or {})["depth"], engine.deepened(r, "A"), r.zcard(engine.DEEPEN)) == (20, 22, False, 0)
    # Opened again on the same position: back in line.
    engine.request("A", E4, r, by=1)
    assert (engine.take_deeper(r, time.time()) or ("",))[0] == "A"


def test_the_lane_stops_once_its_lock_has_gone(monkeypatch, stopping):
    # Held up past its lock (a slow engine, a paused container): another lane may have the lock by now.
    r, looked, freed = FakeRedis(), [], []

    class Stockfish:
        def __init__(self, *args, **kwargs):
            pass

        def evaluate(self, fen):
            looked.append(fen)
            return {"cp": 20, "mate": None, "depth": 18, "nodes": engine.NODES}

        def close(self):
            pass

    engine.request("A", START, r)
    monkeypatch.setattr(engine, "enabled", lambda: True)
    monkeypatch.setattr(engine, "Engine", Stockfish)
    monkeypatch.setattr(bus, "sync_redis", lambda: r)
    monkeypatch.setattr(bus, "try_lock", lambda name, seconds: "token")
    monkeypatch.setattr(bus, "extend", lambda name, seconds, token=None: token != "token")
    monkeypatch.setattr(bus, "release", lambda name, token=None: freed.append(token))
    engine.run_lane()
    assert looked == [] and r.zcard(engine.QUEUE) == 1 and freed == ["token"]  # left for the lane that has it


def script(tmp_path, name: str, body: str) -> str:
    """A stand-in UCI engine: answers uci and isready, then does what `body` says with each other line (`word`)."""
    path = tmp_path / name
    path.write_text(f"""#!/usr/bin/env python3
import sys
for line in sys.stdin:
    word = (line.split() or [""])[0]
    if word in ("uci", "isready"):
        print("uciok" if word == "uci" else "readyok", flush=True)
    elif word == "quit":
        break
{body}""")
    path.chmod(0o755)
    return str(path)


def test_a_stuck_engine_is_killed_not_waited_on(tmp_path, monkeypatch):
    stuck = script(tmp_path, "stuck-engine", "")  # it starts a search and never ends it, "stop" or not
    monkeypatch.setattr(engine, "HALT_SECONDS", 0.3)
    e = engine.Engine(stuck)
    e.search(MIDDLEGAME, depth=30, nodes=1_000_000)
    e.run(0.05)
    with pytest.raises(engine.EngineError):
        e.halt()
    assert e.proc.wait(timeout=2) != 0


@pytest.mark.parametrize("answers", ["", "uci"])
def test_an_engine_that_never_says_its_ready_is_killed_not_waited_on(tmp_path, monkeypatch, answers):
    # BUG-178: an engine that starts but never answers "uci" (a wrong SPORTS_FOLLOW_STOCKFISH, a wedged start), or
    # "isready", held the lane's thread and its process for good; the lane's lock lapsed, and each tick started another.
    mute = tmp_path / "mute-engine"
    mute.write_text(f"#!/usr/bin/env python3\nimport sys\nfor line in sys.stdin:\n    if line.split()[:1] == [{answers!r}]:\n        print('uciok', flush=True)\n")
    mute.chmod(0o755)
    monkeypatch.setattr(engine, "READY_SECONDS", 0.3)
    started: list[subprocess.Popen] = []
    popen = subprocess.Popen
    monkeypatch.setattr(engine.subprocess, "Popen", lambda *a, **k: started.append(popen(*a, **k)) or started[-1])
    failed: list[Exception] = []

    def start() -> None:
        try:
            engine.Engine(str(mute)).close()
        except engine.EngineError as exc:
            failed.append(exc)

    thread = threading.Thread(target=start, daemon=True)
    thread.start()
    thread.join(5)
    try:
        assert not thread.is_alive() and failed  # given up on, not waited for
        assert started[0].wait(timeout=2) is not None  # and its process is gone
    finally:
        for proc in started:
            if proc.poll() is None:
                proc.kill()


# ---------------------------------------------------------------- the poller's side (structured._evaluate)

F30, F31, F32, F33 = (f"8/8/8/8/8/8/8/8 w - - 0 {n}" for n in (30, 31, 32, 33))


@pytest.fixture
def board(monkeypatch):
    """Polls of one board (game g1) on FakeRedis: board.poll(event, fen, ...) evaluates it for one followed
    player's event of it, against that event's last state, and returns the new state; board.reading(fen, cp,
    at) is the engine's latest evaluation of it."""
    r = FakeRedis()
    monkeypatch.setattr(bus, "sync_redis", lambda: r)
    monkeypatch.setattr(engine, "enabled", lambda: True)
    monkeypatch.setattr(structured.matches, "rules", lambda db, event_id: [])
    last: dict[int, dict] = {}

    class Db:
        def __init__(self, levels):
            self.levels = levels

        def scalars(self, query):
            return type("Rows", (), {"all": lambda _: [{"level": level} for level in self.levels]})()

    def poll(event: int, fen: str, result: str | None = None, levels: tuple[str, ...] = ("key",)) -> dict:
        snap = Snapshot("live", "", "", {"kind": "chess", "game_id": "g1", "fen": fen, "result": result})
        prev = last.get(event)
        structured._evaluate(sports.get("chess"), Db(levels), event, snap, type("State", (), {"state": prev})() if prev else None, [event])
        last[event] = snap.state
        return snap.state

    def reading(fen: str, cp: int, at: float) -> None:
        r.set("engine:latest:g1", json.dumps({"cp": cp, "mate": None, "depth": 20, "look": {"cp": cp, "mate": None, "depth": 20}, "fen": fen, "at": at, "first": at}))

    return type("Board", (), {"r": r, "last": last, "poll": staticmethod(poll), "reading": staticmethod(reading)})


def test_a_decided_game_is_let_go_and_its_last_evaluation_stays(board):
    board.r.set(bus.watching_key(1), "1")
    board.reading(F31, 20, 1)
    on = board.poll(1, F31)
    assert engine.deepened(board.r, "g1") and board.r.zcard(engine.DEEPEN) == 1  # open: its position deepened
    # Decided, though a mini-match goes on live; a deeper reading lands after the result.
    board.reading(F31, 900, 2)
    over = board.poll(1, F31, result="1-0")
    assert over["eval"] == on["eval"]  # nothing new folded in, so nothing turns after the result
    assert not engine.deepened(board.r, "g1") and board.r.zcard(engine.DEEPEN) == 0 and not engine.on_it(board.r, "g1", F31)


def test_one_event_with_nobody_wanting_swings_doesnt_stop_the_other(board):
    for event in (1, 2):
        board.r.set(bus.watching_key(event), "1")
    board.reading(F31, 20, 1)
    board.poll(1, F31)
    board.poll(2, F31, levels=("results",))  # the other player's fans hear results only
    assert engine.deepened(board.r, "g1")
    board.poll(1, F31, levels=("results",))
    assert not engine.deepened(board.r, "g1")


def test_a_game_is_deepened_only_while_someone_has_it_open(board, monkeypatch):
    board.reading(F31, 20, 1)
    board.poll(1, F31)
    assert not engine.deepened(board.r, "g1") and board.r.zcard(engine.DEEPEN) == 0  # its first look, and no more
    board.r.set(bus.watching_key(2), "1")  # the other player's event of the board, open on a match page
    board.poll(1, F31)
    board.poll(2, F31)
    assert engine.deepened(board.r, "g1") and board.r.zcard(engine.DEEPEN) == 1
    board.r.delete(bus.watching_key(2))  # closed: the next poll lets it go
    board.poll(2, F31)
    assert not engine.deepened(board.r, "g1") and board.r.zcard(engine.DEEPEN) == 0
    monkeypatch.setattr(engine, "DEEPENING", "all")
    board.poll(1, F31)
    assert engine.deepened(board.r, "g1")
    monkeypatch.setattr(engine, "DEEPENING", "off")
    board.r.set(bus.watching_key(1), "1")
    board.poll(1, F31)
    assert not engine.deepened(board.r, "g1")


def test_both_events_of_a_board_settle_the_same(board):
    def both(fen: str, meanwhile=None) -> list[int]:
        bands = [board.poll(1, fen)["eval"]["band"]]
        if meanwhile:
            meanwhile()
        return [*bands, board.poll(2, fen)["eval"]["band"]]

    board.reading(F30, 20, 1)
    assert both(F30) == [0, 0]
    board.reading(F31, 340, 2)
    assert both(F31) == [0, 0]
    # Move 32 agrees with 31: winning, for the first event. Move 33's first look (level) lands before the second
    # event's poll: it takes the board's settled evaluation and holds 33 against 32, as the first will, instead of
    # against its own last one (31), which would leave it level and its fans never told.
    board.reading(F32, 340, 3)
    assert both(F32, meanwhile=lambda: board.reading(F33, 30, 4)) == [2, 2]


def test_both_events_of_a_board_say_the_same_move_for_a_swing(board):
    for at, (fen, cp) in enumerate([(F30, 20), (F31, 340)], 1):
        board.reading(fen, cp, at)
        board.poll(1, fen)
        board.poll(2, fen)
    # The first event's poll comes after move 32's look (winning); the second's after move 33's: the board's band
    # moved at 32 for both.
    board.reading(F32, 340, 3)
    one = board.poll(1, F32)["eval"]
    board.reading(F33, 345, 4)
    two = board.poll(2, F33)["eval"]
    assert (one["band"], one["since"]) == (two["band"], two["since"]) == (2, 32)


def test_two_polls_settling_at_once_make_one_chain():
    # Each event's poll is a job of its own: the other's settle lands between this one's read and its write.
    r = FakeRedis()
    look = lambda fen, cp, at: {"cp": cp, "mate": None, "depth": 20, "look": {"cp": cp, "mate": None, "depth": 20}, "fen": fen, "at": at}  # noqa: E731
    start = engine.settled("g1", look(F30, 20, 1), chess.settle, r=r)
    meanwhile = []

    def settle(before, ev):
        if not meanwhile:
            meanwhile.append(engine.settled("g1", look(F31, 340, 2), chess.settle, r=r))
        return chess.settle(before, ev)

    late = engine.settled("g1", look(F32, 340, 3), settle, r=r)
    one_after_the_other = chess.settle(chess.settle(start, look(F31, 340, 2)), look(F32, 340, 3))
    assert late is not None and (late["band"], late["raw"], late["since"]) == (one_after_the_other["band"], one_after_the_other["raw"], 32)
    assert json.loads(r.get("engine:settled:g1") or "{}")["at"] == 3 and late["chain"] == meanwhile[0]["chain"] == start["chain"]
    # An older reading than the one kept, or one with no score, settles nothing.
    assert engine.settled("g1", look(F31, 340, 2), chess.settle, r=r) == late
    assert engine.settled("g1", {**look(F32, 0, 4), "cp": None}, chess.settle, r=r) == late


def test_an_events_first_settle_from_the_boards_chain_tells_only_a_real_turn(board):
    # A deploy mid-game: each event's last evaluation was its own. The first to poll starts the board's chain from
    # its own; the other, joining it, is held against the board's band before this settle, not its own stale one.
    line = {"headline": "", "stats": [{"label": "Colour", "value": "White"}, {"label": "Opponent", "value": "Gukesh D"}]}

    def told(event: int, was: dict) -> list[str]:
        return [f.title for f in sports.get("chess").during("Magnus Carlsen", "1503014", Seen("live", was, line), Seen("live", board.last[event], line))]

    board.last[1] = {"kind": "chess", "game_id": "g1", "eval": {"cp": 30, "band": 0, "raw": 0, "told": 0, "move": 31, "fen": F31, "at": 1}}
    board.last[2] = {"kind": "chess", "game_id": "g1", "eval": {"cp": 340, "band": 2, "raw": 2, "told": 2, "move": 31, "fen": F31, "at": 1}}
    was = dict(board.last[2])
    board.reading(F32, 30, 2)
    one, two = board.poll(1, F32)["eval"], board.poll(2, F32)["eval"]
    assert (one["band"], two["band"], one.get("joined"), two.get("joined")) == (0, 0, None, True)
    assert told(2, was) == []  # its own 2 was stale: the board didn't turn
    assert "joined" not in board.poll(2, F32)["eval"]  # from then on, one of the chain

    # The same deploy, but this settle turns the board (two first looks agreeing on Carlsen winning): told to the
    # joining event's fans as the board's turn, not against its own stale band (Black winning: a "Turnaround").
    board.r.delete("engine:settled:g1")
    board.last[1] = {"kind": "chess", "game_id": "g1", "eval": {"cp": 340, "band": 0, "raw": 2, "told": 0, "move": 32, "fen": F32, "at": 2}}
    board.last[2] = {"kind": "chess", "game_id": "g1", "eval": {"cp": -340, "band": -2, "raw": -2, "told": -2, "move": 32, "fen": F32, "at": 2}}
    was = dict(board.last[2])
    board.reading(F33, 345, 3)
    one, two = board.poll(1, F33)["eval"], board.poll(2, F33)["eval"]
    assert (one["band"], two["band"], two.get("joined")) == (2, 2, True)
    assert told(2, was) == ["Magnus Carlsen is winning"]


def test_the_interval_counts_from_when_the_polls_asked():
    r = FakeRedis()
    engine.request("A", E4, r, now=100)
    engine.request("A", E4, r, now=110)  # asked again while it waits: asked at 100 still
    assert engine.take(r, now=130) == ("A", E4, 100)  # the lane got to it late
    r.set("engine:latest:A", json.dumps({"cp": 20, "mate": None, "depth": 18, "fen": E4, "at": 130, "first": 130, "asked": 100}))
    engine.request("A", E4E5, r, now=131)
    assert took(r, now=131) == ("A", E4E5)  # due 20 s after 100, not after 130


def test_an_unknown_deepening_setting_is_watched(caplog):
    assert [engine.deepening(x) for x in ("watched", "all", "off")] == ["watched", "all", "off"]
    assert engine.deepening("sometimes") == "watched" and "SPORTS_FOLLOW_ENGINE_DEEPEN" in caplog.text


def test_positions_from_games_nobody_polls_are_dropped():
    r = FakeRedis()
    engine.request("A", START, r, now=100)
    assert took(r, now=100 + engine.STALE + 1) is None


def test_budget_and_position_keys():
    assert engine.rest(1.0, busy=0.5) == 1.0
    assert engine.rest(1.0, busy=0.25) == 3.0
    assert engine.position_key(E4E5) == engine.position_key(E4E5.replace(" 0 2", " 3 9"))
    assert engine.position_key(E4) != engine.position_key(E4E5)


def test_scores_turn_to_whites_side():
    assert engine.parse_score("info depth 18 seldepth 25 multipv 1 score cp -44 nodes 500000") == (-44, None, 18)
    assert engine.parse_score("info depth 30 score mate 3 nodes 1") == (None, 3, 30)
    assert engine.from_white(E4, 30, None) == (-30, None)  # Black to move and +0.3 for Black
    assert engine.from_white(E4E5, 30, None) == (30, None)


@pytest.mark.skipif(not engine.enabled(), reason="Stockfish isn't installed")
def test_the_engine_finds_mate_for_either_side():
    e = engine.Engine()
    try:
        white = e.evaluate("6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1", nodes=100_000)  # Rd8#
        black = e.evaluate("3r2k1/5ppp/8/8/8/8/5PPP/6K1 b - - 0 1", nodes=100_000)  # ...Rd1#
    finally:
        e.close()
    assert white["mate"] == 1 and black["mate"] == -1


MIDDLEGAME = "r2qr1k1/4bpp1/1np2n1p/pp1p4/3P3B/P1NQPP2/1P4PP/2N1RRK1 w - - 2 16"  # Nakamura v Erigaisi, move 16 (fixtures/lichess)


def paused(e: engine.Engine) -> bool:
    return subprocess.run(["ps", "-o", "stat=", "-p", str(e.proc.pid)], capture_output=True, text=True).stdout.strip().startswith("T")


@pytest.mark.skipif(not engine.enabled(), reason="Stockfish isn't installed")
def test_a_deep_search_is_paused_between_slices_and_goes_deeper_than_a_look():
    e = engine.Engine()
    try:
        look = e.evaluate(MIDDLEGAME, nodes=200_000)
        e.search(MIDDLEGAME, depth=99, nodes=1_000_000)
        readings, over = [], False
        while not over:
            reading, over = e.run(0.25)
            readings.append(reading)
            assert paused(e) != over  # it uses no CPU between slices
        # Stopped part way, with what it had; the engine is then free for the next look.
        e.search(MIDDLEGAME, depth=99, nodes=50_000_000)
        e.run(0.25)
        stopped = e.halt()
        again = e.evaluate(MIDDLEGAME, nodes=10_000)
    finally:
        e.close()
    nodes = [x["nodes"] for x in readings]
    assert nodes == sorted(nodes) and nodes[-1] >= 1_000_000 and readings[-1]["depth"] > look["depth"]
    assert stopped["depth"] > 0 and not paused(e) and again["nodes"] >= 10_000


OTHER = "r1bqr1k1/1p3pb1/2pp2p1/p1n1pPP1/1nP1P3/2NPB3/PP2N1B1/R2Q1RK1 w - - 3 16"  # another of fixtures/lichess, move 16


@pytest.mark.skipif(not engine.enabled(), reason="Stockfish isn't installed")
def test_a_first_look_is_the_same_whatever_was_searched_before():
    # Stockfish versions score differently (CI has 16, the image 17.1): runs are compared here, never scores.
    e = engine.Engine(hash_mb=engine.LOOK_HASH_MB)
    fresh = engine.Engine(hash_mb=engine.LOOK_HASH_MB)
    try:
        first = e.evaluate(MIDDLEGAME, nodes=200_000)
        e.evaluate(OTHER, nodes=300_000)
        again = e.evaluate(MIDDLEGAME, nodes=200_000)
        elsewhere = fresh.evaluate(MIDDLEGAME, nodes=200_000)
    finally:
        e.close()
        fresh.close()
    look = lambda x: (x["cp"], x["mate"], x["depth"], x["nodes"])  # noqa: E731
    assert look(again) == look(first) == look(elsewhere) and "capped" not in first


def test_a_look_past_its_time_is_stopped_and_says_so(tmp_path, monkeypatch):
    # A machine stalled under a look: stopped at LOOK_SECONDS with what it had, which depends on the machine.
    slow = script(tmp_path, "slow-engine", """    elif word == "go":
        print("info depth 9 seldepth 12 score cp 31 nodes 4000", flush=True)
    elif word == "stop":
        print("bestmove e2e4", flush=True)
""")
    monkeypatch.setattr(engine, "LOOK_SECONDS", 0.2)
    e = engine.Engine(slow)
    try:
        look = e.evaluate(MIDDLEGAME)
    finally:
        e.close()
    assert (look["cp"], look["depth"], look["capped"]) == (31, 9, True)


def fake_lane(monkeypatch, r: FakeRedis, engine_class) -> None:
    """The lane on r with a stand-in for the Engine class, its lock always its own."""
    monkeypatch.setattr(engine, "enabled", lambda: True)
    monkeypatch.setattr(engine, "Engine", engine_class)
    monkeypatch.setattr(bus, "sync_redis", lambda: r)
    monkeypatch.setattr(bus, "try_lock", lambda name, seconds: "token")
    monkeypatch.setattr(bus, "extend", lambda name, seconds, token=None: True)
    monkeypatch.setattr(bus, "release", lambda name, token=None: None)
    engine.run_lane()


def test_a_look_the_clock_stopped_is_shown_with_no_look_and_one_with_no_score_isnt_shown(monkeypatch, stopping):
    r = FakeRedis()
    looks = [{"cp": 31, "mate": None, "depth": 9, "nodes": 4000, "capped": True}, {"cp": None, "mate": None, "depth": 0, "nodes": 50, "capped": True}]

    class Stockfish:
        def __init__(self, *args, **kwargs):
            pass

        def evaluate(self, fen):
            if len(looks) == 1:
                stopping.set()
            return looks.pop(0)

        def close(self):
            pass

    engine.request("A", E4, r, deepen=False)
    engine.request("B", E4E5, r, deepen=False)
    fake_lane(monkeypatch, r, Stockfish)
    # A's is shown, with no first look: nothing alerts by it (chess.settle holds the band), and the next game at the
    # position gets a look of its own. B's had no score at all: nothing shown.
    shown = engine.latest("A", r) or {}
    assert (shown["cp"], "look" in shown, "look" in (engine.cached(r, E4) or {})) == (31, False, False)
    assert engine.latest("B", r) is None


def test_the_deep_engine_is_closed_when_nothing_has_been_deepened_a_while(monkeypatch, stopping):
    monkeypatch.setattr(engine, "DEEP_IDLE", 0)
    r, made, closed = FakeRedis(), [], []

    class Stockfish:
        def __init__(self, path=None, hash_mb=engine.HASH_MB):
            self.deep = hash_mb != engine.LOOK_HASH_MB
            made.append(self.deep)

        def evaluate(self, fen):
            return {"cp": 20, "mate": None, "depth": 18, "nodes": engine.NODES}

        def search(self, fen, depth, nodes):
            self.fen = fen

        def run(self, seconds):
            return {"cp": 20, "mate": None, "depth": engine.DEPTH, "nodes": engine.NODES}, True  # deep enough at once

        def halt(self):
            return {}

        def close(self):
            closed.append(self.deep)

    idle = []

    def sleep(seconds):
        if seconds != 1:
            return not stopping.is_set()  # a rest after work
        idle.append(seconds)  # the lane with nothing to do
        if len(idle) == 1:
            engine.request("B", START, r)  # once the deep engine has gone, another game to deepen
        if len(idle) > 3:
            stopping.set()
        return not stopping.is_set()

    monkeypatch.setattr(engine.shutdown, "sleep", sleep)
    monkeypatch.setattr(engine, "IDLE_EXIT", 60)
    engine.request("A", E4, r)
    fake_lane(monkeypatch, r, Stockfish)
    # Looks' engine, A's deep one (closed once A was deep enough and nothing waited), then a new one for B.
    assert made == [False, True, True] and closed[0] is True


def test_the_lane_goes_on_when_the_deep_engine_dies(monkeypatch, stopping):
    monkeypatch.setattr(engine, "DEEP_IDLE", 0)
    r, made, looked = FakeRedis(), [], []

    class Stockfish:
        def __init__(self, path=None, hash_mb=engine.HASH_MB):
            made.append(hash_mb != engine.LOOK_HASH_MB)
            self.dies = made.count(True) == 1  # the first deep engine dies at its first slice

        def evaluate(self, fen):
            looked.append(fen)
            return {"cp": 20, "mate": None, "depth": 18, "nodes": engine.NODES}

        def search(self, fen, depth, nodes):
            pass

        def run(self, seconds):
            if self.dies:
                raise BrokenPipeError("the engine exited")
            stopping.set()
            return {"cp": 25, "mate": None, "depth": 24, "nodes": engine.NODES}, False

        def halt(self):
            return {"cp": 25, "mate": None, "depth": 24, "nodes": engine.NODES}

        def close(self):
            pass

    engine.request("A", E4, r)
    fake_lane(monkeypatch, r, Stockfish)
    # Its engine died: the lane went on, and A was deepened on a new one.
    assert made == [False, True, True] and (engine.latest("A", r) or {})["depth"] == 24


def test_the_lane_stops_when_the_worker_does(monkeypatch, stopping):
    # BHA-51: a deploy while games are on. The lane ends after the position under way, instead of
    # evaluating on until the process is killed, and frees its lock.
    r, evaluated, freed = FakeRedis(), [], []

    class Stockfish:
        def __init__(self, *args, **kwargs):
            pass

        def evaluate(self, fen):
            evaluated.append(fen)
            stopping.set()  # told to stop while this position is evaluated
            return {"score": 0}

        def close(self):
            pass

    monkeypatch.setattr(engine, "enabled", lambda: True)
    monkeypatch.setattr(engine, "Engine", Stockfish)
    monkeypatch.setattr(engine, "take", lambda r, now: ("game", START, now))  # positions keep coming
    monkeypatch.setattr(bus, "sync_redis", lambda: r)
    monkeypatch.setattr(bus, "try_lock", lambda name, seconds: "token")
    monkeypatch.setattr(bus, "extend", lambda name, seconds, token=None: True)
    monkeypatch.setattr(bus, "release", lambda name, token=None: freed.append(name))
    lane = threading.Thread(target=engine.run_lane, daemon=True)
    lane.start()
    lane.join(5)
    assert not lane.is_alive()
    assert evaluated == [START] and freed == [engine.LOCK]
