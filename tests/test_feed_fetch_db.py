"""The feed's fetch against Postgres (candidates.py): a poll of a made-up YouTube channel keeps the videos that name
a followed player and pass the hard filters, one row per player and video, asking YouTube only about new ones; a
day later their numbers are read again; the weekly check drops the ones gone; and a player removed takes their
rows with them. Skipped without a throwaway database at alembic head (tests/test_corrections_db.py says how to
make one)."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, delete, func, select, text, update

from sports_follow import bus, candidates, db as db_module
from sports_follow.models import FeedCandidate, Follow, Player, PlayerAlias
from tests.test_feed_fetch import CHANNEL, NOW, Fake, feed, video

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")


class Redis:
    """The few Redis calls the fetch makes (what it refused, remembered a while), in memory."""

    def __init__(self):
        self.data: dict[str, str] = {}

    def mget(self, keys):
        return [self.data.get(k) for k in keys]

    def pipeline(self):
        return self

    def set(self, key, value, ex=None):
        self.data[key] = value

    def execute(self):
        return []


@pytest.fixture
def world(monkeypatch):
    from tests.test_feed_fetch import youtube

    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE feed_candidates, follow, player_alias, player_identity, player RESTART IDENTITY CASCADE"))
    monkeypatch.setattr(db_module, "SessionLocal", db_module.sessionmaker(bind=engine, expire_on_commit=False))
    made = Fake()
    import httpx

    monkeypatch.setattr(youtube, "_client", httpx.Client(transport=httpx.MockTransport(made)))
    monkeypatch.setattr(youtube, "key", lambda: "test-key")
    monkeypatch.setattr(candidates, "channels", lambda source: {CHANNEL: {"cricket"}})
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
    world.feeds[CHANNEL] = feed(
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
    world.feeds[CHANNEL] = feed(("kohlibumrah", "Kohli and Bumrah on the tour", ""))
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
    world.feeds[CHANNEL] = feed(("kohli000001", "Kohli: the chase", ""))
    world.videos["kohli000001"] = video("kohli000001", views=100, likes=4)
    candidates.poll(NOW)
    world.videos["kohli000001"] = video("kohli000001", views=2500, likes=90)
    candidates.poll(NOW + timedelta(hours=6))
    assert world.api_calls("/videos") == 1  # not a day yet
    later = NOW + timedelta(days=1, minutes=5)
    assert candidates.poll(later)["read_again"] == 1
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate))
        assert row is not None and row.features["views"] == 2500 and row.checked_at == later
        assert [(r["views"], r["at"]) for r in row.features["reads"]] == [(100, NOW.isoformat()), (2500, later.isoformat())]
    candidates.poll(later + timedelta(minutes=30))
    assert world.api_calls("/videos") == 2  # read twice, then left to the weekly check


def test_the_weekly_check_drops_the_videos_gone_and_reads_the_rest_again(world):
    kohli = player("Virat Kohli")
    names = ("removed0000", "private0000", "blockedin00", "noembed0000", "fine0000000")
    world.feeds[CHANNEL] = feed(*((v, f"Kohli {v}", "") for v in names))
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
    world.feeds[CHANNEL] = feed(("kohlibumrah", "Kohli and Bumrah", ""))
    world.videos["kohlibumrah"] = video("kohlibumrah")
    candidates.poll(NOW)
    with db_module.session() as s:
        s.execute(delete(Player).where(Player.id == kohli))
    assert rows() == [(bumrah, "kohlibumrah")]
    candidates.poll(NOW + timedelta(minutes=30))
    assert rows() == [(bumrah, "kohlibumrah")]


def test_without_a_key_nothing_is_read(world, monkeypatch):
    from tests.test_feed_fetch import youtube

    player("Virat Kohli")
    monkeypatch.setattr(youtube, "key", lambda: None)
    world.feeds[CHANNEL] = feed(("kohli000001", "Kohli", ""))
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
