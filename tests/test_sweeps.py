"""The feed's sweeps without a database (sweeps.py, Sport.video_words, adapters/youtube.py): the words each sport
gives a player's search, the famous sweep's search with no date limit, the streams they're counted as, and the
default caps against the day's quota. Every answer here is made up, in the shape YouTube sends."""

from __future__ import annotations

import math

from sports_follow import candidates, feedstats, metrics, rank, searches, sports, sweeps
from sports_follow.adapters import VIDEOS, budget, youtube
from tests.test_feed_fetch import NOW
from tests.test_feed_metrics import counts, found, measured  # noqa: F401
from tests.test_moment_search import fake, hit  # noqa: F401

SOURCE = VIDEOS["youtube"]


def test_every_sport_gives_a_players_search_its_own_words():
    for sport in sports.ALL:
        assert sport.video_words().strip(), sport.key
    assert sweeps.query(candidates.Followed(1, "cricket", "Shreyas Iyer")) == "Shreyas Iyer cricket"
    assert sweeps.query(candidates.Followed(2, "valorant", "TenZ")) == "TenZ Valorant"
    text = open(sweeps.__file__).read().lower()
    assert not any(f'"{s.key}"' in text for s in sports.ALL) and "youtube" not in text


def test_the_famous_sweep_asks_for_the_most_watched_ever(fake):  # noqa: F811
    fake.found["Shreyas Iyer cricket"] = [hit("iyerfamous1", "Shreyas Iyer's best knock")]
    [up] = SOURCE.search("Shreyas Iyer cricket", None, "IN")
    assert up.video_id == "iyerfamous1"
    params = dict(fake.asked[0].url.params)
    assert "publishedAfter" not in params
    assert params == {"part": "snippet", "type": "video", "q": "Shreyas Iyer cricket", "order": "viewCount", "maxResults": "25",
                      "videoEmbeddable": "true", "videoSyndicated": "true", "regionCode": "IN"}


def test_without_a_key_nothing_is_swept(monkeypatch):
    monkeypatch.setattr(youtube, "key", lambda: None)
    assert sweeps.run(NOW) == {"gap searched": 0, "wide searched": 0, "famous searched": 0, "found": 0, "kept": 0, "refused": 0}  # no database read either


def test_a_sweeps_rows_are_counted_as_its_own_stream():
    assert rank.stream("youtube", {"swept": 4, "stream": "gap"}) == "gap"
    assert rank.stream("youtube", {"swept": 5, "stream": "famous"}) == "famous"
    assert rank.stream("youtube", {"searched_for": 9}) == "search" and rank.stream("youtube", {}) == "channels"
    assert rank.stream("youtube", {"stream": "made-up"}) == "channels"
    assert rank.stream("youtube", {"swept": 6, "stream": "wide"}) == "wide"
    assert {"gap", "wide", "famous"} <= set(metrics.STREAMS)


def test_what_a_sweep_finds_stored_already_counts_as_spent_for_nothing(measured):  # noqa: F811
    feedstats.keep_counted("gap", [found(1, "a"), found(1, "b")], {(1, "a")}, kept=["cricket"])
    assert counts(measured, "sports_follow.feed.matched") == {("cricket", "gap"): 2}
    assert counts(measured, "sports_follow.feed.refused") == {("cricket", "gap", "already_stored"): 1}
    metrics.feed_search("cricket", 100, "famous")
    assert counts(measured, "sports_follow.feed.search_units") == {("cricket", "famous"): 100}


def test_sweeps_leave_the_days_moment_searches_their_room(fake, monkeypatch):  # noqa: F811
    """With no moment search run yet, sweeps leave what the moment searches' gate keeps (a quarter) and a whole cap's
    worth of them (40, each with a read of its results): 2,500 + 4,040 of the 9,000 spendable."""
    monkeypatch.setattr(searches, "spent", lambda since: 0)
    budget.take(youtube.BUDGET, cost=2260)  # 6,740 left: a search would leave 6,640
    assert sweeps._room(SOURCE, NOW)  # pyright: ignore[reportPrivateUsage]
    budget.take(youtube.BUDGET, cost=101)
    assert not sweeps._room(SOURCE, NOW)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(searches, "spent", lambda since: 40)  # the day's moment searches have all run
    assert sweeps._room(SOURCE, NOW)  # pyright: ignore[reportPrivateUsage]


def test_however_full_the_sweeps_caps_the_days_moment_searches_all_run(fake, monkeypatch):  # noqa: F811
    from sports_follow import config

    monkeypatch.setattr(searches, "spent", lambda since: 0)
    swept = 0
    while sweeps._room(SOURCE, NOW):  # pyright: ignore[reportPrivateUsage]
        budget.take(youtube.BUDGET, cost=youtube.SEARCH_COST + 1)
        swept += 1
    assert swept == 24  # under the three caps' 39: on a quiet day at 500 players, the gate binds before them
    for _ in range(config.FEED_MOMENT_SEARCHES):
        assert searches.room(SOURCE)
        budget.take(youtube.BUDGET, cost=youtube.SEARCH_COST + 1)


def test_the_default_caps_share_the_day_with_the_moment_searches():
    """A day's 40 moment searches (4,040 units with their videos.list reads) fit under the 9,000 spendable less the
    quarter their gate keeps for the channel poll and the weekly check; the sweeps' caps (20 gap, 14 wide, 5
    famous) are ceilings, and the gate above holds them to what's left of that."""
    from sports_follow import config

    assert config.FEED_MOMENT_SEARCHES * (youtube.SEARCH_COST + 1) <= youtube.PER_DAY - youtube.MARGIN - searches.KEEP_SHARE * youtube.PER_DAY
    assert (config.FEED_GAP_SEARCHES, config.FEED_WIDE_SEARCHES, math.ceil(config.FEED_FAMOUS_SEARCHES / 7)) == (20, 14, 5)


def test_the_daily_sweep_runs_in_the_feed_lane_then_ranks_what_it_kept(monkeypatch):
    import asyncio

    from sports_follow import bus, curator

    did = []
    monkeypatch.setattr(sweeps, "run", lambda: did.append(("sweep", budget.lane())) or {"gap searched": 2, "kept": 1})
    monkeypatch.setattr(curator, "rank_new", lambda: did.append(("rank", budget.lane())) or {})
    monkeypatch.setattr(bus, "try_lock", lambda name, seconds: "token")
    monkeypatch.setattr(bus, "release", lambda name, token=None: None)
    asyncio.run(curator.sweep({}))
    assert did == [("sweep", "feed"), ("rank", "feed")]
    assert next(j for j in curator.CuratorSettings.cron_jobs if j.name == "cron:sweep").hour == {8, 20}
