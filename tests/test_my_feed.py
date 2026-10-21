"""The fan's own Feed (feed.page, GET /api/me/feed/items, BHA-344): every followed player's items at once, under the
day each was published in the fan's days, ranked within a day with the players taking turns, a page at a time
with a cursor that carries on where the last page ended, without what the fan hid. The made-up items stand for
the judge's rows; the routes need Postgres and are skipped without one (tests/test_feed.py has the fixture).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from sports_follow import feed, server
from sports_follow.adapters import VIDEOS
from sports_follow.feed import Candidate, Item, Video
from sports_follow.models import FeedFeedback, Follow, Moment

from .test_feed import db, news, needs_db, player  # noqa: F401  (db is a fixture)

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
IST = 330  # minutes east of UTC


def item(n: str, hours_ago: float = 1, kind: str = "video", published: str | None = None) -> Item:
    url = f"https://example.com/{n}"
    return Item(id=feed.item_id(url), kind=kind, tag="Analysis", title=n, url=url, source="Made-up", reason="Made up.",  # pyright: ignore[reportArgumentType]
                published_at=published or (NOW - timedelta(hours=hours_ago)).isoformat(),
                video=Video(youtube_id="dQw4w9WgXcQ") if kind == "video" else None)  # fmt: skip


# ---------------------------------------------------------------- without the database


def test_players_take_turns_so_no_two_in_a_row_are_one_players_while_another_has_one():
    ranked = [(1, Candidate(item("a1"))), (1, Candidate(item("a2"))), (1, Candidate(item("a3"))), (2, Candidate(item("b1"))), (3, Candidate(item("c1")))]
    turns = feed._turns(ranked, None)  # pyright: ignore[reportPrivateUsage]
    assert [c.item.title for _, c in turns] == ["a1", "b1", "a2", "c1", "a3"]
    # Only when nobody else has one left does a player follow themself; a day carries on from the one before.
    assert [c.item.title for _, c in feed._turns([(1, Candidate(item("a1"))), (1, Candidate(item("a2")))], None)] == ["a1", "a2"]  # pyright: ignore[reportPrivateUsage]
    assert [c.item.title for _, c in feed._turns([(1, Candidate(item("a1"))), (2, Candidate(item("b1")))], 1)] == ["b1", "a1"]  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(("published", "tz", "day"), [
    ((NOW - timedelta(hours=2)).isoformat(), 0, "Today"),
    ("2026-10-20T20:00:00+00:00", 0, "Yesterday"),
    ("2026-10-20T20:00:00+00:00", IST, "Today"),  # 01:30 on the 21st in India: today there
    ("2026-10-21", IST, "Today"),  # a story known only by its day is under that day wherever the fan is
    ("2026-10-15T09:00:00Z", 0, "This week"),  # six days ago
    ("2026-10-14T09:00:00Z", 0, "This month"),  # seven
    ("2026-09-21T09:00:00Z", 0, "This month"),  # thirty
    ("2026-09-20T09:00:00Z", 0, "Earlier"),
    ("2026-10-22T09:00:00Z", 0, "Today"),  # ahead of now: today
])
def test_an_item_is_under_the_day_it_was_published_where_the_fan_is(published, tz, day):
    assert feed._day(Candidate(item("x", published=published)), NOW, tz) == day  # pyright: ignore[reportPrivateUsage]


def test_an_undated_item_is_earlier_and_a_pinned_old_one_this_week():
    undated = Candidate(item("x").model_copy(update={"published_at": None}))
    assert feed._day(undated, NOW, 0) == "Earlier"  # pyright: ignore[reportPrivateUsage]
    assert feed._day(Candidate(item("x", published="2026-03-01T00:00:00Z"), pinned=True), NOW, 0) == "This week"  # pyright: ignore[reportPrivateUsage]


def test_a_cursor_keeps_its_time_and_place_and_one_never_made_is_refused():
    cursor = feed._write_cursor(NOW - timedelta(hours=1), 20, "0123456789abcdef")  # pyright: ignore[reportPrivateUsage]
    assert feed._read_cursor(cursor, NOW) == (NOW - timedelta(hours=1), 20, "0123456789abcdef")  # pyright: ignore[reportPrivateUsage]
    # One from days ago carries on in today's order, from its last item.
    old = feed._write_cursor(NOW - timedelta(days=5), 20, "0123456789abcdef")  # pyright: ignore[reportPrivateUsage]
    assert feed._read_cursor(old, NOW) == (NOW, 20, "0123456789abcdef")  # pyright: ignore[reportPrivateUsage]
    for bad in ("", "not base64!", "W10", "WzEsMiwzXQ", feed._write_cursor(NOW, 1, "x").replace("A", "B")):  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(ValueError, match="isn't a page"):
            feed._read_cursor(bad, NOW)  # pyright: ignore[reportPrivateUsage]


def test_a_video_has_youtubes_thumbnail_that_every_video_has():
    youtube = VIDEOS["youtube"]
    assert youtube.picture("dQw4w9WgXcQ") == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
    assert youtube.picture("not an id") is None


def test_a_storys_picture_is_the_image_its_row_kept_and_only_an_https_one():
    from types import SimpleNamespace

    def story(image: object) -> Item:
        row = SimpleNamespace(url="https://example.com/s", candidate={"title": "A story", "channel": "Daily Wicket", "published": NOW.isoformat(), "image": image},
                              features={}, verdict={}, score=1.0, moment_id=None, review=None, judged_at=NOW, fetched_at=NOW)  # fmt: skip
        c = feed._story(row, NOW)  # pyright: ignore[reportPrivateUsage]
        assert c is not None and c.arrived == NOW
        return c.item

    assert story("https://img.example.com/a.jpg?w=800").picture == "https://img.example.com/a.jpg?w=800"
    assert story("http://img.example.com/a.jpg").picture is None
    assert story('https://img.example.com/a.jpg" onerror="x').picture is None
    assert story(None).picture is None


# ---------------------------------------------------------------- against the database


def follow(db, fan: str, *players: int) -> None:
    for p in players:
        db.add(Follow(fan_id=fan, player_id=p, alert_rules={}))
        db.commit()


@needs_db
def test_the_fans_feed_is_by_day_with_players_taking_turns_and_pages_with_a_cursor(db, monkeypatch):  # noqa: F811
    photo = {"url": "/api/photos/abc.jpg", "credit": "Photo: someone", "page": "https://commons.wikimedia.org/wiki/File:X.jpg", "author": "someone",
             "licence": "CC BY 4.0", "crop": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}}  # fmt: skip
    gukesh = player(db, "gukesh", {"player": {"name": "Gukesh D", "sport": "chess", "photo": photo}, "news": []})
    kohli = player(db, "kohli", {"player": {"name": "Virat Kohli", "sport": "cricket"}, "news": [news("Kohli's knock", "https://example.com/k", "2026-10-20")]})
    quiet = player(db, "quiet", {"player": {"name": "Quiet One", "sport": "chess"}, "news": []})
    db.add(Moment(key="m1", player_id=gukesh, kind="final", level="key", title="Gukesh wins game 4", url="/match/lichess/abc123", created_at=NOW - timedelta(hours=9)))
    db.commit()
    moment_id = db.scalar(select(Moment.id))
    kept = {  # each from a channel of its own: a page holds one video from each (feed._page)
        gukesh: [Candidate(item("g1", 1), score=0.9, moment_id=moment_id, arrived=NOW - timedelta(minutes=30)), Candidate(item("g2", 2), score=0.8, channel="c2"),
                 Candidate(item("g3", 3), score=0.7, channel="c3"), Candidate(item("g-old", 24 * 20), score=0.99)],
        kohli: [Candidate(item("k1", 4, kind="post"), score=0.5, arrived=NOW - timedelta(hours=3))],
    }  # fmt: skip
    kept[kohli][0].item.post = feed.Post(platform="X", own=True)
    monkeypatch.setattr(feed, "judged", lambda db, ids, now=None: {i: kept[i] for i in ids if i in kept})
    follow(db, "fan", gukesh, kohli, quiet)

    def get(**kw):
        return feed.page(db, "fan", now=NOW, **kw)

    whole = get(limit=50, tz=IST)
    # Today: Gukesh's best, then Kohli's turn, then Gukesh's two left; Kohli's card news (no judged news of his) is yesterday's.
    assert [(e.day, e.item.title) for e in whole.items] == [("Today", "g1"), ("Today", "k1"), ("Today", "g2"), ("Today", "g3"),
                                                             ("Yesterday", "Kohli's knock"), ("This month", "g-old")]  # fmt: skip
    assert whole.count == 6 and whole.cursor is None
    first = whole.items[0]
    assert (first.player.id, first.player.name, first.player.sport, first.player.photo) == (gukesh, "Gukesh D", "chess", photo)
    assert (first.moment, first.match) == ("Gukesh wins game 4", "/match/lichess/abc123")
    assert first.arrived == (NOW - timedelta(minutes=30)).isoformat() and whole.items[1].player.photo is None
    # The circles: whoever had something new latest first, the quiet one (nothing) not at all; the newest for the tab's dot.
    assert [(c.id, c.count) for c in whole.players] == [(gukesh, 4), (kohli, 2)]
    assert whole.newest == (NOW - timedelta(minutes=30)).isoformat()

    # A page at a time: the cursor carries on after the last item, in the order it was read in, so an item hidden
    # since (Gukesh's first) doesn't hand his turn back and slide g2 past the fan.
    one = get(limit=2, tz=IST)
    assert [e.item.title for e in one.items] == ["g1", "k1"] and one.cursor and one.count == 6
    feed.feedback(db, "fan", gukesh, kept[gukesh][0].item.id, "not_interested")
    db.execute(update(FeedFeedback).values(created_at=NOW + timedelta(minutes=5)))  # after the first page was read
    db.commit()
    two = get(limit=2, tz=IST, cursor=one.cursor)
    assert [e.item.title for e in two.items] == ["g2", "g3"]
    three = get(limit=2, tz=IST, cursor=two.cursor)
    assert [e.item.title for e in three.items] == ["Kohli's knock", "g-old"] and three.cursor is None

    # Hidden items stay out, of the fan who hid them only, and a fresh read orders without them; one player's
    # alone with ?player=; limit=0 is the circles alone.
    assert [e.item.title for e in get(limit=50).items][:3] == ["k1", "g2", "g3"] and get(limit=50).count == 5  # as read before the hide
    assert [e.item.title for e in feed.page(db, "fan", now=NOW + timedelta(minutes=10)).items][:3] == ["g2", "k1", "g3"]
    assert feed.page(db, "someone", now=NOW).count == 0
    follow(db, "someone", gukesh)
    assert feed.page(db, "someone", now=NOW).count == 4
    assert [e.item.title for e in get(player=kohli, tz=IST).items] == ["k1", "Kohli's knock"] and len(get(player=kohli).players) == 2
    empty = get(limit=0)
    assert empty.items == [] and empty.cursor is None and empty.count == 5 and empty.newest


@needs_db
def test_the_route_refuses_a_cursor_it_never_made_and_answers_no_follows_with_nothing(db):  # noqa: F811
    assert server.my_feed_items(server.Response(), fan="nobody", db=db).model_dump() == {"items": [], "cursor": None, "count": 0, "newest": None, "players": []}
    with pytest.raises(server.HTTPException) as refused:
        server.my_feed_items(server.Response(), cursor="bogus", fan="nobody", db=db)
    assert refused.value.status_code == 422


@needs_db
def test_a_story_two_players_hold_is_one_card_hidden_from_either_and_the_picked_players_copy(db, monkeypatch):  # noqa: F811
    kohli = player(db, "kohli", {"player": {"name": "Virat Kohli", "sport": "cricket"}, "news": []})
    rohit = player(db, "rohit", {"player": {"name": "Rohit Sharma", "sport": "cricket"}, "news": []})
    both = item("Kohli and Rohit see India home", 2, kind="news")
    kept = {kohli: [Candidate(both, score=0.9)], rohit: [Candidate(both, score=0.4), Candidate(item("r1", 3, kind="news"), score=0.3)]}
    monkeypatch.setattr(feed, "judged", lambda db, ids, now=None: {i: kept[i] for i in ids if i in kept})
    follow(db, "fan", kohli, rohit)
    whole = feed.page(db, "fan", now=NOW)
    assert [(e.player.id, e.item.title) for e in whole.items] == [(kohli, "Kohli and Rohit see India home"), (rohit, "r1")]
    # Picking Rohit shows his copy of it; hiding it from Kohli's card hides it from Rohit's too.
    assert [e.item.title for e in feed.page(db, "fan", player=rohit, now=NOW).items] == ["Kohli and Rohit see India home", "r1"]
    feed.feedback(db, "fan", kohli, both.id, "not_interested")
    db.execute(update(FeedFeedback).values(created_at=NOW - timedelta(minutes=1)))
    db.commit()
    assert [e.item.title for e in feed.page(db, "fan", now=NOW).items] == ["r1"]
    assert [e.item.title for e in feed.page(db, "fan", player=rohit, now=NOW).items] == ["r1"]


@needs_db
def test_only_the_last_two_days_light_the_dot_and_a_later_page_leaves_out_what_came_in_since(db, monkeypatch):  # noqa: F811
    gukesh = player(db, "gukesh", {"player": {"name": "Gukesh D", "sport": "chess"}, "news": []})
    # A months-old video the sweep only just found comes in fresh, but isn't new: no dot, no ring.
    found = Candidate(item("old one", 24 * 150), score=0.9, arrived=NOW - timedelta(minutes=5))
    kept = {gukesh: [found]}
    monkeypatch.setattr(feed, "judged", lambda db, ids, now=None: {i: kept[i] for i in ids if i in kept})
    follow(db, "fan", gukesh)
    quiet = feed.page(db, "fan", now=NOW)
    assert [e.day for e in quiet.items] == ["Earlier"] and quiet.newest is None and quiet.players[0].newest is None

    kept[gukesh] = [Candidate(item("a", 1), score=0.9, arrived=NOW - timedelta(hours=1), channel="a"),
                    Candidate(item("b", 2), score=0.8, arrived=NOW - timedelta(hours=2), channel="b"), found]  # fmt: skip
    first = feed.page(db, "fan", limit=1, now=NOW)
    assert [e.item.title for e in first.items] == ["a"] and first.newest == (NOW - timedelta(hours=1)).isoformat()
    # Judged after the first page was read, and better than what's left: it waits for the next first page.
    kept[gukesh].append(Candidate(item("late", 0.5), score=0.95, arrived=NOW + timedelta(minutes=10), channel="late"))
    later = NOW + timedelta(minutes=20)
    nxt = feed.page(db, "fan", cursor=first.cursor, limit=5, now=later)
    assert [e.item.title for e in nxt.items] == ["b", "old one"]
    assert [e.item.title for e in feed.page(db, "fan", now=later).items][:2] == ["late", "a"]
