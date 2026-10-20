"""The feed's ranking at ingest (rank.py, BHA-301), without the database: how fast a video is watched from its
two reads, its like ratio, the judge's interest and its channel's trust make one score; a verdict that isn't
about the player, is paid for or names a risk is never served; a share published within two days after one
of the player's moments is about the biggest of them; older videos still being watched are evergreen; and a
stored row reads as the judge's Candidate. Made-up numbers and channels only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sports_follow import controls, curate, rank

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
CHANNEL = "UC" + "b" * 22


def read(hours_ago: float, views: int, likes: int = 0) -> dict:
    return {"at": (NOW - timedelta(hours=hours_ago)).isoformat(), "views": views, "likes": likes}


def numbers(*reads: dict, seconds: int = 400) -> dict:
    last = reads[-1]
    return {"views": last["views"], "likes": last["likes"], "seconds": seconds, "reads": list(reads)}


def verdict(**kw) -> dict:
    return {"about_player": "subject", "by": "fan_or_creator", "kind": "match_moment", "interest": 0.7, "risks": [], "sponsored": False,
            "reason": "A fan edit of the winning moment.", "stage": "model", **kw}  # fmt: skip


def test_views_an_hour_come_from_the_two_reads_else_from_its_life_so_far():
    two = numbers(read(30, 1_000), read(6, 25_000))
    assert rank.growth(two) == pytest.approx(1_000.0)
    assert rank.pace(two, NOW - timedelta(hours=40), NOW) == pytest.approx(1_000.0)
    # One read, two hours after it was published: 4,000 views over those two hours.
    one = numbers(read(1, 4_000))
    assert rank.growth(one) is None
    assert rank.pace(one, NOW - timedelta(hours=3), NOW) == pytest.approx(2_000.0)
    # A read minutes after it's out counts as an hour of its life, not a burst.
    assert rank.pace(numbers(read(0, 600)), NOW - timedelta(minutes=5), NOW) == pytest.approx(600.0)
    assert rank.pace(numbers(read(0, 600)), None, NOW) == 0.0
    # Three reads: how fast it's watched lately, between the last two.
    assert rank.growth(numbers(read(24 * 30, 1_000), read(24 * 7, 500_000), read(1, 516_700))) == pytest.approx(100.0)
    # Reads that fell (YouTube recounting) aren't negative.
    assert rank.growth(numbers(read(30, 5_000), read(6, 4_000))) == 0.0


def test_the_like_ratio_needs_enough_views_to_mean_anything():
    assert rank.like_part(numbers(read(1, 50, 49))) == rank.LIKES_UNKNOWN
    assert rank.like_part({"views": 10_000, "likes": None}) == rank.LIKES_UNKNOWN  # likes hidden
    assert rank.like_part(numbers(read(1, 10_000, 300))) == pytest.approx(0.5)
    assert rank.like_part(numbers(read(1, 10_000, 2_000))) == 1.0


def test_what_the_judge_lets_through():
    assert rank.passes(verdict())
    assert not rank.passes(verdict(about_player="mention"))  # a passing mention is kept, never served
    assert not rank.passes(verdict(about_player="no"))
    assert not rank.passes(verdict(sponsored=True))
    assert not rank.passes(verdict(risks=["deepfake"]))
    assert not rank.passes(None) and not rank.passes({})


def test_the_score_weighs_interest_pace_and_likes_and_trust_multiplies_it():
    fast = numbers(read(30, 1_000, 20), read(6, 241_000, 9_000))  # 10,000 views an hour, a 3.7% like ratio
    slow = numbers(read(30, 100, 2), read(6, 340, 7))
    published = NOW - timedelta(hours=40)
    assert rank.score(verdict(interest=1.0), fast, 1.0, published, NOW) > rank.score(verdict(interest=1.0), slow, 1.0, published, NOW)
    assert rank.score(verdict(interest=0.9), slow, 1.0, published, NOW) > rank.score(verdict(interest=0.1), slow, 1.0, published, NOW)
    top = rank.score(verdict(interest=1.0), {**fast, "likes": 241_000}, 1.0, published, NOW)
    assert top == pytest.approx(1.0)
    assert rank.score(verdict(), fast, 0.5, published, NOW) == pytest.approx(rank.score(verdict(), fast, 1.0, published, NOW) / 2, abs=1e-3)
    assert rank.score(verdict(about_player="mention"), fast, 1.0, published, NOW) == pytest.approx(rank.score(verdict(), fast, 1.0, published, NOW) * rank.MENTION, abs=1e-3)
    assert rank.score(verdict(interest="lots"), fast, 1.0, published, NOW) < rank.score(verdict(), fast, 1.0, published, NOW)  # a bad value counts as none


def test_a_channels_trust_is_the_admins_word_else_whether_a_sport_lists_it_and_fans_reports_lower_it_to_half():
    listed = {CHANNEL}
    assert rank.trust(CHANNEL, listed, {}) == rank.LISTED
    assert rank.trust("UC" + "z" * 22, listed, {}) == rank.UNLISTED
    assert rank.trust(CHANNEL, listed, {CHANNEL: 0.4}) == 0.4
    assert rank.trust(CHANNEL, listed, {CHANNEL: 0.0}) == 0.0
    weight = rank.feedback_weight([("i1", "report"), ("i2", "report"), ("i1", "not_about_player"), ("i3", "not_interested"), ("other", "report")],
                                  {"i1": CHANNEL, "i2": CHANNEL, "i3": CHANNEL})  # fmt: skip
    assert weight == {CHANNEL: pytest.approx(2.5)}  # "not interested" is taste: it doesn't count against the channel
    assert rank.trust(CHANNEL, listed, {}, weight[CHANNEL]) == pytest.approx(1 / (1 + 2.5 / rank.FEEDBACK_SCALE))
    assert rank.trust(CHANNEL, listed, {}, 4 * rank.FEEDBACK["report"]) == pytest.approx(0.5)  # four reports halve it
    assert rank.trust(CHANNEL, listed, {}, 400.0) == pytest.approx(rank.LISTED * rank.FEEDBACK_FLOOR)  # and no number of them sinks it
    assert rank.trust(CHANNEL, listed, {CHANNEL: 1.0}, 400.0) == 1.0  # the admin's word stands as it is


def test_the_panels_channel_trust_is_kept_as_a_control_and_odd_values_are_ignored():
    controls.set_channel_trust(CHANNEL, 0.4)
    assert controls.current().channel_trust == {CHANNEL: 0.4}
    controls.set_channel_trust(CHANNEL, None)
    assert controls.current().channel_trust == {}
    assert controls.parse({controls.CHANNEL_TRUST: {CHANNEL: 2, "x": "high", "y": True, "z": 0.7}}).channel_trust == {"z": 0.7}
    assert controls.parse({controls.CHANNEL_TRUST: ["not", "a", "dict"]}).channel_trust == {}


def test_a_share_is_about_the_biggest_moment_in_the_two_days_before_it():
    fifty = rank.Head(1, 9, NOW - timedelta(hours=30), 70)
    hundred = rank.Head(2, 9, NOW - timedelta(hours=28), 90)
    result = rank.Head(3, 9, NOW - timedelta(hours=26), 80)
    heads = [fifty, hundred, result]
    assert rank.moment_for(NOW, heads) == 2
    assert rank.moment_for(NOW - timedelta(hours=29), heads) == 1  # published before the hundred: about the fifty
    assert rank.moment_for(NOW - timedelta(hours=31), heads) is None  # before all of them
    assert rank.moment_for(NOW + timedelta(hours=21), heads) == 3  # the hundred is more than two days back by then
    assert rank.moment_for(NOW + timedelta(hours=23), heads) is None
    assert rank.moment_for(None, heads) is None
    same = [rank.Head(4, 9, NOW - timedelta(hours=5), 80), rank.Head(5, 9, NOW - timedelta(hours=2), 80)]
    assert rank.moment_for(NOW, same) == 5  # the latest of equals


def test_older_videos_still_being_watched_are_evergreen():
    old = NOW - timedelta(days=40)
    growing = numbers(read(24 * 39, 2_000), read(24 * 8, 900_000), read(1, 960_000))
    assert rank.evergreen(growing, old, NOW)
    assert not rank.evergreen(growing, NOW - timedelta(days=3), NOW)  # still this week's
    assert not rank.evergreen(numbers(read(24 * 8, 900_000), read(1, 900_100)), old, NOW)  # hardly watched now
    assert not rank.evergreen(numbers(read(1, 960_000)), old, NOW)  # one read: can't tell
    assert not rank.evergreen(numbers(read(24 * 8, 1_000), read(1, 9_000)), old, NOW)  # growing, but few have seen it
    # Watched fast once and not since: its last two reads say so, whatever its first one did.
    assert not rank.evergreen(numbers(read(24 * 39, 2_000), read(24 * 8, 960_000), read(1, 960_000)), old, NOW)


@pytest.mark.parametrize(("kind", "by", "tag"), [
    ("match_moment", "fan_or_creator", "Fan edit"), ("match_moment", "official", "Highlights"), ("interview_or_analysis", "outlet", "Interview"),
    ("interview_or_analysis", "fan_or_creator", "Analysis"), ("personal_or_fun", "player", "Fun"), ("milestone", "official", "Milestone"),
    ("something new", "unknown", "Video"),
])  # fmt: skip
def test_the_feeds_tag_says_what_the_judge_found(kind, by, tag):
    assert rank.tag({"kind": kind, "by": by}) == tag


def test_a_stored_row_reads_as_the_judges_candidate():
    who = curate.PlayerContext(name="Asha Verma", aliases=["Ashu"], teams=["Riverside XI"], sport="cricket")
    kept = {"title": "Asha Verma's hundred", "description": "Every boundary", "channel": "Boundary Line", "channel_id": CHANNEL,
            "published": "2026-10-19T10:00:00+00:00", "details": {"status": {"embeddable": True}}}  # fmt: skip
    c = rank.as_candidate("youtube", "https://www.youtube.com/watch?v=abcdefghijk", kept, numbers(read(2, 5_000, 300), seconds=480), who)
    assert (c.kind, c.source, c.author_name, c.author_handle, c.title, c.text) == ("video", "youtube", "Boundary Line", CHANNEL, "Asha Verma's hundred", "Every boundary")
    assert c.published_at == datetime(2026, 10, 19, 10, tzinfo=timezone.utc) and c.duration_seconds == 480
    assert (c.engagement.views, c.engagement.likes, c.platform_flags.embeddable) == (5_000, 300, True)
    assert c.player == who and c.author_kind is None  # the model tells who made it
    assert not c.platform_flags.paid_partnership
    assert rank.as_candidate("youtube", "https://www.youtube.com/watch?v=abcdefghijk", {**kept, "paid": True}, {}, who).platform_flags.paid_partnership
    gone = rank.as_candidate("other", "https://example.com/v", {"details": {"status": {"embeddable": False}}}, {}, who)
    assert gone.source == "other" and not gone.platform_flags.embeddable and gone.published_at is None
