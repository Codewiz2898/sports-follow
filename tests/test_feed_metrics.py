"""The feed's metrics (BHA-326): the funnel counted by sport and stream, once for each player and video; why what
was matched wasn't kept; the judge's verdicts, errors and time; a channel's uploads noted for its yield; and each
sport's coverage, reported as gauges. Labels are sport, stream and reason: never a channel or a player."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from llm_providers import LlmError

from sports_follow import bus, curate, feedstats, metrics
from sports_follow.adapters import Upload, VideoFacts
from sports_follow.candidates import Found

from .test_curate import GOOD, FakeGateway, candidate
from .test_metrics import measured, points  # noqa: F401  (the fixture)

NOW = datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc)
CHANNEL = "UC" + "b" * 22


def counts(reader, name: str) -> dict[tuple[str, ...], float]:
    """A counter's values by its labels' values, in the labels' order."""
    return {tuple(str(v) for v in p.attributes.values()): p.value for p in points(reader).get(name, [])}


def upload(vid: str, published: datetime | None = NOW) -> Upload:
    return Upload(vid, CHANNEL, "Made-up", f"Video {vid}", "", f"https://www.youtube.com/watch?v={vid}", published)


def found(pid: int, vid: str, sport: str = "cricket") -> Found:
    return Found(pid, sport, upload(vid), "kohli", "title")


# ---------------------------------------------------------------- the funnel


def test_a_step_is_counted_by_sport_and_stream_and_a_sport_nobody_knows_is_other(measured):  # noqa: F811
    metrics.feed_step("matched", "channels", ["cricket", "Soccer", "football", "golf", None])
    metrics.feed_step("kept", "search", [])
    assert counts(measured, "sports_follow.feed.matched") == {("cricket", "channels"): 1, ("football", "channels"): 2, ("other", "channels"): 2}
    assert "sports_follow.feed.kept" not in points(measured)  # nothing kept: no series made


@pytest.mark.parametrize(("why", "seconds", "label"), [
    ("not embeddable", 300, "not_embeddable"), ("blocked in IN", 300, "blocked"), ("length", 40, "too_short"), ("length", 3600, "too_long"),
    ("length", None, "too_short"), ("live", None, "live"), ("private", 300, "private"), ("gone", None, "gone"),
])  # fmt: skip
def test_a_hard_filters_refusal_is_labelled_by_its_reason(why, seconds, label):
    facts = VideoFacts(video_id="x", seconds=seconds)
    assert feedstats.reason(why, facts) == label and label in metrics.REFUSALS


def test_a_channels_uploads_count_once_for_each_player_and_video(measured):  # noqa: F811
    # Kohli's "a" is stored already (read again at every poll: not counted again), "b" was refused last week (the
    # same), "c" is refused now for its length, "d" is kept for him and Mandhana, "e" is new for Mandhana only.
    got = [found(1, "a"), found(1, "b"), found(1, "c"), found(1, "d"), found(2, "d"), found(2, "e")]
    feedstats.keep_counted("channels", got, {(1, "a")}, skip={"b"}, why={"c": "length"}, facts={"c": VideoFacts(video_id="c", seconds=40)},
                           kept=["cricket", "cricket", "cricket"])  # fmt: skip
    assert counts(measured, "sports_follow.feed.matched") == {("cricket", "channels"): 4}
    assert counts(measured, "sports_follow.feed.refused") == {("cricket", "channels", "too_short"): 1}
    assert counts(measured, "sports_follow.feed.kept") == {("cricket", "channels"): 3}


def test_what_a_search_finds_that_was_stored_or_refused_already_counts_as_spent_for_nothing(measured):  # noqa: F811
    got = [found(1, "a"), found(1, "b"), found(1, "c")]
    feedstats.keep_counted("search", got, {(1, "a")}, skip={"b"}, kept=["cricket"])
    assert counts(measured, "sports_follow.feed.matched") == {("cricket", "search"): 3}
    assert counts(measured, "sports_follow.feed.refused") == {("cricket", "search", "already_stored"): 1, ("cricket", "search", "refused_before"): 1}
    assert counts(measured, "sports_follow.feed.kept") == {("cricket", "search"): 1}


def test_a_moment_search_counts_its_units_by_sport(measured):  # noqa: F811
    metrics.feed_search("cricket", 100)
    metrics.feed_search("cricket", 100)
    assert counts(measured, "sports_follow.feed.searches") == {("cricket",): 2}
    assert counts(measured, "sports_follow.feed.search_units") == {("cricket",): 200}


# ---------------------------------------------------------------- the judge


@pytest.mark.parametrize(("error", "kind"), [
    (LlmError("bad_request", "Insufficient credits. Add more using https://openrouter.ai/settings/credits", status=402), "out_of_credit"),
    (LlmError("server", "upstream said no", status=402), "out_of_credit"),
    (LlmError("budget_exceeded", "daily budget spent"), "out_of_credit"),
    (LlmError("timeout", "slow"), "timeout"),
    (LlmError("parse", "not json"), "invalid_output"),
    (LlmError("network", "refused"), "unavailable"),
])  # fmt: skip
def test_a_failed_model_call_is_told_by_kind(error, kind):
    assert curate.error_kind(error) == kind and kind in metrics.JUDGE_ERRORS


def test_the_judges_verdicts_errors_and_time_are_counted(measured, monkeypatch):  # noqa: F811
    from sports_follow import config

    monkeypatch.setattr(config, "CURATE_AI", True)
    items = [candidate(url="https://example.test/ad", text="#ad"), candidate(url="https://example.test/1"), candidate(url="https://example.test/2")]
    out_of_credit = LlmError("bad_request", "Insufficient credits", status=402)
    answers = iter([GOOD, out_of_credit])

    class Gateway:
        def complete(self, **request: Any) -> Any:
            answer = next(answers)
            return FakeGateway(answer).complete(**request)

    verdicts = curate.judge_many(items, Gateway(), workers=1)  # type: ignore[arg-type]
    assert [v.stage if v else None for v in verdicts] == ["rules", "model", None]
    assert counts(measured, "sports_follow.feed.verdicts") == {("no", "true", "false", "rules"): 1, ("subject", "false", "false", "model"): 1}
    assert counts(measured, "sports_follow.feed.judge_errors") == {("out_of_credit",): 1}
    assert {p.attributes["stage"]: p.count for p in points(measured)["sports_follow.feed.judge.duration"]} == {"rules": 1, "model": 1, "failed": 1}


def test_a_model_turned_off_is_its_own_kind(measured, monkeypatch):  # noqa: F811
    from sports_follow import config

    monkeypatch.setattr(config, "CURATE_AI", False)
    assert curate.judge_many([candidate()]) == [None]
    assert counts(measured, "sports_follow.feed.judge_errors") == {("off",): 1}


def test_the_curators_start_counts_zero_for_what_the_alerts_watch(measured, monkeypatch):  # noqa: F811
    from sports_follow import sports

    monkeypatch.setattr("sports_follow.adapters.videos", lambda: [object()])
    metrics.feed_seed()
    kept = counts(measured, "sports_follow.feed.kept")
    assert set(kept) == {(s.key, stream) for s in sports.ENABLED for stream in metrics.STREAMS} and set(kept.values()) == {0}
    assert counts(measured, "sports_follow.feed.judge_errors") == {(k,): 0 for k in metrics.JUDGE_ERRORS}


def test_without_a_video_source_no_kept_series_is_started(measured, monkeypatch):  # noqa: F811
    monkeypatch.setattr("sports_follow.adapters.videos", lambda: [])
    metrics.feed_seed()
    assert "sports_follow.feed.kept" not in points(measured)


# ---------------------------------------------------------------- coverage, as gauges


def test_coverage_is_reported_by_sport_until_the_next_poll_says_otherwise(measured):  # noqa: F811
    metrics.feed_coverage({"cricket": (4, 1), "chess": (2, 0)})

    def gauge(name: str) -> dict[str, float]:
        return {p.attributes["sport"]: p.value for p in points(measured).get(name, [])}

    assert gauge("sports_follow.feed.followed") == {"cricket": 4, "chess": 2}
    assert gauge("sports_follow.feed.covered") == {"cricket": 1, "chess": 0}
    assert gauge("sports_follow.feed.coverage") == {"cricket": 0.25, "chess": 0.0}
    metrics.feed_coverage({"cricket": (4, 4)})
    assert gauge("sports_follow.feed.coverage") == {"cricket": 1.0}  # chess, no longer in it, isn't reported


# ---------------------------------------------------------------- a channel's uploads, for its yield


class Redis:
    """The Redis calls the yield makes, in memory: sets, a hash, and their expiry."""

    def __init__(self) -> None:
        self.sets: dict[str, set[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.expiry: dict[str, int] = {}
        self.queued: list[Any] = []

    def pipeline(self) -> Redis:
        return self

    def sadd(self, key: str, *members: str) -> None:
        def add() -> int:
            have = self.sets.setdefault(key, set())
            new = set(members) - have
            have.update(new)
            return len(new)

        self.queued.append(add)

    def expireat(self, key: str, at: int) -> None:
        self.queued.append(lambda: self.expiry.__setitem__(key, at) or True)

    def hset(self, key: str, field: str, value: str) -> None:
        self.queued.append(lambda: self.hashes.setdefault(key, {}).__setitem__(field, value) or 1)

    def scard(self, key: str) -> None:
        self.queued.append(lambda: len(self.sets.get(key, ())))

    def hmget(self, key: str, fields: list[str]) -> None:
        self.queued.append(lambda: [self.hashes.get(key, {}).get(f) for f in fields])

    def execute(self) -> list[Any]:
        done, self.queued = [f() for f in self.queued], []
        return done


def test_a_channels_new_uploads_are_counted_once_and_noted_by_the_day_they_were_published(measured, monkeypatch):  # noqa: F811
    redis = Redis()
    monkeypatch.setattr(bus, "sync_redis", lambda: redis)
    uploads = [upload("new1"), upload("new2", NOW - timedelta(days=2)), upload("old", NOW - timedelta(days=40))]
    named = [Found(1, "cricket", uploads[0], "kohli", "title")]
    assert feedstats.read_channel(CHANNEL, {"cricket"}, uploads, named, NOW) == 2  # the 40-day-old one is old news
    assert feedstats.read_channel(CHANNEL, {"cricket"}, uploads, named, NOW + timedelta(minutes=30)) == 0  # read again: nothing new
    assert counts(measured, "sports_follow.feed.uploads") == {("cricket", "channels"): 2}
    assert redis.sets == {
        f"feed:yield:{CHANNEL}:2026-10-21:read": {"new1"}, f"feed:yield:{CHANNEL}:2026-10-21:matched": {"new1"},
        f"feed:yield:{CHANNEL}:2026-10-19:read": {"new2"},
    }  # fmt: skip
    # Each day's sets go a day after the yield stops counting it.
    assert redis.expiry[f"feed:yield:{CHANNEL}:2026-10-19:read"] == int(datetime(2026, 11, 19, tzinfo=timezone.utc).timestamp())
    assert redis.hashes == {"feed:yield:last": {CHANNEL: NOW.isoformat()}}


def test_an_esports_channel_counts_for_its_game_not_its_group_too(measured, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bus, "sync_redis", lambda: Redis())
    feedstats.read_channel(CHANNEL, {"valorant", "esports"}, [upload("v1")], [], NOW)
    assert counts(measured, "sports_follow.feed.uploads") == {("valorant", "channels"): 1}


def test_without_redis_a_channels_uploads_arent_noted_and_the_poll_goes_on(measured, monkeypatch):  # noqa: F811
    def down() -> Any:
        raise ConnectionError("no redis")

    monkeypatch.setattr(bus, "sync_redis", down)
    assert feedstats.read_channel(CHANNEL, {"cricket"}, [upload("v1")], [], NOW) == 0
    assert "sports_follow.feed.uploads" not in points(measured)


def test_the_feed_dashboard_reads_only_metrics_the_app_sends():
    """deploy/grafana/dashboards/feed.json (apply-dashboards.sh): every metric it reads is one the app sends, by
    the name Grafana Cloud stores it under."""
    import json
    import re
    from pathlib import Path

    from .test_metrics import FEED_COUNTERS, FEED_GAUGES

    board = json.loads((Path(__file__).resolve().parent.parent / "deploy" / "grafana" / "dashboards" / "feed.json").read_text())
    exprs = [t["expr"] for p in board["panels"] for t in p.get("targets", []) if p["datasource"]["type"] == "prometheus"]
    read = {m for e in exprs for m in re.findall(r"\b(sports_follow_[a-z_]+)\{", e)}
    sources = {"sports_follow_source_used", "sports_follow_source_allowance", "sports_follow_source_requests_this_hour"}
    assert read <= FEED_COUNTERS | FEED_GAUGES | sources | {"sports_follow_feed_judge_duration_seconds_bucket"}, read - FEED_COUNTERS - FEED_GAUGES
    assert {f"sports_follow_feed_{step}_total" for step in metrics.FEED_STEPS} <= read  # every step of the funnel is on it
    assert len({p["id"] for p in board["panels"]}) == len(board["panels"])
