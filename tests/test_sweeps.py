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
    iyer, tenz = candidates.Followed(1, "cricket", "Shreyas Iyer"), candidates.Followed(2, "valorant", "TenZ")
    assert sweeps.query(iyer, candidates.phrases([iyer])) == "Shreyas Iyer cricket"
    assert sweeps.query(tenz, candidates.phrases([tenz])) == "TenZ Valorant"
    text = open(sweeps.__file__).read().lower()
    assert not any(f'"{s.key}"' in text for s in sports.ALL) and "youtube" not in text


def named(players: list[candidates.Followed]) -> list[str]:
    names = candidates.phrases(players)
    return [candidates.search_name(p, names) for p in players]


def test_a_search_asks_for_a_player_by_the_name_fans_write():
    bumrah = candidates.Followed(1, "cricket", "Jasprit Jasbirsingh Bumrah", ["jasprit jasbirsingh bumrah", "jasprit bumrah", "Jasprit Bumrah", "Bumrah", "Boom Boom"])
    dhoni = candidates.Followed(2, "cricket", "Mahendra Singh Dhoni", ["mahendra singh dhoni", "MS Dhoni", "Thala", "महेंद्र सिंह धोनी"])
    tenz = candidates.Followed(3, "cricket", "TenZ")
    # The shortest of their names, with capitals; never a surname or a nickname alone, nor one without their name's words.
    assert named([bumrah, dhoni, tenz]) == ["Jasprit Bumrah", "MS Dhoni", "TenZ"]
    assert sweeps.query(bumrah, candidates.phrases([bumrah, dhoni])) == "Jasprit Bumrah cricket"


def test_a_search_never_asks_by_a_name_another_followed_player_shares():
    rohit = candidates.Followed(1, "cricket", "Rohit Gurunath Sharma", ["Rohit Sharma"])
    namesake = candidates.Followed(2, "cricket", "Rohit Sharma")
    assert named([rohit, namesake]) == ["Rohit Gurunath Sharma", "Rohit Sharma"]  # the namesake has nothing better than his own
    # A one-word name of their own, when another player has it too, isn't theirs: their own name it is, as before.
    assert named([candidates.Followed(3, "valorant", "Ace"), candidates.Followed(4, "valorant", "Ace Ventura")]) == ["Ace", "Ace Ventura"]
    assert named([candidates.Followed(5, "cricket", "Virat Kohli", ["Kohli", "King Kohli"])]) == ["Virat Kohli"]  # never an alias as long as their name
    # Namesakes' slugs (pipeline._free_slug), aliases once their hyphens go: never a search, which wants the name.
    salahs = [candidates.Followed(6, "football", "Mohamed Salah", ["mohamed salah"]), candidates.Followed(7, "football", "Mohamed Salah", ["mohamed salah football"]),
              candidates.Followed(8, "football", "Mohamed Salah", ["mohamed salah football q123456"])]
    assert named(salahs) == ["Mohamed Salah"] * 3
    assert named([candidates.Followed(9, "football", "Pedri", ["pedri football"]), candidates.Followed(10, "football", "Pedri Gonzalez")]) == ["Pedri", "Pedri Gonzalez"]


def test_a_name_initial_players_search_asks_for_their_name_without_the_initial():
    """BHA-437: FIDE's "Gukesh D" is "Gukesh" to fans and titles: the search asks for that, while it's his alone among
    those followed. A leading initial stays ("MS Dhoni" is how titles write him)."""
    gukesh = candidates.Followed(1, "chess", "Gukesh D", ["gukesh d", "D Gukesh", "Dommaraju Gukesh"])
    pragg = candidates.Followed(2, "chess", "Praggnanandhaa R", ["R Praggnanandhaa", "Rameshbabu Praggnanandhaa"])
    carlsen = candidates.Followed(3, "chess", "Magnus Carlsen")
    assert named([gukesh, pragg, carlsen]) == ["Gukesh", "Praggnanandhaa", "Magnus Carlsen"]
    assert sweeps.query(gukesh, candidates.phrases([gukesh, pragg])) == "Gukesh chess"
    # Another followed Gukesh: the name alone isn't his, so his own as it is.
    assert named([gukesh, candidates.Followed(4, "chess", "Gukesh Kumar")]) == ["Gukesh D", "Gukesh Kumar"]
    assert named([candidates.Followed(5, "cricket", "M S Dhoni")]) == ["M S Dhoni"]


def test_the_famous_sweep_asks_for_the_most_watched_ever(fake):  # noqa: F811
    fake.found["Shreyas Iyer cricket"] = [hit("iyerfamous1", "Shreyas Iyer's best knock")]
    [up] = SOURCE.search("Shreyas Iyer cricket", None, "IN")
    assert up.video_id == "iyerfamous1"
    params = dict(fake.asked[0].url.params)
    assert "publishedAfter" not in params
    assert params == {"part": "snippet", "type": "video", "q": "Shreyas Iyer cricket -#shorts", "order": "viewCount", "maxResults": "50",
                      "videoEmbeddable": "true", "videoSyndicated": "true", "regionCode": "IN"}


def test_without_a_key_nothing_is_swept(monkeypatch):
    monkeypatch.setattr(youtube, "key", lambda: None)
    assert sweeps.run(NOW) == {"gap searched": 0, "wide searched": 0, "famous searched": 0, "pool searched": 0, "found": 0, "kept": 0, "refused": 0}  # no database read either


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
    metrics.feed_search("cricket", 1, "famous")
    assert counts(measured, "sports_follow.feed.search_units") == {("cricket", "famous"): 1}


def test_sweeps_leave_the_days_moment_searches_their_room(fake, monkeypatch):  # noqa: F811
    """With no moment search run yet, sweeps leave a whole cap's worth of them (40) of the day's 95 spendable searches."""
    monkeypatch.setattr(searches, "spent", lambda since: 0)
    budget.take(youtube.SEARCH_BUDGET, cost=54)  # 41 left: a search would leave 40
    assert sweeps._room(SOURCE, NOW)  # pyright: ignore[reportPrivateUsage]
    budget.take(youtube.SEARCH_BUDGET)
    assert not sweeps._room(SOURCE, NOW)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(searches, "spent", lambda since: 30)  # 10 moment searches still to come
    assert sweeps._room(SOURCE, NOW)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(searches, "spent", lambda since: 40)  # the day's moment searches have all run
    assert sweeps._room(SOURCE, NOW)  # pyright: ignore[reportPrivateUsage]


def test_however_full_the_sweeps_caps_the_days_moment_searches_all_run(fake, monkeypatch):  # noqa: F811
    from sports_follow import config

    monkeypatch.setattr(searches, "spent", lambda since: 0)
    swept = 0
    while sweeps._room(SOURCE, NOW):  # pyright: ignore[reportPrivateUsage]
        budget.take(youtube.SEARCH_BUDGET, cost=youtube.SEARCH_COST)
        swept += 1
    assert swept == 55  # over the three caps' 39: they bind before the gate does
    for _ in range(config.FEED_MOMENT_SEARCHES):
        assert searches.room(SOURCE)
        budget.take(youtube.SEARCH_BUDGET, cost=youtube.SEARCH_COST)
    assert not searches.room(SOURCE)


def test_the_default_caps_share_the_day_with_the_moment_searches():
    """A day's 40 moment searches and the sweeps' caps (20 gap, 14 wide, 5 famous) fit in the day's 95 spendable
    searches together, so on a quiet day the caps bind, not the gate above."""
    from sports_follow import config

    sweeps_a_day = config.FEED_GAP_SEARCHES + config.FEED_WIDE_SEARCHES + math.ceil(config.FEED_FAMOUS_SEARCHES / 7)
    assert (config.FEED_GAP_SEARCHES, config.FEED_WIDE_SEARCHES, math.ceil(config.FEED_FAMOUS_SEARCHES / 7)) == (20, 14, 5)
    assert (config.FEED_MOMENT_SEARCHES + sweeps_a_day) * youtube.SEARCH_COST <= youtube.SEARCHES_PER_DAY - youtube.SEARCH_MARGIN


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
