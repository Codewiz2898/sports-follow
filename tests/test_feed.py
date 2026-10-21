"""A player's Feed (feed.py, BHA-299): until the pipeline is wired in, the card's news as news items with ids
that stay put; once the judge keeps videos and posts, each grouped around the moment it's about, then This
week, This month and Earlier, the famous ones apart; what a fan hides leaves their feed at once and nobody else's; Today's
rail takes a followed player's top video or post and never news. The made-up videos and posts here stand for
the judge's rows. The rows themselves need Postgres (ON CONFLICT, cascades), so those tests run against a
throwaway database at alembic head and are skipped without one (see tests/test_players.py for setting it up).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import accounts, feed, server
from sports_follow.feed import Candidate, Item, Post, Video
from sports_follow.models import FeedFeedback, Follow, Moment, Player, PlayerCard

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime.now(timezone.utc).replace(microsecond=0)  # the routes serve as of now


def news(headline: str, url: str, published: str | None = None, source: str = "ChessBase") -> dict:
    return {"headline": headline, "summary": "", "source": source, "url": url, "published": published}


CARD = {"player": {"name": "Gukesh D"}, "news": [
    news("Game 4 annotated", "https://example.com/g4", (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z")),
    news("The match so far", "https://example.com/so-far", (NOW - timedelta(days=18)).date().isoformat()),
    news("No address", ""),
    news("Undated", "https://example.com/undated"),
]}


def video(url: str, title: str, tag: str = "Analysis", days_ago: float = 1, youtube_id: str = "dQw4w9WgXcQ") -> Item:
    return Item(id=feed.item_id(url), kind="video", tag=tag, title=title, url=url, source="Endgame Hour", published_at=(NOW - timedelta(days=days_ago)).isoformat(),
                reason="A creator on the chess list breaking down game 4.", video=Video(youtube_id=youtube_id, duration=1122, views=41000))


def post(url: str, text_: str, own: bool, days_ago: float = 0.5) -> Item:
    return Item(id=feed.item_id(url), kind="post", tag="Post", title=text_, url=url, source="@DGukesh" if own else "Rook Lift", published_at=(NOW - timedelta(days=days_ago)).isoformat(),
                reason="His own account, after the game." if own else "A fan account the judge trusts.", post=Post(platform="X", own=own))


# ---------------------------------------------------------------- without the database


def test_the_cards_news_become_news_items_with_ids_from_their_address():
    items = [c.item for c in feed._news(CARD)]  # pyright: ignore[reportPrivateUsage]
    assert [i.title for i in items] == ["Game 4 annotated", "The match so far", "Undated"]  # one with no address can't be opened
    first = items[0]
    assert (first.kind, first.tag, first.source, first.id) == ("news", "News", "ChessBase", feed.item_id("https://example.com/g4"))
    assert first.published_at == (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    # A story known only by its day stays a day: the app shows it as that day wherever the fan is.
    assert items[1].published_at == (NOW - timedelta(days=18)).date().isoformat()
    assert first.reason == "A story about Gukesh D from ChessBase, from the news on their page."
    assert feed.item_id("https://example.com/g4") == feed.item_id("https://example.com/g4") and len(first.id) == 16


def test_news_is_this_week_this_month_and_earlier_newest_first():
    groups = feed._arrange(feed._news(CARD), {}, NOW)  # pyright: ignore[reportPrivateUsage]
    assert [(g.head, g.moment, [i.title for i in g.items]) for g in groups] == [
        ("This week", False, ["Game 4 annotated"]), ("This month", False, ["The match so far"]), ("Earlier", False, ["Undated"])]


def test_this_month_is_eight_to_thirty_days_old_and_a_pin_stays_in_this_week():
    eight = Candidate(video("https://youtu.be/8", "Eight days on", days_ago=8))
    thirty = Candidate(video("https://youtu.be/30", "A month on", days_ago=29.9))
    older = Candidate(video("https://youtu.be/31", "Over a month on", days_ago=31))
    pinned = Candidate(video("https://youtu.be/p", "Pinned, from spring", days_ago=200), pinned=True)
    seven = Candidate(video("https://youtu.be/7", "Six days on", days_ago=6.9))
    groups = feed._arrange([eight, thirty, older, pinned, seven], {}, NOW)  # pyright: ignore[reportPrivateUsage]
    assert [(g.head, [i.title for i in g.items]) for g in groups] == [
        ("This week", ["Six days on", "Pinned, from spring"]), ("This month", ["Eight days on", "A month on"]), ("Earlier", ["Over a month on"])]


def test_videos_around_a_moment_come_first_and_the_judges_score_leads_within_a_group():
    moment = Moment(id=7, title="Gukesh wins game 4", url="/match/lichess/abc123", created_at=NOW - timedelta(days=2))
    edit = Candidate(video("https://youtu.be/1", "Every move", "Fan edit", days_ago=0.2), moment_id=7)
    best = Candidate(video("https://youtu.be/2", "Where the Catalan turned", days_ago=1), moment_id=7, score=0.9)
    loose = Candidate(post("https://x.com/p/1", "Proud of the team", own=True))
    gone = Candidate(video("https://youtu.be/3", "About a moment since pruned"), moment_id=99)  # its moment is gone: This week
    groups = feed._arrange([edit, best, loose, gone], {7: moment}, NOW)  # pyright: ignore[reportPrivateUsage]
    assert [(g.head, g.moment, g.match, [i.title for i in g.items]) for g in groups] == [
        ("Gukesh wins game 4", True, "/match/lichess/abc123", ["Where the Catalan turned", "Every move"]),
        ("This week", False, None, ["Proud of the team", "About a moment since pruned"]),
    ]


def test_only_links_the_app_may_open_and_videos_it_can_play():
    assert feed._safe(video("https://youtu.be/1", "ok"))  # pyright: ignore[reportPrivateUsage]
    assert not feed._safe(video("javascript:alert(1)", "bad link"))  # pyright: ignore[reportPrivateUsage]
    assert not feed._safe(video("https://youtu.be/1", "bad id", youtube_id="x\" onload=\"1"))  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------- against the database


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL or "postgresql+psycopg://")
    with engine.begin() as c:
        c.execute(text("TRUNCATE account, player, player_card, follow, moment, feed_feedback RESTART IDENTITY CASCADE"))
    made = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def unit():
        s = made()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(accounts, "session", unit)
    s = made()
    yield s
    s.close()
    engine.dispose()


def player(db, slug: str, card: dict | None = None) -> int:
    p = Player(slug=slug, name=slug.title(), sport="chess")
    db.add(p)
    db.flush()
    db.add(PlayerCard(player_id=p.id, status="ready", card=card if card is not None else CARD))
    db.commit()
    return p.id


@needs_db
def test_a_feed_is_the_news_until_the_pipeline_and_hiding_drops_an_item_for_that_fan_only(db):
    gukesh = player(db, "gukesh")
    shown = server.player_feed(gukesh, server.Response(), "fan", db)
    assert shown.count == 3 and [g.head for g in shown.groups] == ["This week", "This month", "Earlier"]
    assert shown.evergreen == []
    first = shown.groups[0].items[0]

    assert server.feed_feedback(gukesh, first.id, {"verdict": "not_interested"}, "fan", db) == {"item_id": first.id, "hidden": True}
    after = server.player_feed(gukesh, server.Response(), "fan", db)
    assert after.count == 2 and first.id not in {i.id for g in after.groups for i in g.items}
    assert server.player_feed(gukesh, server.Response(), "someone else", db).count == 3

    # A second word on it replaces the first; nothing else is kept twice.
    server.feed_feedback(gukesh, first.id, {"verdict": "report"}, "fan", db)
    assert [(f.fan_id, f.item_id, f.verdict) for f in db.scalars(select(FeedFeedback))] == [("fan", first.id, "report")]


@needs_db
def test_odd_feedback_and_missing_players_are_refused_plainly(db):
    gukesh = player(db, "gukesh")
    real = feed.item_id("https://example.com/g4")
    for item, body in (("not-an-id", {"verdict": "report"}), (real, {"verdict": "love it"}), (real, {"verdict": 3}), (real, {})):
        with pytest.raises(server.HTTPException) as refused:
            server.feed_feedback(gukesh, item, body, "fan", db)
        assert refused.value.status_code == 422
    # Only about an item the feed has: a fan can't fill the table with ids of nothing.
    with pytest.raises(server.HTTPException) as nothing:
        server.feed_feedback(gukesh, "0123456789abcdef", {"verdict": "report"}, "fan", db)
    assert nothing.value.status_code == 404
    assert db.scalars(select(FeedFeedback)).all() == []
    with pytest.raises(server.HTTPException) as missing:
        server.feed_feedback(9999, "0123456789abcdef", {"verdict": "report"}, "fan", db)
    assert missing.value.status_code == 404
    with pytest.raises(server.HTTPException) as missing:
        server.player_feed(9999, server.Response(), "fan", db)
    assert missing.value.status_code == 404


@needs_db
def test_judged_videos_and_posts_join_the_news_and_todays_rail_takes_the_top_one_never_news(db, monkeypatch):
    gukesh, kohli, quiet = player(db, "gukesh"), player(db, "kohli", {"player": {"name": "Kohli"}, "news": [news("A story", "https://example.com/k")]}), player(db, "quiet")
    db.add(Moment(key="m1", player_id=gukesh, kind="final", level="key", title="Gukesh wins game 4", url="/match/lichess/abc123", created_at=NOW - timedelta(hours=9)))
    db.commit()
    moment_id = db.scalar(select(Moment.id))
    analysis = video("https://youtu.be/a", "Where the Catalan turned")
    own = post("https://x.com/DGukesh/1", "Proud of the team", own=True)
    classic = video("https://youtu.be/c", "The 2024 title: the final moments", "Classic", days_ago=700)
    kept = {gukesh: [Candidate(analysis, moment_id=moment_id, score=0.8), Candidate(own, moment_id=moment_id), Candidate(classic, evergreen=True)]}
    monkeypatch.setattr(feed, "judged", lambda db, ids, now=None: {i: kept[i] for i in ids if i in kept})
    for who in (gukesh, kohli, quiet):
        db.add(Follow(fan_id="fan", player_id=who, alert_rules={}))
        db.commit()

    shown = server.player_feed(gukesh, server.Response(), "fan", db)
    assert [(g.head, [i.kind for i in g.items]) for g in shown.groups] == [("Gukesh wins game 4", ["video", "post"]), ("This week", ["news"]), ("This month", ["news"]), ("Earlier", ["news"])]
    assert shown.groups[0].match == "/match/lichess/abc123"
    assert [i.title for i in shown.evergreen] == ["The 2024 title: the final moments"] and shown.count == 6

    # Kohli has only news, and the quiet one nothing: neither is on Today's rail.
    assert [(s.player_id, s.item.title) for s in server.my_feed("fan", db)["shares"]] == [(gukesh, "Where the Catalan turned")]
    server.feed_feedback(gukesh, analysis.id, {"verdict": "not_about_player"}, "fan", db)
    assert [s.item.title for s in server.my_feed("fan", db)["shares"]] == ["Proud of the team"]
    assert server.my_feed("nobody", db) == {"shares": []}


@needs_db
def test_feedback_goes_with_its_player_and_follows_the_fan_into_an_account(db):
    gukesh, kohli = player(db, "gukesh"), player(db, "kohli")
    for who in (gukesh, kohli):
        feed.feedback(db, "anon", who, feed.item_id("https://example.com/g4"), "not_interested")
    db.commit()

    account_id = accounts.find_or_create(db, "fan@example.com")
    db.commit()
    done = accounts.sign_in(db, account_id, "anon")
    db.commit()
    assert sorted((f.fan_id, f.player_id) for f in db.scalars(select(FeedFeedback))) == [(done.fan_id, gukesh), (done.fan_id, kohli)]

    db.delete(db.get(Player, kohli))  # removing a player takes what hangs off them
    db.commit()
    assert [f.player_id for f in db.scalars(select(FeedFeedback))] == [gukesh]

    accounts.delete_account(db, account_id)
    db.commit()
    assert db.scalars(select(FeedFeedback)).all() == []
