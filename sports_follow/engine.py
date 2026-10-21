"""Engine evaluations of live chess games, on a fixed CPU budget.

A chess poll asks for its game's current position to be evaluated (request()) and reads back the
game's latest evaluation (latest()). One engine lane evaluates the waiting positions (run_lane(), a
worker job the scheduler tick starts), and the CPU it may use is bounded every way:

- Per game, not per follower: a game has at most one waiting position, the newest (older ones are
  dropped), and a game two followed players share is evaluated once.
- Positions are cached (board, side to move, castling, en passant), so openings and repeated
  positions are evaluated once across all games.
- Fair: the game that has waited longest goes first, so one fast blitz game can't starve the rest.
- Each game at most once every GAME_INTERVAL seconds. Moves in between are skipped; a swing still
  shows between the evaluations either side of them.
- One lane for the whole system (a Redis lock), one engine thread, a fixed node budget for a look
  and as long a slice of a deep search (about a second on one core), at most POSITION_NODES in any
  one position, low CPU priority, and a duty cycle: the engine works at most BUSY of the time.

A new position gets a quick look first (NODES, about depth 20), which alerts go by as soon as it's
made. Then, while the game stays on it, the lane deepens it with the time the engine would otherwise
idle: one search of the position, run a SLICE at a time and paused in between (the duty cycle's rest,
or a look at any game's new position, which always goes first), until it reaches DEPTH or has
searched POSITION_NODES. Each deeper reading becomes the game's latest; a new move drops the old
position at once. With several games deepening, a search has a TURN before the game that has waited
longest gets the engine, so many boards each get less depth and one big game gets a lot.

One search paused, not a new search a step: Stockfish starts every search from depth 1, and what its
hash table keeps of the last one takes the next only a ply or two further. Measured on Stockfish 17.1
(the image's), one thread, 128 MB hash, on eight middlegames from tests/fixtures/lichess: a 500k-node
look reaches depth 18-21 in about a second; looks one after another, hash kept, hardly add up (500k-node
ones are at depth 24 typically after 64M nodes, 4M-node ones at 29 after 32M, whatever the hash's
size); one search reaches depth 30 after 4-22M nodes, 9M typically (about 20 s of work), and the
same depth at the same nodes when it's paused between 1 s slices. Searched again after a look
elsewhere, it needs a third to four fifths of its nodes again to get back, hence long turns.

Stockfish (GPL) runs as its own process, spoken to over UCI. Without it installed, nothing is
evaluated and chess notifications keep to starts and results.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

from . import bus, shutdown

log = logging.getLogger("sports_follow.engine")

STOCKFISH = os.environ.get("SPORTS_FOLLOW_STOCKFISH") or shutil.which("stockfish") or ""
NODES = int(os.environ.get("SPORTS_FOLLOW_ENGINE_NODES", 500_000))  # a look: about a second on one core
BUSY = min(1.0, max(0.05, float(os.environ.get("SPORTS_FOLLOW_ENGINE_BUSY", 0.5))))  # share of one core
GAME_INTERVAL = int(os.environ.get("SPORTS_FOLLOW_ENGINE_GAME_INTERVAL", 20))
DEPTH = int(os.environ.get("SPORTS_FOLLOW_ENGINE_DEPTH", 30))  # a position is deepened to this depth...
POSITION_NODES = int(os.environ.get("SPORTS_FOLLOW_ENGINE_POSITION_NODES", 32_000_000))  # ...or this much work in it
HASH_MB = int(os.environ.get("SPORTS_FOLLOW_ENGINE_HASH_MB", 128))  # the deep search's tree, kept as it goes
MAX_SECONDS = 5  # a slow machine stops a look here, whatever the node budget
SLICE = 1.0  # seconds a deep search runs before the lane looks again: about a look
TURN = 8_000_000  # nodes a deep search goes on for while another game waits to be deepened
SESSION = 10 * 60  # the lane hands over like a poll job; the next tick starts a fresh one
IDLE_EXIT = 2 * 60  # ...or ends early with nothing to do
STALE = 5 * 60  # a waiting position this old is from a game nobody polls any more
CACHE_TTL = 24 * 3600
LATEST_TTL = 6 * 3600

WANT = "engine:want"  # game -> {"fen", "at"}: the newest position waiting
QUEUE = "engine:queue"  # game -> the time it may be evaluated from; lowest first
DEEPEN = "engine:deepen"  # game -> when it last had the engine; lowest first
LOCK = "engine-lane"


def enabled() -> bool:
    return bool(STOCKFISH) and os.access(STOCKFISH, os.X_OK)


# ---------------------------------------------------------------- the queue


def latest(game: str, r: Any = None) -> dict[str, Any] | None:
    """The game's latest evaluation, from White's side: {"cp" or "mate", "depth", "nodes", "fen", "at",
    "first"}. "at" is when it was made, "first" when its position was first looked at, and nodes all the
    work in the position so far."""
    raw = (r or bus.sync_redis()).get(f"engine:latest:{game}")
    return json.loads(raw) if raw else None


def request(game: str, fen: str, r: Any = None, now: float | None = None) -> None:
    """Ask for this position to be evaluated. It replaces the game's waiting one, if any, and keeps
    that one's place in the queue. Asked again for the position it's on, the game is still polled, so
    that position goes on being deepened; a new one stops that at once."""
    r = r or bus.sync_redis()
    now = now or time.time()
    r.set(f"engine:polled:{game}", "1", ex=STALE)
    done = latest(game, r)
    if done and done["fen"] == fen:
        return
    r.zrem(DEEPEN, game)
    r.hset(WANT, game, json.dumps({"fen": fen, "at": now}))
    # Deeper readings aren't new looks: the interval counts from the position's first.
    r.zadd(QUEUE, {game: max(now, done.get("first", done["at"]) + GAME_INTERVAL) if done else now}, nx=True)


def take(r: Any, now: float) -> tuple[str, str] | None:
    """The game that has waited longest among those due, with its newest position."""
    while True:
        due = r.zrangebyscore(QUEUE, "-inf", now, start=0, num=1)
        if not due:
            return None
        game = due[0]
        r.zrem(QUEUE, game)
        raw = r.hget(WANT, game)
        r.hdel(WANT, game)
        if raw:
            want = json.loads(raw)
            if now - want["at"] <= STALE:
                return game, want["fen"]


def take_deeper(r: Any, now: float) -> tuple[str, dict[str, Any]] | None:
    """The game that has waited longest to have the position it's on deepened, with its latest
    evaluation; it stays in the queue until its search starts. A game leaves it once it's off the
    position (a newer one is waiting, or nobody has polled it for STALE) or the position is deep enough."""
    while True:
        due = r.zrangebyscore(DEEPEN, "-inf", now, start=0, num=1)
        if not due:
            return None
        game = due[0]
        done = latest(game, r)
        if done and on_it(r, game, done["fen"]) and not deep_enough(cached(r, done["fen"]) or done):
            return game, done
        r.zrem(DEEPEN, game)


def on_it(r: Any, game: str, fen: str) -> bool:
    """Whether a game is still on this position: polled, nothing newer waiting, and its latest
    evaluation of it."""
    return bool(r.get(f"engine:polled:{game}")) and not r.hget(WANT, game) and (latest(game, r) or {}).get("fen") == fen


def needs_lane() -> bool:
    r = bus.sync_redis()
    return enabled() and bool(r.zcard(QUEUE) or r.zcard(DEEPEN)) and not bus.is_locked(LOCK)


def position_key(fen: str) -> str:
    """Positions that differ only in move counters are the same position."""
    return hashlib.sha1(" ".join(fen.split()[:4]).encode()).hexdigest()


def cached(r: Any, fen: str) -> dict[str, Any] | None:
    """The deepest evaluation of this position so far, with all the nodes searched in it."""
    raw = r.get(f"engine:pos:{position_key(fen)}")
    return json.loads(raw) if raw else None


def keep(r: Any, fen: str, result: dict[str, Any]) -> dict[str, Any]:
    """Add a search of this position to the cache: the deeper of it and what's there stays (a shallower
    reading never replaces a deeper one), and its nodes add to the position's. Returns what's kept."""
    held = cached(r, fen)
    best = result if held is None or result.get("depth", 0) > held.get("depth", 0) else held
    kept = {**best, "nodes": (held or {}).get("nodes", 0) + result.get("nodes", 0)}
    r.set(f"engine:pos:{position_key(fen)}", json.dumps(kept), ex=CACHE_TTL)
    return kept


def deep_enough(ev: dict[str, Any]) -> bool:
    """Whether a position has had all the work it gets: DEPTH reached, or POSITION_NODES searched."""
    return ev.get("depth", 0) >= DEPTH or ev.get("nodes", 0) >= POSITION_NODES


def rest(spent: float, busy: float = BUSY) -> float:
    """How long to idle after `spent` seconds of work, to stay busy at most `busy` of the time."""
    return spent * (1 - busy) / busy


# ---------------------------------------------------------------- the engine


def parse_score(line: str) -> tuple[int | None, int | None, int]:
    """A UCI info line -> (centipawns, mate in N, depth), from the side to move's view."""
    score = re.search(r" score (cp|mate) (-?\d+)", line)
    depth = re.search(r" depth (\d+)", line)
    cp = int(score.group(2)) if score and score.group(1) == "cp" else None
    mate = int(score.group(2)) if score and score.group(1) == "mate" else None
    return cp, mate, int(depth.group(1)) if depth else 0


def from_white(fen: str, cp: int | None, mate: int | None) -> tuple[int | None, int | None]:
    """UCI scores are from the side to move; turn them to White's view."""
    if fen.split()[1] == "w":
        return cp, mate
    return (-cp if cp is not None else None), (-mate if mate is not None else None)


class EngineError(RuntimeError):
    pass


class Engine:
    """One Stockfish process: one thread, low CPU priority, one search at a time. A look (evaluate)
    runs start to finish; a deep search (search) runs only while run() lets it, and is stopped (SIGSTOP)
    in between, holding its place in the tree."""

    def __init__(self, path: str = STOCKFISH) -> None:
        self.proc = subprocess.Popen(["nice", "-n", "10", path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.lines: queue.Queue[str | None] = queue.Queue()  # read as they come, so a paused search never blocks the lane
        threading.Thread(target=self._reader, name="stockfish", daemon=True).start()
        self.fen, self.searching = "", False
        self.reading: dict[str, Any] = {}
        self._send("uci")
        self._until("uciok")
        self._send("setoption name Threads value 1")
        self._send(f"setoption name Hash value {HASH_MB}")
        self._send("isready")
        self._until("readyok")

    def evaluate(self, fen: str, nodes: int = NODES) -> dict[str, Any]:
        """A look: one search, start to finish. Its score, and the depth it reached and the nodes it
        searched, as Stockfish reports them."""
        self._go(fen, f"nodes {nodes} movetime {MAX_SECONDS * 1000}")
        while not self._note(self._read()):
            pass
        return dict(self.reading)

    def search(self, fen: str, depth: int, nodes: int) -> None:
        """Start a deep search of a position, to `depth` or `nodes`, whichever comes first. The caller
        lets it go on with run() straight away; it's paused from then on, except while run() lets it."""
        self._go(fen, f"depth {depth} nodes {nodes}")

    def run(self, seconds: float) -> tuple[dict[str, Any], bool]:
        """Let the deep search go on for up to `seconds`, then pause it. Its latest reading, and whether
        it's over (it reached its depth or nodes)."""
        self._signal(signal.SIGCONT)
        deadline = time.monotonic() + seconds
        over = False
        while not over and (line := self._read(deadline - time.monotonic())):
            over = self._note(line)
        if not over:
            self._signal(signal.SIGSTOP)
            while not over and (line := self._read(0)):
                over = self._note(line)  # what it said before it stopped
            if over:
                self._signal(signal.SIGCONT)  # it had finished: idle, not paused
        return dict(self.reading), over

    def halt(self) -> dict[str, Any]:
        """End the deep search, if one is under way. Its last reading."""
        if self.searching:
            self._signal(signal.SIGCONT)
            self._send("stop")
            while not self._note(self._read()):
                pass
        return dict(self.reading)

    def close(self) -> None:
        try:
            self._signal(signal.SIGCONT)  # a paused engine can't read "quit"
            self._send("quit")
            self.proc.wait(timeout=2)
        except Exception:
            self.proc.kill()

    def _go(self, fen: str, limits: str) -> None:
        self.halt()
        self.fen, self.reading, self.searching = fen, {"cp": None, "mate": None, "depth": 0, "nodes": 0}, True
        self._send(f"position fen {fen}")
        self._send(f"go {limits}")

    def _note(self, line: str) -> bool:
        """Note a line about the search under way; True once it's over."""
        if line.startswith("info"):
            if nodes := re.search(r" nodes (\d+)", line):
                self.reading["nodes"] = int(nodes.group(1))
            if " score " in line and " upperbound" not in line and " lowerbound" not in line:
                cp, mate, depth = parse_score(line)
                cp, mate = from_white(self.fen, cp, mate)
                self.reading.update(cp=cp, mate=mate, depth=depth)
        elif line.startswith("bestmove"):
            self.searching = False
            return True
        return False

    def _signal(self, sig: int) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(sig)

    def _send(self, command: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

    def _reader(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _read(self, timeout: float | None = None) -> str:
        """The engine's next line; with a timeout, "" if none comes in time."""
        try:
            line = self.lines.get(timeout=max(0.0, timeout)) if timeout is not None else self.lines.get()
        except queue.Empty:
            return ""
        if line is None:
            self.lines.put(None)
            raise EngineError("the engine exited")
        return line

    def _until(self, prefix: str) -> None:
        while not self._read().startswith(prefix):
            pass


# ---------------------------------------------------------------- the lane


@dataclass
class Deepening:
    """A game's turn at the deep search: its position, searched a slice at a time. A look at another
    game's new position stops the search; it's searched again after, in the same turn."""

    game: str
    fen: str
    first: float  # when the position was first looked at
    shown: int  # the depth of the game's latest evaluation
    searching: bool = False  # a search of it is under way (paused between slices)
    counted: int = 0  # that search's nodes already added to the position's
    turn: int = 0  # nodes searched in this turn


def run_lane() -> None:
    """Evaluate waiting positions, and in between deepen the ones games are on, until SESSION is up
    or nothing has needed the engine for IDLE_EXIT."""
    if not enabled() or not bus.try_lock(LOCK, 120):
        return
    r = bus.sync_redis()
    engine: Engine | None = None
    deep: Deepening | None = None
    evaluated = hits = slices = 0
    busy = 0.0
    started = idle_since = time.monotonic()
    try:
        while time.monotonic() - started < SESSION and not shutdown.stopping.is_set():
            bus.extend(LOCK, 120)
            now = time.time()
            if deep is not None and not on_it(r, deep.game, deep.fen):
                _end(r, engine, deep)  # the game moved, or nobody polls it: dropped at once
                deep = None
            spent = 0.0
            if job := take(r, now):
                # A new position goes first, from the cache or a look.
                game, fen = job
                done = cached(r, fen)
                if done:
                    hits += 1
                else:
                    engine = engine or Engine()
                    if deep is not None and deep.searching:
                        _note(r, deep, engine.halt())
                        deep.searching = False
                    t = time.monotonic()
                    done = keep(r, fen, engine.evaluate(fen))
                    spent = time.monotonic() - t
                    evaluated += 1
                first = time.time()
                r.set(f"engine:latest:{game}", json.dumps({**done, "fen": fen, "at": first, "first": first}), ex=LATEST_TTL)
                if not deep_enough(done) and not r.hget(WANT, game):
                    r.zadd(DEEPEN, {game: first})
            else:
                if deep is not None and deep.turn >= TURN and take_deeper(r, now):
                    _end(r, engine, deep, again=True)  # its turn is over: the game that has waited longest goes next
                    deep = None
                if deep is None and (waiting := take_deeper(r, now)):
                    game, shown = waiting
                    deep = Deepening(game, shown["fen"], shown.get("first", shown["at"]), shown.get("depth", 0))
                    r.zrem(DEEPEN, game)
                if deep is None:
                    if time.monotonic() - idle_since > IDLE_EXIT:
                        break
                    shutdown.sleep(1)
                    continue
                engine = engine or Engine()
                if not deep.searching:
                    engine.search(deep.fen, DEPTH, max(NODES, POSITION_NODES - (cached(r, deep.fen) or {}).get("nodes", 0)))
                    deep.searching, deep.counted = True, 0
                t = time.monotonic()
                reading, over = engine.run(SLICE)
                spent = time.monotonic() - t
                slices += 1
                _note(r, deep, reading)
                if over:
                    deep = None  # deep enough
            idle_since = time.monotonic()
            busy += spent
            if spent:
                shutdown.sleep(rest(spent))
    except Exception:
        log.exception("engine lane crashed")
    finally:
        if engine is not None:
            if deep is not None:
                try:
                    _end(r, engine, deep, again=True)  # the next lane goes on with it
                except Exception:
                    log.exception("engine lane: ending the deep search")
            engine.close()
        bus.release(LOCK)
        wall = time.monotonic() - started
        if evaluated or hits or slices:
            log.info("engine: %d position(s) evaluated, %d from cache, %d deeper slice(s), busy %.0fs of %.0fs (%.0f%% of one core), %d game(s) waiting, %d to deepen",
                     evaluated, hits, slices, busy, wall, 100 * busy / max(wall, 1), r.zcard(QUEUE), r.zcard(DEEPEN))


def _note(r: Any, deep: Deepening, reading: dict[str, Any]) -> None:
    """A deep search's reading: its new nodes into the position's cache and the turn, and the game's
    latest evaluation once it's deeper, unless the game has moved meanwhile."""
    nodes = reading.get("nodes", 0)
    done = keep(r, deep.fen, {**reading, "nodes": nodes - deep.counted})
    deep.turn += nodes - deep.counted
    deep.counted = nodes
    if done.get("depth", 0) > deep.shown and not r.hget(WANT, deep.game):
        r.set(f"engine:latest:{deep.game}", json.dumps({**done, "fen": deep.fen, "at": time.time(), "first": deep.first}), ex=LATEST_TTL)
        deep.shown = done["depth"]


def _end(r: Any, engine: Engine | None, deep: Deepening, again: bool = False) -> None:
    """End a game's turn, keeping what its search found. Again: it waits for another turn, if it's still
    on the position and the position isn't deep enough."""
    if engine is not None and deep.searching:
        _note(r, deep, engine.halt())
    if again and on_it(r, deep.game, deep.fen) and not deep_enough(cached(r, deep.fen) or {}):
        r.zadd(DEEPEN, {deep.game: time.time()})
