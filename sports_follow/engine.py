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
- One lane for the whole system (a Redis lock), one engine thread running at a time, a fixed node
  budget for a look and as long a slice of a deep search (about a second on one core), at most
  POSITION_NODES in any one position, low CPU priority, and a duty cycle: the engine works at most
  BUSY of the time.

Every new position gets a first look (NODES, about depth 20), and alerts go by first looks alone. A
look is deterministic: its own Stockfish process, its hash table and histories cleared before each
(ucinewgame), one thread and a node budget with no clock, so a position gets the same score whatever
the machine's speed or load and whatever was searched before. That holds for one Stockfish version:
the image's 17.1 and CI's 16 score differently, so tests compare runs rather than scores.

While someone has the game open (SPORTS_FOLLOW_ENGINE_DEEPEN: watched, the default, or all, or off),
the lane deepens the position it's on with the time the engine would otherwise idle: one search of it,
in a second process with its own tree (HASH_MB), run a SLICE at a time and stopped (SIGSTOP) in
between, for the duty cycle's rest or a look at any game's new position, which always goes first; until
it reaches DEPTH or has searched POSITION_NODES. Each deeper reading becomes what the game's latest
shows (cp, mate, depth), beside its first look, which stays what the alerts go by: a position scores
the same whether the game is watched, and deeper readings never alert. Which positions get first looks
hangs on the game's moves and when its polls asked (GAME_INTERVAL counts from there), not on what else
the engine was doing. A new move drops the old position at once; a decided game (drop()), or one
nobody has open or polls any more, is let go before the next slice. A board two followed players share
is a fixture of each: either keeps it evaluated, and both take the one settled evaluation (settled()).
With several games deepening, a search has a TURN before the game that has waited longest gets the
engine, so many boards each get less depth and one big game gets a lot.

One search paused, not a new search a step: Stockfish starts every search from depth 1, and what its
hash table keeps of the last one takes the next only a ply or two further. Measured on Stockfish 17.1
(the image's), one thread, 128 MB hash, on eight middlegames from tests/fixtures/lichess: a 500k-node
look reaches depth 18-21 in about a second; looks one after another, hash kept, hardly add up (500k-node
ones are at depth 24 typically after 64M nodes, 4M-node ones at 29 after 32M, whatever the hash's
size); one search reaches depth 30 after 4-22M nodes, 9M typically (about 20 s of work), and the
same depth at the same nodes when it's paused between 1 s slices. Searched again after a look
elsewhere, it needs a third to four fifths of its nodes again to get back: hence a process of its own
for looks, and long turns. Each process is about 250 MB besides its hash table.

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
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from . import bus, shutdown

log = logging.getLogger("sports_follow.engine")


def deepening(setting: str) -> str:
    """SPORTS_FOLLOW_ENGINE_DEEPEN as the lane takes it: watched, all or off; anything else is watched."""
    if setting in ("watched", "all", "off"):
        return setting
    log.warning("SPORTS_FOLLOW_ENGINE_DEEPEN=%r isn't watched, all or off: watched it is", setting)
    return "watched"


STOCKFISH = os.environ.get("SPORTS_FOLLOW_STOCKFISH") or shutil.which("stockfish") or ""
NODES = int(os.environ.get("SPORTS_FOLLOW_ENGINE_NODES", 500_000))  # a first look: about a second on one core
BUSY = min(1.0, max(0.05, float(os.environ.get("SPORTS_FOLLOW_ENGINE_BUSY", 0.5))))  # share of one core
GAME_INTERVAL = int(os.environ.get("SPORTS_FOLLOW_ENGINE_GAME_INTERVAL", 20))
DEEPENING = deepening(os.environ.get("SPORTS_FOLLOW_ENGINE_DEEPEN", "watched"))
DEPTH = int(os.environ.get("SPORTS_FOLLOW_ENGINE_DEPTH", 30))  # a position is deepened to this depth...
POSITION_NODES = int(os.environ.get("SPORTS_FOLLOW_ENGINE_POSITION_NODES", 32_000_000))  # ...or this much work in it
HASH_MB = int(os.environ.get("SPORTS_FOLLOW_ENGINE_HASH_MB", 128))  # the deep search's tree, kept as it goes
LOOK_HASH_MB = 16  # the looks' engine, cleared before each look
LOOK_SECONDS = 30  # a look still going this long (a stalled machine) is stopped, its score then the machine's
HALT_SECONDS = 5  # an engine that hasn't ended a search this long after it was told to is killed
READY_SECONDS = 30  # ...and one that hasn't answered "uci" or "isready" this long (a wrong path, a wedged start)
SLICE = 1.0  # seconds a deep search runs before the lane looks again: about a look
TURN = 8_000_000  # nodes a deep search goes on for while another game waits to be deepened
DEEP_IDLE = 60  # seconds with nothing to deepen before the deep search's engine is closed (or after it failed, started again)
SESSION = 10 * 60  # the lane hands over like a poll job; the next tick starts a fresh one
IDLE_EXIT = 2 * 60  # ...or ends early with nothing to do
STALE = 5 * 60  # a waiting position this old is from a game nobody polls any more
CACHE_TTL = 24 * 3600
LATEST_TTL = 6 * 3600

WANT = "engine:want"  # game -> {"fen", "at"}: the newest position waiting
QUEUE = "engine:queue"  # game -> the time it may be evaluated from; lowest first
DEEPEN = "engine:deepen"  # game -> when it last had the engine (it stays in through its turn); lowest first
LOCK = "engine-lane"
READING = ("cp", "mate", "depth")


def enabled() -> bool:
    return bool(STOCKFISH) and os.access(STOCKFISH, os.X_OK)


def deepens(event_id: int) -> bool:
    """Whether a fixture's game is deepened past its first look: while someone has the game's match page
    open (watched, the default; on the web or Android, bus.watching_key), always (all) or never (off)."""
    return DEEPENING == "all" or (DEEPENING == "watched" and bus.watched(event_id))


# ---------------------------------------------------------------- the queue


def latest(game: str, r: Any = None) -> dict[str, Any] | None:
    """The game's latest evaluation, from White's side: what it shows ("cp" or "mate", "depth": the
    deepest reading of its position), its position's first look ("look": the same, what alerts go by;
    none where the engine had to stop it), "nodes" (all the work in the position so far), "fen", "at" (when
    it was made), "first" (when its position was first looked at) and "asked" (when a poll first asked)."""
    raw = (r or bus.sync_redis()).get(f"engine:latest:{game}")
    return json.loads(raw) if raw else None


def request(game: str, fen: str, r: Any = None, now: float | None = None, by: object = "", deepen: bool = True) -> None:
    """Ask for this position to be evaluated, for one fixture of the game (`by`: its event; a board two
    followed players share is a fixture of each). It replaces the game's waiting position, if any, and
    keeps that one's place in the queue. Asked again for the position it's on, with `deepen` (deepens()),
    it goes on being deepened (back in line, if a gap in polling or in watching had let it drop out); a
    new position stops that at once, and so does the last fixture asking without `deepen`."""
    r = r or bus.sync_redis()
    now = now or time.time()
    if deepen:
        r.hset(f"engine:deepen:{game}", str(by), now)
        r.expire(f"engine:deepen:{game}", STALE)
    else:
        r.hdel(f"engine:deepen:{game}", str(by))
    done = latest(game, r)
    if done and done["fen"] == fen:
        if not deepened(r, game, now):
            r.zrem(DEEPEN, game)
        elif not deep_enough(cached(r, fen) or done):
            r.zadd(DEEPEN, {game: now}, nx=True)
        return
    r.zrem(DEEPEN, game)
    waiting = json.loads(r.hget(WANT, game) or "null") or {}
    asked = waiting.get("asked", now) if waiting.get("fen") == fen else now
    r.hset(WANT, game, json.dumps({"fen": fen, "at": now, "asked": asked}))
    # The interval counts from when the polls asked for the last position, not from when the lane got to it,
    # so the positions looked at hang on the game's moves and its polls, not on what else the engine was doing.
    r.zadd(QUEUE, {game: max(now, (done.get("asked") or done.get("first") or done["at"]) + GAME_INTERVAL) if done else now}, nx=True)


def drop(game: str, r: Any = None, by: object | None = None) -> None:
    """Stop evaluating a game for one of its fixtures (`by`: nobody there wants its swings any more), or
    for all of them (it's over, or not on: nothing of it waits either). Once no fixture wants it
    deepened, its position isn't; the lane lets go of it before its next slice."""
    r = r or bus.sync_redis()
    if by is None:
        r.delete(f"engine:deepen:{game}")
        r.hdel(WANT, game)
    else:
        r.hdel(f"engine:deepen:{game}", str(by))
    if not deepened(r, game):
        r.zrem(DEEPEN, game)


def deepened(r: Any, game: str, now: float | None = None) -> bool:
    """Whether a fixture of the game has asked for it deepened in the last STALE seconds."""
    now = now or time.time()
    return any(now - float(at) <= STALE for at in (r.hgetall(f"engine:deepen:{game}") or {}).values())


def take(r: Any, now: float) -> tuple[str, str, float] | None:
    """The game that has waited longest among those due, with its newest position and when it was first
    asked for."""
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
                return game, want["fen"], want.get("asked", want["at"])


def take_deeper(r: Any, now: float, besides: str | None = None) -> tuple[str, dict[str, Any]] | None:
    """The game that has waited longest to have the position it's on deepened (besides the one whose turn
    it is), with its latest evaluation. A game stays in this queue through its turn, and leaves it once
    it's off the position (a newer one is waiting, or nobody wants it deepened) or it's deep enough."""
    for game in r.zrangebyscore(DEEPEN, "-inf", now):
        if game == besides:
            continue
        done = latest(game, r)
        if done and on_it(r, game, done["fen"], now) and not deep_enough(cached(r, done["fen"]) or done):
            return game, done
        r.zrem(DEEPEN, game)
    return None


def on_it(r: Any, game: str, fen: str, now: float | None = None) -> bool:
    """Whether a game is still on this position and wanted deepened: nothing newer waiting, a fixture
    asking for it deepened, and its latest evaluation of it."""
    return deepened(r, game, now) and not r.hget(WANT, game) and (latest(game, r) or {}).get("fen") == fen


# Set the game's settled evaluation only if it's still the one this one was settled from; else the one there.
SETTLE = """local held = redis.call('get', KEYS[1])
if (held or '') == ARGV[1] then
  redis.call('set', KEYS[1], ARGV[2], 'EX', ARGV[3])
  return ARGV[2]
end
return held"""


def settled(game: str, ev: dict[str, Any], settle: Callable[[dict[str, Any] | None, dict[str, Any]], dict[str, Any]],
            before: dict[str, Any] | None = None, r: Any = None) -> dict[str, Any] | None:
    """The game's latest evaluation as its sport settles it (Sport.settle), once for the board: every
    fixture of the game takes the same settled evaluation, whichever polls first, so two players' events
    never disagree about a band. `before`, the fixture's own last one, stands in while none is kept.

    Two fixtures' polls can settle at once (each its own job): a settle is kept only if the one it was
    settled from is still the one there, and otherwise made again from that one, so the board's settles
    are one chain. A reading with no score (stopped before it had one) settles nothing. The chain has an
    id ("chain"): a fixture's first evaluation from a chain it didn't start (after a deploy, or a lost
    key) says so ("joined"): its own last one may be stale, so the sport holds it against the board's from
    before this settle instead (Chess.during, settle's "prev")."""
    r = r or bus.sync_redis()
    key = f"engine:settled:{game}"
    raw = r.get(key) or ""
    now: dict[str, Any] | None = None
    for _ in range(5):
        kept = json.loads(raw) if raw else before
        if kept and ev.get("at", 0) <= kept.get("at", 0) or (ev.get("cp") is None and ev.get("mate") is None):
            now = kept  # nothing newer to settle: read before another poll settled a newer one, or no score
            break
        now = {**settle(kept, ev), "chain": (json.loads(raw) if raw else {}).get("chain") or uuid.uuid4().hex[:12]}
        new = json.dumps(now)
        held = r.eval(SETTLE, 1, key, raw, new, LATEST_TTL)
        if held == new:
            break
        raw = held or ""  # another poll settled first: again, from that
    if now is not None and before is not None and before.get("chain") != now.get("chain") and raw:
        now = {**now, "joined": True}
    return now


def needs_lane() -> bool:
    r = bus.sync_redis()
    return enabled() and bool(r.zcard(QUEUE) or r.zcard(DEEPEN)) and not bus.is_locked(LOCK)


def position_key(fen: str) -> str:
    """Positions that differ only in move counters are the same position."""
    return hashlib.sha1(" ".join(fen.split()[:4]).encode()).hexdigest()


def cached(r: Any, fen: str) -> dict[str, Any] | None:
    """This position as far as it's been searched: its deepest reading (cp, mate, depth), its first look
    ("look") and all the nodes searched in it."""
    raw = r.get(f"engine:pos:{position_key(fen)}")
    return json.loads(raw) if raw else None


def keep(r: Any, fen: str, result: dict[str, Any], look: bool = False) -> dict[str, Any]:
    """Add a search of this position to the cache: the deepest reading stays (a shallower one never
    replaces it) and its nodes add up; a first look is the position's look too, the one its alerts go by
    wherever it comes up. Returns what's kept."""
    held = cached(r, fen) or {}
    reading = {k: result.get(k) for k in READING}
    best = reading if not held or (reading["depth"] or 0) > (held.get("depth") or 0) else {k: held.get(k) for k in READING}
    kept = {**best, "nodes": held.get("nodes", 0) + (result.get("nodes") or 0)}
    if first := held.get("look") or (reading if look else None):
        kept["look"] = first
    r.set(f"engine:pos:{position_key(fen)}", json.dumps(kept), ex=CACHE_TTL)
    return kept


def deep_enough(ev: dict[str, Any]) -> bool:
    """Whether a position has had all the work it gets: DEPTH reached, or POSITION_NODES searched."""
    return (ev.get("depth") or 0) >= DEPTH or (ev.get("nodes") or 0) >= POSITION_NODES


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
    """One Stockfish process: one thread, low CPU priority, one search at a time. The lane has two, never
    running at once: one for looks (evaluate), cleared before each, and one for deep searches (search),
    which run only while run() lets them and are stopped (SIGSTOP) in between, holding their place in the
    tree."""

    def __init__(self, path: str = STOCKFISH, hash_mb: int = HASH_MB) -> None:
        self.proc = subprocess.Popen(["nice", "-n", "10", path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.lines: queue.Queue[str | None] = queue.Queue()  # read as they come, so a paused search never blocks the lane
        threading.Thread(target=self._reader, name="stockfish", daemon=True).start()
        self.fen, self.searching = "", False
        self.reading: dict[str, Any] = {}
        self._send("uci")
        self._until("uciok")
        self._send("setoption name Threads value 1")
        self._send(f"setoption name Hash value {hash_mb}")
        self._send("isready")
        self._until("readyok")

    def evaluate(self, fen: str, nodes: int = NODES) -> dict[str, Any]:
        """A look: from a cleared engine (its hash table and histories: ucinewgame), one search to `nodes`
        with no clock, so a position gets the same score however fast the machine is and whatever it
        searched before. Its score, and the depth it reached and the nodes it searched, as Stockfish reports
        them. One still going after LOOK_SECONDS is stopped and says so ("capped"): its score is the
        machine's."""
        self.halt()
        self._send("ucinewgame")
        self._send("isready")
        self._until("readyok")
        self._go(fen, f"nodes {nodes}")
        if not self._ended(LOOK_SECONDS):
            self._stop()
            log.warning("engine: a look at %s ran past %ds and was stopped; its score depends on the machine", fen, LOOK_SECONDS)
            return {**self.reading, "capped": True}
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
            self._stop()
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

    def _ended(self, seconds: float) -> bool:
        """Read the search out, for up to `seconds`; whether it ended."""
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            if (line := self._read(left)) and self._note(line):
                return True
        return False

    def _stop(self) -> None:
        """Tell the search to end now, and read it out. An engine that doesn't end it within HALT_SECONDS
        is stuck: killed, so the lane never waits on it (the next lane starts another)."""
        self._signal(signal.SIGCONT)
        self._send("stop")
        if not self._ended(HALT_SECONDS):
            self.searching = False
            self.proc.kill()
            raise EngineError("the engine didn't end its search")

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
        """Read up to the engine's answer (uciok, readyok). One that doesn't give it within READY_SECONDS is
        stuck: killed, so the lane never waits on it (the next lane starts another)."""
        deadline = time.monotonic() + READY_SECONDS
        while (left := deadline - time.monotonic()) > 0:
            if self._read(left).startswith(prefix):
                return
        self.proc.kill()
        raise EngineError(f"the engine didn't answer {prefix} in {READY_SECONDS} s")


# ---------------------------------------------------------------- the lane


@dataclass
class Deepening:
    """A game's turn at the deep search: its position, searched a slice at a time. Ending the turn early
    (the game moves, or nobody wants it deepened) stops the search; it's searched again at its next turn."""

    game: str
    fen: str
    first: float  # when the position was first looked at
    shown: int  # the depth the game's latest evaluation shows
    look: dict[str, Any] | None  # the position's first look, which stays what the alerts go by
    asked: float | None = None  # when the polls first asked for the position, which the game's next look counts from
    searching: bool = False  # a search of it is under way (paused between slices)
    counted: int = 0  # that search's nodes already added to the position's
    turn: int = 0  # nodes searched in this turn


def run_lane() -> None:
    """Evaluate waiting positions, and in between deepen the ones games are on, until SESSION is up
    or nothing has needed the engine for IDLE_EXIT."""
    if not enabled():
        return
    token = bus.try_lock(LOCK, 120)
    if not token:
        return
    r = bus.sync_redis()
    looks: Engine | None = None  # first looks
    searches: Engine | None = None  # deep searches
    deep: Deepening | None = None
    evaluated = hits = slices = 0
    busy = 0.0
    started = idle_since = deep_since = time.monotonic()  # deep_since: when the deep search's engine last worked, or failed
    deep_fails = 0.0  # when it last failed
    try:
        while time.monotonic() - started < SESSION and not shutdown.stopping.is_set():
            if not bus.extend(LOCK, 120, token):
                break  # the lock has gone (it lapsed while the lane was held up): another lane may have it
            now = time.time()
            if deep is not None and not on_it(r, deep.game, deep.fen):
                try:
                    _end(r, searches, deep)  # the game moved, or nobody wants it deepened: dropped at once
                except (EngineError, OSError) as exc:
                    searches, deep_fails = _lost(searches, exc), time.monotonic()
                deep = None
            spent = 0.0
            if job := take(r, now):
                # A new position goes first, from the cache or a look; a deep search under way stays paused.
                game, fen, asked = job
                done = cached(r, fen)
                if done and done.get("look"):
                    hits += 1
                else:
                    looks = looks or Engine(hash_mb=LOOK_HASH_MB)
                    t = time.monotonic()
                    result = looks.evaluate(fen)
                    spent = time.monotonic() - t
                    evaluated += 1
                    # One the clock stopped is shown, but isn't the position's look: no alert goes by it. One
                    # stopped before it had a score shows nothing.
                    scored = result.get("cp") is not None or result.get("mate") is not None
                    done = keep(r, fen, result, look=not result.get("capped")) if scored else None
                if done is not None:
                    first = time.time()
                    r.set(f"engine:latest:{game}", json.dumps({**done, "fen": fen, "at": first, "first": first, "asked": asked}), ex=LATEST_TTL)
                    if deepened(r, game) and not deep_enough(done) and not r.hget(WANT, game):
                        r.zadd(DEEPEN, {game: first})
            elif time.monotonic() - deep_fails < DEEP_IDLE:
                # Its engine failed a moment ago: looks go on, and deepening after a rest.
                if time.monotonic() - idle_since > IDLE_EXIT:
                    break
                shutdown.sleep(1)
                continue
            else:
                try:
                    if deep is not None and deep.turn >= TURN and take_deeper(r, now, besides=deep.game):
                        _end(r, searches, deep, again=True)  # its turn is over: the game that has waited longest goes next
                        deep = None
                    if deep is None and (waiting := take_deeper(r, now)):
                        game, shown = waiting
                        deep = Deepening(game, shown["fen"], shown.get("first", shown["at"]), shown.get("depth") or 0, shown.get("look"), shown.get("asked"))
                    if deep is None:
                        if searches is not None and time.monotonic() - deep_since > DEEP_IDLE:
                            searches.close()  # nothing to deepen for a while: its memory back
                            searches = None
                        if time.monotonic() - idle_since > IDLE_EXIT:
                            break
                        shutdown.sleep(1)
                        continue
                    searches = searches or Engine()
                    if not deep.searching:
                        searches.search(deep.fen, DEPTH, max(NODES, POSITION_NODES - (cached(r, deep.fen) or {}).get("nodes", 0)))
                        deep.searching, deep.counted = True, 0
                    t = time.monotonic()
                    reading, over = searches.run(SLICE)
                    spent, deep_since = time.monotonic() - t, time.monotonic()
                    slices += 1
                    _note(r, deep, reading)
                    if over:
                        deep = None  # deep enough
                except (EngineError, OSError) as exc:
                    searches, deep_fails = _lost(searches, exc), time.monotonic()
                    if deep is not None and on_it(r, deep.game, deep.fen):
                        r.zadd(DEEPEN, {deep.game: time.time()})  # deepened again once there's an engine for it
                    deep = None
            idle_since = time.monotonic()
            busy += spent
            if spent:
                shutdown.sleep(rest(spent))
    except Exception:
        log.exception("engine lane crashed")
    finally:
        if deep is not None:
            try:
                _end(r, searches, deep, again=True)  # the next lane goes on with it
            except Exception:
                log.exception("engine lane: ending the deep search")
        for engine in (looks, searches):
            if engine is not None:
                engine.close()
        bus.release(LOCK, token)
        wall = time.monotonic() - started
        if evaluated or hits or slices:
            log.info("engine: %d position(s) evaluated, %d from cache, %d deeper slice(s), busy %.0fs of %.0fs (%.0f%% of one core), %d game(s) waiting, %d to deepen",
                     evaluated, hits, slices, busy, wall, 100 * busy / max(wall, 1), r.zcard(QUEUE), r.zcard(DEEPEN))


def _lost(engine: Engine | None, exc: Exception) -> None:
    """The deep search's engine failed (it died, or wedged and was killed): closed, and the lane goes on with
    looks; a new one starts once there's something to deepen, DEEP_IDLE on."""
    log.warning("engine lane: the deep search's engine failed (%s); looks go on, and deepening after a rest", exc)
    if engine is not None:
        engine.close()


def _note(r: Any, deep: Deepening, reading: dict[str, Any]) -> None:
    """A deep search's reading: its new nodes into the position's cache and the turn, and the game's
    latest evaluation once it's deeper, while the game is still on the position and wanted deepened."""
    nodes = reading.get("nodes") or 0
    done = keep(r, deep.fen, {**reading, "nodes": nodes - deep.counted})
    deep.turn += nodes - deep.counted
    deep.counted = nodes
    if (done.get("depth") or 0) > deep.shown and on_it(r, deep.game, deep.fen):
        r.set(f"engine:latest:{deep.game}", json.dumps({"look": deep.look, **done, "fen": deep.fen, "at": time.time(), "first": deep.first, "asked": deep.asked}), ex=LATEST_TTL)
        deep.shown = done["depth"]


def _end(r: Any, engine: Engine | None, deep: Deepening, again: bool = False) -> None:
    """End a game's turn, keeping what its search found. Again: it waits for another turn, if it's still
    on the position and the position isn't deep enough."""
    if engine is not None and deep.searching:
        _note(r, deep, engine.halt())
    if again and on_it(r, deep.game, deep.fen) and not deep_enough(cached(r, deep.fen) or {}):
        r.zadd(DEEPEN, {deep.game: time.time()})
