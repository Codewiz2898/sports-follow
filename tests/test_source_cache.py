"""The sources' shared cache (adapters/cache.py): what one process read, another takes from Redis, each
judging it by its own ttl; nothing kept for a ttl of 0; one request for many threads asking at once; and
a Redis that's down costs the sharing, never a read. Every response here is made up."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import zlib
from types import SimpleNamespace
from typing import Any

import pytest

from sports_follow.adapters import cache, espn, lichess_chess, liquipedia, pandascore, sportmonks
from sports_follow.adapters.base import AdapterError


class Source:
    """A made-up source: each fetch says how many came before it."""

    def __init__(self) -> None:
        self.fetched = 0

    def __call__(self) -> dict[str, Any]:
        self.fetched += 1
        return {"data": {"id": 1, "read": self.fetched}}


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1_800_000_000.0}
    monkeypatch.setattr(time, "time", lambda: now["t"])
    return now


def _key(namespace: str, key: str) -> str:
    return f"src:{namespace}:{hashlib.sha1(key.encode()).hexdigest()}"


def test_what_one_process_read_another_takes_from_redis():
    api, worker = cache.Cache("example"), cache.Cache("example")  # one source's cache, in two processes
    source = Source()
    assert api.read("/players/7", 3600, source) == {"data": {"id": 1, "read": 1}}
    assert worker.read("/players/7", 3600, source) == {"data": {"id": 1, "read": 1}} and source.fetched == 1
    assert "/players/7" in worker.entries  # its own copy from then on: no round trip for the next read
    assert cache.Cache("another").read("/players/7", 3600, source)["data"]["read"] == 2  # another source's is its own


def test_freshness_is_each_callers_ttl_against_when_it_was_fetched(clock):
    api, worker = cache.Cache("example"), cache.Cache("example")
    source = Source()
    api.read("/fixtures/1", 5, source)  # a live poll
    clock["t"] += 60
    assert worker.read("/fixtures/1", 24 * 3600, source)["data"]["read"] == 1  # a final read takes the minute-old copy
    assert api.read("/fixtures/1", 5, source)["data"]["read"] == 2  # the next live poll doesn't
    clock["t"] += 60
    # The worker's copy is as old as its fetch (two minutes), not as its read from Redis (one): too old for
    # 100 s, so the API's newer one in Redis is taken.
    assert worker.read("/fixtures/1", 100, source)["data"]["read"] == 2 and source.fetched == 2


def test_an_older_read_that_finishes_late_never_replaces_a_newer_one(clock):
    # The API's match page asks for a match just before full time and its answer is slow; the worker's live
    # poll asks a moment later, sees full time and is kept first. The slower, older answer must not replace
    # it: a final read (a refresh after a deploy) would take the in-play state for a day.
    api, worker = cache.Cache("example"), cache.Cache("example")

    def full_time() -> dict[str, Any]:
        clock["t"] += 0.3
        return {"data": {"state": "FT"}}

    def in_play() -> dict[str, Any]:
        clock["t"] += 0.2
        poll = threading.Thread(target=worker.read, args=("/fixtures/1", 5, full_time))  # another process
        poll.start()
        poll.join(5)
        clock["t"] += 0.9
        return {"data": {"state": "INPLAY_2ND_HALF"}}

    assert api.read("/fixtures/1", 30, in_play)["data"]["state"] == "INPLAY_2ND_HALF"  # what the API itself was told
    assert cache.Cache("example").read("/fixtures/1", 24 * 3600, Source()) == {"data": {"state": "FT"}}
    assert api.read("/fixtures/1", 24 * 3600, Source()) == {"data": {"state": "FT"}}  # and the API from then on


def test_a_fetch_that_waits_for_its_turn_is_as_old_as_its_request(clock):
    # A source taking one request at a time (Lichess): the wait for the line doesn't age the answer.
    reads = cache.Cache("example")

    def queued() -> dict[str, Any]:
        clock["t"] += 4  # behind other requests
        cache.sending()
        clock["t"] += 0.5
        return {"data": "made up"}

    reads.read("/broadcast/round/1", 5, queued)
    assert reads.entries["/broadcast/round/1"][0] == clock["t"] - 0.5
    assert reads.read("/broadcast/round/1", 5, Source()) == {"data": "made up"}  # still fresh for a 5 s ttl


def test_a_ttl_of_0_is_fetched_every_time_and_never_kept(source_cache):
    reads, source = cache.Cache("example"), Source()
    reads.read("/livescores", 0, source)
    reads.read("/livescores", 0, source)
    assert source.fetched == 2 and reads.entries == {} and source_cache.data == {}


def test_threads_asking_for_one_cold_key_send_one_request():
    # A card refresh reads its recent finals side by side (PR #127): two of them may be one match.
    reads = cache.Cache("example")
    started, release = threading.Event(), threading.Event()
    calls: list[int] = []

    def fetch() -> dict[str, Any]:
        calls.append(1)
        started.set()
        release.wait(5)
        return {"data": "made up"}

    results: list[Any] = []
    threads = [threading.Thread(target=lambda: results.append(reads.read("/fixtures/9", 3600, fetch))) for _ in range(5)]
    for thread in threads:
        thread.start()
    assert started.wait(5)
    time.sleep(0.1)  # the other four reach the key's line meanwhile
    release.set()
    for thread in threads:
        thread.join(5)
    assert len(calls) == 1 and results == [{"data": "made up"}] * 5 and reads._flights == {}


def test_different_keys_are_fetched_side_by_side():
    reads = cache.Cache("example")
    both = threading.Barrier(2, timeout=5)  # broken unless the two fetches are under way at once

    def fetch() -> dict[str, Any]:
        both.wait()
        return {"data": "made up"}

    results: list[Any] = []

    def read(key: str) -> None:
        try:
            results.append(reads.read(key, 3600, fetch))
        except threading.BrokenBarrierError as exc:
            results.append(exc)

    threads = [threading.Thread(target=read, args=(f"/fixtures/{i}",)) for i in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert results == [{"data": "made up"}] * 2


def test_a_failed_fetch_keeps_nothing(source_cache):
    reads, source = cache.Cache("example"), Source()

    def refused() -> dict[str, Any]:
        raise AdapterError("made up: the source said no")

    with pytest.raises(AdapterError):
        reads.read("/players/7", 3600, refused)
    assert reads.entries == {} and source_cache.data == {} and reads._flights == {}
    assert reads.read("/players/7", 3600, source)["data"]["read"] == 1  # the next read asks again


def test_a_redis_outage_never_fails_a_read(monkeypatch, clock, source_cache):
    class Down:
        calls = 0

        def get(self, key: str) -> tuple[float, bytes] | None:
            Down.calls += 1
            raise ConnectionError("made up: no route to the cache")

        def put(self, key: str, at: float, doc: bytes, ex: int) -> bool:
            Down.calls += 1
            raise ConnectionError("made up: no route to the cache")

    monkeypatch.setattr(cache, "STORE", Down())
    reads, source = cache.Cache("example"), Source()
    assert reads.read("/players/7", 3600, source)["data"]["read"] == 1
    assert reads.read("/players/7", 3600, source)["data"]["read"] == 1  # this process's copy still serves
    assert cache.Cache("example").read("/players/7", 3600, source)["data"]["read"] == 2  # another process asks the source
    assert Down.calls == 1  # and Redis is left alone for RETRY seconds after it failed
    clock["t"] += cache.RETRY + 1
    monkeypatch.setattr(cache, "STORE", source_cache)  # back
    assert cache.Cache("example").read("/players/7", 3600, source)["data"]["read"] == 3
    assert list(source_cache.data) == [_key("example", "/players/7")]


def test_kept_in_redis_compressed_under_the_sources_name_with_an_expiry(source_cache, clock):
    reads = cache.Cache("example")
    reads.read("/fixtures/1", 5, Source())
    reads.read("/seasons/2", 7 * 24 * 3600, Source())
    live, season = _key("example", "/fixtures/1"), _key("example", "/seasons/2")
    at, doc, _ = source_cache.data[live]
    assert at == clock["t"] and json.loads(zlib.decompress(doc)) == {"data": {"id": 1, "read": 1}}
    # A live poll's copy outlives its 5 s, for a caller with a longer ttl; a longer ttl keeps it longer.
    assert source_cache.expiry == {live: cache.KEEP, season: 7 * 24 * 3600}
    clock["t"] += cache.KEEP + 1
    assert source_cache.get(live) is None and source_cache.get(season) is not None


@pytest.mark.parametrize("module", [sportmonks, pandascore, liquipedia, espn, lichess_chess], ids=lambda m: m.__name__.rsplit(".", 1)[1])
def test_each_source_keeps_its_answers_under_its_budgets_name(module):
    assert module._cache.namespace == module.BUDGET.name


def test_a_deploy_doesnt_empty_what_a_source_answered(monkeypatch, source_budgets):
    # The worker read a player a second after the API had: each process asked Sportmonks, about a second
    # from the server each time. Now the second read, and any after a deploy, is Redis's.
    sent: list[str] = []

    def get(url: str, params: Any = None, headers: Any = None) -> SimpleNamespace:
        sent.append(url)
        return SimpleNamespace(status_code=200, headers={}, json=lambda: {"data": {"id": 7, "display_name": "Made Up"}})

    monkeypatch.setattr(sportmonks, "token", lambda: "example")
    monkeypatch.setattr(sportmonks._client, "get", get)
    first = sportmonks.get_json("/football/players/7", 3 * 3600, include="teams")
    cache.forget()  # a deploy: every process starts with nothing of its own
    assert sportmonks.get_json("/football/players/7", 3 * 3600, include="teams") == first and len(sent) == 1
    spent = source_budgets.keys("budget:use:sportmonks:*:sent:h*")
    assert [source_budgets.get(key) for key in spent] == [1]  # and only the request sent was counted


def test_the_redis_store_writes_through_its_script_and_reads_the_hash():
    # The calls production makes, to a stand-in for redis-py's client: the script gets the key, the request's
    # time exactly, the document and the expiry; a key with no copy (or half of one) is a miss.
    asked: list[tuple[list[str], list[Any]]] = []

    class Client:
        def register_script(self, script: str) -> Any:
            assert "HSET" in script and "EXPIRE" in script
            return lambda keys, args: asked.append((keys, args)) or 1

        def hmget(self, key: str, *fields: str) -> list[bytes | None]:
            assert fields == ("at", "doc")
            return [b"1800000000.25", b"made up"] if key == "src:example:kept" else [None, None]

    store = cache.RedisStore()
    store._client, store._put = Client(), Client().register_script(cache._PUT)
    assert store.put("src:example:kept", 1_800_000_000.123456, b"made up", 86400) is True
    assert asked == [(["src:example:kept"], ["1800000000.123456", b"made up", 86400])]
    assert store.get("src:example:kept") == (1_800_000_000.25, b"made up")
    assert store.get("src:example:missing") is None
