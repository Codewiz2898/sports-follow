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
- One lane for the whole system (a Redis lock), one engine thread, a fixed node budget (about a
  second on one core), low CPU priority, and a duty cycle: the engine works at most BUSY of the time.

Stockfish (GPL) runs as its own process, spoken to over UCI. Without it installed, nothing is
evaluated and chess notifications keep to starts and results.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Any

from . import bus

log = logging.getLogger("sports_follow.engine")

STOCKFISH = os.environ.get("SPORTS_FOLLOW_STOCKFISH") or shutil.which("stockfish") or ""
NODES = int(os.environ.get("SPORTS_FOLLOW_ENGINE_NODES", 500_000))  # about a second on one core
BUSY = min(1.0, max(0.05, float(os.environ.get("SPORTS_FOLLOW_ENGINE_BUSY", 0.5))))  # share of one core
GAME_INTERVAL = int(os.environ.get("SPORTS_FOLLOW_ENGINE_GAME_INTERVAL", 20))
MAX_SECONDS = 5  # a slow machine stops here, whatever the node budget
HASH_MB = 32
SESSION = 10 * 60  # the lane hands over like a poll job; the next tick starts a fresh one
IDLE_EXIT = 2 * 60  # ...or ends early with nothing to do
STALE = 5 * 60  # a waiting position this old is from a game nobody polls any more
CACHE_TTL = 24 * 3600
LATEST_TTL = 6 * 3600

WANT = "engine:want"  # game -> {"fen", "at"}: the newest position waiting
QUEUE = "engine:queue"  # game -> the time it may be evaluated from; lowest first
LOCK = "engine-lane"


def enabled() -> bool:
    return bool(STOCKFISH) and os.access(STOCKFISH, os.X_OK)


# ---------------------------------------------------------------- the queue


def latest(game: str, r: Any = None) -> dict[str, Any] | None:
    """The game's latest evaluation, from White's side: {"cp" or "mate", "depth", "fen", "at"}."""
    raw = (r or bus.sync_redis()).get(f"engine:latest:{game}")
    return json.loads(raw) if raw else None


def request(game: str, fen: str, r: Any = None, now: float | None = None) -> None:
    """Ask for this position to be evaluated. It replaces the game's waiting one, if any, and keeps
    that one's place in the queue."""
    r = r or bus.sync_redis()
    now = now or time.time()
    done = latest(game, r)
    if done and done["fen"] == fen:
        return
    r.hset(WANT, game, json.dumps({"fen": fen, "at": now}))
    r.zadd(QUEUE, {game: max(now, done["at"] + GAME_INTERVAL) if done else now}, nx=True)


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


def needs_lane() -> bool:
    return enabled() and bool(bus.sync_redis().zcard(QUEUE)) and not bus.is_locked(LOCK)


def position_key(fen: str) -> str:
    """Positions that differ only in move counters are the same position."""
    return hashlib.sha1(" ".join(fen.split()[:4]).encode()).hexdigest()


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
    """One Stockfish process: one thread, a small hash table, low CPU priority."""

    def __init__(self, path: str = STOCKFISH) -> None:
        self.proc = subprocess.Popen(["nice", "-n", "10", path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self._send("uci")
        self._until("uciok")
        self._send("setoption name Threads value 1")
        self._send(f"setoption name Hash value {HASH_MB}")
        self._send("isready")
        self._until("readyok")

    def evaluate(self, fen: str, nodes: int = NODES) -> dict[str, Any]:
        self._send(f"position fen {fen}")
        self._send(f"go nodes {nodes} movetime {MAX_SECONDS * 1000}")
        last = ""
        while True:
            line = self._read()
            if line.startswith("info") and " score " in line and " upperbound" not in line and " lowerbound" not in line:
                last = line
            elif line.startswith("bestmove"):
                break
        cp, mate, depth = parse_score(last)
        cp, mate = from_white(fen, cp, mate)
        return {"cp": cp, "mate": mate, "depth": depth}

    def close(self) -> None:
        try:
            self._send("quit")
            self.proc.wait(timeout=2)
        except Exception:
            self.proc.kill()

    def _send(self, command: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

    def _read(self) -> str:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            raise EngineError("the engine exited")
        return line

    def _until(self, prefix: str) -> None:
        while not self._read().startswith(prefix):
            pass


# ---------------------------------------------------------------- the lane


def run_lane() -> None:
    """Evaluate waiting positions until SESSION is up, or nothing has waited for IDLE_EXIT."""
    if not enabled() or not bus.try_lock(LOCK, 120):
        return
    r = bus.sync_redis()
    engine: Engine | None = None
    evaluated = cached = 0
    busy = 0.0
    started = idle_since = time.monotonic()
    try:
        while time.monotonic() - started < SESSION:
            bus.extend(LOCK, 120)
            job = take(r, time.time())
            if job is None:
                if time.monotonic() - idle_since > IDLE_EXIT:
                    break
                time.sleep(1)
                continue
            idle_since = time.monotonic()
            game, fen = job
            key = f"engine:pos:{position_key(fen)}"
            hit = r.get(key)
            if hit:
                result, cached = json.loads(hit), cached + 1
            else:
                engine = engine or Engine()
                t = time.monotonic()
                result = engine.evaluate(fen)
                spent = time.monotonic() - t
                busy, evaluated = busy + spent, evaluated + 1
                r.set(key, json.dumps(result), ex=CACHE_TTL)
            r.set(f"engine:latest:{game}", json.dumps({**result, "fen": fen, "at": time.time()}), ex=LATEST_TTL)
            if not hit:
                time.sleep(rest(spent))
    except Exception:
        log.exception("engine lane crashed")
    finally:
        if engine is not None:
            engine.close()
        bus.release(LOCK)
        wall = time.monotonic() - started
        if evaluated or cached:
            log.info("engine: %d position(s) evaluated, %d from cache, busy %.0fs of %.0fs (%.0f%% of one core), %d game(s) waiting",
                     evaluated, cached, busy, wall, 100 * busy / max(wall, 1), r.zcard(QUEUE))
