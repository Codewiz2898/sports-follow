"""The feed's moment searches against Postgres (searches.py): a followed player's big moment, stored as a live poll
stores it (notify.record), queues one search for the game; it runs a few hours on and again a day on, keeping what
names the player and passes the hard filters, with the moment attached; the day's cap runs the biggest moments
first; and what's stored already isn't kept again. Skipped without a throwaway database at alembic head
(tests/test_corrections_db.py says how to make one)."""

from __future__ import annotations

import os
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import create_engine, delete, select, text, update

from sports_follow import bus, db as db_module, notify, searches
from sports_follow.models import Event, FeedCandidate, FeedSearch, Follow, Moment, Player, PlayerAlias
from sports_follow.sports.base import Found
from tests.test_feed_fetch import NOW, video
from tests.test_feed_fetch_db import Redis
from tests.test_moment_search import Searching, hit

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

FIRST, SECOND = NOW + searches.FIRST_AFTER, NOW + searches.SECOND_AFTER


@pytest.fixture
def world(monkeypatch):
    from sports_follow.adapters import youtube

    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE feed_search, feed_candidates, moment, event, follow, player_alias, player_identity, player RESTART IDENTITY CASCADE"))
    monkeypatch.setattr(db_module, "SessionLocal", db_module.sessionmaker(bind=engine, expire_on_commit=False))
    made = Searching()
    monkeypatch.setattr(youtube, "_client", httpx.Client(transport=httpx.MockTransport(made)))
    monkeypatch.setattr(youtube, "key", lambda: "test-key")
    monkeypatch.setattr(bus, "sync_redis", lambda: made.redis)
    made.redis = Redis()
    yield made
    engine.dispose()


def player(name: str, sport: str = "cricket", followed: bool = True) -> int:
    with db_module.session() as s:
        p = Player(slug=name.lower().replace(" ", "-"), name=name, sport=sport, teams=[])
        s.add(p)
        s.flush()
        if followed:
            s.add(Follow(fan_id="fan-1", player_id=p.id, alert_rules={}))
        return p.id


def game(title: str, sport: str = "cricket", venue: str | None = None, competition: str = "") -> int:
    with db_module.session() as s:
        e = Event(key=title.lower().replace(" ", "-")[:64], sport=sport, title=title, venue=venue, competition=competition, status="live")
        s.add(e)
        s.flush()
        return e.id


def fire(pid: int, eid: int, found: Found, at=NOW) -> int:
    """A moment as a live poll stores it, at that time."""
    with db_module.session() as s:
        [mid] = notify.record(s, pid, eid, [found])
        s.execute(update(Moment).where(Moment.id == mid).values(created_at=at))
    return mid


def hundred(who: str = "Virat Kohli", detail: str = "100") -> Found:
    return Found("hundred", "key", f"{who} reaches {detail}", "103 (98)", detail, 90)


def queued() -> list[tuple]:
    with db_module.session() as s:
        return [tuple(r) for r in s.execute(select(FeedSearch.player_id, FeedSearch.moment_id, FeedSearch.query, FeedSearch.priority, FeedSearch.first_at, FeedSearch.second_at)
                                            .order_by(FeedSearch.id)).all()]


def kept() -> list[tuple]:
    with db_module.session() as s:
        return [tuple(r) for r in s.execute(select(FeedCandidate.player_id, FeedCandidate.external_id, FeedCandidate.moment_id).order_by(FeedCandidate.external_id)).all()]


def test_a_big_moment_of_a_followed_player_queues_one_search_in_its_sports_words(world):
    kohli = player("Virat Kohli")
    nobody = player("Rohit Sharma", followed=False)
    lords = game("England vs India", venue="Lord's, London")
    mid = fire(kohli, lords, hundred())
    fire(kohli, lords, Found("fifty", "key", "Kohli reaches 50", "", "50", 70))  # no words for a fifty: not searched
    fire(nobody, lords, hundred("Rohit Sharma"))  # nobody follows him
    assert searches.queue(NOW + timedelta(minutes=5)) == {"queued": 1, "taken over": 0, "taken back": 0}
    assert queued() == [(kohli, mid, "Virat Kohli century Lord's", 90, None, None)]
    assert searches.queue(NOW + timedelta(minutes=35)) == {"queued": 0, "taken over": 0, "taken back": 0}  # once


def test_a_moment_search_asks_for_the_player_by_the_name_fans_write(world):
    bumrah = player("Jasprit Jasbirsingh Bumrah")
    with db_module.session() as s:
        s.add(PlayerAlias(alias="jasprit-bumrah", player_id=bumrah))
    mid = fire(bumrah, game("England vs India", venue="Lord's, London"), Found("wickets", "key", "Bumrah takes 5", "5/31", "w5", 95))
    searches.queue(NOW + timedelta(minutes=5))
    assert queued() == [(bumrah, mid, "jasprit bumrah 5 wickets Lord's", 95, None, None)]


def test_a_later_moment_in_the_game_takes_its_search_over_until_its_last_run(world):
    salah = player("Mohamed Salah", sport="football")
    derby = game("Liverpool vs Everton", sport="football")
    fire(salah, derby, Found("goal", "key", "Goal: Mohamed Salah", "1–0 · 12'", "goal@101", 85))
    searches.queue(NOW + timedelta(minutes=5))
    second = fire(salah, derby, Found("goal", "key", "Goal: Mohamed Salah (2)", "2–0 · 40'", "goal@140", 90), at=NOW + timedelta(minutes=30))
    assert searches.queue(NOW + timedelta(minutes=35)) == {"queued": 0, "taken over": 1, "taken back": 0}
    [(_, moment, query, priority, _, _)] = queued()
    assert (moment, query, priority) == (second, "Mohamed Salah brace Liverpool vs Everton", 90)
    with db_module.session() as s:
        assert s.scalar(select(FeedSearch.fired_at)) == NOW  # it still looks from the first goal


def test_a_queued_search_runs_a_few_hours_on_and_again_a_day_on_keeping_what_names_the_player(world):
    kohli = player("Virat Kohli")
    mid = fire(kohli, game("England vs India", venue="Lord's"), hundred())
    searches.queue(NOW + timedelta(minutes=5))
    query = "Virat Kohli century Lord's"
    world.found[query] = [hit("kohliedit01", "Virat Kohli 100 at Lord's | fan edit"), hit("shortclip01", "Kohli century 🔥"),
                          hit("otherplayer", "Root's hundred at Lord's"), hit("blockedin01", "Kohli: the hundred")]  # fmt: skip
    world.videos.update({v["id"]: v for v in (video("kohliedit01"), video("shortclip01", duration="PT30S"), video("blockedin01", region={"blocked": ["IN"]}))})
    assert searches.run(NOW + timedelta(hours=2))["searched"] == 0  # too soon
    done = searches.run(FIRST)
    assert (done["searched"], done["found"], done["kept"], done["refused"]) == (1, 3, 1, 2)
    assert kept() == [(kohli, "kohliedit01", mid)]
    assert searches.run(FIRST + timedelta(hours=1))["searched"] == 0  # run once till a day on
    world.found[query].insert(0, hit("kohliedit02", "Virat Kohli Lord's century, every boundary"))
    world.videos["kohliedit02"] = video("kohliedit02")
    done = searches.run(SECOND)
    assert (done["searched"], done["kept"]) == (1, 1)
    assert kept() == [(kohli, "kohliedit01", mid), (kohli, "kohliedit02", mid)]
    [(_, _, _, _, first_at, second_at)] = queued()
    assert (first_at, second_at) == (FIRST, SECOND)
    assert searches.run(SECOND + timedelta(hours=6))["searched"] == 0  # two runs, no more
    assert world.api_calls("/search") == 2
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate).where(FeedCandidate.external_id == "kohliedit02"))
        assert row is not None and row.verdict is None and row.candidate["channel"] == "A fan" and row.candidate["named"] == "virat kohli"


def test_a_video_stored_already_isnt_kept_again(world):
    kohli = player("Virat Kohli")
    fire(kohli, game("England vs India", venue="Lord's"), hundred())
    world.found["Virat Kohli century Lord's"] = [hit("kohliedit01", "Virat Kohli 100 at Lord's")]
    world.videos["kohliedit01"] = video("kohliedit01")
    with db_module.session() as s:  # the channel poll found it first
        s.add(FeedCandidate(player_id=kohli, source="youtube", external_id="kohliedit01", url="https://www.youtube.com/watch?v=kohliedit01", candidate={}, features={}))
    searches.queue(NOW + timedelta(minutes=5))
    done = searches.run(FIRST)
    assert (done["searched"], done["found"], done["kept"]) == (1, 1, 0)
    assert kept() == [(kohli, "kohliedit01", None)] and world.api_calls("/videos") == 0  # not asked about either


def test_the_days_cap_runs_the_biggest_moments_first(world, monkeypatch):
    monkeypatch.setattr(searches, "FEED_MOMENT_SEARCHES", 2)
    names = {"Virat Kohli": 90, "Jasprit Bumrah": 95, "Shubman Gill": 70}
    for i, (name, priority) in enumerate(names.items()):
        detail = "w5" if priority == 95 else "100"
        kind = "wickets" if priority == 95 else "hundred"
        fire(player(name), game(f"Game {i}", venue=f"Ground {i}"), Found(kind, "key", name, "", detail, priority))
    searches.queue(NOW + timedelta(minutes=5))
    assert searches.run(FIRST)["searched"] == 2
    with db_module.session() as s:
        ran = dict(s.execute(select(FeedSearch.query, FeedSearch.first_at)).all())
    assert ran == {"Jasprit Bumrah 5 wickets Ground 1": FIRST, "Virat Kohli century Ground 0": FIRST, "Shubman Gill century Ground 2": None}
    assert searches.run(FIRST + timedelta(hours=1))["searched"] == 0  # the two of these 24 hours are spent
    assert searches.run(SECOND)["searched"] == 0  # a day on, but the first runs are still within the last 24 hours
    # 24 hours after the first runs: Gill's search is over a day old, so it runs once, as its last; the other two
    # have their second runs, and go first.
    later = FIRST + searches.DAY + timedelta(minutes=1)
    assert searches.run(later)["searched"] == 2
    with db_module.session() as s:
        last = {q: (f, l) for q, f, l in s.execute(select(FeedSearch.query, FeedSearch.first_at, FeedSearch.second_at))}
    assert last["Jasprit Bumrah 5 wickets Ground 1"] == (FIRST, later) and last["Virat Kohli century Ground 0"] == (FIRST, later)
    assert last["Shubman Gill century Ground 2"] == (None, None)
    monkeypatch.setattr(searches, "FEED_MOMENT_SEARCHES", 3)
    assert searches.run(later + timedelta(minutes=30))["searched"] == 1
    # Two days on, the searches go, once their runs no longer count against the cap.
    assert searches.run(NOW + searches.GIVE_UP + timedelta(minutes=1))["dropped"] == 0
    assert searches.run(later + timedelta(minutes=30) + searches.DAY + timedelta(minutes=1))["dropped"] == 3 and queued() == []


def test_a_search_the_cap_held_back_runs_its_last_well_after_its_first(world, monkeypatch):
    fire(player("Virat Kohli"), game("England vs India", venue="Lord's"), hundred())
    searches.queue(NOW + timedelta(minutes=5))
    late = NOW + timedelta(hours=23)
    assert searches.run(late)["searched"] == 1
    assert searches.run(SECOND + timedelta(minutes=5))["searched"] == 0  # due a day on, but it ran an hour ago
    assert searches.run(late + searches.RUNS_APART)["searched"] == 1
    [(_, _, _, _, first_at, second_at)] = queued()
    assert (first_at, second_at) == (late, late + searches.RUNS_APART)


def test_a_search_no_source_answered_is_tried_again(world):
    fire(player("Virat Kohli"), game("England vs India", venue="Lord's"), hundred())
    searches.queue(NOW + timedelta(minutes=5))
    world.error = (500, "backendError")
    assert searches.run(FIRST)["searched"] == 0 and queued()[0][4] is None
    world.error = None
    assert searches.run(FIRST + timedelta(minutes=30))["searched"] == 1 and searches.spent(NOW) == 1


def test_a_sport_turned_off_has_its_queued_searches_held(world, monkeypatch):
    from sports_follow import sports

    fire(player("Virat Kohli"), game("England vs India", venue="Lord's"), hundred())
    searches.queue(NOW + timedelta(minutes=5))
    monkeypatch.setattr(sports, "ENABLED", [s for s in sports.ALL if s.key != "cricket"])
    assert searches.run(FIRST)["searched"] == 0 and world.api_calls("/search") == 0


def test_a_moment_taken_back_after_its_search_was_queued_takes_the_search_back_to_what_stands(world):
    salah = player("Mohamed Salah", sport="football")
    derby = game("Liverpool vs Everton", sport="football")
    first = fire(salah, derby, Found("goal", "key", "Goal: Mohamed Salah", "", "goal@101", 85))
    fire(salah, derby, Found("goal", "key", "Goal: Mohamed Salah (2)", "", "goal@140", 85), at=NOW + timedelta(minutes=20))
    searches.queue(NOW + timedelta(minutes=25))
    assert queued()[0][2] == "Mohamed Salah brace Liverpool vs Everton"
    fire(salah, derby, Found("correction", "key", "Goal ruled out", "", "goal:goal@140", 85), at=NOW + timedelta(minutes=30))
    assert searches.queue(NOW + timedelta(minutes=35)) == {"queued": 1, "taken over": 0, "taken back": 1}
    [(_, moment, query, _, _, _)] = queued()
    assert (moment, query) == (first, "Mohamed Salah goal Liverpool vs Everton")


def test_the_ranking_keeps_the_moment_a_search_found_a_video_for(world, monkeypatch):
    from sports_follow import rank

    kohli = player("Virat Kohli")
    lords = game("England vs India", venue="Lord's")
    fire(kohli, lords, Found("wickets", "key", "Kohli takes 5 wickets", "", "w5", 95), at=NOW - timedelta(hours=1))  # bigger, and near
    mid = fire(kohli, lords, hundred())
    searches.queue(NOW + timedelta(minutes=5))
    world.found["Virat Kohli century Lord's"] = [{**hit("kohliedit01", "Virat Kohli 100 at Lord's"),
                                                   "snippet": {**hit("kohliedit01", "Virat Kohli 100 at Lord's")["snippet"], "publishedAt": "2026-10-20T08:00:00Z"}}]
    world.videos["kohliedit01"] = video("kohliedit01")
    searches.run(FIRST)
    with db_module.session() as s:
        s.execute(update(FeedCandidate).values(verdict={"about_player": "yes"}))
    rank.rescore(FIRST)
    assert kept() == [(kohli, "kohliedit01", mid)]


def test_no_room_left_in_the_days_quota_holds_the_searches(world):
    from sports_follow.adapters import budget, youtube

    fire(player("Virat Kohli"), game("England vs India", venue="Lord's"), hundred())
    searches.queue(NOW + timedelta(minutes=5))
    budget.take(youtube.SEARCH_BUDGET, cost=youtube.SEARCHES_PER_DAY - youtube.SEARCH_MARGIN)
    assert searches.run(FIRST)["searched"] == 0 and world.api_calls("/search") == 0
    assert queued()[0][4] is None  # still due


def test_a_moment_taken_back_queues_nothing_and_a_removed_player_takes_their_searches(world):
    salah = player("Mohamed Salah", sport="football")
    derby = game("Liverpool vs Everton", sport="football")
    fire(salah, derby, Found("goal", "key", "Goal: Mohamed Salah", "", "goal@101", 85))
    fire(salah, derby, Found("correction", "key", "Goal ruled out", "", "goal:goal@101", 85))
    assert searches.queue(NOW + timedelta(minutes=5))["queued"] == 0
    kohli = player("Virat Kohli")
    fire(kohli, game("England vs India", venue="Lord's"), hundred())
    assert searches.queue(NOW + timedelta(minutes=5))["queued"] == 1
    with db_module.session() as s:
        s.execute(delete(Player).where(Player.id == kohli))
    assert queued() == [] and searches.queue(NOW + timedelta(minutes=35))["queued"] == 0


def test_without_a_key_nothing_is_queued_or_searched(world, monkeypatch):
    from sports_follow.adapters import youtube

    fire(player("Virat Kohli"), game("England vs India", venue="Lord's"), hundred())
    monkeypatch.setattr(youtube, "key", lambda: None)
    assert searches.queue(NOW)["queued"] == 0 and searches.run(FIRST)["searched"] == 0
    assert queued() == [] and world.asked == []
