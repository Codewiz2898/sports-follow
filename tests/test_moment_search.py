"""The feed's moment searches without a database (searches.py, Sport.search_words, adapters/youtube.py): the words
each sport gives a big moment, the search request and what it costs of the day's quota, and the share the channel
poll and the weekly check keep. Every answer here is made up, in the shape YouTube sends."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from sports_follow import searches, sports
from sports_follow.adapters import VIDEOS, budget, youtube
from sports_follow.sports import Fired
from tests.test_feed_fetch import NOW, Fake

SOURCE = VIDEOS["youtube"]
CHANNEL = "UC" + "b" * 22


def hit(vid: str, title: str, description: str = "", channel: str = CHANNEL) -> dict:
    """One item of search.list, as YouTube sends it."""
    return {
        "kind": "youtube#searchResult", "id": {"kind": "youtube#video", "videoId": vid},
        "snippet": {"publishedAt": "2026-10-19T15:00:00Z", "channelId": channel, "title": title, "description": description, "channelTitle": "A fan",
                    "thumbnails": {"high": {"url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}}, "liveBroadcastContent": "none"},
    }  # fmt: skip


def asked_for(request: httpx.Request) -> str:
    """What a search asked for, without what every search leaves out (youtube.NOT_SHORTS)."""
    return request.url.params["q"].removesuffix(f" {youtube.NOT_SHORTS}")


class Searching(Fake):
    """YouTube, made up, with search.list: what each query finds, but for what its NOT leaves out."""

    def __init__(self):
        super().__init__()
        self.found: dict[str, list[dict]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/youtube/v3/search" and not self.error:
            self.asked.append(request)
            items = self.found.get(asked_for(request), [])
            if request.url.params["q"].endswith(f" {youtube.NOT_SHORTS}"):  # as YouTube's NOT does: what's tagged #shorts, left out
                items = [i for i in items if "#shorts" not in " ".join(str((i.get("snippet") or {}).get(k) or "") for k in ("title", "description")).lower()]
            return httpx.Response(200, json={"kind": "youtube#searchListResponse", "items": items})
        return super().__call__(request)


@pytest.fixture
def fake(monkeypatch):
    made = Searching()
    monkeypatch.setattr(youtube, "_client", httpx.Client(transport=httpx.MockTransport(made)))
    monkeypatch.setattr(youtube, "key", lambda: "test-key")
    return made


def said(sport: str, kind: str, detail: str = "", title: str = "", body: str = "", count: int = 1, game: str = "", competition: str = "", venue: str = "") -> str | None:
    return sports.of(sport).search_words(Fired(kind, detail, title, body, 90, count, game, competition, venue))


# ---------------------------------------------------------------- each sport's words


def test_each_sport_words_its_big_moments_and_leaves_the_rest_unsearched():
    assert said("cricket", "hundred", "100", venue="Lord's, London") == "century Lord's"
    assert said("cricket", "hundred", "i2:200", game="India vs England") == "double century India vs England"
    assert said("cricket", "wickets", "w5", venue="Eden Gardens") == "5 wickets Eden Gardens"
    assert said("cricket", "wickets", "w4", venue="Eden Gardens") is None  # four isn't a haul worth a search
    assert said("cricket", "fifty", "50") is None and said("cricket", "out", "out") is None
    assert said("football", "goal", "goal@101", game="Liverpool vs Everton") == "goal Liverpool vs Everton"
    assert said("football", "goal", "goal@140", count=2, game="Liverpool vs Everton") == "brace Liverpool vs Everton"
    assert said("football", "goal", "goal@170", count=3, game="Liverpool vs Everton") == "hat-trick Liverpool vs Everton"
    assert said("football", "assist") is None and said("football", "red") is None
    assert said("basketball", "points", "40", game="Lakers vs Celtics") == "40 points Lakers vs Celtics"
    assert said("basketball", "points", "30") is None and said("basketball", "triple", game="Lakers vs Celtics") == "triple-double Lakers vs Celtics"
    assert said("chess", "final", title="Gukesh: Won", game="World Championship, game 4") == "wins World Championship, game 4"
    assert said("chess", "final", title="Gukesh: Drew", game="World Championship, game 4") is None
    assert said("tennis", "final", title="Sinner: Won in 3 sets", game="Final", competition="ATP Finals") == "wins final ATP Finals"
    assert said("tennis", "final", title="Sinner: Won in 3 sets", game="Round of 32", competition="Shanghai Masters") is None
    assert said("tennis", "upset", competition="Shanghai Masters") == "upset Shanghai Masters"
    assert said("cs2", "final", title="donk: Spirit win 2–1", body="v Vitality · Grand final", competition="IEM Cologne") == "wins grand final IEM Cologne"
    assert said("cs2", "final", title="donk: Spirit win 2–1", body="v Vitality · Group A", competition="IEM Cologne") is None
    assert said("bgmi", "final", title="Jonathan: GodLike win the lobby · 87 pts", competition="BGIS") == "wins BGIS"
    assert said("golf", "final", title="Rory: Won") is None  # a sport no module knows searches nothing


def test_every_sport_has_its_own_words_or_none():
    """The hook is each sport's: shared code (searches.py) asks it and names no sport."""
    for sport in sports.ALL:
        assert said(sport.key, "start") is None and said(sport.key, "lineup") is None, sport.key
    text = open(searches.__file__).read().lower()
    assert not any(f'"{s.key}"' in text for s in sports.ALL) and "youtube" not in text


# ---------------------------------------------------------------- the search itself


def test_a_search_asks_for_embeddable_syndicated_videos_since_the_moment_by_views_in_india(fake):
    fake.found["Virat Kohli century Lord's"] = [hit("kohli100abc", "Kohli 100 at Lord's 🔥 fan edit"), {"id": {"kind": "youtube#channel", "channelId": CHANNEL}}]
    [up] = SOURCE.search("Virat Kohli century Lord's", datetime(2026, 10, 19, 11, 30, tzinfo=timezone.utc), "IN")
    assert (up.video_id, up.channel_id, up.channel, up.url) == ("kohli100abc", CHANNEL, "A fan", "https://www.youtube.com/watch?v=kohli100abc")
    assert up.published == datetime(2026, 10, 19, 15, tzinfo=timezone.utc) and up.thumbnail
    [request] = fake.asked
    params = dict(request.url.params)
    assert params == {"part": "snippet", "type": "video", "q": "Virat Kohli century Lord's -#shorts", "order": "viewCount", "maxResults": "50",
                      "publishedAfter": "2026-10-19T11:30:00Z", "videoEmbeddable": "true", "videoSyndicated": "true", "regionCode": "IN"}


def test_a_search_spends_a_hundred_units_of_the_days_quota(fake):
    SOURCE.search("Virat Kohli century", NOW, "IN")
    assert budget.room(youtube.BUDGET) == (youtube.PER_DAY - youtube.MARGIN - 100, youtube.PER_DAY)
    assert SOURCE.search_cost == 100 and [s.system for s in searches.searching()] == ["youtube"]


def test_without_a_key_no_source_searches(monkeypatch):
    monkeypatch.setattr(youtube, "key", lambda: None)
    assert searches.searching() == []
    assert searches.queue(NOW) == {"queued": 0, "taken over": 0, "taken back": 0}  # no database read either
    assert searches.run(NOW)["searched"] == 0


def test_a_costly_request_is_refused_whole_and_not_counted():
    tight = budget.Budget("tight", windows=(budget.Window(86400, 1000, margin=100),))
    budget.take(tight, cost=850)
    with pytest.raises(budget.RateLimited):
        budget.take(tight, cost=100)
    assert budget.room(tight) == (50, 1000)
    budget.take(tight, cost=50)
    assert budget.room(tight) == (0, 1000)


def test_searches_leave_a_quarter_of_the_day_to_the_channel_poll_and_the_weekly_check(fake):
    assert searches.room(SOURCE)
    budget.take(youtube.BUDGET, cost=6400)  # 2,600 of the 9,000 spendable left: a search would leave 2,500
    assert searches.room(SOURCE)
    budget.take(youtube.BUDGET)
    assert not searches.room(SOURCE)
    budget.observe(youtube.BUDGET, "", 3000, 3600)  # what the source says is left counts too
    assert budget.room(youtube.BUDGET) == (3000 - youtube.MARGIN, youtube.PER_DAY)


def test_the_default_cap_is_well_inside_the_days_quota():
    """40 searches a day are 4,000 units of the 9,000 spendable; the channel poll's worst day (every feed down, its
    playlists read instead, every half hour) and the weekly check's reads are under 2,000."""
    from sports_follow import config

    channels = len({c for s in sports.ALL for c in s.video_channels})
    poll_worst = channels * 48
    assert config.FEED_MOMENT_SEARCHES == 40 and SOURCE.search_cost == 100
    assert config.FEED_MOMENT_SEARCHES * SOURCE.search_cost + poll_worst + 40 <= youtube.PER_DAY - youtube.MARGIN
    assert config.FEED_MOMENT_SEARCHES * SOURCE.search_cost <= (youtube.PER_DAY - youtube.MARGIN) * (1 - searches.KEEP_SHARE)
