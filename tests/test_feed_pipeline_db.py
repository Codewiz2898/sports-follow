"""The Feed's pipeline end to end against Postgres (BHA-301): the curator has the judge read new candidates
(rank.judge), scores the ones it lets through and ties each to the moment it's about (rank.rescore); the Feed
serves them as videos beside the news (feed.feed_for), faded by age, one a channel, two a moment, the
evergreen ones apart, a fan's hides applied, and Today's rail takes each player's top one; an admin's word
and a channel's trust stand over the judge's; and if the videos can't be read, a feed is the news alone.
The judge and YouTube are made up; so is every player, channel and video. Skipped without a throwaway
database at alembic head (tests/test_corrections_db.py says how to make one)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine, literal_column, select, text, update
from sqlalchemy.orm import sessionmaker

from sports_follow import admin, candidates, controls, curate, curator, db as db_module, feed, rank
from sports_follow.adapters import youtube
from sports_follow.models import FeedCandidate, FeedFeedback, Follow, Moment, Player, PlayerAlias, PlayerCard
from tests.test_feed_fetch import Fake, listing, video as yt_video

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime.now(timezone.utc).replace(microsecond=0)  # the Feed serves as of now
EDITS, TALKS, STATS, OTHER, COACH = ("UC" + c * 22 for c in "efghi")  # made-up channels; all but OTHER are listed
NEWS = {"player": {"name": "Asha Verma"}, "news": [{"headline": "Verma named captain", "url": "https://example.com/captain", "source": "Daily Wicket",
                                                    "published": (NOW - timedelta(days=2)).isoformat(), "summary": ""}]}  # fmt: skip


@pytest.fixture
def made(monkeypatch):
    engine = create_engine(URL or "postgresql+psycopg://")
    with engine.begin() as c:
        c.execute(text("TRUNCATE feed_candidates, feed_feedback, follow, moment, player_card, player_alias, player RESTART IDENTITY CASCADE"))
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "SessionLocal", maker)
    monkeypatch.setattr(rank, "listed_channels", lambda: {EDITS, TALKS, STATS, COACH})
    listing(monkeypatch, EDITS, TALKS, STATS, COACH)
    yield maker
    engine.dispose()


def asha() -> int:
    with db_module.session() as s:
        p = Player(slug="asha-verma", name="Asha Verma", sport="cricket", teams=["Riverside XI"], socials=[{"platform": "x", "handle": "ashaverma"}])
        s.add(p)
        s.flush()
        s.add(PlayerAlias(alias="ashu", player_id=p.id))
        s.add(PlayerCard(player_id=p.id, status="ready", card=NEWS))
        s.add(Follow(fan_id="fan", player_id=p.id, alert_rules={}))
        return p.id


def moment(player_id: int, kind: str, title: str, hours_ago: float, priority: int, level: str = "key") -> int:
    with db_module.session() as s:
        m = Moment(key=f"{kind}-{hours_ago}", player_id=player_id, kind=kind, level=level, priority=priority, title=title, url="/match/espn/401", created_at=NOW - timedelta(hours=hours_ago))
        s.add(m)
        s.flush()
        return m.id


def stored(player_id: int, vid: str, title: str, channel: str, hours_ago: float, views=(1_000, 20_000), likes=(10, 800), verdict: dict | None = None) -> int:
    """A candidate as the fetch keeps it: read when found, and again six hours ago."""
    published = NOW - timedelta(hours=hours_ago)
    first = published + timedelta(hours=1)
    reads = [{"at": first.isoformat(), "views": views[0], "likes": likes[0]}]
    if NOW - timedelta(hours=6) > first + timedelta(hours=1):
        reads.append({"at": (NOW - timedelta(hours=6)).isoformat(), "views": views[1], "likes": likes[1]})
    with db_module.session() as s:
        row = FeedCandidate(
            player_id=player_id, source="youtube", external_id=vid, url=f"https://www.youtube.com/watch?v={vid}",
            candidate={"title": title, "description": "", "channel": f"Channel {channel[2]}", "channel_id": channel, "published": published.isoformat(), "sport": "cricket",
                       "details": {"status": {"embeddable": True}}},
            features={"views": reads[-1]["views"], "likes": reads[-1]["likes"], "seconds": 420, "reads": reads},
            fetched_at=first, checked_at=NOW - timedelta(hours=6), judged_at=NOW if verdict else None,
            **({"verdict": verdict} if verdict else {}),  # unjudged: SQL NULL, as the fetch leaves it (not JSON null)
        )  # fmt: skip
        s.add(row)
        s.flush()
        return row.id


def said(**kw) -> dict:
    return {"about_player": "subject", "by": "fan_or_creator", "kind": "match_moment", "interest": 0.7, "risks": [], "sponsored": False,
            "reason": "A fan edit of her hundred.", "stage": "model", **kw}  # fmt: skip


def rows() -> dict[str, FeedCandidate]:
    with db_module.session() as s:
        return {r.external_id: r for r in s.scalars(select(FeedCandidate))}


def page(maker, fan: str = "fan", player_id: int = 1) -> feed.Feed:
    s = maker()
    try:
        found = feed.feed_for(s, player_id, fan)
        assert found is not None
        return found
    finally:
        s.close()


def titles(f: feed.Feed) -> list[tuple[str, list[str]]]:
    return [(g.head, [i.title for i in g.items]) for g in f.groups]


# ---------------------------------------------------------------- L1: judged, scored, tied to a moment


def test_the_curator_judges_whats_new_scores_what_passes_and_ties_it_to_its_moment(made, monkeypatch):
    pid = asha()
    hundred = moment(pid, "hundred", "Verma's hundred", 10, 90)
    moment(pid, "lineup", "Verma starts", 11, 50)  # before the game: never a head
    moment(pid, "on", "Verma comes on", 9, 99, level="minor")  # minor: never a head
    stored(pid, "edit0000001", "Asha Verma's hundred, every boundary", EDITS, 8)
    stored(pid, "promo000001", "Asha Verma's bat, 20% off", TALKS, 7)
    stored(pid, "namesake001", "Asha Verma the singer", STATS, 6)
    stored(pid, "slowmodel01", "Verma on the hundred", OTHER, 5)
    stored(pid, "older000001", "Verma's last season", COACH, 24 * 5, verdict=said(reason="Judged long ago."))
    asked: list[curate.Candidate] = []

    def judge_many(cands):
        cands = list(cands)
        asked.extend(cands)
        verdicts = {"edit": said(), "promo": said(kind="promo", sponsored=True, reason="Selling a bat."), "namesake": said(about_player="no", reason="Another Asha Verma.")}
        return [curate.Verdict.model_validate(verdicts[c.url.split("=")[1].rstrip("0123456789")]) if c.url.split("=")[1].rstrip("0123456789") in verdicts else None for c in cands]

    monkeypatch.setattr(curate, "judge_many", judge_many)
    assert rank.judge(NOW) == {"judged": 3, "unjudged": 1}
    assert [c.url[-11:] for c in asked] == ["slowmodel01", "namesake001", "promo000001", "edit0000001"]  # newest first, the judged one left alone
    first = asked[-1]
    assert (first.player.name, first.player.aliases, first.player.teams, first.player.sport) == ("Asha Verma", ["ashu"], ["Riverside XI"], "cricket")
    assert [(h.platform, h.handle) for h in first.player.handles] == [("x", "ashaverma")]
    assert first.title == "Asha Verma's hundred, every boundary" and first.author_handle == EDITS and first.engagement.views == 1_000

    assert rank.rescore(NOW) == {"scored": 4, "served": 2, "changed": 4}  # every judged one gets its moment
    got = rows()
    assert got["edit0000001"].score and got["edit0000001"].moment_id == hundred and got["edit0000001"].verdict["reason"] == "A fan edit of her hundred."
    assert got["promo000001"].score is None and got["namesake001"].score is None  # kept, never served
    assert got["slowmodel01"].verdict is None and got["slowmodel01"].judged_at is None  # tried again next run
    assert got["older000001"].score and got["older000001"].moment_id is None  # published long before the hundred
    assert rank.rescore(NOW)["changed"] == 0  # nothing new: nothing written


def test_rescore_from_the_command_works_scores_out_again_and_rejudge_asks_the_judge_again(made, monkeypatch, capsys):
    pid = asha()
    stored(pid, "edit0000001", "Verma's hundred", EDITS, 8, verdict=said(about_player="no"))
    monkeypatch.setattr(curate, "judge_many", lambda cands: [curate.Verdict.model_validate(said()) for _ in list(cands)])
    assert curator.main(["rescore"]) == 0
    assert rows()["edit0000001"].score is None and "1 judged candidates: 0 served" in capsys.readouterr().out
    assert curator.main(["rescore", "--rejudge"]) == 0
    assert rows()["edit0000001"].score and "judged 1" in capsys.readouterr().out


def test_a_young_videos_numbers_are_read_again_every_few_hours_for_two_days(made, monkeypatch):
    pid = asha()
    fake = Fake()
    monkeypatch.setattr(youtube, "_client", httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(youtube, "key", lambda: "test-key")
    with db_module.session() as s:
        for vid, found_ago, checked_ago in (("young000due", 10, 7), ("young0fresh", 10, 2), ("old00000000", 60, 30)):
            s.add(FeedCandidate(player_id=pid, source="youtube", external_id=vid, url=f"https://www.youtube.com/watch?v={vid}", candidate={},
                                features={"views": 10, "likes": 1, "reads": [{"at": (NOW - timedelta(hours=found_ago)).isoformat(), "views": 10, "likes": 1}]},
                                fetched_at=NOW - timedelta(hours=found_ago), checked_at=NOW - timedelta(hours=checked_ago)))  # fmt: skip
    fake.videos = {v: yt_video(v, views=9_000, likes=400) for v in ("young000due", "young0fresh", "old00000000")}
    assert candidates.fresh_reads(NOW) == {"read": 1, "dropped": 0}
    assert fake.api_calls("/videos") == 1 and "young000due" in str(fake.asked[-1].url)
    got = rows()
    assert got["young000due"].features["views"] == 9_000 and [r["views"] for r in got["young000due"].features["reads"]] == [10, 9_000]
    assert got["young0fresh"].features["views"] == 10 and got["old00000000"].features["views"] == 10


# ---------------------------------------------------------------- L2: what a fan sees


def test_the_feed_serves_scored_videos_beside_the_news_one_a_channel_two_a_moment_the_evergreen_apart(made):
    pid = asha()
    moment(pid, "hundred", "Verma's hundred", 60, 90)
    stored(pid, "edit0000001", "Every boundary of the hundred", EDITS, 58, verdict=said(interest=0.9))
    stored(pid, "talk0000001", "Verma talks through the hundred", TALKS, 57, verdict=said(kind="interview_or_analysis", by="official", reason="Her own words after the hundred."))
    stored(pid, "stat0000001", "The hundred in numbers", STATS, 56, verdict=said(interest=0.3))  # a third about the moment: left out
    stored(pid, "edit0000002", "Verma's fielding", EDITS, 4, views=(10, 10), likes=(1, 1), verdict=said(interest=0.05))  # the first's channel: one a page
    stored(pid, "tips0000001", "Verma's batting, explained", COACH, 10, verdict=said(kind="interview_or_analysis", interest=0.6, reason="A coach breaks down her stance."))
    stored(pid, "classic0001", "The catch everyone remembers", COACH, 24 * 60, views=(900_000, 980_000), likes=(20_000, 22_000), verdict=said(reason="Her catch from 2024."))
    stored(pid, "sponsor0001", "Verma's new bat", TALKS, 3, verdict=said(sponsored=True))
    rank.rescore()

    f = page(made, player_id=pid)
    assert titles(f) == [
        ("Verma's hundred", ["Every boundary of the hundred", "Verma talks through the hundred"]),
        ("This week", ["Verma's batting, explained", "Verma named captain"]),
    ]
    assert f.groups[0].moment and f.groups[0].match == "/match/espn/401"
    assert [i.title for i in f.evergreen] == ["The catch everyone remembers"] and f.count == 5
    talk = f.groups[0].items[1]
    assert (talk.kind, talk.tag, talk.reason, talk.source) == ("video", "Interview", "Her own words after the hundred.", "Channel f")
    assert talk.video is not None and talk.video.youtube_id == "talk0000001" and talk.video.duration == 420 and talk.video.views == 20_000
    assert f.groups[0].items[0].tag == "Fan edit" and f.groups[1].items[1].kind == "news"

    # The fan hides the edit of the hundred: the next of its channel takes its place, for them alone.
    s = made()
    feed.feedback(s, "fan", pid, feed.item_id("https://www.youtube.com/watch?v=edit0000001"), "not_interested")
    s.commit()
    s.close()
    mine, theirs = page(made, player_id=pid), page(made, "someone-else", player_id=pid)
    assert titles(mine) == [
        ("Verma's hundred", ["Verma talks through the hundred", "The hundred in numbers"]),
        ("This week", ["Verma's batting, explained", "Verma's fielding", "Verma named captain"]),
    ]
    assert titles(theirs) == titles(f)

    # Today's rail: her top video, never the news or an evergreen one.
    s = made()
    shares = feed.shares_for(s, "fan")
    s.close()
    assert [(x.player_id, x.item.kind, x.item.title) for x in shares] == [(pid, "video", "Verma's batting, explained")]  # fresher than the talk, as strong


def test_a_videos_score_fades_with_age_so_todays_good_one_leads_last_weeks_best(made):
    pid = asha()
    stored(pid, "old00000001", "Last week's best", EDITS, 24 * 6, verdict=said(interest=1.0))
    stored(pid, "new00000001", "Today's good one", TALKS, 3, verdict=said(interest=0.6))
    rank.rescore()
    assert rows()["old00000001"].score > rows()["new00000001"].score  # type: ignore[operator]
    assert titles(page(made, player_id=pid))[0][1][:2] == ["Today's good one", "Last week's best"]


def test_if_the_videos_cant_be_read_a_feed_is_the_news_alone(made, monkeypatch):
    pid = asha()
    stored(pid, "edit0000001", "Every boundary", EDITS, 8, verdict=said())
    rank.rescore()

    # The query itself fails (as before a migration, or past a statement timeout): the savepoint keeps the request's
    # session usable, and the feed is the news.
    def broken_select(*columns):
        query = select(*columns)
        return query.add_columns(literal_column("no_such_column")) if columns and columns[0] is FeedCandidate.player_id else query

    monkeypatch.setattr(feed, "select", broken_select)
    s = made()
    try:
        f = feed.feed_for(s, pid, "fan")
        assert f is not None and [i.kind for g in f.groups for i in g.items] == ["news"]
        assert feed.shares_for(s, "fan") == []
        assert s.scalar(select(Player.name).where(Player.id == pid)) == "Asha Verma"  # the request's session still works
    finally:
        s.close()
    # A bug in the ranking after the rows are read: the same.
    monkeypatch.undo()
    monkeypatch.setattr(rank, "evergreen", lambda *_: 1 / 0)
    s = made()
    try:
        f = feed.feed_for(s, pid, "fan")
        assert f is not None and [i.kind for g in f.groups for i in g.items] == ["news"]
    finally:
        s.close()


def test_a_pinned_video_leads_however_old_and_never_among_the_evergreen(made):
    pid = asha()
    old = stored(pid, "classic0001", "The catch everyone remembers", COACH, 24 * 40, views=(900_000, 980_000), likes=(20_000, 22_000), verdict=said())
    stored(pid, "new00000001", "Today's good one", TALKS, 3, verdict=said(interest=1.0))
    rank.rescore()
    assert [i.title for i in page(made, player_id=pid).evergreen] == ["The catch everyone remembers"]
    admin.review_candidate(old, "pinned")
    f = page(made, player_id=pid)
    assert f.evergreen == [] and [i.title for g in f.groups for i in g.items if i.kind == "video"] == ["The catch everyone remembers", "Today's good one"]


# ---------------------------------------------------------------- the admin's word, the channel's trust, fans' reports


def test_an_admins_word_stands_over_the_judges_and_a_channels_trust_scales_or_hides_it(made):
    pid = asha()
    edit = stored(pid, "edit0000001", "Every boundary", EDITS, 8, verdict=said())
    namesake = stored(pid, "namesake001", "Asha Verma the singer", TALKS, 7, verdict=said(about_player="no", reason="Another Asha Verma."))
    rank.rescore()
    served = lambda: [i.title for g in page(made, player_id=pid).groups for i in g.items if i.kind == "video"]  # noqa: E731
    assert served() == ["Every boundary"]

    assert admin.review_candidate(namesake, "approved")
    assert served() == ["Every boundary", "Asha Verma the singer"] or served() == ["Asha Verma the singer", "Every boundary"]
    assert admin.review_candidate(namesake, "pinned")
    assert served()[0] == "Asha Verma the singer" and rows()["namesake001"].score >= rank.PIN  # type: ignore[operator]
    assert admin.review_candidate(edit, "hidden") and served() == ["Asha Verma the singer"]
    assert admin.review_candidate(edit, None) and "Every boundary" in served()
    assert not admin.review_candidate(9999, "hidden")
    with pytest.raises(ValueError):
        admin.review_candidate(edit, "promoted")

    before = rows()["edit0000001"].score
    admin.set_channel_trust(EDITS, 0.4)
    assert rows()["edit0000001"].score == pytest.approx(before * 0.4, abs=1e-3)  # type: ignore[operator]
    admin.set_channel_trust(EDITS, 0)
    assert rows()["edit0000001"].score is None and "Every boundary" not in served()
    admin.set_channel_trust(EDITS, None)
    assert rows()["edit0000001"].score == pytest.approx(before)
    for bad in ((EDITS, 0.5), (EDITS, True), (EDITS, "high"), ("not a channel!", 1)):
        with pytest.raises(ValueError):
            admin.set_channel_trust(*bad)
    assert controls.current().channel_trust == {}

    # An approved video stands over its channel's Hidden: the admin's word on the one video is the nearer one.
    assert admin.review_candidate(edit, "approved")
    admin.set_channel_trust(EDITS, 0)
    assert "Every boundary" in served()


def test_signed_in_fans_reports_lower_their_channels_trust_to_half_at_most_and_anonymous_ones_dont(made):
    pid = asha()
    stored(pid, "edit0000001", "Every boundary", EDITS, 8, verdict=said())
    stored(pid, "edit0000002", "Every wicket", EDITS, 30, verdict=said())
    rank.rescore()
    before = rows()["edit0000001"].score
    wicket = feed.item_id("https://www.youtube.com/watch?v=edit0000002")

    def say(*fans: str, verdict: str = "report") -> None:
        with db_module.session() as s:
            s.add_all(FeedFeedback(fan_id=fan, player_id=pid, item_id=wicket, verdict=verdict) for fan in fans)

    say(*(f"anon-{i}" for i in range(40)))  # a new cookie is free: anonymous reports hide it from them alone
    say("acct-1", "acct-2", "acct-3", verdict="not_interested")  # taste, not the channel's fault
    rank.rescore()
    assert rows()["edit0000001"].score == pytest.approx(before)
    say("acct-4", "acct-5", "acct-6", "acct-7")
    rank.rescore()
    assert rows()["edit0000001"].score == pytest.approx(before / 2, abs=1e-3)  # type: ignore[operator]
    say(*(f"acct-x{i}" for i in range(40)))
    rank.rescore()
    assert rows()["edit0000001"].score == pytest.approx(before * rank.FEEDBACK_FLOOR, abs=1e-3)  # type: ignore[operator]
    admin.set_channel_trust(EDITS, 1)  # the admin's word stands as it is
    assert rows()["edit0000001"].score == pytest.approx(before)


def test_the_admin_feed_page_lists_candidates_with_their_verdicts_and_every_channels_trust(made):
    pid = asha()
    stored(pid, "edit0000001", "Every boundary", EDITS, 8, verdict=said())
    stored(pid, "namesake001", "Asha Verma the singer", TALKS, 7, verdict=said(about_player="no"))
    stored(pid, "waiting0001", "Not judged yet", OTHER, 6)
    rank.rescore()
    s = made()
    try:
        everything = admin.feed_page(s)
        assert [c["title"] for c in everything["candidates"]] == ["Not judged yet", "Asha Verma the singer", "Every boundary"]
        first = everything["candidates"][2]
        assert first["served"] and first["passes"] and first["video"] == {"youtube_id": "edit0000001"} and first["player"]["name"] == "Asha Verma"
        assert first["verdict"]["reason"] == "A fan edit of her hundred." and first["views"] == 1_000
        assert everything["totals"] == {"all": 3, "unjudged": 1, "served": 1} and everything["next"] is None
        assert [c["title"] for c in admin.feed_page(s, "served")["candidates"]] == ["Every boundary"]
        assert [c["title"] for c in admin.feed_page(s, "held")["candidates"]] == ["Asha Verma the singer"]
        assert [c["title"] for c in admin.feed_page(s, "unjudged")["candidates"]] == ["Not judged yet"]
        with pytest.raises(ValueError):
            admin.feed_page(s, "secret")
        channels = {c["channel_id"]: c for c in everything["channels"]}
        assert set(channels) == {EDITS, TALKS, STATS, OTHER, COACH}
        assert (channels[EDITS]["listed"], channels[EDITS]["effective"], channels[EDITS]["candidates"], channels[EDITS]["served"]) == (True, 1.0, 1, 1)
        assert (channels[OTHER]["listed"], channels[OTHER]["default"]) == (False, rank.UNLISTED)
        assert channels[STATS]["candidates"] == 0 and channels[STATS]["name"] == ""
    finally:
        s.close()


def test_a_stored_video_from_a_channel_the_sport_no_longer_lists_stops_being_served_but_an_admins_approval_stands(made):
    pid = asha()
    kept = stored(pid, "listed00001", "Verma's hundred", EDITS, 5, verdict=said())
    dropped = stored(pid, "unlisted001", "VERMA'S HUNDRED 🔥", OTHER, 4, verdict=said())
    with db_module.session() as s:  # served before the list was cut (BHA-349)
        s.execute(update(FeedCandidate).values(score=0.5))
    assert rank.rescore(NOW)["served"] == 1
    got = rows()
    assert got["listed00001"].score and got["unlisted001"].score is None
    assert [i.title for g in page(made, player_id=pid).groups for i in g.items if i.kind == "video"] == ["Verma's hundred"]
    admin.review_candidate(dropped, "approved")
    assert rows()["unlisted001"].score is not None and rows()["listed00001"].id == kept


# ---------------------------------------------------------------- a video that won't play inside the app (BHA-380)


def test_a_video_a_fans_app_says_wont_play_inside_it_leaves_every_feed_and_saying_it_again_changes_nothing(made):
    """Its owner keeps it off other sites, which videos.list didn't say (embeddable, no region rule): the app's
    player said so, so no fan is served it again, whatever an admin said of it, and the admin panel says why."""
    pid = asha()
    with db_module.session() as s:
        p = Player(slug="ravi-rao", name="Ravi Rao", sport="cricket", teams=["Riverside XI"])
        s.add(p)
        s.flush()
        s.add(PlayerCard(player_id=p.id, status="ready", card={"player": {"name": "Ravi Rao"}, "news": []}))
        other = p.id
    blocked = stored(pid, "blocked0001", "Verma's hundred, every boundary", EDITS, 5, verdict=said())
    asha_too = stored(other, "blocked0001", "Verma and Rao's stand", EDITS, 5, verdict=said())
    stored(pid, "plays000001", "Verma talks through the hundred", TALKS, 4, verdict=said())
    rank.rescore()
    admin.review_candidate(blocked, "pinned")
    assert [i.title for g in page(made, player_id=pid).groups for i in g.items if i.kind == "video"] == ["Verma's hundred, every boundary", "Verma talks through the hundred"]
    item = feed.item_id("https://www.youtube.com/watch?v=blocked0001")
    s = made()
    try:
        assert feed.unplayable(s, pid, item, NOW) is True
        assert feed.unplayable(s, pid, item) is False  # already: nothing changes
        with pytest.raises(ValueError):
            feed.unplayable(s, pid, "not an id")
        with pytest.raises(LookupError):
            feed.unplayable(s, pid, feed.item_id("https://www.youtube.com/watch?v=nobody00000"))
        s.commit()
    finally:
        s.close()
    got = {r.id: r for r in all_rows()}
    assert got[blocked].unplayable_at == NOW and got[asha_too].unplayable_at == NOW  # every player's row of it
    rank.rescore()
    assert [i.title for g in page(made, player_id=pid).groups for i in g.items if i.kind == "video"] == ["Verma talks through the hundred"]
    s = made()
    try:
        assert feed.judged(s, [other]) == {}
        marked = next(c for c in admin.feed_page(s)["candidates"] if c["id"] == blocked)
        assert not marked["served"] and marked["unplayable_at"] == NOW.isoformat()
        assert blocked not in {c["id"] for c in admin.feed_page(s, "served")["candidates"]}
        assert blocked in {c["id"] for c in admin.feed_page(s, "held")["candidates"]}
    finally:
        s.close()


def all_rows() -> list[FeedCandidate]:
    with db_module.session() as s:
        return list(s.scalars(select(FeedCandidate)))
