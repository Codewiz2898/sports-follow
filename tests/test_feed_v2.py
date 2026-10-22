"""Feed v2 (BHA-349): official and top channels only, tidier titles, For you's mix of followed and other players,
and the layout the apps draw, a setting the admin panel changes live. The made-up items stand for the judge's rows;
the tests against the database are skipped without one (tests/test_feed.py has the fixture)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sports_follow import admin, candidates, config, controls, feed, server, sports, titles
from sports_follow.adapters import VIDEOS
from sports_follow.feed import Candidate
from sports_follow.models import FeedCandidate, Follow, Moment, Player, PlayerCard

from .test_admin import admin_cookie, password, sign_in  # noqa: F401  (password is a fixture)
from .test_api import api  # noqa: F401  (a fixture)
from .test_feed import db, needs_db  # noqa: F401  (db is a fixture)
from .test_my_feed import NOW, follow, item

CRICKET = sports.BY_KEY["cricket"]
ICC = "UCt2JXOLNxqry7B_4rRZME3Q"  # on cricket's list
SPORTS_YAARI = "UCjFw-0Vdfy2KW78NClGECXw"  # a small channel the list left out


# ---------------------------------------------------------------- titles


@pytest.mark.parametrize(("title", "channel", "keep", "shown"), [
    # The board's own example (FeedRules): emoji, the channel's name and the shouting off; the words stay.
    ('🔴PRESS CONFERENCE: "BHUVI HAS WON THE IPL TWICE..!" SHREYAS IYER READY | Sports Yaari', "Sports Yaari", ("Shreyas Iyer",),
     'Press conference: "Bhuvi has won the IPL twice!" Shreyas Iyer ready'),
    ("VIRAT KOHLI'S 100 VS AUS 🔥🔥 #viratkohli #cricket #shorts", "Cricket Fan", ("Virat Kohli",), "Virat Kohli's 100 vs AUS"),
    ("KOHLI'S FIRST TEST HUNDRED IN ENGLAND, EDGBASTON 2018 | England & Wales Cricket Board", "England & Wales Cricket Board", ("Virat Kohli",),
     "Kohli's first Test hundred in England, Edgbaston 2018"),
    ("SMRITI MANDHANA SMASHES 117 IN THE 1ST ODI vs AUSTRALIA - Highlights", "ICC", ("Smriti Mandhana",),
     "Smriti Mandhana smashes 117 in the 1st ODI vs Australia - highlights"),
    ("WHAT I LEARNED FROM MAGNUS CARLSEN - GothamChess", "GothamChess", ("Magnus Carlsen",), "What I learned from Magnus Carlsen"),
    # Partly in capitals: those are names, left as they are.
    ("IND vs AUS 2nd ODI Highlights | India vs Australia | Star Sports", "Star Sports", (), "IND vs AUS 2nd ODI Highlights · India vs Australia"),
    # Each part between the bars is read alone, and the bars become the app's dots.
    ("FODEN HAT-TRICK FOR CITY! | Man City 5-1 AEK Athens | Champions League", "Man City", ("Phil Foden", "City", "Man City"),
     "Foden hat-trick for City! · Man City 5-1 AEK Athens · Champions League"),
    ("Kohli hits a brilliant century, Windies v India 2nd ODI 2019", "Windies Cricket", (), "Kohli hits a brilliant century, Windies v India 2nd ODI 2019"),
    # A hashtag in the middle keeps its word; a part with someone else's name stays.
    ("Watch #Gukesh beat Ding | FIDE World Championship", "ChessBase India", (), "Watch Gukesh beat Ding · FIDE World Championship"),
    ("Gukesh 🇮🇳 beats Magnus!!! ⭐⭐ #chess #shorts", "FIDE", (), "Gukesh beats Magnus!"),
    ("  Haaland   and Anderson on City's 3-0 win  ", "Sky Sports Premier League", (), "Haaland and Anderson on City's 3-0 win"),
    ("🔥🔥🔥", "Someone", (), "🔥🔥🔥"),  # nothing left: as it came
    # The channel's name comes off the end, never a part that holds more, nor one word of it alone.
    ("Liverpool 1 - 2 Man City", "Man City", (), "Liverpool 1 - 2 Man City"),
    ("Arsenal 2 - 1 Chelsea", "Chelsea", (), "Arsenal 2 - 1 Chelsea"),
    ("Highlights: Arsenal - Chelsea", "Chelsea FC", (), "Highlights: Arsenal - Chelsea"),
    ("The final over - ICC Men's T20 World Cup 2024", "ICC", (), "The final over - ICC Men's T20 World Cup 2024"),
    ("Djokovic vs Alcaraz | Final | Wimbledon 2024", "Wimbledon", (), "Djokovic vs Alcaraz · Final · Wimbledon 2024"),
    ("Kohli's 50 | Sky Sports", "Sky Sports Cricket", (), "Kohli's 50"),  # the start of its name
    ("Sinner returns to world #1", "Tennis TV", (), "Sinner returns to world #1"),  # a number, not a hashtag
    ("Wow?! What a finish", "ICC", (), "Wow? What a finish"),  # a question stays one
    # A kept phrase only whole: "Test" is cricket's, "New" alone isn't a name.
    ("MI VS CSK: ROHIT SHARMA ON THE NEW BALL IN THE TEST", "Star Sports", ("Rohit Sharma",), "MI vs CSK: Rohit Sharma on the new ball in the Test"),
    # A search's title stored as it came, HTML-escaped (before BHA-358): read as written.
    ("Kylian Mbappe &amp; Bijan Robinson SWAP sports!", "NFL", (), "Kylian Mbappe & Bijan Robinson SWAP sports!"),
    ("Gukesh&#39;s win &quot;for India&quot;", "FIDE", (), "Gukesh's win \"for India\""),
])  # fmt: skip
def test_a_title_is_tidied_as_its_served(title, channel, keep, shown):
    assert titles.tidy(title, channel, CRICKET.title_words, keep) == shown


def test_only_a_title_mostly_in_capitals_is_shouting():
    assert titles.shouted("PRESS CONFERENCE: SHREYAS IYER READY")
    assert not titles.shouted("IND vs AUS 2nd ODI Highlights")  # three of six
    assert not titles.shouted("IND v AUS")  # too few words to tell
    assert titles.sentence_case("TENZ REACTS TO VCT MASTERS", ("TenZ", "VCT", "Masters")) == "TenZ reacts to VCT Masters"
    assert titles.sentence_case("I THINK I'M READY. BUT ARE YOU?") == "I think I'm ready. But are you?"


# ---------------------------------------------------------------- the channels' list


def test_a_video_counts_only_from_a_channel_its_players_sport_lists():
    assert candidates.on_list("cricket", ICC)
    assert not candidates.on_list("football", ICC)  # another sport's
    assert not candidates.on_list("cricket", SPORTS_YAARI)
    assert not candidates.on_list("golf", ICC) and not candidates.on_list("cricket", "")  # a sport no module knows lists none
    # Every game's pros take the esports list, and so does a pro whose game can't be told.
    vct = "UCA1d3HFGFUmkKr2JIUA5Vlw"
    assert candidates.on_list("valorant", vct) and candidates.on_list("esports", vct) and candidates.on_list("bgmi", vct)


def test_every_sport_lists_official_channels_and_a_few_top_creators():
    named = {"cricket": 10, "football": 15, "chess": 6, "tennis": 6, "basketball": 3, "esports": 10}
    for key, least in named.items():
        assert len(sports.BY_KEY[key].video_channels) >= least, key
    gone = {"UCVXCo0W9pk2dDkEBNLhTt7A", SPORTS_YAARI, "UCjGC9zWjHHF6CG1j8YfJK9g", "UC5oTaFLOFlLNeAJt_dt5rBw"}  # Sports Tak, Sports Yaari, RevSportz, CricTracker
    assert not gone & set(CRICKET.video_channels)
    assert {"IPL", "ODI", "ICC", "T20I"} <= set(CRICKET.title_words)


# ---------------------------------------------------------------- For you's mix


def test_for_you_takes_three_of_every_five_from_followed_players_then_whichever_has_some():
    mine, theirs = [f"m{i}" for i in range(7)], [f"t{i}" for i in range(3)]
    assert feed._mix(mine, theirs) == ["m0", "m1", "m2", "t0", "t1", "m3", "m4", "m5", "t2", "m6"]  # pyright: ignore[reportPrivateUsage]
    assert feed._mix([], ["t0", "t1"]) == ["t0", "t1"]  # pyright: ignore[reportPrivateUsage]
    assert feed._mix(["m0"], ["t0", "t1", "t2"]) == ["m0", "t0", "t1", "t2"]  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------- the layout


def test_the_layout_is_the_servers_setting_until_the_admin_panel_changes_it(monkeypatch):
    monkeypatch.setattr(feed, "FEED_LAYOUT", "edge")
    assert feed.layout() == "edge" and config.FEED_LAYOUT in feed.LAYOUTS
    controls.set_feed_layout("full")
    assert feed.layout() == "full" and admin.layout_view() == {"layout": "full", "default": config.FEED_LAYOUT, "set": "full", "choices": ["edge", "full"]}
    with pytest.raises(ValueError):
        controls.set_feed_layout("grid")
    assert feed.layout() == "full"
    controls.set_feed_layout(None)
    assert feed.layout() == "edge"
    monkeypatch.setattr(feed, "FEED_LAYOUT", "carousel")  # a setting that isn't one: the first layout
    assert feed.layout() == "edge"
    assert controls.parse({controls.FEED_LAYOUT: "grid"}).feed_layout is None  # a row written by hand, of the wrong shape


def test_the_admin_route_sets_the_layout_live_and_the_config_says_it(monkeypatch, api, password):  # noqa: F811
    monkeypatch.setattr(feed, "FEED_LAYOUT", "edge")
    cookie = admin_cookie(sign_in(api))
    assert api.call("GET", "/api/config").json()["feed_layout"] == "edge"
    assert api.call("PUT", "/api/admin/feed/layout", body={"layout": "full"}).status_code == 401  # the panel's alone
    answer = api.call("PUT", "/api/admin/feed/layout", body={"layout": "full"}, headers=cookie)  # not taken for a row's id
    assert answer.status_code == 200 and answer.json()["layout"]["layout"] == "full"
    assert api.call("GET", "/api/config").json()["feed_layout"] == "full"
    assert api.call("PUT", "/api/admin/feed/layout", body={"layout": 3}, headers=cookie).status_code == 400
    assert api.call("PUT", "/api/admin/feed/layout", body={"layout": "grid"}, headers=cookie).status_code == 400
    back = api.call("PUT", "/api/admin/feed/layout", body={"layout": None}, headers=cookie).json()["layout"]
    assert back == {"layout": "edge", "default": config.FEED_LAYOUT, "set": None, "choices": ["edge", "full"]}


# ---------------------------------------------------------------- the picture


def test_a_videos_picture_is_the_biggest_youtube_has_for_it():
    youtube = VIDEOS["youtube"]
    assert youtube.picture("dQw4w9WgXcQ", {"snippet": {"thumbnails": ["default", "high", "maxres", "medium", "standard"]}}) == "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
    assert youtube.picture("dQw4w9WgXcQ", {"snippet": {"thumbnails": ["default", "high", "medium", "standard"]}}) == "https://i.ytimg.com/vi/dQw4w9WgXcQ/sddefault.jpg"
    assert youtube.picture("dQw4w9WgXcQ", {"snippet": {}}) == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"  # read before the sizes were kept
    assert youtube.picture("dQw4w9WgXcQ") == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"


# ---------------------------------------------------------------- against the database


def made_player(db, name: str, sport: str = "cricket", followers: int = 0, served: bool = True, source: str = "youtube") -> int:
    """A player with a card, followed by this many others, and a row the Feed serves when served."""
    p = Player(slug=name.lower().replace(" ", "-"), name=name, sport=sport, teams=[])
    db.add(p)
    db.flush()
    db.add(PlayerCard(player_id=p.id, status="ready", card={"player": {"name": name, "sport": sport}, "news": []}))
    for n in range(followers):
        db.add(Follow(fan_id=f"other-{name}-{n}", player_id=p.id, alert_rules={}))
    if served:
        db.add(FeedCandidate(player_id=p.id, source=source, external_id=f"v{p.id:010d}", url=f"https://www.youtube.com/watch?v=v{p.id:010d}",
                             candidate={"sport": sport, "channel_id": ICC}, features={}, verdict={}, score=0.5, fetched_at=NOW, checked_at=NOW))  # fmt: skip
    db.commit()
    return p.id


@needs_db
def test_for_yous_others_are_the_fans_sports_first_then_a_big_moment_today_then_the_most_followed(db):  # noqa: F811
    from sqlalchemy import text

    db.execute(text("TRUNCATE feed_candidates RESTART IDENTITY CASCADE"))
    mine = made_player(db, "Shreyas Iyer")
    star = made_player(db, "Virat Kohli", followers=9)
    quiet = made_player(db, "Smriti Mandhana", followers=2)
    hundred = made_player(db, "Abhishek Sharma", followers=1)
    keeper = made_player(db, "Kylian Mbappe", sport="football", followers=20)
    made_player(db, "Nobody Serves", followers=50, served=False)  # nothing to serve: never mixed in
    made_player(db, "Only Stories", followers=60, source="publisher_rss")  # stories only: For you takes others' videos alone
    db.add(Moment(key="h", player_id=hundred, kind="hundred", level="key", priority=90, title="Abhishek's hundred", url="/", created_at=NOW - timedelta(hours=3)))
    db.add(Moment(key="o", player_id=quiet, kind="fifty", level="key", priority=70, title="Mandhana's fifty", url="/", created_at=NOW - timedelta(hours=3)))  # not big enough
    db.commit()
    follow(db, "fan", mine)
    # Cricket first, the hundred today first of those; each with its tier (For you serves a tier before the next).
    assert list(feed.others(db, [mine], NOW).items()) == [(hundred, 0), (star, 1), (quiet, 1), (keeper, 3)]
    assert list(feed.others(db, [], NOW).items()) == [(hundred, 0), (keeper, 1), (star, 1), (quiet, 1), (mine, 1)]  # following nobody: every sport alike
    assert list(feed.others(db, [mine], NOW + timedelta(days=2)))[0] == star  # the hundred's day is over


@needs_db
def test_for_you_mixes_in_other_players_videos_says_whose_and_pages_while_following_is_the_fans_own(db, monkeypatch):  # noqa: F811
    a = made_player(db, "Shreyas Iyer", served=False)
    b = made_player(db, "Virat Kohli", served=False)
    x = made_player(db, "Gukesh D", sport="chess", served=False)
    y = made_player(db, "Magnus Carlsen", sport="chess", served=False)
    kept = {
        a: [Candidate(item(f"a{i}", i + 1), score=0.9 - i / 10, channel=f"a{i}") for i in range(4)],
        b: [Candidate(item(f"b{i}", i + 1), score=0.8 - i / 10, channel=f"b{i}") for i in range(2)],
        x: [Candidate(item(f"x{i}", i + 1), score=0.7 - i / 10, channel=f"x{i}") for i in range(3)],
        y: [Candidate(item("y0", 2), score=0.6), Candidate(item("ynews", 1, kind="news"), score=0.99)],  # an other's news never comes
    }  # fmt: skip
    monkeypatch.setattr(feed, "judged", lambda db, ids, now=None: {i: kept[i] for i in ids if i in kept})
    # Magnus's tier before Gukesh's (the fan's sport, say): his video leads theirs though Gukesh's is newer and better.
    monkeypatch.setattr(feed, "others", lambda db, followed, now: {p: t for p, t in ((y, 0), (x, 1), (a, 0)) if p not in followed})
    follow(db, "fan", a, b)

    mine = feed.page(db, "fan", now=NOW, limit=50)
    assert mine.tab == "following" and {e.player.id for e in mine.items} == {a, b} and all(e.following for e in mine.items)
    mixed = feed.page(db, "fan", now=NOW, limit=50, tab=feed.FOR_YOU)
    assert mixed.tab == "for_you" and mixed.layout in feed.LAYOUTS
    assert [(e.item.title, e.following) for e in mixed.items] == [
        ("a0", True), ("b0", True), ("a1", True), ("y0", False), ("x0", False),
        ("b1", True), ("a2", True), ("a3", True), ("x1", False), ("x2", False),
    ]
    assert [c.id for c in mixed.players] == [c.id for c in mine.players]  # the circles are the fan's own
    # A page at a time, carrying on where the last ended.
    first = feed.page(db, "fan", now=NOW, limit=4, tab=feed.FOR_YOU)
    second = feed.page(db, "fan", now=NOW + timedelta(minutes=5), cursor=first.cursor, limit=4, tab=feed.FOR_YOU)
    third = feed.page(db, "fan", now=NOW + timedelta(minutes=6), cursor=second.cursor, limit=4, tab=feed.FOR_YOU)
    assert [e.item.title for e in (*first.items, *second.items, *third.items)] == [e.item.title for e in mixed.items] and third.cursor is None
    # One player picked: theirs alone, whichever tab.
    assert {e.player.id for e in feed.page(db, "fan", now=NOW, player=b, tab=feed.FOR_YOU).items} == {b}
    with pytest.raises(ValueError):
        feed.page(db, "fan", now=NOW, tab="everything")


@needs_db
def test_a_fan_who_follows_nobody_gets_for_you_all_from_the_top_players_and_following_empty(db, monkeypatch):  # noqa: F811
    x = made_player(db, "Gukesh D", sport="chess", served=False)
    monkeypatch.setattr(feed, "judged", lambda db, ids, now=None: {x: [Candidate(item("x0"), score=0.5), Candidate(item("x1", 2), score=0.4, channel="c")]} if x in ids else {})
    monkeypatch.setattr(feed, "others", lambda db, followed, now: {x: 3})
    assert feed.page(db, "new-fan", now=NOW, tab=feed.FOR_YOU).items[0].following is False
    assert [e.item.title for e in feed.page(db, "new-fan", now=NOW, tab=feed.FOR_YOU).items] == ["x0", "x1"]
    assert feed.page(db, "new-fan", now=NOW).items == []


@needs_db
def test_the_route_takes_the_tab_and_refuses_one_that_isnt(db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(feed, "others", lambda db, followed, now: {})
    assert server.my_feed_items(server.Response(), tab="for_you", fan="nobody", db=db).tab == "for_you"
    with pytest.raises(server.HTTPException) as refused:
        server.my_feed_items(server.Response(), tab="all", fan="nobody", db=db)
    assert refused.value.status_code == 422


@needs_db
def test_a_served_videos_title_is_tidied_and_its_picture_the_biggest_there_is(db):  # noqa: F811
    from sqlalchemy import text

    db.execute(text("TRUNCATE feed_candidates RESTART IDENTITY CASCADE"))
    pid = made_player(db, "Shreyas Iyer", served=False)
    db.add(FeedCandidate(player_id=pid, source="youtube", external_id="iyerpress01", url="https://www.youtube.com/watch?v=iyerpress01",
                         candidate={"sport": "cricket", "channel": "Sony Sports Network", "channel_id": "UC_WKb6N9iTGc77hxwXLDrbA", "published": NOW.isoformat(),
                                    "title": "🔴SHREYAS IYER ON THE IPL AUCTION | Sony Sports Network", "details": {"snippet": {"thumbnails": ["high", "maxres"]}}},
                         features={"seconds": 300}, verdict={"kind": "interview_or_analysis"}, score=0.5, fetched_at=NOW, checked_at=NOW))  # fmt: skip
    db.commit()
    [c] = feed.judged(db, [pid], datetime.now(timezone.utc))[pid]
    assert c.item.title == "Shreyas Iyer on the IPL auction" and c.item.source == "Sony Sports Network"
    assert c.item.picture == "https://i.ytimg.com/vi/iyerpress01/maxresdefault.jpg"
    stored = db.scalar(text("SELECT candidate->>'title' FROM feed_candidates"))
    assert stored == "🔴SHREYAS IYER ON THE IPL AUCTION | Sony Sports Network"  # tidied as it's served, never as it's stored


@needs_db
def test_for_yous_others_last_two_months_are_one_list_best_first_whatever_the_day_and_older_ones_after(db, monkeypatch):  # noqa: F811
    """BHA-382: a strong video of three weeks ago comes before a weak one of today's; one over OTHERS_WINDOW old comes
    after them both, however strong. The fan's own players stay in their days."""
    a = made_player(db, "Shreyas Iyer", served=False)
    x = made_player(db, "Gukesh D", sport="chess", served=False)
    y = made_player(db, "Magnus Carlsen", sport="chess", served=False)
    z = made_player(db, "Hikaru Nakamura", sport="chess", served=False)
    kept = {
        a: [Candidate(item("a0", 2), score=0.2, channel="a0"), Candidate(item("a1", 24 * 21), score=0.9, channel="a1")],
        x: [Candidate(item("x-weak-today", 2), score=0.3, channel="x0")],
        y: [Candidate(item("y-strong-3wk", 24 * 21), score=0.8, channel="y0")],
        z: [Candidate(item("z-old-best", 24 * 90), score=0.95, channel="z0")],
    }  # fmt: skip
    monkeypatch.setattr(feed, "judged", lambda db, ids, now=None: {i: kept[i] for i in ids if i in kept})
    monkeypatch.setattr(feed, "others", lambda db, followed, now: {p: 0 for p in (x, y, z)})
    follow(db, "fan", a)
    mixed = feed.page(db, "fan", now=NOW, limit=50, tab=feed.FOR_YOU)
    assert [e.item.title for e in mixed.items] == ["a0", "a1", "y-strong-3wk", "x-weak-today", "z-old-best"]
    assert [e.day for e in mixed.items][2:] == [feed.THIS_MONTH, feed.TODAY, feed.EARLIER]  # each keeps its own day


@needs_db
def test_for_you_shows_a_video_a_followed_player_holds_as_theirs_in_its_place_not_as_an_others(db, monkeypatch):  # noqa: F811
    # BUG-122: one video kept for Kohli (followed) and for Rohit (not) came under Rohit, with a Follow button, among
    # the others, and left Kohli's place: the others' list was made first and took it.
    kohli = made_player(db, "Virat Kohli", served=False)
    rohit = made_player(db, "Rohit Sharma", served=False)
    both = item("both", 2)
    kept = {
        kohli: [Candidate(item("k0", 1), score=0.9, channel="k0"), Candidate(both, score=0.5, channel="icc")],
        rohit: [Candidate(both, score=0.5, channel="icc"), Candidate(item("r1", 3), score=0.4, channel="r1")],
    }
    monkeypatch.setattr(feed, "judged", lambda db, ids, now=None: {i: kept[i] for i in ids if i in kept})
    monkeypatch.setattr(feed, "others", lambda db, followed, now: {p: 0 for p in (rohit,) if p not in followed})
    follow(db, "fan", kohli)
    mixed = feed.page(db, "fan", now=NOW, limit=50, tab=feed.FOR_YOU)
    assert [(e.item.title, e.player.id, e.following) for e in mixed.items] == [("k0", kohli, True), ("both", kohli, True), ("r1", rohit, False)]
