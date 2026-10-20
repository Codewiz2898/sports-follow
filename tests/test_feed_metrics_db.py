"""The feed's metrics against Postgres (BHA-326): a poll's funnel, counted once for each player and video; each
sport's coverage (followed players with a video the Feed would serve this week); and a channel's yield for the
admin's Feed tab. Skipped without a throwaway database at alembic head (tests/test_corrections_db.py says how)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sports_follow import db as db_module
from sports_follow import candidates, feedstats
from sports_follow.models import FeedCandidate
from tests.test_feed_fetch import CHANNEL, NOW, feed, video
from tests.test_feed_fetch_db import pytestmark, player, world  # noqa: F401  (the marker and the fixture)
from tests.test_feed_metrics import Redis, counts
from tests.test_metrics import measured  # noqa: F401  (the fixture)


class Both(Redis):
    """The yield's Redis calls, and the few the fetch makes (what it refused, remembered a while)."""

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[str, str] = {}

    def mget(self, keys: list[str]) -> list[str | None]:
        return [self.data.get(k) for k in keys]

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value


def test_a_polls_funnel_counts_each_player_and_video_once(world, measured):  # noqa: F811
    world.redis = Both()
    player("Virat Kohli")
    player("Jasprit Bumrah")
    world.feeds[CHANNEL] = feed(
        ("kohlibumrah", "Kohli and Bumrah on the tour", "Analysis"),
        ("kohlishort0", "Kohli's best shot", "#shorts"),
        ("bumrahblock", "Bumrah's yorkers", ""),
        ("nobody00000", "Pitch report", "Weather and the toss"),
    )
    world.videos.update({v["id"]: v for v in (video("kohlibumrah"), video("kohlishort0", duration="PT40S"), video("bumrahblock", region={"blocked": ["IN"]}))})
    candidates.poll(NOW)
    candidates.poll(NOW + timedelta(minutes=30))  # the same uploads again: nothing counted twice
    assert counts(measured, "sports_follow.feed.uploads") == {("cricket", "channels"): 4}
    assert counts(measured, "sports_follow.feed.matched") == {("cricket", "channels"): 4}
    assert counts(measured, "sports_follow.feed.kept") == {("cricket", "channels"): 2}
    assert counts(measured, "sports_follow.feed.refused") == {("cricket", "channels", "too_short"): 1, ("cricket", "channels", "blocked"): 1}
    with db_module.session() as s:
        got = feedstats.channel_yield(s, [CHANNEL, "UC" + "z" * 22], [CHANNEL, "UC" + "z" * 22], NOW)
    assert got[CHANNEL] == {"read": 4, "matched": 3, "kept": 1, "served": 0, "last_upload": "2026-10-19T12:00:00+00:00"}
    assert got["UC" + "z" * 22] == {"read": 0, "matched": 0, "kept": 0, "served": 0, "last_upload": None}


def row(pid: int, vid: str, published: Any, score: float | None = 0.5, review: str | None = None, channel: str = CHANNEL, fetched: Any = NOW) -> None:
    with db_module.session() as s:
        s.add(FeedCandidate(player_id=pid, source="youtube", external_id=vid, url=f"https://www.youtube.com/watch?v={vid}", fetched_at=fetched, checked_at=fetched,
                            candidate={"published": published.isoformat() if published else None, "channel_id": channel, "sport": "cricket"},
                            features={}, score=score, review=review))  # fmt: skip


def test_coverage_counts_followed_players_with_a_servable_video_this_week(world):  # noqa: F811
    covered = player("Virat Kohli")
    old = player("Jasprit Bumrah")
    hidden = player("Smriti Mandhana")
    judged_out = player("Rohit Sharma")
    pinned = player("Shubman Gill")
    player("Magnus Carlsen", sport="chess")
    player("Nobody Followed", followed=False)
    player("Rory McIlroy", sport="golf")  # a sport no module knows: no channel could cover him
    row(covered, "fresh", NOW - timedelta(days=2))
    row(old, "lastmonth", NOW - timedelta(days=20))
    row(hidden, "hidden", NOW - timedelta(days=1), review="hidden")
    row(judged_out, "promo", NOW - timedelta(days=1), score=None)
    row(pinned, "pinned", NOW - timedelta(days=40), score=10.5, review="pinned")
    with db_module.session() as s:
        assert feedstats.coverage(s, NOW) == {"cricket": (5, 2), "chess": (1, 0)}


def test_a_channels_kept_and_served_count_each_video_once_over_thirty_days(world):  # noqa: F811
    a, b = player("Virat Kohli"), player("Jasprit Bumrah")
    row(a, "both", NOW, fetched=NOW - timedelta(days=1))
    row(b, "both", NOW, fetched=NOW - timedelta(days=1))  # one video, two players: one kept, one served
    row(a, "held", NOW, score=None, fetched=NOW - timedelta(days=3))
    row(a, "ancient", NOW, fetched=NOW - timedelta(days=45))  # before the yield's window
    with db_module.session() as s:
        got = feedstats.channel_yield(s, [CHANNEL], [CHANNEL], NOW)
    assert (got[CHANNEL]["kept"], got[CHANNEL]["served"]) == (2, 1)


def test_judged_passed_and_newly_served_are_counted_by_stream(world, measured, monkeypatch):  # noqa: F811
    from sports_follow import config, curate, rank
    from tests.test_curate import GOOD, FakeGateway

    kohli = player("Virat Kohli")
    with db_module.session() as s:
        for vid, kept in (("edit", {"title": "Kohli's hundred, the edit"}), ("promo", {"title": "Kohli bat review", "paid": True}),
                          ("searched", {"title": "Kohli's hundred", "searched_for": 7})):
            s.add(FeedCandidate(player_id=kohli, source="youtube", external_id=vid, url=f"https://www.youtube.com/watch?v={vid}", fetched_at=NOW, checked_at=NOW,
                                candidate={**kept, "channel_id": CHANNEL, "sport": "cricket", "published": NOW.isoformat()}, features={}))  # fmt: skip
    monkeypatch.setattr(config, "CURATE_AI", True)
    monkeypatch.setattr(curate, "gateway", lambda: FakeGateway(GOOD, GOOD))
    assert rank.judge(NOW)["judged"] == 3
    assert counts(measured, "sports_follow.feed.judged") == {("cricket", "channels"): 2, ("cricket", "search"): 1}
    assert counts(measured, "sports_follow.feed.passed") == {("cricket", "channels"): 1, ("cricket", "search"): 1}  # the paid one doesn't
    rank.rescore(NOW)
    rank.rescore(NOW + timedelta(minutes=30))  # served already: not counted again
    assert counts(measured, "sports_follow.feed.served") == {("cricket", "channels"): 1, ("cricket", "search"): 1}
