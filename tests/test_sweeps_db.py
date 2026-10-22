"""The feed's sweeps against Postgres (sweeps.py): a gap search for each followed player with nothing to watch this
week, the most followed first under the day's cap, once a day and less often while it keeps nothing; the wide
sweep's for every followed player, weekly, under its own cap, keeping only what the gap search hadn't; the famous
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
from sports_follow.models import Account, FeedCandidate, FeedSweep, Follow, Player, PlayerAlias
from tests.test_feed_fetch import NOW, listing, video
from tests.test_feed_metrics import counts, measured  # noqa: F401
from tests.test_feed_fetch_db import Redis
from tests.test_moment_search import CHANNEL as SEARCHED, Searching, asked_for, hit

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

DAY = timedelta(days=1)


@pytest.fixture
def world(monkeypatch):
    from sports_follow.adapters import youtube

    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE feed_sweep, feed_search, feed_candidates, moment, event, follow, player_alias, player_identity, player, account RESTART IDENTITY CASCADE"))
    monkeypatch.setattr(db_module, "SessionLocal", db_module.sessionmaker(bind=engine, expire_on_commit=False))
    made = Searching()
    monkeypatch.setattr(youtube, "_client", httpx.Client(transport=httpx.MockTransport(made)))
    monkeypatch.setattr(youtube, "key", lambda: "test-key")
    listing(monkeypatch, SEARCHED)
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
    return [(asked_for(r), r.url.params.get("publishedAfter")) for r in world.asked if r.url.path == "/youtube/v3/search"]


def swept() -> list[tuple]:
    with db_module.session() as s:
        return [tuple(r) for r in s.execute(select(FeedSweep.player_id, FeedSweep.stream, FeedSweep.query, FeedSweep.ran_at, FeedSweep.found, FeedSweep.kept)
                                            .order_by(FeedSweep.id)).all()]


def only(monkeypatch, gap: int, famous: int, wide: int = 0, pool: int = 0) -> None:
    monkeypatch.setattr(sweeps, "FEED_GAP_SEARCHES", gap)
    monkeypatch.setattr(sweeps, "FEED_WIDE_SEARCHES", wide)
    monkeypatch.setattr(sweeps, "FEED_FAMOUS_SEARCHES", famous)
    monkeypatch.setattr(sweeps, "FEED_POOL_SEARCHES", pool)


# ---------------------------------------------------------------- the gap search


def test_a_followed_player_with_nothing_to_watch_this_week_gets_a_gap_search_in_their_sports_words(world, monkeypatch):
    only(monkeypatch, gap=20, famous=0)
    iyer = player("Shreyas Iyer", followers=4)
    kohli = player("Virat Kohli", followers=9)
    served(kohli, "kohlirecent")  # covered: nothing searched for him
    old = player("Rohit Sharma")
    served(old, "rohitlastyr", published=NOW - timedelta(days=20))  # served, but not this week: a gap
    player("Nobody Follows", followers=0)
    world.found["Shreyas Iyer cricket"] = [hit("iyerknock01", "Shreyas Iyer 90 vs Australia | highlights"), hit("iyershort01", "Iyer 🔥"),
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


def test_a_sweep_asks_for_the_player_by_the_name_fans_write_but_never_one_another_player_shares(world, monkeypatch):
    only(monkeypatch, gap=20, famous=0)
    bumrah, rohit, namesake = player("Jasprit Jasbirsingh Bumrah", followers=3), player("Rohit Gurunath Sharma", followers=2), player("Rohit Sharma")
    with db_module.session() as s:
        s.add_all([PlayerAlias(alias="jasprit-bumrah", player_id=bumrah), PlayerAlias(alias="rohit-sharma-cricket", player_id=rohit)])
        s.add(PlayerAlias(alias="rohit-sharma", player_id=rohit))  # "Rohit Sharma" is the namesake's own name too: never his search
    world.found["jasprit bumrah cricket"] = [hit("bumrahyork1", "Jasprit Bumrah's yorkers"), hit("bumrahfull1", "Jasprit Jasbirsingh Bumrah profile")]
    world.videos.update({v: video(v) for v in ("bumrahyork1", "bumrahfull1")})
    assert sweeps.run(NOW)["kept"] == 2  # matched by any of his names, as before
    assert [q for q, _ in searched(world)] == ["jasprit bumrah cricket", "Rohit Gurunath Sharma cricket", "Rohit Sharma cricket"]
    assert [p for p, *_ in swept()] == [bumrah, rohit, namesake]


def test_a_search_asks_for_fifty_leaving_out_tagged_shorts_but_not_the_short_highlight_clips(world, monkeypatch, measured):  # noqa: F811
    only(monkeypatch, gap=20, famous=0)
    player("Shreyas Iyer")
    results = {"iyertag0001": ("Iyer six #shorts", "PT25S"), "iyertag0002": ("Iyer catch", "PT40S"),  # tagged in the description below
               "iyerplain01": ("Iyer's reaction", "PT30S"),  # a Short no tag gives away: still refused, as before
               "iyerclip001": ("Shreyas Iyer 3 sixes in an over", "PT1M50S"), "iyerclip002": ("Shreyas Iyer run out", "PT3M10S"),
               "iyerhigh001": ("Shreyas Iyer 90 highlights", "PT9M")}
    world.found["Shreyas Iyer cricket"] = [hit(v, f"Shreyas {t}", "#Shorts #cricket" if v == "iyertag0002" else "") for v, (t, _) in results.items()]
    world.videos.update({v: video(v, duration=d) for v, (_, d) in results.items()})
    assert sweeps.run(NOW)["kept"] == 3
    [asked] = [r for r in world.asked if r.url.path == "/youtube/v3/search"]
    assert asked.url.params["maxResults"] == "50"
    assert counts(measured, "sports_follow.feed.refused") == {("cricket", "gap", "too_short"): 1}  # the untagged Short only
    assert counts(measured, "sports_follow.feed.kept") == {("cricket", "gap"): 3}  # both 1-4 minute clips and the highlights


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


def test_the_days_gap_searches_go_to_signed_in_fans_players_first_and_cookies_only_break_a_tie(world, monkeypatch):
    # Six cookies' follows (one script's) took the day's gap searches ahead of a player a signed-in fan follows: each is
    # one of YouTube's 100 searches a day (BUG-293). Accounts an admin hasn't blocked count, as starters count them.
    only(monkeypatch, gap=2, famous=0)
    pushed = player("Pushed Player", followers=6)
    real = player("Smriti Mandhana", followers=0)
    blocked = player("Blocked Pick", followers=0)
    quiet = player("Richa Ghosh", followers=2)
    with db_module.session() as s:
        s.add_all([Account(email="a@example.com", fan_id="acct-a"), Account(email="b@example.com", fan_id="acct-b", blocked_at=NOW)])
        s.flush()
        s.add_all([Follow(fan_id="acct-a", player_id=real, alert_rules={}), Follow(fan_id="acct-b", player_id=blocked, alert_rules={})])
    assert sweeps.run(NOW)["gap searched"] == 2
    assert [p for p, *_ in swept()] == [real, pushed]
    only(monkeypatch, gap=20, famous=0)
    assert sweeps.run(NOW + timedelta(hours=3))["gap searched"] == 2
    assert [p for p, *_ in swept()] == [real, pushed, quiet, blocked]  # a blocked account's follow is as a cookie's


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
    budget.take(youtube.SEARCH_BUDGET, cost=55)  # 40 of the 95 spendable searches left: the moment searches' cap
    assert sweeps.run(NOW) == {"gap searched": 0, "wide searched": 0, "famous searched": 0, "pool searched": 0, "found": 0, "kept": 0, "refused": 0}
    assert searched(world) == [] and swept() == []


def test_a_removed_player_takes_their_sweeps(world, monkeypatch):
    only(monkeypatch, gap=20, famous=35)
    iyer = player("Shreyas Iyer")
    sweeps.run(NOW)
    assert len(swept()) == 2  # a gap search and the famous sweep's
    with db_module.session() as s:
        s.execute(delete(Player).where(Player.id == iyer))
    assert swept() == []


# ---------------------------------------------------------------- the wide sweep


def test_every_followed_player_gets_a_wide_search_but_one_gap_searched_waits_and_whats_stored_isnt_kept_again(world, monkeypatch):
    only(monkeypatch, gap=20, famous=0, wide=14)
    iyer = player("Shreyas Iyer", followers=4)
    kohli = player("Virat Kohli", followers=9)
    served(kohli, "kohlirecent")  # covered this week: no gap search, but the wide sweep's
    world.found["Shreyas Iyer cricket"] = [hit("iyerknock01", "Shreyas Iyer 90 vs Australia"), hit("iyerknock02", "Shreyas Iyer's catch")]
    world.found["Virat Kohli cricket"] = [hit("kohliwide01", "Virat Kohli's season in the nets")]
    world.videos.update({v: video(v) for v in ("iyerknock01", "iyerknock02", "kohliwide01")})
    done = sweeps.run(NOW)
    gap, wide = ((NOW - d).strftime("%Y-%m-%dT%H:%M:%SZ") for d in (sweeps.GAP_DAYS, sweeps.WIDE_DAYS))
    assert searched(world) == [("Shreyas Iyer cricket", gap), ("Virat Kohli cricket", wide)]  # Iyer's gap search stands for today
    assert (done["gap searched"], done["wide searched"], done["kept"]) == (1, 1, 3)
    with db_module.session() as s:  # the judge served one of Iyer's: covered, so tomorrow it's his wide search
        s.execute(update(FeedCandidate).where(FeedCandidate.external_id == "iyerknock01").values(score=0.5, verdict={"about_player": "subject"}))
    done = sweeps.run(NOW + DAY)
    assert (done["gap searched"], done["wide searched"], done["found"], done["kept"]) == (0, 1, 2, 0)  # what the gap search stored
    assert [(p, s, found, kept) for p, s, _, _, found, kept in swept()] == [(iyer, "gap", 2, 2), (kohli, "wide", 1, 1), (iyer, "wide", 2, 0)]
    with db_module.session() as s:
        streams = {vid: stream for vid, stream in s.execute(select(FeedCandidate.external_id, FeedCandidate.candidate["stream"].astext))}
    assert streams == {"iyerknock01": "gap", "iyerknock02": "gap", "kohliwide01": "wide", "kohlirecent": None}


def test_what_the_famous_sweep_stored_isnt_kept_again_by_the_wide_one(world, monkeypatch):
    only(monkeypatch, gap=0, famous=35, wide=0)
    iyer = player("Shreyas Iyer")
    world.found["Shreyas Iyer cricket"] = [hit("iyerfamous1", "Shreyas Iyer's 105 against New Zealand")]
    world.videos["iyerfamous1"] = video("iyerfamous1", views=4_000_000)
    assert sweeps.run(NOW)["kept"] == 1
    only(monkeypatch, gap=0, famous=35, wide=14)
    calls = world.api_calls("/videos")
    done = sweeps.run(NOW + DAY)  # the famous sweep isn't due for a month; the wide one finds the same video
    assert (done["wide searched"], done["famous searched"], done["found"], done["kept"]) == (1, 0, 1, 0)
    assert world.api_calls("/videos") == calls  # stored already: not asked about again
    assert [(p, s, kept) for p, s, _, _, _, kept in swept()] == [(iyer, "famous", 1), (iyer, "wide", 0)]


def test_a_player_gap_searched_every_day_still_gets_a_wide_search_a_week_on(world, monkeypatch):
    only(monkeypatch, gap=20, famous=0, wide=14)
    iyer = player("Shreyas Iyer")
    for day in range(9):  # each day's gap search keeps something new, which the judge never serves: still a gap
        world.found["Shreyas Iyer cricket"] = [hit(f"iyerclip{day:03d}", f"Shreyas Iyer clip {day}")]
        world.videos[f"iyerclip{day:03d}"] = video(f"iyerclip{day:03d}")
        sweeps.run(NOW + day * DAY)
    streams = [(s, at) for p, s, _, at, _, _ in swept() if p == iyer]
    assert [s for s, _ in streams].count("gap") == 9
    assert [at for s, at in streams if s == "wide"] == [NOW + 7 * DAY]  # a week after his first gap search


def squad(n: int) -> list[int]:
    """n followed players, the first the most followed."""
    return [player(f"Player {chr(65 + i)}{chr(65 + i)}", followers=n - i) for i in range(n)]


def test_the_wide_sweep_spreads_the_week_over_its_days_under_its_cap(world, monkeypatch):
    only(monkeypatch, gap=0, famous=0, wide=14)
    ids = squad(14)  # two a day
    sweeps.run(NOW)
    assert [p for p, *_ in swept()] == ids[:2]
    sweeps.run(NOW + timedelta(hours=12))
    assert len(swept()) == 2  # the day's share is spent
    for day in range(1, 7):
        sweeps.run(NOW + day * DAY)
    assert [p for p, *_ in swept()] == ids  # all fourteen in the week, the most followed first
    only(monkeypatch, gap=0, famous=0, wide=1)
    sweeps.run(NOW + 7 * DAY - timedelta(seconds=1))  # a run a moment earlier than last week's: due again, under the cap
    assert [p for p, *_ in swept()] == [*ids, ids[0]]


def test_when_the_wide_cap_binds_everyone_is_searched_before_anyone_again(world, monkeypatch):
    only(monkeypatch, gap=0, famous=0, wide=2)  # 2 a day of 14: a week gets round only half
    ids = squad(14)
    sweeps.run(NOW)
    sweeps.run(NOW + 7 * DAY)  # the first two are due again, but twelve have never been searched
    assert [p for p, *_ in swept()] == [ids[0], ids[1], ids[2], ids[3]]


def test_no_room_after_the_gap_searches_holds_the_wide_sweep(world, monkeypatch):
    from sports_follow.adapters import budget, youtube

    only(monkeypatch, gap=20, famous=35, wide=14)
    player("Shreyas Iyer")
    player("Virat Kohli")
    budget.take(youtube.SEARCH_BUDGET, cost=54)  # 41 left: room for one search before the moment searches' cap
    done = sweeps.run(NOW)
    assert (done["gap searched"], done["wide searched"], done["famous searched"]) == (1, 0, 0)


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


def test_a_daily_run_a_moment_earlier_than_yesterdays_still_has_its_caps(world, monkeypatch):
    only(monkeypatch, gap=1, famous=7)  # one gap search and one famous a day
    player("Alpha One", followers=2)
    player("Bravo Two")
    sweeps.run(NOW)
    early = NOW + timedelta(days=1) - timedelta(seconds=1)
    done = sweeps.run(early)
    assert (done["gap searched"], done["famous searched"]) == (1, 1)
    assert [(q, s) for _, s, q, *_ in swept()] == [("Alpha One cricket", "gap"), ("Alpha One cricket", "famous"),
                                                    ("Bravo Two cricket", "gap"), ("Bravo Two cricket", "famous")]  # fmt: skip


def test_when_the_famous_cap_binds_everyone_is_swept_before_anyone_again(world, monkeypatch):
    only(monkeypatch, gap=0, famous=7)  # one a day
    monkeypatch.setattr(sweeps, "FAMOUS_EVERY", timedelta(days=2))
    a, b, c = player("Alpha One", followers=5), player("Bravo Two", followers=3), player("Charlie Three", followers=1)
    for day in range(4):
        sweeps.run(NOW + day * DAY)
    assert [p for p, *_ in swept()] == [a, b, c, a]  # Charlie before Alpha's second, though Alpha's the most followed


@pytest.mark.parametrize(("stream", "caps"), [("gap", {"gap": 20, "famous": 0}), ("wide", {"gap": 0, "famous": 0, "wide": 14}), ("famous", {"gap": 0, "famous": 35})])
def test_each_sweep_keeps_only_whats_from_a_channel_the_players_sport_lists(world, monkeypatch, stream, caps):
    only(monkeypatch, **caps)
    iyer = player("Shreyas Iyer")
    if stream != "gap":
        served(iyer, "iyerrecent1")  # covered this week: no gap search
    world.found["Shreyas Iyer cricket"] = [hit("iyerlisted1", "Shreyas Iyer 90 vs Australia"), hit("iyeroffli01", "SHREYAS IYER 🔥🔥", channel="UC" + "z" * 22)]
    world.videos.update({v: video(v) for v in ("iyerlisted1", "iyeroffli01")})
    done = sweeps.run(NOW)
    assert (done[f"{stream} searched"], done["kept"]) == (1, 1)
    with db_module.session() as s:
        assert set(s.scalars(select(FeedCandidate.external_id))) == {"iyerlisted1"} | ({"iyerrecent1"} if stream != "gap" else set())
    assert "iyeroffli01" not in "".join(str(r.url) for r in world.asked if r.url.path == "/youtube/v3/videos")  # never asked about


def test_the_pool_sweep_searches_pool_players_last_two_months_after_the_followed_players_under_its_own_cap(world, monkeypatch):
    """BHA-382: pool players nobody follows (feedpool.ids) get a search of their own, after the followed players',
    FEED_POOL_SEARCHES a day, each once a fortnight, the pool's best known first."""
    import json

    from sports_follow import feedpool

    only(monkeypatch, gap=20, famous=0, pool=2)
    iyer = player("Shreyas Iyer")
    first, second, third = (player(n, followers=0) for n in ("Smriti Mandhana", "Jemimah Rodrigues", "Richa Ghosh"))
    world.redis.data[feedpool.POOL_KEY] = json.dumps({"players": {"cricket": [first, second, third, iyer]}})  # a followed one in it is searched as followed
    world.found["Smriti Mandhana cricket"] = [hit("mandhana117", "Smriti Mandhana 117 vs Australia | highlights")]
    world.videos["mandhana117"] = video("mandhana117")
    done = sweeps.run(NOW)
    assert (done["gap searched"], done["pool searched"], done["kept"]) == (1, 2, 1)
    after = (NOW - sweeps.POOL_DAYS).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert searched(world)[1:] == [("Smriti Mandhana cricket", after), ("Jemimah Rodrigues cricket", after)]
    assert [(pid, stream) for pid, stream, *_ in swept()] == [(iyer, "gap"), (first, "pool"), (second, "pool")]
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate).where(FeedCandidate.external_id == "mandhana117"))
        assert row is not None and row.player_id == first and row.candidate["stream"] == "pool"
    # The day's cap is spent; the next day the one not yet searched goes first, and the others wait out the fortnight.
    assert sweeps.run(NOW + timedelta(hours=1))["pool searched"] == 0
    assert sweeps.run(NOW + DAY)["pool searched"] == 1 and swept()[-1][:2] == (third, "pool")
    assert sweeps.run(NOW + DAY * 15)["pool searched"] == 2
