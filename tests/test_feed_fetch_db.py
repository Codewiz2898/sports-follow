"""The feed's fetch against Postgres (candidates.py): a poll of a made-up YouTube channel keeps the videos that name
a followed player and pass the hard filters, one row per player and video, asking YouTube only about new ones; a
day later their numbers are read again; the weekly check drops the ones gone; and a player removed takes their
rows with them. Skipped without a throwaway database at alembic head (tests/test_corrections_db.py says how to
make one)."""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import create_engine, delete, func, select, text, update

from sports_follow import bus, candidates, config, db as db_module, feed as feed_module, metrics, server
from sports_follow.adapters import VIDEOS, Upload, youtube
from sports_follow.models import FeedCandidate, Follow, Player, PlayerAlias
from tests.test_feed_fetch import CHANNEL, NOW, Fake, listing, uploads, video
from tests.test_feed_metrics import counts
from tests.test_metrics import measured  # noqa: F401  (the fixture)

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
SOURCE = VIDEOS["youtube"]
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")


class Redis:
    """The few Redis calls the fetch makes (what it refused, remembered a while; the pool), in memory."""

    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}

    def get(self, key):
        return self.data.get(key)

    def mget(self, keys):
        return [self.data.get(k) for k in keys]

    def pipeline(self):
        return self

    def set(self, key, value, ex=None):
        self.data[key] = value
        self.ttl[key] = ex

    def execute(self):
        return []


@pytest.fixture
def world(monkeypatch):
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE feed_candidates, follow, player_alias, player_identity, player RESTART IDENTITY CASCADE"))
    monkeypatch.setattr(db_module, "SessionLocal", db_module.sessionmaker(bind=engine, expire_on_commit=False))
    made = Fake()
    monkeypatch.setattr(youtube, "_client", httpx.Client(transport=httpx.MockTransport(made)))
    monkeypatch.setattr(youtube, "key", lambda: "test-key")
    monkeypatch.setattr(candidates, "channels", lambda source: {CHANNEL: {"cricket"}})
    listing(monkeypatch, CHANNEL)
    monkeypatch.setattr(bus, "sync_redis", lambda: made.redis)
    made.redis = Redis()
    yield made
    engine.dispose()


def player(name: str, sport: str = "cricket", aliases: tuple[str, ...] = (), followed: bool = True) -> int:
    with db_module.session() as s:
        p = Player(slug=name.lower().replace(" ", "-"), name=name, sport=sport, teams=[])
        s.add(p)
        s.flush()
        for a in aliases:
            s.add(PlayerAlias(alias=a, player_id=p.id))
        if followed:
            s.add(Follow(fan_id="fan-1", player_id=p.id, alert_rules={}))
        return p.id


def rows() -> list[tuple[int, str]]:
    with db_module.session() as s:
        return [tuple(r) for r in s.execute(select(FeedCandidate.player_id, FeedCandidate.external_id).order_by(FeedCandidate.player_id, FeedCandidate.external_id)).all()]


def test_a_poll_keeps_the_videos_that_name_a_followed_player_and_pass_the_hard_filters(world):
    kohli = player("Virat Kohli", aliases=("virat-kohli",))
    bumrah = player("Jasprit Bumrah")
    player("Rohit Sharma", followed=False)  # nobody follows him: never looked for
    world.uploads[CHANNEL] = uploads(
        ("kohlibumrah", "Kohli and Bumrah on the tour", "Analysis"),
        ("kohlishort0", "Kohli's best shot", "#shorts"),
        ("bumrahblock", "Bumrah's yorkers", ""),
        ("rohitonly00", "Rohit Sharma's pull", ""),
        ("nobody00000", "Pitch report", "Weather and the toss"),
    )
    world.videos.update({v["id"]: v for v in (
        video("kohlibumrah", views=1000, likes=50), video("kohlishort0", duration="PT40S"), video("bumrahblock", region={"blocked": ["IN"]}),
    )})
    done = candidates.poll(NOW)
    assert rows() == [(kohli, "kohlibumrah"), (bumrah, "kohlibumrah")]
    assert done["kept"] == 2 and done["refused"] == 2 and done["found"] == 4 and done["channels"] == 1
    assert world.api_calls("/videos") == 1  # three new videos, one request
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate).where(FeedCandidate.player_id == kohli))
        assert row is not None and row.source == "youtube" and row.url == "https://www.youtube.com/watch?v=kohlibumrah"
        assert row.candidate["named"] == "kohli" and row.candidate["where"] == "title" and row.candidate["channel_id"] == CHANNEL
        assert row.candidate["details"]["status"]["embeddable"] is True and row.features["views"] == 1000 and row.features["seconds"] == 510
        assert row.fetched_at == row.checked_at == NOW and row.verdict is None and row.score is None and row.judged_at is None
    # Polled again: nothing new to ask about, and the refused aren't asked about again.
    assert candidates.poll(NOW + timedelta(minutes=30))["kept"] == 0
    assert world.api_calls("/videos") == 1


def test_a_video_already_kept_is_kept_for_a_newly_followed_player_without_asking_again(world):
    kohli = player("Virat Kohli")
    world.uploads[CHANNEL] = uploads(("kohlibumrah", "Kohli and Bumrah on the tour", ""))
    world.videos["kohlibumrah"] = video("kohlibumrah")
    candidates.poll(NOW)
    bumrah = player("Jasprit Bumrah")
    assert candidates.poll(NOW + timedelta(minutes=30))["kept"] == 1
    assert rows() == [(kohli, "kohlibumrah"), (bumrah, "kohlibumrah")] and world.api_calls("/videos") == 1
    with db_module.session() as s:
        theirs = s.scalar(select(FeedCandidate).where(FeedCandidate.player_id == bumrah))
        assert theirs is not None and theirs.candidate["named"] == "bumrah" and theirs.features["views"] == 5000


def test_a_day_later_a_kept_video_is_read_again_for_its_pace(world):
    player("Virat Kohli")
    world.uploads[CHANNEL] = uploads(("kohli000001", "Kohli: the chase", ""))
    world.videos["kohli000001"] = video("kohli000001", views=100, likes=4)
    candidates.poll(NOW)
    with db_module.session() as s:  # the judge read it
        s.execute(update(FeedCandidate).values(verdict={"about_player": "subject", "interest": 0.8}, judged_at=NOW, score=0.5))
        s.commit()
    world.videos["kohli000001"] = video("kohli000001", views=2500, likes=90, paid=True)  # its maker declares paid promotion meanwhile
    candidates.poll(NOW + timedelta(hours=6))
    assert world.api_calls("/videos") == 1  # not a day yet
    later = NOW + timedelta(days=1, minutes=5)
    assert candidates.poll(later)["read_again"] == 1
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate))
        assert row is not None and row.features["views"] == 2500 and row.checked_at == later and row.candidate["paid"] is True
        assert row.verdict is None and row.score is None  # judged again before it's served: the rules make it a promo
        assert s.scalar(select(FeedCandidate.id).where(FeedCandidate.verdict.is_(None))) == row.id  # SQL's NULL, not JSON's: in the judge's queue
        assert [(r["views"], r["at"]) for r in row.features["reads"]] == [(100, NOW.isoformat()), (2500, later.isoformat())]
    candidates.poll(later + timedelta(minutes=30))
    assert world.api_calls("/videos") == 2  # read twice, then left to the weekly check


def test_the_weekly_check_drops_the_videos_gone_and_reads_the_rest_again(world):
    kohli = player("Virat Kohli")
    names = ("removed0000", "private0000", "blockedin00", "noembed0000", "fine0000000")
    world.uploads[CHANNEL] = uploads(*((v, f"Kohli {v}", "") for v in names))
    world.videos.update({v: video(v) for v in names})
    candidates.poll(NOW)
    assert len(rows()) == 5
    del world.videos["removed0000"]
    world.videos["private0000"] = video("private0000", privacy="private")
    world.videos["blockedin00"] = video("blockedin00", region={"allowed": ["US"]})
    world.videos["noembed0000"] = video("noembed0000", embeddable=False)
    world.videos["fine0000000"] = video("fine0000000", views=9000)
    assert candidates.check(NOW + timedelta(days=3)) == {"read": 0, "dropped": 0}  # read less than a week ago
    week = NOW + timedelta(days=7)
    assert candidates.check(week) == {"read": 1, "dropped": 4}
    assert rows() == [(kohli, "fine0000000")]
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate))
        assert row is not None and row.checked_at == week and row.features["views"] == 9000 and len(row.features["reads"]) == 2


def test_removing_a_player_takes_their_candidates_and_a_poll_doesnt_bring_them_back(world):
    kohli = player("Virat Kohli")
    bumrah = player("Jasprit Bumrah")
    world.uploads[CHANNEL] = uploads(("kohlibumrah", "Kohli and Bumrah", ""))
    world.videos["kohlibumrah"] = video("kohlibumrah")
    candidates.poll(NOW)
    with db_module.session() as s:
        s.execute(delete(Player).where(Player.id == kohli))
    assert rows() == [(bumrah, "kohlibumrah")]
    candidates.poll(NOW + timedelta(minutes=30))
    assert rows() == [(bumrah, "kohlibumrah")]


def test_without_a_key_nothing_is_read(world, monkeypatch):
    player("Virat Kohli")
    monkeypatch.setattr(youtube, "key", lambda: None)
    world.uploads[CHANNEL] = uploads(("kohli000001", "Kohli", ""))
    assert candidates.poll(NOW)["channels"] == 0 and candidates.check(NOW + timedelta(days=30)) == {"read": 0, "dropped": 0}
    assert world.asked == [] and rows() == []


def test_a_followed_player_is_known_by_their_aliases(world):
    pid = player("Suryakumar Yadav", aliases=("suryakumar-yadav", "sky"))
    with db_module.session() as s:
        [found] = candidates.followed(s)
        assert (found.player_id, found.sport, found.name) == (pid, "cricket", "Suryakumar Yadav") and "sky" in found.aliases
        s.execute(update(Player).where(Player.id == pid).values(sport="Cricket"))
    with db_module.session() as s:
        assert candidates.followed(s)[0].sport == "cricket"
        assert s.scalar(select(func.count()).select_from(FeedCandidate)) == 0


def test_a_premiere_not_aired_yet_is_asked_about_again_within_the_hour(world):
    kohli = player("Virat Kohli")
    world.uploads[CHANNEL] = uploads(("premiere000", "Kohli: the documentary", ""), ("shortclip00", "Kohli in 30 seconds", ""))
    world.videos["premiere000"] = video("premiere000", duration="P0D", live="upcoming")
    world.videos["shortclip00"] = video("shortclip00", duration="PT30S")
    candidates.poll(NOW)
    assert rows() == []
    expiries = {k.rsplit(":", 1)[1]: v for k, v in world.redis.ttl.items()}
    assert expiries == {"premiere000": candidates.REFUSED_AWHILE, "shortclip00": 7 * 86400}
    # It aired: once the hour is up (Redis forgets it), it's asked about again and kept; the Short isn't asked again.
    del world.redis.data["feed:refused:youtube:premiere000"]
    world.videos["premiere000"] = video("premiere000", duration="PT9M")
    candidates.poll(NOW + timedelta(hours=1, minutes=5))
    assert rows() == [(kohli, "premiere000")]
    assert [r.url.params["id"] for r in world.asked if r.url.path.endswith("/videos")] == ["premiere000,shortclip00", "premiere000"]


def test_a_row_no_check_could_read_for_a_month_goes(world, monkeypatch):
    player("Virat Kohli")
    world.uploads[CHANNEL] = uploads(("kohli000001", "Kohli: the chase", ""))
    world.videos["kohli000001"] = video("kohli000001")
    candidates.poll(NOW)
    monkeypatch.setattr(youtube, "key", lambda: None)  # the key taken away: nothing can read it again
    assert candidates.check(NOW + timedelta(days=29)) == {"read": 0, "dropped": 0} and len(rows()) == 1
    assert candidates.check(NOW + timedelta(days=31)) == {"read": 0, "dropped": 1} and rows() == []


@pytest.mark.parametrize("stream", metrics.STREAMS)
def test_every_stream_keeps_a_video_only_from_a_channel_the_players_sport_lists(world, stream, measured):  # noqa: F811
    """A channel's poll, a moment's search and each sweep all keep through candidates.keep: what's from a channel the
    player's sport doesn't list is dropped there, before YouTube is asked about it (BHA-349)."""
    kohli = player("Virat Kohli")
    off = "UC" + "z" * 22  # a small channel no sport lists
    ups = [Upload("listedvid01", CHANNEL, "Made-up Cricket", "Kohli's hundred", "", "https://www.youtube.com/watch?v=listedvid01"),
               Upload("offlistvid1", off, "Sports Yaari", "KOHLI'S HUNDRED 🔥", "", "https://www.youtube.com/watch?v=offlistvid1")]  # fmt: skip
    world.videos.update({v["id"]: v for v in (video("listedvid01"), video("offlistvid1"))})
    got = candidates.keep(SOURCE, [candidates.Found(kohli, "cricket", u, "kohli", "title") for u in ups], NOW, stream=stream)
    assert got == {"kept": 1, "refused": 0, "off_list": 1}
    assert rows() == [(kohli, "listedvid01")]
    [asked] = [r for r in world.asked if r.url.path == "/youtube/v3/videos"]
    assert asked.url.params["id"] == "listedvid01"
    assert counts(measured, "sports_follow.feed.refused") == {("cricket", stream, "off_list"): 1}


def test_reading_a_stored_video_again_learns_its_thumbnail_sizes_and_the_feed_serves_the_biggest(world):
    from sports_follow import feed

    kohli = player("Virat Kohli")
    with db_module.session() as s:  # stored before the sizes were kept (BHA-349): served with hqdefault
        s.add(FeedCandidate(player_id=kohli, source="youtube", external_id="oldrow00001", url="https://www.youtube.com/watch?v=oldrow00001",
                            candidate={"title": "Kohli's hundred", "channel_id": CHANNEL, "sport": "cricket", "details": {"status": {"embeddable": True}}},
                            features={"views": 10}, verdict={"about_player": "subject"}, score=0.5, fetched_at=NOW, checked_at=NOW))  # fmt: skip
    world.videos["oldrow00001"] = video("oldrow00001", thumbs=("default", "high", "maxres"))
    with db_module.session() as s:
        assert feed.judged(s, [kohli], NOW)[kohli][0].item.picture == "https://i.ytimg.com/vi/oldrow00001/hqdefault.jpg"
    assert candidates._again(SOURCE, ["oldrow00001"], NOW)["read"] == 1  # pyright: ignore[reportPrivateUsage]
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate))
        assert row is not None and row.candidate["details"] == {"status": {"embeddable": True}, "snippet": {"thumbnails": ["default", "high", "maxres"]}}
        assert feed.judged(s, [kohli], NOW)[kohli][0].item.picture == "https://i.ytimg.com/vi/oldrow00001/maxresdefault.jpg"


def test_reading_a_stored_video_again_learns_its_shape_and_the_feed_serves_an_upright_one_upright(world):
    from sports_follow import feed

    kohli = player("Virat Kohli")
    with db_module.session() as s:  # stored before its shape was asked (BHA-358): served wide
        s.add(FeedCandidate(player_id=kohli, source="youtube", external_id="shortrow001", url="https://www.youtube.com/watch?v=shortrow001",
                            candidate={"title": "Kohli's catch", "channel_id": CHANNEL, "sport": "cricket", "details": {"status": {"embeddable": True}}},
                            features={"views": 10}, verdict={"about_player": "subject"}, score=0.5, fetched_at=NOW, checked_at=NOW))  # fmt: skip
    world.videos["shortrow001"] = video("shortrow001", size=(1280, 2276))
    with db_module.session() as s:
        [before] = feed.judged(s, [kohli], NOW)[kohli]
        assert before.item.video is not None and not before.item.video.upright
    assert candidates._again(SOURCE, ["shortrow001"], NOW)["read"] == 1  # pyright: ignore[reportPrivateUsage]
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate))
        assert row is not None and row.candidate["details"]["player"] == {"width": 1280, "height": 2276}
        [after] = feed.judged(s, [kohli], NOW)[kohli]
        assert after.item.video is not None and after.item.video.upright


def test_a_video_a_fans_app_said_wont_play_inside_it_stays_unserved_when_found_for_another_player(world):
    kohli = player("Virat Kohli")
    world.uploads[CHANNEL] = listed = uploads(("kohlibumrah", "Kohli and Bumrah on the tour", ""))
    world.videos["kohlibumrah"] = video("kohlibumrah")
    candidates.poll(NOW)
    with db_module.session() as s:
        assert feed_module.unplayable(s, "youtube:kohlibumrah", NOW)
    bumrah = player("Jasprit Bumrah")
    world.uploads[CHANNEL] = listed
    assert candidates.poll(NOW + timedelta(minutes=30))["kept"] == 1
    with db_module.session() as s:
        marks = dict(s.execute(select(FeedCandidate.player_id, FeedCandidate.unplayable_at)).all())
    assert marks == {kohli: NOW, bumrah: NOW}


# ---------------------------------------------------------------- a video fans' apps say won't play (BUG-100)


def reported(world, monkeypatch, pid: int, vid: str, networks: range) -> tuple[list[bool], list[tuple]]:
    """Fans' apps in India, one on each of these networks (81.2.70.n), say the player's video won't play inside them
    (POST .../unplayable), as an app does when YouTube's player refuses it: whether each report took it from every
    Feed, and the jobs they queued."""
    from tests.test_api import KEY, Queue, Redis as ApiRedis

    api, queue = ApiRedis(), Queue()
    monkeypatch.setitem(server.state, "redis", api)
    monkeypatch.setitem(server.state, "arq", queue)
    monkeypatch.setattr(server, "cookie_key", lambda: KEY)
    item = feed_module.item_id(f"https://www.youtube.com/watch?v={vid}")

    async def say(n: int) -> httpx.Response:
        transport = httpx.ASGITransport(app=server.app, client=(f"81.2.70.{n}", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(f"/api/players/{pid}/feed/{item}/unplayable", headers={"Cookie": f"{config.FAN_COOKIE}={server.sign_fan(f'fan-{n}')}"},
                                     json={"timezone": "Asia/Kolkata"})  # fmt: skip

    marked = []
    for n in networks:
        answer = asyncio.run(say(n))
        assert answer.status_code == 200, answer.text
        marked.append(answer.json()["marked"])
    return marked, queue.jobs


def marks() -> dict[tuple[int, str], bool]:
    with db_module.session() as s:
        return {(p, v): at is not None for p, v, at in s.execute(select(FeedCandidate.player_id, FeedCandidate.external_id, FeedCandidate.unplayable_at))}


def test_three_networks_saying_a_video_wont_play_take_it_from_every_feed_at_once_and_ask_youtube_nothing(world, monkeypatch):
    """BUG-100: three networks' apps saying a video won't play inside them take it off every player's row at once, and
    nothing is queued or asked of YouTube: a report never spends its quota."""
    kohli, bumrah = player("Virat Kohli"), player("Jasprit Bumrah")
    world.uploads[CHANNEL] = uploads(("kohlibumrah", "Kohli and Bumrah on the tour", ""))
    world.videos["kohlibumrah"] = video("kohlibumrah")
    candidates.poll(NOW)
    asked = world.api_calls()
    assert reported(world, monkeypatch, kohli, "kohlibumrah", range(1, 4)) == ([False, False, True], [])
    assert marks() == {(kohli, "kohlibumrah"): True, (bumrah, "kohlibumrah"): True}
    assert world.api_calls() == asked


def test_the_weekly_check_drops_a_video_fans_apps_took_out_once_youtube_says_it_cant_be_shown(world):
    """BUG-100: a video taken from every Feed by fans' apps stays listed in the admin panel until an admin puts it back,
    unless YouTube says at its weekly check (candidates.check) that it's removed, private or no longer embeddable: then
    it goes, marked or not, as any stored video does. One YouTube still says plays stays, marked, for the admin."""
    kohli = player("Virat Kohli")
    player("Jasprit Bumrah")
    world.uploads[CHANNEL] = uploads(("kohlibumrah", "Kohli and Bumrah on the tour", ""), ("kohlicover0", "Kohli's cover drive", ""),
                                ("kohlipull00", "Kohli's pull shot", ""), ("kohlisweep0", "Kohli's sweep", ""))  # fmt: skip
    world.videos.update({v["id"]: v for v in (video("kohlibumrah"), video("kohlicover0"), video("kohlipull00"), video("kohlisweep0"))})
    candidates.poll(NOW)
    with db_module.session() as s:
        for vid in ("kohlibumrah", "kohlicover0", "kohlipull00", "kohlisweep0"):
            assert feed_module.unplayable(s, f"youtube:{vid}", NOW)
    world.videos["kohlibumrah"] = video("kohlibumrah", embeddable=False)  # its owner turned embedding off since
    world.videos["kohlicover0"] = video("kohlicover0", privacy="private")
    del world.videos["kohlipull00"]  # removed
    assert candidates.check(NOW + timedelta(days=8))["dropped"] == 3  # every player's row of each of the three
    assert marks() == {(kohli, "kohlisweep0"): True}


def test_a_video_an_admin_hid_stays_hidden_when_found_for_another_player(world):
    """BHA-438: an admin's hide is the video's: a later poll that keeps it for someone else keeps it hidden."""
    from sports_follow import rank

    kohli = player("Virat Kohli")
    world.uploads[CHANNEL] = listed = uploads(("kohlibumrah", "Kohli and Bumrah on the tour", ""))
    world.videos["kohlibumrah"] = video("kohlibumrah")
    candidates.poll(NOW)
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate.id).where(FeedCandidate.player_id == kohli))
        assert row is not None and rank.review(s, row, "hidden")
    bumrah = player("Jasprit Bumrah")
    world.uploads[CHANNEL] = listed
    assert candidates.poll(NOW + timedelta(minutes=30))["kept"] == 1
    with db_module.session() as s:
        said = dict(s.execute(select(FeedCandidate.player_id, FeedCandidate.review)).all())
    assert said == {kohli: "hidden", bumrah: "hidden"}


def test_a_video_an_admin_hides_while_a_poll_asks_youtube_is_hidden_for_the_player_it_keeps_it_for(world, monkeypatch):
    """BUG-288: a poll read whether a stored video was hidden, asked YouTube about the new ones, and then kept the stored
    one for another player as it had read it: an admin's hide (or fans' apps' "won't play") meanwhile missed that row."""
    from sports_follow import rank

    kohli = player("Virat Kohli")
    world.uploads[CHANNEL] = uploads(("kohlibumrah", "Kohli and Bumrah on the tour", ""), ("bumrahyork1", "Bumrah's yorker", ""))
    world.videos["kohlibumrah"], world.videos["bumrahyork1"] = video("kohlibumrah"), video("bumrahyork1")
    candidates.poll(NOW)
    bumrah = player("Jasprit Bumrah")
    asked = SOURCE.facts

    def facts(ids, *args, **kwargs):  # an admin hides the stored one while YouTube is asked about the new one
        with db_module.session() as s:
            row = s.scalar(select(FeedCandidate.id).where(FeedCandidate.player_id == kohli))
            assert row is not None and rank.review(s, row, "hidden")
        return asked(ids, *args, **kwargs)

    monkeypatch.setattr(SOURCE, "facts", facts)
    assert candidates.poll(NOW + timedelta(minutes=30))["kept"] == 2
    with db_module.session() as s:
        said = {(p, v): r for p, v, r in s.execute(select(FeedCandidate.player_id, FeedCandidate.external_id, FeedCandidate.review))}
    assert said == {(kohli, "kohlibumrah"): "hidden", (bumrah, "kohlibumrah"): "hidden", (bumrah, "bumrahyork1"): None}


def test_a_poll_keeps_pool_players_videos_and_a_teams_video_for_its_players_as_their_teams(world):
    """BHA-382: the pool's players (feedpool.ids: nobody follows them) are looked for beside followed ones, and a video
    naming a team and none of its players is kept for them as the team's."""
    import json

    from sports_follow import feedpool

    kohli = player("Virat Kohli")
    with db_module.session() as s:
        s.get(Player, kohli).teams = ["India", "Royal Challengers Bengaluru"]  # pyright: ignore[reportOptionalMemberAccess]
    mandhana = player("Smriti Mandhana", followed=False)
    player("Rohit Sharma", followed=False)  # neither followed nor in the pool: never looked for
    world.redis.data[feedpool.POOL_KEY] = json.dumps({"players": {"cricket": [mandhana]}})
    world.uploads[CHANNEL] = uploads(
        ("indaus00001", "India vs Australia, 2nd ODI | Highlights", ""),
        ("mandhana001", "Smriti Mandhana's 117 at Brisbane", ""),
        ("rohit000001", "Rohit Sharma's pull", ""),
        ("indwomen001", "India Women vs Australia Women | Highlights", ""),
    )
    world.videos.update({v["id"]: v for v in (video("indaus00001"), video("mandhana001"), video("rohit000001"), video("indwomen001"))})
    done = candidates.poll(NOW)
    assert rows() == [(kohli, "indaus00001"), (mandhana, "mandhana001")] and done["kept"] == 2
    with db_module.session() as s:
        team = s.scalar(select(FeedCandidate).where(FeedCandidate.external_id == "indaus00001"))
        mine = s.scalar(select(FeedCandidate).where(FeedCandidate.external_id == "mandhana001"))
        assert team is not None and (team.candidate["about"], team.candidate["team"], team.candidate["named"]) == ("team", "India", "india")
        assert mine is not None and "about" not in mine.candidate and mine.candidate["named"] == "smriti mandhana"


def test_a_served_video_brings_its_sources_icon_and_one_made_for_kids_goes_at_its_next_read(world):
    """Each video item carries its source's icon for its card, linked there to the video where it plays (YouTube's
    III.F.2); a stored video made for kids isn't served, and is dropped the next time it's read (III.E.4.j)."""
    from sports_follow import feed

    kohli = player("Virat Kohli")
    with db_module.session() as s:
        s.add(FeedCandidate(player_id=kohli, source="youtube", external_id="kidsrow0001", url="https://www.youtube.com/watch?v=kidsrow0001",
                            candidate={"title": "Kohli for kids", "channel_id": CHANNEL, "sport": "cricket", "details": {"status": {"embeddable": True}}},
                            features={"views": 10}, verdict={"about_player": "subject"}, score=0.5, fetched_at=NOW, checked_at=NOW))  # fmt: skip
    with db_module.session() as s:
        [served] = feed.judged(s, [kohli], NOW)[kohli]
    assert served.item.video is not None and served.item.video.mark is not None and SOURCE.mark is not None
    mark = served.item.video.mark
    assert (mark.name, mark.min_height, [(p.fill, p.d) for p in mark.paths]) == (SOURCE.mark.name, SOURCE.mark.min_height, list(SOURCE.mark.paths))
    # Kept before the filter, with the status it was read with: not served even before its next read drops it.
    with db_module.session() as s:
        s.execute(update(FeedCandidate).values(candidate=FeedCandidate.candidate.op("||")(
            text("""'{"details": {"status": {"embeddable": true, "madeForKids": true}}}'::jsonb"""))))
    with db_module.session() as s:
        assert feed.judged(s, [kohli], NOW).get(kohli, []) == []
    world.videos["kidsrow0001"] = video("kidsrow0001")
    world.videos["kidsrow0001"]["status"]["madeForKids"] = True
    assert candidates._again(SOURCE, ["kidsrow0001"], NOW) == {"read": 0, "dropped": 1}  # pyright: ignore[reportPrivateUsage]
    assert rows() == []
