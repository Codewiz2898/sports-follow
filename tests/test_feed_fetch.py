"""The feed's fetch (candidates.py, adapters/youtube.py) without a database: a channel's feed read, a followed
player's names found in a video's title or description, the hard filters, and what each request costs of
YouTube's daily quota. Every feed and API answer here is made up, in the shape YouTube sends."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from sports_follow import candidates, naming, sports
from sports_follow.adapters import VIDEOS, AdapterError, Upload, VideoFacts, budget, videos, youtube
from sports_follow.candidates import Followed

CHANNEL = "UC" + "a" * 22


def listing(monkeypatch, *channels: str) -> None:
    """Every sport lists these made-up channels too, as its module would (Sport.video_channels): the feed keeps
    videos only from a channel the player's sport lists (candidates.on_list)."""
    for sport in (*sports.ALL, *sports.GROUPS):
        monkeypatch.setattr(sport, "video_channels", (*sport.video_channels, *channels))
NOW = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)


def feed(*entries: tuple[str, str, str]) -> str:
    """A channel's public feed (Atom) with these (video id, title, description) entries, newest first."""
    items = "".join(f"""
 <entry>
  <id>yt:video:{vid}</id>
  <yt:videoId>{vid}</yt:videoId>
  <yt:channelId>{CHANNEL}</yt:channelId>
  <title>{title}</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v={vid}"/>
  <author><name>Made-up Cricket</name><uri>https://www.youtube.com/channel/{CHANNEL}</uri></author>
  <published>2026-10-19T12:00:00+00:00</published>
  <updated>2026-10-19T13:00:00+00:00</updated>
  <media:group>
   <media:title>{title}</media:title>
   <media:thumbnail url="https://i4.ytimg.com/vi/{vid}/hqdefault.jpg" width="480" height="360"/>
   <media:description>{description}</media:description>
   <media:community><media:statistics views="1200"/></media:community>
  </media:group>
 </entry>""" for vid, title, description in entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <id>yt:channel:{CHANNEL[2:]}</id>
 <yt:channelId>{CHANNEL[2:]}</yt:channelId>
 <title>Made-up Cricket</title>{items}
</feed>"""


def video(vid: str, duration: str = "PT8M30S", views: int = 5000, likes: int = 300, embeddable: bool = True, privacy: str = "public",
          region: dict | None = None, live: str = "none", paid: bool | None = None, thumbs: tuple[str, ...] = (),
          size: tuple[int, int] | None = None) -> dict:
    """One item of videos.list, as YouTube sends it; thumbs: the thumbnail sizes it has ("maxres" only for some);
    size: its player's (width, height) as the player part gives it with maxWidth, else no player part."""
    details = {"duration": duration, "dimension": "2d", "definition": "hd", "caption": "false", **({"regionRestriction": region} if region else {})}
    return {
        "kind": "youtube#video", "id": vid,
        "snippet": {"title": "t", "liveBroadcastContent": live, "categoryId": "17", "tags": ["cricket"],
                    **({"thumbnails": {t: {"url": f"https://i.ytimg.com/vi/{vid}/{t}.jpg", "width": 1} for t in thumbs}} if thumbs else {})},
        "contentDetails": details,
        "statistics": {"viewCount": str(views), "likeCount": str(likes), "commentCount": "12"},
        "status": {"uploadStatus": "processed", "privacyStatus": privacy, "license": "youtube", "embeddable": embeddable, "publicStatsViewable": True},
        **({"paidProductPlacementDetails": {"hasPaidProductPlacement": paid}} if paid is not None else {}),
        **({"player": {"embedHtml": f'<iframe width="{size[0]}" height="{size[1]}" src="//www.youtube.com/embed/{vid}"></iframe>',
                       "embedWidth": str(size[0]), "embedHeight": str(size[1])}} if size else {}),
    }


class Fake:
    """YouTube, made up: its public feeds and its Data API, counting what each was asked."""

    def __init__(self):
        self.feeds: dict[str, str | int] = {}  # channel id -> the feed, or the HTTP status it answers with
        self.videos: dict[str, dict] = {}
        self.playlists: dict[str, list[dict]] = {}
        self.error: tuple[int, str] | None = None  # (status, reason) every API call answers with
        self.asked: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        if request.url.path == "/feeds/videos.xml":
            body = self.feeds.get(request.url.params["channel_id"], 404)
            return httpx.Response(body, text="<html>not found</html>") if isinstance(body, int) else httpx.Response(200, text=body)
        assert request.headers.get("x-goog-api-key") == "test-key" and "key" not in request.url.params
        if self.error:
            status, reason = self.error
            return httpx.Response(status, json={"error": {"code": status, "message": reason, "errors": [{"reason": reason, "domain": "youtube.quota"}]}})
        if request.url.path == "/youtube/v3/videos":
            ids = request.url.params["id"].split(",")
            assert len(ids) <= 50
            return httpx.Response(200, json={"kind": "youtube#videoListResponse", "items": [self.videos[v] for v in ids if v in self.videos]})
        if request.url.path == "/youtube/v3/playlistItems":
            return httpx.Response(200, json={"items": self.playlists.get(request.url.params["playlistId"], [])})
        return httpx.Response(404, json={})

    def api_calls(self, path: str = "") -> int:
        return sum(1 for r in self.asked if r.url.host == "www.googleapis.com" and r.url.path.endswith(path))


@pytest.fixture
def fake(monkeypatch):
    made = Fake()
    monkeypatch.setattr(youtube, "_client", httpx.Client(transport=httpx.MockTransport(made)))
    monkeypatch.setattr(youtube, "key", lambda: "test-key")
    return made


SOURCE = VIDEOS["youtube"]


# ---------------------------------------------------------------- the source describes itself


def test_the_video_source_describes_itself_and_is_off_without_its_key(monkeypatch):
    for system, source in VIDEOS.items():
        assert source.system == system and source.name and source.covers and source.provider.budgets
        assert source.channel_pattern.fullmatch(CHANNEL) and not source.id_pattern.fullmatch("")
    monkeypatch.setattr(youtube, "key", lambda: None)
    assert videos() == []
    monkeypatch.setattr(youtube, "key", lambda: "test-key")
    assert videos() == [SOURCE]


def test_every_sports_channels_are_of_a_video_sources_shape():
    for sport in (*sports.ALL, *sports.GROUPS):
        assert len(set(sport.video_channels)) == len(sport.video_channels), sport.key
        for channel in sport.video_channels:
            assert any(v.channel_pattern.fullmatch(channel) for v in VIDEOS.values()), (sport.key, channel)


def test_a_sport_thats_off_has_no_channel_read(monkeypatch):
    on = [s for s in sports.ALL if s.video_channels]
    assert {s.key for s in on} >= {"cricket", "football", "chess"}
    monkeypatch.setattr(sports, "ENABLED", [s for s in sports.ALL if s.key != "cricket"])
    listed = candidates.channels(SOURCE)
    assert listed and not set(sports.BY_KEY["cricket"].video_channels) & set(listed)
    assert all("cricket" not in keys for keys in listed.values())


# ---------------------------------------------------------------- reading a channel


def test_a_channels_feed_is_read_as_its_uploads(fake):
    fake.feeds[CHANNEL] = feed(("dQw4w9WgXcQ", "Kohli &amp; Rohit: the chase", "Full analysis #viratkohli"), ("bad", "not an id", ""))
    [up] = SOURCE.uploads(CHANNEL)
    assert (up.video_id, up.title, up.description, up.channel) == ("dQw4w9WgXcQ", "Kohli & Rohit: the chase", "Full analysis #viratkohli", "Made-up Cricket")
    assert up.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ" and up.thumbnail and up.published == datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    assert fake.api_calls() == 0  # the feed costs no quota
    SOURCE.uploads(CHANNEL)
    assert len(fake.asked) == 1  # read again only after FEED_TTL


def test_a_feed_that_doesnt_answer_falls_back_to_the_uploads_playlist_for_a_unit(fake, source_budgets):
    fake.feeds[CHANNEL] = 404  # YouTube's feeds often answer 404 for a while
    fake.playlists["UU" + CHANNEL[2:]] = [{"snippet": {"title": "Bumrah's yorkers", "description": "", "publishedAt": "2026-10-19T10:00:00Z",
                                                       "videoOwnerChannelId": CHANNEL, "videoOwnerChannelTitle": "Made-up Cricket",
                                                       "resourceId": {"kind": "youtube#video", "videoId": "abcdefghijk"}}}]
    with budget.in_lane("feed"):
        [up] = SOURCE.uploads(CHANNEL)
    assert up.video_id == "abcdefghijk" and up.title == "Bumrah's yorkers" and fake.api_calls("/playlistItems") == 1
    assert budget.standing(youtube.BUDGET)["windows"][0]["used"] == 1


def test_without_a_key_a_feed_that_doesnt_answer_is_an_error(fake, monkeypatch):
    monkeypatch.setattr(youtube, "key", lambda: None)
    fake.feeds[CHANNEL] = 500
    with pytest.raises(AdapterError):
        SOURCE.uploads(CHANNEL)
    assert fake.api_calls() == 0
    with pytest.raises(AdapterError):
        SOURCE.uploads("UCnot-a-channel")


# ---------------------------------------------------------------- what a video says, and the quota


def test_a_video_is_read_as_its_facts(fake):
    fake.videos["abcdefghijk"] = video("abcdefghijk", duration="PT1H2M3S", region={"blocked": ["IN", "PK"]})
    facts = SOURCE.facts(["abcdefghijk", "missing0000"])
    f = facts["abcdefghijk"]
    assert (f.seconds, f.views, f.likes, f.embeddable, f.public, f.live, f.blocked) == (3723, 5000, 300, True, True, False, ("IN", "PK"))
    assert not f.plays_in("IN") and f.plays_in("GB") and "missing0000" not in facts
    assert f.raw["status"]["privacyStatus"] == "public" and f.raw["snippet"] == {"liveBroadcastContent": "none", "categoryId": "17", "tags": ["cricket"], "thumbnails": []}
    fake.videos["withsizes00"] = video("withsizes00", thumbs=("high", "maxres", "default", "standard", "medium"))
    assert SOURCE.facts(["withsizes00"])["withsizes00"].raw["snippet"]["thumbnails"] == ["default", "high", "maxres", "medium", "standard"]  # the sizes, never the addresses
    assert not f.paid


def test_a_declared_paid_promotion_is_read_in_the_same_unit(fake):
    fake.videos.update({"promoted000": video("promoted000", paid=True), "unpromoted0": video("unpromoted0", paid=False)})
    with budget.in_lane("feed"):
        got = SOURCE.facts(["promoted000", "unpromoted0"])
    assert got["promoted000"].paid and not got["unpromoted0"].paid
    [asked] = fake.asked
    assert "paidProductPlacementDetails" in asked.url.params["part"].split(",")
    assert budget.standing(youtube.BUDGET)["windows"][0]["used"] == 1


def test_a_videos_shape_is_read_in_the_same_unit_and_an_upright_one_is_told_from_a_wide_one(fake):
    fake.videos.update({"uprightvid1": video("uprightvid1", size=(1280, 2276)), "widevideo01": video("widevideo01", size=(1280, 720)),
                        "noshape0001": video("noshape0001")})  # fmt: skip
    with budget.in_lane("feed"):
        got = SOURCE.facts(["uprightvid1", "widevideo01", "noshape0001"])
    [asked] = fake.asked
    assert "player" in asked.url.params["part"].split(",") and asked.url.params["maxWidth"] == youtube.PLAYER_WIDTH
    assert got["uprightvid1"].raw["player"] == {"width": 1280, "height": 2276}  # its size, never the player's HTML
    assert "player" not in got["noshape0001"].raw
    assert [SOURCE.upright(got[v].raw) for v in ("uprightvid1", "widevideo01", "noshape0001")] == [True, False, False]
    assert not SOURCE.upright(None) and not SOURCE.upright({"player": {"width": "wide", "height": 9}})
    assert budget.standing(youtube.BUDGET)["windows"][0]["used"] == 1


def test_a_searchs_titles_are_read_as_written():
    item = {"id": {"videoId": "searched001"}, "snippet": {
        "channelId": CHANNEL, "channelTitle": "Rock &amp; Roll", "title": "Kylian Mbappe &amp; Bijan Robinson SWAP sports! It&#39;s on",
        "description": "&quot;Wow&quot;"}}  # fmt: skip
    [found] = youtube.parse_search({"items": [item]})
    assert (found.title, found.channel, found.description) == ("Kylian Mbappe & Bijan Robinson SWAP sports! It's on", "Rock & Roll", '"Wow"')


def test_each_request_is_one_unit_of_the_days_quota_for_up_to_fifty_videos(fake):
    ids = [f"vid{n:08d}" for n in range(120)]
    for v in ids:
        fake.videos[v] = video(v)
    with budget.in_lane("feed"):
        assert len(SOURCE.facts(ids)) == 120
    assert fake.api_calls("/videos") == 3  # 50 + 50 + 20
    assert budget.standing(youtube.BUDGET)["windows"] == [{"bucket": "", "window": "day", "used": 3, "allowance": 10_000}]
    assert [(r["lane"], r["sent_d"]) for r in budget.usage([youtube.BUDGET])] == [("feed", 3)]
    with budget.in_lane("feed"):
        SOURCE.facts(ids[:50])  # the same ids within the hour: from the cache
    assert fake.api_calls("/videos") == 3


def test_the_days_quota_less_its_margin_is_never_spent(fake, source_budgets):
    for v in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        fake.videos[v] = video(v)
    now = budget.time.time()
    source_budgets.data[budget._window_key(youtube.BUDGET, "", youtube.BUDGET.windows[0], now)] = (youtube.PER_DAY - youtube.MARGIN - 1, now + 3600)
    with budget.in_lane("feed"):
        SOURCE.facts(["aaaaaaaaaaa"])
        with pytest.raises(budget.RateLimited):
            SOURCE.facts(["bbbbbbbbbbb"])
    assert fake.api_calls() == 1


def test_youtube_saying_the_quota_is_spent_stops_every_request_until_it_resets(fake):
    fake.error = (403, "quotaExceeded")
    with budget.in_lane("feed"), pytest.raises(budget.RateLimited):
        SOURCE.facts(["aaaaaaaaaaa"])
    fake.error = None
    fake.videos["aaaaaaaaaaa"] = video("aaaaaaaaaaa")
    with budget.in_lane("feed"), pytest.raises(budget.RateLimited):
        SOURCE.facts(["aaaaaaaaaaa"])
    assert fake.api_calls() == 1
    assert budget.left(youtube.BUDGET) == 0


def test_the_quota_resets_at_midnight_pacific():
    assert youtube.reset_in(datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)) == pytest.approx(3600)  # 23:00 PDT
    assert youtube.reset_in(datetime(2026, 12, 20, 8, 0, tzinfo=timezone.utc)) == pytest.approx(24 * 3600)  # midnight PST


def test_a_rejected_key_is_an_error_not_a_pause(fake):
    fake.error = (400, "keyInvalid")
    with pytest.raises(AdapterError, match="rejected the key") as raised:
        SOURCE.facts(["aaaaaaaaaaa"])
    assert not isinstance(raised.value, budget.RateLimited)


@pytest.mark.parametrize("duration, seconds", [("PT4M13S", 253), ("PT20M", 1200), ("P0D", None), ("PT0S", None), ("P1DT2H", 93600), ("", None), ("4:13", None)])
def test_durations(duration, seconds):
    assert youtube.seconds(duration) == seconds


# ---------------------------------------------------------------- the hard filters


def facts(**kw) -> VideoFacts:
    return VideoFacts(**{"video_id": "abcdefghijk", "seconds": 300, "views": 10, "likes": 1, "embeddable": True, "public": True, **kw})


@pytest.mark.parametrize("kw, why", [
    ({}, None),
    ({"seconds": 60}, None),
    ({"seconds": 1200}, None),
    ({"seconds": 59}, "length"),  # a Short
    ({"seconds": 1201}, "length"),
    ({"seconds": None}, "length"),
    ({"live": True}, "live"),
    ({"embeddable": False}, "not embeddable"),
    ({"public": False}, "private"),
    ({"blocked": ("IN",)}, "blocked in IN"),
    ({"allowed": ("GB", "US")}, "blocked in IN"),
    ({"allowed": ("IN",)}, None),
])
def test_the_hard_filters(kw, why):
    assert candidates.refused(facts(**kw), "IN") == why


def test_a_video_the_source_doesnt_return_is_gone():
    assert candidates.refused(None) == candidates.gone(None) == "gone"
    assert candidates.gone(facts(seconds=5000)) is None  # its length doesn't matter once it's kept


def test_parsed_facts_meet_the_filters(fake):
    fake.videos.update({v["id"]: v for v in (
        video("keep0000000"), video("short000000", duration="PT45S"), video("embedoff000", embeddable=False),
        video("private0000", privacy="unlisted"), video("onlygb00000", region={"allowed": ["GB"]}), video("livenow0000", duration="P0D", live="live"),
    )})
    got = SOURCE.facts(list(fake.videos))
    assert {v: candidates.refused(got.get(v), "IN") for v in fake.videos} == {
        "keep0000000": None, "short000000": "length", "embedoff000": "not embeddable", "private0000": "private", "onlygb00000": "blocked in IN", "livenow0000": "live",
    }


# ---------------------------------------------------------------- names


def up(title: str, description: str = "") -> Upload:
    return Upload("abcdefghijk", CHANNEL, "Made-up", title, description, "https://www.youtube.com/watch?v=abcdefghijk")


KOHLI = Followed(1, "cricket", "Virat Kohli", ["virat kohli", "King Kohli"])
ROHIT = Followed(2, "cricket", "Rohit Sharma", ["rohit sharma"])
ISHANT = Followed(3, "cricket", "Ishant Sharma", [])
MULLER = Followed(4, "football", "Thomas Müller", [])
TENZ = Followed(5, "valorant", "TenZ", ["Tyson Ngo"])
SKY = Followed(6, "cricket", "Suryakumar Yadav", ["sky"])
BUMRAH = Followed(7, "cricket", "Jasprit Bumrah", [])


def test_a_player_is_named_by_their_full_name_a_hashtag_or_a_surname_only_they_have():
    names = candidates.phrases([KOHLI, ROHIT, ISHANT, SKY, BUMRAH])
    assert candidates.named(up("What a knock from Virat Kohli!"), names[1]) == ("virat kohli", "title")
    assert candidates.named(up("Chase master", "Full breakdown #ViratKohli #cricket"), names[1]) == ("viratkohli", "description")
    assert candidates.named(up("KOHLI's 52nd ODI hundred"), names[1]) == ("kohli", "title")
    assert candidates.named(up("Bumrah's yorkers explained"), names[7]) == ("bumrah", "title")
    # Two Sharmas followed: "Sharma" alone names neither; their full names name each.
    assert candidates.named(up("Sharma in the nets"), names[2]) is None and candidates.named(up("Sharma in the nets"), names[3]) is None
    assert candidates.named(up("Rohit Sharma's pull shot"), names[2]) == ("rohit sharma", "title")
    assert candidates.named(up("Rohit Sharma's pull shot"), names[3]) is None
    # A nickname of a few letters ("sky") never names anyone by itself; a word inside another doesn't either.
    assert candidates.named(up("Blue sky over Wankhede"), names[6]) is None
    assert candidates.named(up("Kohlis everywhere"), names[1]) is None
    assert candidates.named(up("King Kohli returns"), names[1]) == ("king kohli", "title")  # the longest name found


def test_only_a_descriptions_opening_names_who_a_video_is_about():
    names = candidates.phrases([KOHLI, BUMRAH])
    related = ("Our pick of the best spells this season, ball by ball.\n\nRelated videos:\n"
               "Virat Kohli's 52nd hundred: https://www.youtube.com/watch?v=aaaaaaaaaaa\nBumrah on the yorker: https://youtu.be/bbbbbbbbbbb")
    assert candidates.named(up("Spells of the season", related), names[1]) is None
    assert candidates.named(up("Spells of the season", related), names[7]) is None
    # A podcast's links straight under the opening, with no blank line between them.
    podcast = "Selection talk before the first Test.\nListen on Spotify: https://example.test/pod\nLast week: Jasprit Bumrah on his comeback https://example.test/ep9"
    assert candidates.named(up("Who opens in Perth?", podcast), names[7]) is None
    # Named in the opening, it counts, a link list under it or not.
    assert candidates.named(up("Who opens in Perth?", "Why Jasprit Bumrah should lead the attack.\n\nPodcast: https://example.test/pod"), names[7]) == ("jasprit bumrah", "description")
    # A description with no break is read only so far.
    long = "A look back at a great season for the whole side. " * 6 + "Virat Kohli"
    assert len(long) > naming.LEAD_MAX and candidates.named(up("Season review", long), names[1]) is None
    assert naming.lead("  Opening line\nsecond line\n\nlinks") == "Opening line\nsecond line"
    assert naming.lead("Watch: https://example.test/1\nmore") == "Watch: https://example.test/1\nmore"  # a link in the first line is still the opening
    assert naming.lead("x" * 294 + " Kohli more") == "x" * 294 + " Kohli"  # a word ending at the cut stays whole


def test_names_match_in_any_plain_spelling():
    names = candidates.phrases([MULLER])
    assert candidates.named(up("Thomas Mueller's last dance"), names[4]) == ("thomas mueller", "title")
    assert candidates.named(up("THOMAS MULLER interview"), names[4]) == ("thomas muller", "title")
    assert candidates.named(up("Müller: the Raumdeuter"), names[4]) == ("muller", "title")


def test_a_one_word_handle_is_its_own_name_while_nobody_else_followed_has_it():
    names = candidates.phrases([TENZ])
    assert candidates.named(up("TenZ on the new agent"), names[5]) == ("tenz", "title")
    assert candidates.named(up("Tyson Ngo interview"), names[5]) == ("tyson ngo", "title")
    # Another followed pro's name has the word too: the handle alone names neither.
    names = candidates.phrases([TENZ, Followed(8, "valorant", "Tenz Kim", [])])
    assert candidates.named(up("TenZ on the new agent"), names[5]) is None
    assert candidates.named(up("Tenz Kim on the new agent"), names[8]) == ("tenz kim", "title")


def test_each_upload_is_matched_once_for_each_player_it_names():
    group = [KOHLI, ROHIT, BUMRAH]
    found = candidates.match([up("Kohli and Bumrah talk"), up("Nothing here")], group, candidates.phrases(group))
    assert [(f.player_id, f.name) for f in found] == [(1, "kohli"), (7, "bumrah")]


# FIDE writes a player's name as "Given Initial"; titles write the given name alone, or the name their aliases end in.
GUKESH = Followed(20, "chess", "Gukesh D", ["gukesh d", "D Gukesh", "Dommaraju Gukesh"])
CARLSEN = Followed(21, "chess", "Magnus Carlsen", ["magnus carlsen"])
PRAGG = Followed(22, "chess", "Praggnanandhaa R", ["R Praggnanandhaa", "Rameshbabu Praggnanandhaa"])
VAISHALI = Followed(23, "chess", "Vaishali R", ["R Vaishali", "Vaishali Rameshbabu"])
DRAVID = Followed(24, "cricket", "Rahul Dravid", ["The Wall"])
BOOM = Followed(25, "cricket", "Jasprit Jasbirsingh Bumrah", ["Jasprit Bumrah", "Boom Boom"])


def test_a_name_initial_player_is_named_by_their_name_alone_and_an_aliass_last_word_by_itself():
    """BHA-437: "Gukesh D" is "Gukesh" in titles: a name ending in an initial is named by the word before it, and the
    last word of each of their aliases that's a form of their name names them too, while it's theirs alone among
    those followed. An initial never names anyone, and neither does a nickname's last word ("The Wall")."""
    group = [GUKESH, CARLSEN, PRAGG, VAISHALI, DRAVID, BOOM]
    names = candidates.phrases(group)

    def who(title: str) -> list[tuple[int, str]]:
        return [(f.player_id, f.name) for f in candidates.match([up(title)], group, names)]

    assert who("Gukesh vs Sindarov | Game 5 | World Championship Match") == [(20, "gukesh")]
    assert who("Gukesh beats Magnus Carlsen in Norway Chess 2026") == [(20, "gukesh"), (21, "magnus carlsen")]
    assert who("Magnus Carlsen loses to Gukesh") == [(20, "gukesh"), (21, "magnus carlsen")]
    assert who("D Gukesh wins Round 5") == [(20, "d gukesh")]
    assert who("Praggnanandhaa holds Firouzja") == [(22, "praggnanandhaa")]
    assert who("Vaishali's road to the Candidates") == [(23, "vaishali")]
    # A word two of them have names neither (the siblings' father's name); a first name ending nothing doesn't either.
    assert who("The Rameshbabu family at the Olympiad") == [] and who("Magnus effect, explained") == []
    # A nickname's last word isn't one of their name's words: it never names them by itself.
    assert who("Inside the Great Wall of China") == [] and who("Boom! What a finish") == []
    assert who("Behind the wall with Rahul Dravid") == [(24, "rahul dravid")]
    # Another followed player has the word in their name: "Gukesh" alone names neither.
    names = candidates.phrases([GUKESH, Followed(26, "chess", "Gukesh Kumar")])
    assert candidates.named(up("Gukesh wins again"), names[20]) is None and candidates.named(up("Gukesh D wins again"), names[20]) == ("gukesh d", "title")
    assert not any(len(phrase) == 1 and len(phrase[0]) == 1 for phrase in candidates.phrases(group)[20])  # no initial alone


# ---------------------------------------------------------------- what a row keeps


def test_a_candidates_numbers_keep_its_first_read_and_its_latest_two():
    first = candidates.features(facts(views=100, likes=5), NOW)
    assert first == {"views": 100, "likes": 5, "seconds": 300, "reads": [{"at": NOW.isoformat(), "views": 100, "likes": 5}]}
    later = NOW.replace(day=21)
    second = candidates.read_again(first, facts(views=900, likes=40), later)
    third = candidates.read_again(second, facts(views=1500, likes=60), later.replace(day=28))
    assert second["views"] == 900 and [r["views"] for r in second["reads"]] == [100, 900]
    fourth = candidates.read_again(third, facts(views=1600, likes=61), later.replace(day=30))
    assert [r["views"] for r in third["reads"]] == [100, 900, 1500] and third["seconds"] == 300
    assert [r["views"] for r in fourth["reads"]] == [100, 1500, 1600]


def test_a_candidate_keeps_what_was_fetched_and_the_name_it_was_kept_for():
    f = facts()
    f.raw = {"status": {"embeddable": True}, "statistics": {"viewCount": "10"}}
    kept = candidates.candidate(up("Kohli 100", "desc"), "kohli", "title", "cricket", f)
    assert kept["title"] == "Kohli 100" and kept["named"] == "kohli" and kept["where"] == "title" and kept["sport"] == "cricket"
    assert kept["details"] == {"status": {"embeddable": True}}  # the numbers live in features
    assert kept["paid"] is False and candidates.candidate(up("Kohli 100"), "kohli", "title", "cricket", facts(paid=True))["paid"] is True
    json.dumps(kept)


# ---------------------------------------------------------------- the curator


def test_the_curator_runs_its_own_queue_its_jobs_logged_and_stopping_as_the_worker_does():
    from sports_follow import curator, worker

    assert curator.CuratorSettings.queue_name not in ("arq:queue", getattr(worker.WorkerSettings, "queue_name", "arq:queue"))
    assert all(hasattr(j.coroutine, "__wrapped__") for j in curator.CuratorSettings.cron_jobs)
    assert {j.name for j in curator.CuratorSettings.cron_jobs} == {"cron:poll_channels", "cron:check_videos", "cron:poll_news", "cron:prune_news", "cron:sweep",
                                                                   "cron:refresh_pool"}
    # The pool's refresh is a job too, queued by a curator starting without a pool (feedpool.POOL_KEY).
    assert [f.name for f in curator.CuratorSettings.functions] == ["refresh_pool"]
    assert curator.CuratorSettings.on_shutdown is worker.wind_down


def test_the_curators_requests_are_in_the_feed_lane_a_background_one(monkeypatch):
    import asyncio

    from sports_follow import bus, curator

    lanes = []
    monkeypatch.setattr(candidates, "poll", lambda: lanes.append(budget.lane()) or {"kept": 0})
    monkeypatch.setattr(bus, "try_lock", lambda name, seconds: "token")
    monkeypatch.setattr(bus, "release", lambda name, token=None: None)
    asyncio.run(curator.poll_channels({}))
    assert lanes == ["feed"] and "feed" in budget.BACKGROUND
    monkeypatch.setattr(bus, "try_lock", lambda name, seconds: None)  # another curator is polling
    asyncio.run(curator.poll_channels({}))
    assert lanes == ["feed"]
