"""The feed's sweeps against Postgres (sweeps.py): a gap search for each followed player with nothing to watch this
week, the most followed first under the day's cap, once a day and less often while it keeps nothing; the famous
sweep's search with no date limit, each player at most monthly, spread over the week under its own cap; what's
stored already isn't kept again; and each search is recorded with what it found, kept and serves. Skipped without a
throwaway database at alembic head (tests/test_corrections_db.py says how to make one)."""

from __future__ import annotations

import os
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import create_engine, delete, select, text, update

from sports_follow import bus, db as db_module, sweeps
from sports_follow.models import FeedCandidate, FeedSweep, Follow, Player
from tests.test_feed_fetch import NOW, video
from tests.test_feed_fetch_db import Redis
from tests.test_moment_search import Searching, hit

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

DAY = timedelta(days=1)


@pytest.fixture
def world(monkeypatch):
    from sports_follow.adapters import youtube

    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE feed_sweep, feed_search, feed_candidates, moment, event, follow, player_alias, player_identity, player RESTART IDENTITY CASCADE"))
    monkeypatch.setattr(db_module, "SessionLocal", db_module.sessionmaker(bind=engine, expire_on_commit=False))
    made = Searching()
    monkeypatch.setattr(youtube, "_client", httpx.Client(transport=httpx.MockTransport(made)))
    monkeypatch.setattr(youtube, "key", lambda: "test-key")
    monkeypatch.setattr(bus, "sync_redis", lambda: made.redis)
    made.redis = Redis()
    yield made
    engine.dispose()


def player(name: str, sport: str = "cricket", followers: int = 1) -> int:
    with db_module.session() as s:
        p = Player(slug=name.lower().replace(" ", "-"), name=name, sport=sport, teams=[])
        s.add(p)
        s.flush()
        for n in range(followers):
            s.add(Follow(fan_id=f"fan-{n}", player_id=p.id, alert_rules={}))
        return p.id


def served(pid: int, vid: str, published=NOW - timedelta(days=2)) -> None:
    """A video the Feed serves for them, published then."""
    with db_module.session() as s:
        s.add(FeedCandidate(player_id=pid, source="youtube", external_id=vid, url=f"https://www.youtube.com/watch?v={vid}",
                            candidate={"published": published.isoformat()}, features={}, verdict={"about_player": "subject"}, score=0.5))  # fmt: skip


def searched(world: Searching) -> list[tuple[str, str | None]]:
    """Each search asked of YouTube: its query and the date it looked from."""
    return [(r.url.params["q"], r.url.params.get("publishedAfter")) for r in world.asked if r.url.path == "/youtube/v3/search"]


def swept() -> list[tuple]:
    with db_module.session() as s:
        return [tuple(r) for r in s.execute(select(FeedSweep.player_id, FeedSweep.stream, FeedSweep.query, FeedSweep.ran_at, FeedSweep.found, FeedSweep.kept)
                                            .order_by(FeedSweep.id)).all()]


def only(monkeypatch, gap: int, famous: int) -> None:
    monkeypatch.setattr(sweeps, "FEED_GAP_SEARCHES", gap)
    monkeypatch.setattr(sweeps, "FEED_FAMOUS_SEARCHES", famous)


# ---------------------------------------------------------------- the gap search


def test_a_followed_player_with_nothing_to_watch_this_week_gets_a_gap_search_in_their_sports_words(world, monkeypatch):
    only(monkeypatch, gap=20, famous=0)
    iyer = player("Shreyas Iyer", followers=4)
    kohli = player("Virat Kohli", followers=9)
    served(kohli, "kohlirecent")  # covered: nothing searched for him
    old = player("Rohit Sharma")
    served(old, "rohitlastyr", published=NOW - timedelta(days=20))  # served, but not this week: a gap
    player("Nobody Follows", followers=0)
    world.found["Shreyas Iyer cricket"] = [hit("iyerknock01", "Shreyas Iyer 90 vs Australia | highlights"), hit("iyershort01", "Iyer #shorts"),
                                           hit("otherplayer", "Rahul's fifty")]  # fmt: skip
    world.videos.update({v["id"]: v for v in (video("iyerknock01"), video("iyershort01", duration="PT30S"))})
    done = sweeps.run(NOW)
    assert (done["gap searched"], done["found"], done["kept"], done["refused"]) == (2, 2, 1, 1)
    after = (NOW - sweeps.GAP_DAYS).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert searched(world) == [("Shreyas Iyer cricket", after), ("Rohit Sharma cricket", after)]  # the most followed first
    assert swept() == [(iyer, "gap", "Shreyas Iyer cricket", NOW, 2, 1), (old, "gap", "Rohit Sharma cricket", NOW, 0, 0)]
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate).where(FeedCandidate.external_id == "iyerknock01"))
        assert row is not None and row.player_id == iyer and row.verdict is None  # the judge's, next
        sweep_id = s.scalar(select(FeedSweep.id).where(FeedSweep.player_id == iyer))
        assert (row.candidate["stream"], row.candidate["swept"], row.moment_id) == ("gap", sweep_id, None)


def test_the_days_cap_takes_the_most_followed_and_a_search_runs_once_a_day(world, monkeypatch):
    only(monkeypatch, gap=2, famous=0)
    a, b, c = player("Alpha One", followers=1), player("Bravo Two", followers=5), player("Charlie Three", followers=3)
    for name in ("Alpha One", "Bravo Two", "Charlie Three"):
        world.found[f"{name} cricket"] = [hit(f"{name[:5].lower()}vid001", f"{name} best moments")]
        world.videos[f"{name[:5].lower()}vid001"] = video(f"{name[:5].lower()}vid001")
    assert sweeps.run(NOW)["gap searched"] == 2
    assert [p for p, *_ in swept()] == [b, c]
    assert sweeps.run(NOW + timedelta(hours=2))["gap searched"] == 0  # the cap's spent for 24 hours
    only(monkeypatch, gap=20, famous=0)
    assert sweeps.run(NOW + timedelta(hours=3))["gap searched"] == 1  # with room: the one left, not Bravo and Charlie again
    assert [p for p, *_ in swept()] == [b, c, a]


def test_a_search_that_kept_nothing_waits_a_few_days_and_whats_stored_isnt_kept_again(world, monkeypatch):
    only(monkeypatch, gap=20, famous=0)
    iyer = player("Shreyas Iyer")
    world.found["Shreyas Iyer cricket"] = [hit("iyerknock01", "Shreyas Iyer 90 vs Australia")]
    world.videos["iyerknock01"] = video("iyerknock01")
    assert sweeps.run(NOW)["kept"] == 1
    with db_module.session() as s:  # the judge said it's only a mention: still nothing to watch
        s.execute(update(FeedCandidate).values(verdict={"about_player": "mention"}))
    calls = world.api_calls("/videos")
    done = sweeps.run(NOW + DAY)
    assert (done["gap searched"], done["found"], done["kept"]) == (1, 1, 0)  # found again, stored already
    assert world.api_calls("/videos") == calls  # not asked about again
    assert sweeps.run(NOW + 2 * DAY)["gap searched"] == 0  # kept nothing a day ago: waits
    assert sweeps.run(NOW + DAY + sweeps.GAP_BACKOFF + timedelta(minutes=1))["gap searched"] == 1
    assert [(p, kept) for p, _, _, _, _, kept in swept()] == [(iyer, 1), (iyer, 0), (iyer, 0)]


def test_a_sport_turned_off_isnt_swept(world, monkeypatch):
    from sports_follow import sports

    only(monkeypatch, gap=20, famous=35)
    player("Shreyas Iyer")
    player("Magnus Carlsen", sport="chess")
    monkeypatch.setattr(sports, "on", lambda name: sports.family(name) != "cricket")
    sweeps.run(NOW)
    assert searched(world) == [("Magnus Carlsen chess", (NOW - sweeps.GAP_DAYS).strftime("%Y-%m-%dT%H:%M:%SZ")), ("Magnus Carlsen chess", None)]


def test_no_room_left_in_the_days_quota_holds_the_sweeps(world, monkeypatch):
    from sports_follow.adapters import budget, youtube

    only(monkeypatch, gap=20, famous=35)
    player("Shreyas Iyer")
    budget.take(youtube.BUDGET, cost=4000)  # 5,000 of the 9,000 spendable left: a search would leave less than half
    assert sweeps.run(NOW) == {"gap searched": 0, "famous searched": 0, "found": 0, "kept": 0, "refused": 0}
    assert searched(world) == [] and swept() == []


def test_a_removed_player_takes_their_sweeps(world, monkeypatch):
    only(monkeypatch, gap=20, famous=35)
    iyer = player("Shreyas Iyer")
    sweeps.run(NOW)
    assert len(swept()) == 2  # a gap search and the famous sweep's
    with db_module.session() as s:
        s.execute(delete(Player).where(Player.id == iyer))
    assert swept() == []


# ---------------------------------------------------------------- the famous sweep


def test_the_famous_sweep_looks_back_forever_and_fills_the_ones_everyones_seen(world, monkeypatch):
    only(monkeypatch, gap=0, famous=35)
    iyer = player("Shreyas Iyer")
    served(iyer, "iyerrecent1")  # covered this week: no gap search, but the famous sweep still runs
    old = hit("iyerfamous1", "Shreyas Iyer's famous 105 on debut")
    old["snippet"]["publishedAt"] = "2021-11-26T10:00:00Z"
    world.found["Shreyas Iyer cricket"] = [old]
    world.videos["iyerfamous1"] = video("iyerfamous1", views=4_000_000)
    done = sweeps.run(NOW)
    assert (done["famous searched"], done["gap searched"], done["kept"]) == (1, 0, 1)
    assert searched(world) == [("Shreyas Iyer cricket", None)]
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate).where(FeedCandidate.external_id == "iyerfamous1"))
        assert row is not None and row.candidate["stream"] == "famous"


def test_the_famous_sweep_spreads_players_over_the_week_and_refreshes_each_monthly(world, monkeypatch):
    only(monkeypatch, gap=0, famous=7)  # one a day
    a, b, c = player("Alpha One", followers=1), player("Bravo Two", followers=5), player("Charlie Three", followers=3)
    for day in range(5):
        sweeps.run(NOW + day * DAY)
    assert [p for p, *_ in swept()] == [b, c, a]  # the most followed first, a day each, then none due
    sweeps.run(NOW + sweeps.FAMOUS_EVERY + timedelta(minutes=1))
    assert [p for p, *_ in swept()] == [b, c, a, b]  # a month on: again


def test_the_famous_sweeps_weekly_cap_holds_the_rest_to_next_week(world, monkeypatch):
    only(monkeypatch, gap=0, famous=2)  # one a day, two a week
    for n, name in enumerate(("Alpha One", "Bravo Two", "Charlie Three")):
        player(name, followers=3 - n)
    for day in range(7):
        sweeps.run(NOW + day * DAY)
    assert len(swept()) == 2
    sweeps.run(NOW + 7 * DAY + timedelta(minutes=1))  # the first is a week old
    assert [q for _, _, q, *_ in swept()] == ["Alpha One cricket", "Bravo Two cricket", "Charlie Three cricket"]


# ---------------------------------------------------------------- what each sweep found, kept and serves


def test_each_sweep_says_what_it_found_kept_and_serves(world, monkeypatch):
    only(monkeypatch, gap=20, famous=0)
    player("Shreyas Iyer")
    world.found["Shreyas Iyer cricket"] = [hit("iyerknock01", "Shreyas Iyer 90 vs Australia"), hit("iyerknock02", "Shreyas Iyer catch")]
    world.videos.update({v: video(v) for v in ("iyerknock01", "iyerknock02")})
    sweeps.run(NOW)
    with db_module.session() as s:
        s.execute(update(FeedCandidate).where(FeedCandidate.external_id == "iyerknock01").values(score=0.4))
        [row] = sweeps.report(s, NOW - DAY)
    assert row == {"player": "Shreyas Iyer", "stream": "gap", "query": "Shreyas Iyer cricket", "ran_at": NOW, "found": 2, "kept": 2, "served": 1}
