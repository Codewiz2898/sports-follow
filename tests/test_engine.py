"""The chess engine's queue and budget (engine.py): newest position per game, longest wait first,
at most one evaluation per game per interval, and the engine itself when it's installed."""

from __future__ import annotations

import json

import pytest

from sports_follow import engine


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
    assert engine.take(r, now=103) == ("A", E4)
    assert engine.take(r, now=103) == ("B", START)
    assert engine.take(r, now=103) is None


def test_each_game_at_most_once_per_interval():
    r = FakeRedis()
    evaluated(r, "A", E4, at=100)
    engine.request("A", E4, r, now=105)  # already evaluated: nothing to do
    assert engine.take(r, now=105) is None
    engine.request("A", E4E5, r, now=106)
    assert engine.take(r, now=100 + engine.GAME_INTERVAL - 1) is None
    assert engine.take(r, now=100 + engine.GAME_INTERVAL) == ("A", E4E5)


def test_positions_from_games_nobody_polls_are_dropped():
    r = FakeRedis()
    engine.request("A", START, r, now=100)
    assert engine.take(r, now=100 + engine.STALE + 1) is None


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
