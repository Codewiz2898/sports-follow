"""The Feed's pipeline end to end against Postgres (BHA-301): the curator has the judge read new candidates
(rank.judge), scores the ones it lets through and ties each to the moment it's about (rank.rescore); the Feed
serves them as videos beside the news (feed.feed_for), faded by age, one a channel, two a moment, the
evergreen ones apart, a fan's hides applied, and Today's rail takes each player's top one; an admin's word
and a channel's trust stand over the judge's; and if the videos can't be read, a feed is the news alone.
The judge and YouTube are made up; so is every player, channel and video. Skipped without a throwaway
database at alembic head (tests/test_corrections_db.py says how to make one)."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, literal_column, select, text, update
from sqlalchemy.orm import sessionmaker

from sports_follow import admin, candidates, config, controls, curate, curator, db as db_module, feed, rank, server
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
        return [curate.Verdict.model_validate(verdicts[c.url.split("=")[1].rstrip("0123456789")]) if c.url.split("=")[1].rstrip("0123456789") in verdicts
                else curate.CurateError("Model call failed (timeout).", "timeout") for c in cands]

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


def test_an_admins_hide_takes_the_video_off_every_players_feed_and_taking_it_back_brings_every_one_back(made):
    """BHA-438: an admin hides a video (it's abusive, or fake), not one player's row of it: a video kept for a whole
    team is hidden under every player it was kept for, so For you, which mixes in players the fan doesn't follow,
    doesn't serve it either. Taking the hide back (or approving the video) brings every row of it back at once."""
    pid = asha()
    with db_module.session() as s:
        p = Player(slug="ravi-rao", name="Ravi Rao", sport="cricket", teams=["Riverside XI"])
        s.add(p)
        s.flush()
        s.add(PlayerCard(player_id=p.id, status="ready", card={"player": {"name": "Ravi Rao"}, "news": []}))
        other = p.id
    mine = stored(pid, "shared00001", "Verma and Rao's stand", EDITS, 5, verdict=said())
    stored(other, "shared00001", "Verma and Rao's stand", EDITS, 5, verdict=said())
    stored(other, "raoonly0001", "Rao in the nets", TALKS, 4, verdict=said())
    rank.rescore()

    def served(player_id: int) -> set[str]:
        s = made()
        try:
            return {c.item.title for c in feed.judged(s, [player_id]).get(player_id, [])}
        finally:
            s.close()

    assert served(pid) == {"Verma and Rao's stand"} and served(other) == {"Verma and Rao's stand", "Rao in the nets"}
    assert admin.review_candidate(mine, "hidden")
    assert served(pid) == set() and served(other) == {"Rao in the nets"}
    assert {r.player_id: r.review for r in rows_of("shared00001")} == {pid: "hidden", other: "hidden"}
    rank.rescore()  # the curator's next run leaves it hidden
    assert served(other) == {"Rao in the nets"}
    assert admin.review_candidate(mine, None)
    assert served(pid) == {"Verma and Rao's stand"} and served(other) == {"Verma and Rao's stand", "Rao in the nets"}
    assert {r.player_id: r.review for r in rows_of("shared00001")} == {pid: None, other: None}
    assert admin.review_candidate(mine, "hidden") and admin.review_candidate(mine, "approved")
    assert served(other) == {"Verma and Rao's stand", "Rao in the nets"}


def test_an_admins_approval_or_pin_serves_a_video_the_judge_hasnt_read_or_held_back(made):
    """BHA-439: with the judge down or out of credit, an admin can still put a video in front of fans: approving or
    pinning one the judge has no verdict on scores it as one about the player (rank.UNREAD), and so does one the judge
    held back (its "held" verdict is no word on the video). Taking the word back takes it out again until the judge
    has read it, and the curator's next run keeps what the admin said."""
    pid = asha()
    unread = stored(pid, "unread00001", "Verma in the nets", EDITS, 5)
    held = stored(pid, "heldback001", "Verma on the hundred", TALKS, 6, verdict=rank.held("declined", 3))
    rank.rescore()
    served = lambda: [i.title for g in page(made, player_id=pid).groups for i in g.items if i.kind == "video"]  # noqa: E731
    assert served() == []

    approved = admin.review_candidate(unread, "approved")
    assert approved is not None and approved["served"] and approved["score"] is not None
    assert served() == ["Verma in the nets"]
    rank.rescore()  # the curator's next run
    assert served() == ["Verma in the nets"]
    pinned = admin.review_candidate(unread, "pinned")
    assert pinned is not None and pinned["score"] >= rank.PIN

    was_held = admin.review_candidate(held, "approved")
    assert was_held is not None and was_held["served"]
    row = rows()["heldback001"]
    assert row.score == rank.score(rank.UNREAD, row.features, rank.LISTED, rank.when(row.candidate["published"]))  # not the held verdict's nothing
    assert served() == ["Verma in the nets", "Verma on the hundred"]

    taken_back = admin.review_candidate(unread, None)
    assert taken_back is not None and not taken_back["served"] and taken_back["score"] is None
    assert served() == ["Verma on the hundred"]


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


def test_one_fans_reports_count_once_against_a_channel_however_many_of_its_videos_and_players_they_cover(made):
    # BUG-120: one signed-in fan reporting three of a channel's videos, one of them held for two players, halved its
    # trust for everyone: each report, and each player's copy, counted as another fan's.
    pid = asha()
    with db_module.session() as s:
        other = Player(slug="riya-sen", name="Riya Sen", sport="cricket", teams=["Riverside XI"])
        s.add(other)
        s.flush()
        rohit = other.id
    stored(pid, "edit0000001", "Every boundary", EDITS, 8, verdict=said())
    for n in (2, 3, 4):
        stored(pid, f"edit000000{n}", f"Every wicket, part {n}", EDITS, 30, verdict=said())
    stored(rohit, "edit0000004", "Every wicket, part 4", EDITS, 30, verdict=said())
    rank.rescore()
    before = rows()["edit0000001"].score
    assert before

    def report(fan: str, player_id: int, vid: str, verdict: str = "report") -> None:
        with db_module.session() as s:
            s.add(FeedFeedback(fan_id=fan, player_id=player_id, item_id=feed.item_id(f"https://www.youtube.com/watch?v={vid}"), verdict=verdict))

    for player_id, vid in ((pid, "edit0000002"), (pid, "edit0000003"), (pid, "edit0000004"), (rohit, "edit0000004")):
        report("acct-one", player_id, vid)
    rank.rescore()
    assert rows()["edit0000001"].score == pytest.approx(before / (1 + 1 / rank.FEEDBACK_SCALE), abs=1e-3)  # one fan: one report's worth
    report("acct-two", pid, "edit0000002", "not_about_player")
    report("acct-two", pid, "edit0000003")  # their strongest word counts, once
    rank.rescore()
    assert rows()["edit0000001"].score == pytest.approx(before / (1 + 2 / rank.FEEDBACK_SCALE), abs=1e-3)


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
        assert everything["totals"] == {"all": 3, "unjudged": 1, "served": 1, "unplayable": 0} and everything["next"] is None
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


def test_a_video_fans_apps_say_wont_play_inside_them_leaves_every_feed_and_saying_it_again_changes_nothing(made):
    """Its owner keeps it off other sites, which videos.list didn't say (embeddable, no region rule): enough fans' apps
    said so (feed.unplayable), so no fan is served it again, and the admin panel says why. An admin approving or
    pinning it says it plays, and stands over any app that says otherwise later."""
    pid = asha()
    with db_module.session() as s:
        p = Player(slug="ravi-rao", name="Ravi Rao", sport="cricket", teams=["Riverside XI"])
        s.add(p)
        s.flush()
        s.add(PlayerCard(player_id=p.id, status="ready", card={"player": {"name": "Ravi Rao"}, "news": []}))
        other = p.id
    blocked = stored(pid, "blocked0001", "Verma's hundred, every boundary", EDITS, 5, verdict=said())
    stored(other, "blocked0001", "Verma and Rao's stand", EDITS, 5, verdict=said())
    pinned = stored(pid, "pinned00001", "Verma talks through the hundred", TALKS, 4, verdict=said())
    rank.rescore()
    admin.review_candidate(pinned, "pinned")
    videos = lambda: {i.title for g in page(made, player_id=pid).groups for i in g.items if i.kind == "video"}  # noqa: E731
    assert videos() == {"Verma's hundred, every boundary", "Verma talks through the hundred"}
    s = made()
    try:
        assert feed.unplayable(s, "youtube:blocked0001", NOW) is True
        assert feed.unplayable(s, "youtube:blocked0001") is False  # already: nothing changes
        assert feed.unplayable(s, "youtube:pinned00001") is False  # an admin vouched for it
        assert feed.unplayable(s, "youtube:nobody00000") is False  # no such video
        s.commit()
    finally:
        s.close()
    marks = {(r.player_id, r.external_id): r.unplayable_at for r in all_rows()}
    assert marks == {(pid, "blocked0001"): NOW, (other, "blocked0001"): NOW, (pid, "pinned00001"): None}  # every player's row of it
    rank.rescore()
    assert videos() == {"Verma talks through the hundred"}
    s = made()
    try:
        assert feed.judged(s, [other]) == {}
        marked = next(c for c in admin.feed_page(s)["candidates"] if c["id"] == blocked)
        assert not marked["served"] and marked["unplayable_at"] == NOW.isoformat()
        assert blocked not in {c["id"] for c in admin.feed_page(s, "served")["candidates"]}
        assert blocked in {c["id"] for c in admin.feed_page(s, "held")["candidates"]}
    finally:
        s.close()
    # It does play after all: an admin approving it (or pinning it) says so, for every row of it, and apps saying
    # otherwise later don't take it away again.
    admin.review_candidate(blocked, "approved")
    rank.rescore()
    assert all(r.unplayable_at is None for r in all_rows())
    assert videos() == {"Verma's hundred, every boundary", "Verma talks through the hundred"}
    s = made()
    try:
        assert feed.unplayable(s, "youtube:blocked0001") is False
        s.commit()
    finally:
        s.close()
    assert all(r.unplayable_at is None for r in all_rows())


def reports(monkeypatch) -> tuple[Any, Any]:
    """The API's Redis and job queue, made up (tests/test_api.py), for fans' apps saying videos won't play."""
    from tests.test_api import KEY, Queue, Redis

    redis, queue = Redis(), Queue()
    monkeypatch.setitem(server.state, "redis", redis)
    monkeypatch.setitem(server.state, "arq", queue)
    monkeypatch.setattr(server, "cookie_key", lambda: KEY)
    return redis, queue


def test_one_fans_app_saying_a_video_wont_play_takes_it_from_their_feed_alone_until_enough_networks_say_so(made, monkeypatch):
    """BHA-422: one fan's word, or one network's with any number of new cookies, takes the video out of that fan's Feed
    only; feed.UNPLAYABLE_VOTES networks saying so take it out of every fan's at once. An IPv6 home's /48 is one
    network. BUG-100: nothing is queued, so a report never asks the video's source anything."""
    monkeypatch.setattr(feed, "UNPLAYABLE_VOTES", 3, raising=False)
    pid = asha()
    stored(pid, "blocked0001", "Verma's hundred, every boundary", EDITS, 5, verdict=said())
    stored(pid, "plays000001", "Verma talks through the hundred", TALKS, 4, verdict=said())
    rank.rescore()
    _, queue = reports(monkeypatch)
    item = feed.item_id("https://www.youtube.com/watch?v=blocked0001")

    def say(fan: str, address: str) -> dict:
        return said_unplayable(pid, item, fan, address, "Asia/Kolkata")

    videos = lambda fan: {i.title for g in page(made, fan, pid).groups for i in g.items if i.kind == "video"}  # noqa: E731
    both = {"Verma's hundred, every boundary", "Verma talks through the hundred"}
    assert say("anon-1", "81.2.69.142") == {"item_id": item, "unplayable": True, "marked": False}
    assert videos("anon-1") == {"Verma talks through the hundred"} and videos("fan") == both
    for n in range(5):  # new cookies, the same network: still its one word
        say(f"anon-new-{n}", "81.2.69.142")
    say("home-a", "2a02:8010:1234:1::1")
    say("home-b", "2a02:8010:1234:2::1")  # another /64 of the same home's /48
    assert videos("fan") == both and all(r.unplayable_at is None for r in rows_of("blocked0001"))
    assert say("anon-3", "81.2.69.200")["marked"] is True  # a third network
    assert videos("fan") == {"Verma talks through the hundred"} and all(r.unplayable_at for r in rows_of("blocked0001"))
    assert say("anon-4", "81.2.69.201")["marked"] is False  # already
    assert queue.jobs == []


def test_a_report_past_a_fans_or_a_networks_hour_still_takes_the_video_from_their_feed_and_only_its_vote_is_dropped(made, monkeypatch):
    """BUG-100: past the votes a fan may cast in an hour (feed.UNPLAYABLE_PER_FAN), or an address may
    (UNPLAYABLE_PER_ADDRESS), a report used to be refused, so the video stayed in that fan's Feed. It leaves it now;
    only the vote isn't counted."""
    pid = asha()
    stored(pid, "blocked0001", "Verma's hundred, every boundary", EDITS, 5, verdict=said())
    stored(pid, "blocked0002", "Verma's fifty", STATS, 6, verdict=said())
    stored(pid, "plays000001", "Verma talks through the hundred", TALKS, 4, verdict=said())
    rank.rescore()
    redis, _ = reports(monkeypatch)
    fans, networks = "feed:unplayable:fan:anon-1", "feed:unplayable:address:81.2.69.50"
    redis.data[fans], redis.data[networks] = feed.UNPLAYABLE_PER_FAN, feed.UNPLAYABLE_PER_ADDRESS  # their hours' votes all cast
    videos = lambda fan: {i.title for g in page(made, fan, pid).groups for i in g.items if i.kind == "video"}  # noqa: E731
    every = {"Verma's hundred, every boundary", "Verma's fifty", "Verma talks through the hundred"}
    for fan, address, vid, title in (("anon-1", "81.2.69.142", "blocked0001", "Verma's hundred, every boundary"), ("anon-2", "81.2.69.50", "blocked0002", "Verma's fifty")):
        item = feed.item_id(f"https://www.youtube.com/watch?v={vid}")
        assert said_unplayable(pid, item, fan, address, "Asia/Kolkata") == {"item_id": item, "unplayable": True, "marked": False}
        assert videos(fan) == every - {title}  # gone from their Feed
        assert f"feed:unplayable:votes:youtube:{vid}" not in redis.data  # and no vote
    assert videos("fan") == every
    assert (redis.data[fans], redis.data[networks]) == (feed.UNPLAYABLE_PER_FAN, feed.UNPLAYABLE_PER_ADDRESS)


def said_unplayable(pid: int, item: str, fan: str, address: str, zone: str | None) -> dict:
    """POST .../unplayable as a fan's app sends it, from this address, with the device's time zone (none: an app from
    before it said)."""

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=server.app, client=(address, 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(f"/api/players/{pid}/feed/{item}/unplayable", headers={"Cookie": f"{config.FAN_COOKIE}={server.sign_fan(fan)}"},
                                     **({"json": {"timezone": zone}} if zone is not None else {}))  # fmt: skip

    answer = asyncio.run(go())
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_a_fan_abroad_saying_a_video_wont_play_hides_it_from_them_and_takes_it_from_nobody_else(made, monkeypatch):
    """BHA-436: a video is kept because it plays in FEED_COUNTRY, and may play there alone (a broadcaster's rights
    there): an app abroad is refused it and says so. It leaves that fan's Feed, but their word isn't a vote on
    everyone's: only an app in FEED_COUNTRY's time zone casts one (Chrome names India's the old way, Asia/Calcutta),
    and an app that names none (from before it said) doesn't."""
    monkeypatch.setattr(feed, "UNPLAYABLE_VOTES", 3, raising=False)
    pid = asha()
    stored(pid, "indiaonly01", "Verma's hundred, every boundary", EDITS, 5, verdict=said())
    stored(pid, "plays000001", "Verma talks through the hundred", TALKS, 4, verdict=said())
    rank.rescore()
    _, queue = reports(monkeypatch)
    item = feed.item_id("https://www.youtube.com/watch?v=indiaonly01")
    videos = lambda fan: {i.title for g in page(made, fan, pid).groups for i in g.items if i.kind == "video"}  # noqa: E731
    both = {"Verma's hundred, every boundary", "Verma talks through the hundred"}
    abroad = [("dubai", "81.2.69.1", "Asia/Dubai"), ("london", "81.2.69.2", "Europe/London"), ("newyork", "81.2.69.3", "America/New_York"),
              ("colombo", "81.2.69.4", "Asia/Colombo"), ("oldapp", "81.2.69.5", None), ("nozone", "81.2.69.6", "")]  # fmt: skip
    for fan, address, zone in abroad:
        assert said_unplayable(pid, item, fan, address, zone) == {"item_id": item, "unplayable": True, "marked": False}
        assert videos(fan) == {"Verma talks through the hundred"}  # gone from their own Feed all the same
    assert videos("fan") == both and all(r.unplayable_at is None for r in rows_of("indiaonly01"))
    # Three networks in India saying so is another matter: it won't play inside the app there either.
    said_unplayable(pid, item, "pune", "81.2.70.1", "Asia/Kolkata")
    said_unplayable(pid, item, "delhi", "81.2.70.2", "Asia/Calcutta")
    assert videos("fan") == both
    assert said_unplayable(pid, item, "chennai", "81.2.70.3", "Asia/Kolkata")["marked"] is True
    assert videos("fan") == {"Verma talks through the hundred"} and all(r.unplayable_at for r in rows_of("indiaonly01"))
    assert queue.jobs == []


def as_admin(method: str, path: str, body: dict) -> dict:
    """A request as the admin panel sends it, signed in: its answer."""

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=server.app, client=("81.2.69.9", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=body)

    server.app.dependency_overrides[server.require_admin] = lambda: None
    try:
        answer = asyncio.run(go())
    finally:
        server.app.dependency_overrides.pop(server.require_admin, None)
    assert answer.status_code == 200, answer.text
    return answer.json()


def admin_review(row: int, said: str | None) -> dict:
    """PUT /api/admin/feed/<row>: an admin's word on a candidate."""
    return as_admin("PUT", f"/api/admin/feed/{row}", {"review": said})


def test_an_admins_word_on_a_video_fans_said_wont_play_starts_their_votes_on_it_over(made, monkeypatch):
    """BUG-100: an admin approving a video three networks' apps said won't play, and later taking that back, left their
    votes kept for a month, so the next fan's app to say so took it from every Feed at once. An admin's approve, pin,
    hide or take-back clears its votes: it takes three networks again."""
    monkeypatch.setattr(feed, "UNPLAYABLE_VOTES", 3, raising=False)
    pid = asha()
    row = stored(pid, "blocked0001", "Verma's hundred, every boundary", EDITS, 5, verdict=said())
    rank.rescore()
    redis, _ = reports(monkeypatch)
    votes = "feed:unplayable:votes:youtube:blocked0001"
    for said_ in ("pinned", "hidden", "approved", None):  # None: the approval taken back
        redis.data[votes] = {"81.2.69.1", "81.2.69.2", "81.2.69.3"}  # three networks said so before
        admin_review(row, said_)
        assert votes not in redis.data, said_
    # The next fan's app to say so: their Feed loses it, nobody else's.
    item = feed.item_id("https://www.youtube.com/watch?v=blocked0001")
    assert said_unplayable(pid, item, "anon-4", "81.2.69.4", "Asia/Kolkata")["marked"] is False
    assert all(r.unplayable_at is None for r in rows_of("blocked0001"))
    assert redis.data[votes] == {"81.2.69.4"}


def test_the_admin_panel_lists_each_video_fans_reports_took_out_once_and_put_back_leaves_the_judges_word_as_it_was(made, monkeypatch):
    """BUG-100: a video fans' apps on three networks said won't play leaves every Feed at once, and the admin panel's
    Feed tab lists each such video once (admin.taken_out), newest first, with every player it's kept for, and counts
    videos, not rows; one an admin hid isn't listed. Put back (feed.put_back) takes the mark off every player's row of
    it and clears its votes, approving nothing: a row the judge held back for its player stays held. Approving one
    puts it back too, as before."""
    monkeypatch.setattr(feed, "UNPLAYABLE_VOTES", 3, raising=False)
    pid = asha()
    with db_module.session() as s:
        p = Player(slug="ravi-rao", name="Ravi Rao", sport="cricket", teams=["Riverside XI"])
        s.add(p)
        s.flush()
        s.add(PlayerCard(player_id=p.id, status="ready", card={"player": {"name": "Ravi Rao"}, "news": []}))
        other = p.id
    stored(pid, "blocked0001", "Verma's hundred, every boundary", EDITS, 5, verdict=said())
    held = stored(other, "blocked0001", "Verma and Rao's stand", EDITS, 5, verdict=said(about_player="no"))  # not about Rao, the judge said
    later = stored(pid, "blocked0002", "Verma's fifty", STATS, 6, verdict=said())
    hid = stored(pid, "blocked0003", "Verma in the nets", TALKS, 7, verdict=said())
    stored(pid, "plays000001", "Verma talks through the hundred", TALKS, 4, verdict=said())
    rank.rescore()
    redis, queue = reports(monkeypatch)
    for vid in ("blocked0001", "blocked0003", "blocked0002"):  # taken out in this order
        item = feed.item_id(f"https://www.youtube.com/watch?v={vid}")
        assert [said_unplayable(pid, item, f"fan-{n}", f"81.2.70.{n}", "Asia/Kolkata")["marked"] for n in (1, 2, 3)] == [False, False, True]
    admin.review_candidate(hid, "hidden")
    videos = lambda: {i.title for g in page(made, "fan", pid).groups for i in g.items if i.kind == "video"}  # noqa: E731
    assert videos() == {"Verma talks through the hundred"}

    def listed() -> tuple[list[tuple[str, list[str]]], int]:
        s = made()
        try:
            shown = admin.feed_page(s, "unplayable")
            return [(c["title"], [p["name"] for p in c["players"]]) for c in shown["candidates"]], shown["totals"]["unplayable"]
        finally:
            s.close()

    assert listed() == ([("Verma's fifty", ["Asha Verma"]), ("Verma's hundred, every boundary", ["Asha Verma", "Ravi Rao"])], 2)
    # Put back from Rao's row, which the judge held back: every row loses the mark, and Rao's stays held.
    assert as_admin("POST", f"/api/admin/feed/{held}/put-back", {}) == {"put_back": "youtube:blocked0001"}
    assert {r.player_id: (r.unplayable_at, r.review, r.served_now) for r in served_rows("blocked0001")} == {pid: (None, None, True), other: (None, None, False)}
    assert videos() == {"Verma's hundred, every boundary", "Verma talks through the hundred"}
    assert "feed:unplayable:votes:youtube:blocked0001" not in redis.data and "feed:unplayable:votes:youtube:blocked0002" in redis.data
    assert listed() == ([("Verma's fifty", ["Asha Verma"])], 1)
    # Approving one puts it back too, and clears its votes.
    assert admin_review(later, "approved")["unplayable_at"] is None
    assert "feed:unplayable:votes:youtube:blocked0002" not in redis.data and listed() == ([], 0)
    assert queue.jobs == []


def served_rows(vid: str) -> list[Any]:
    """Each player's row of a video, and whether the Feed serves it now (FeedCandidate.served)."""
    with db_module.session() as s:
        return list(s.execute(select(FeedCandidate.player_id, FeedCandidate.unplayable_at, FeedCandidate.review, FeedCandidate.served().label("served_now"))
                              .where(FeedCandidate.external_id == vid)))  # fmt: skip


def all_rows() -> list[FeedCandidate]:
    with db_module.session() as s:
        return list(s.scalars(select(FeedCandidate)))


def test_a_teams_video_kept_for_several_of_its_players_is_judged_once_and_its_verdict_is_theirs_as_the_teams(made, monkeypatch):
    """BHA-382: one share about a team, kept for each of its players (candidates.py), is one model call; a verdict
    that it's about the first of them is their team's for the rest; and one kept later takes the stored verdict."""
    with db_module.session() as s:
        ids = []
        for name in ("Asha Verma", "Mira Das", "Tara Singh"):
            p = Player(slug=name.lower().replace(" ", "-"), name=name, sport="cricket", teams=["Riverside XI"])
            s.add(p)
            s.flush()
            ids.append(p.id)
    first, second, third = ids
    for pid in (first, second):
        row = stored(pid, "riverside01", "Riverside XI v Hilltop, the final", EDITS, 5)
        with db_module.session() as s:
            s.execute(update(FeedCandidate).where(FeedCandidate.id == row).values(candidate=FeedCandidate.candidate.op("||")(
                literal_column("""'{"about": "team", "team": "Riverside XI"}'::jsonb"""))))
    asked: list[curate.Candidate] = []

    def judge_many(cands):
        cands = list(cands)
        asked.extend(cands)
        return [curate.Verdict.model_validate(said(about_player="subject")) for _ in cands]

    monkeypatch.setattr(curate, "judge_many", judge_many)
    assert rank.judge(NOW) == {"judged": 2, "unjudged": 0}
    assert len(asked) == 1 and asked[0].team == "Riverside XI"
    verdicts = {r.player_id: r.verdict["about_player"] for r in rows_of("riverside01")}
    assert verdicts == {first: "subject", second: "team"}  # the judge's word on the first; the team's for the second
    # A teammate found with it later takes the stored verdict, without asking again.
    row = stored(third, "riverside01", "Riverside XI v Hilltop, the final", EDITS, 5)
    with db_module.session() as s:
        s.execute(update(FeedCandidate).where(FeedCandidate.id == row).values(candidate=FeedCandidate.candidate.op("||")(
            literal_column("""'{"about": "team", "team": "Riverside XI"}'::jsonb"""))))
    assert rank.judge(NOW) == {"judged": 1, "unjudged": 0} and len(asked) == 1
    assert {r.player_id: r.verdict["about_player"] for r in rows_of("riverside01")}[third] == "team"
    assert rank.rescore(NOW)["served"] == 3


def rows_of(vid: str) -> list[FeedCandidate]:
    with db_module.session() as s:
        return list(s.scalars(select(FeedCandidate).where(FeedCandidate.external_id == vid)))


# ---------------------------------------------------------------- a row the model won't judge (BHA-440)


def test_a_row_the_model_declines_or_answers_off_the_schema_for_waits_days_then_is_held_back_for_good(made, monkeypatch):
    """Asking again at the next run would get the same answer, and paid for each time: the curator runs the judge
    after every poll, over a hundred times a day. Such a row waits days before it's asked again, longer each time,
    and after JUDGE_TRIES it's held back for good, with a verdict the Feed never serves (an admin can still approve
    it). A failure that isn't the row's (the gateway down) is asked again at the next run, as before, and never
    counts against it."""
    monkeypatch.setattr(rank, "JUDGE_TRIES", 3, raising=False)
    pid = asha()
    stored(pid, "declined001", "Asha Verma, every boundary", EDITS, 5)
    stored(pid, "offschema01", "Asha Verma on the hundred", TALKS, 6)
    stored(pid, "unavailable", "Asha Verma in the nets", STATS, 7)
    kinds = {"declined001": "declined", "offschema01": "invalid_output", "unavailable": "unavailable"}
    asked: list[str] = []

    def ask(llm, c, facts):
        asked.append(c.url[-11:])
        raise curate.CurateError("The model couldn't judge this.", kinds[c.url[-11:]])

    monkeypatch.setattr(config, "CURATE_AI", True)
    monkeypatch.setattr(curate, "gateway", lambda: object())
    monkeypatch.setattr(curate, "_ask", ask)
    for run in range(4 * 30):  # four runs a day for a month
        rank.judge(NOW + timedelta(hours=6 * run))
    assert {vid: asked.count(vid) for vid in kinds} == {"declined001": 3, "offschema01": 3, "unavailable": 120}
    got = rows()
    for vid in ("declined001", "offschema01"):
        verdict = got[vid].verdict
        assert verdict and verdict["stage"] == "held" and not rank.passes(verdict)
        assert (got[vid].judge_tries, got[vid].judge_error) == (3, kinds[vid])
    assert got["unavailable"].verdict is None and (got["unavailable"].judge_tries, got["unavailable"].judge_error) == (0, "unavailable")
    rank.rescore(NOW + timedelta(days=30))
    assert all(r.score is None for r in rows().values())
    s = made()
    try:
        held = admin.feed_page(s, "held")["candidates"]
        assert {c["title"] for c in held} == {"Asha Verma, every boundary", "Asha Verma on the hundred"} and not any(c["passes"] for c in held)
    finally:
        s.close()
    # A run that asks again whatever was said (curator rescore --rejudge) asks the held ones too.
    monkeypatch.setattr(curate, "judge_many", lambda cands: [curate.Verdict.model_validate(said()) for _ in list(cands)])
    assert rank.judge(NOW + timedelta(days=31), again=True)["judged"] == 3
