"""Where a game can be watched (watch.py): each stream named by the platform its address is on, with what the
app's own player plays where it has one, and in the order a fan wants them."""

from __future__ import annotations

from sports_follow import watch
from sports_follow.adapters import Stream


def raw(url: str, language: str = "", official: bool = False, main: bool = False) -> dict:
    """A stream as a game's read keeps it (Stream's fields)."""
    return {"url": url, "language": language, "official": official, "main": main}


def test_each_stream_is_named_by_its_platform_and_twitch_kick_and_youtube_play_in_the_page():
    twitch, kick, video, channel, other = watch.offer([
        raw("https://www.twitch.tv/riotgames", "en", True, True), raw("https://kick.com/cct_cs2", "en", True, True),
        raw("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "en", True, True), raw("https://www.youtube.com/@lolesports/live", "en", True, True),
        raw("https://www.douyu.com/9441366?dyshid=0-ac10", "en", True, True),
    ])
    assert twitch == {"url": "https://www.twitch.tv/riotgames", "platform": "Twitch", "language": "en", "language_name": "English", "official": True, "main": True,
                      "embed": {"player": "twitch", "id": "riotgames"}}
    assert (kick["platform"], kick["embed"]) == ("Kick", {"player": "kick", "id": "cct_cs2"})
    assert (video["platform"], video["embed"]) == ("YouTube", {"player": "youtube", "id": "dQw4w9WgXcQ"})
    assert (channel["platform"], channel["embed"]) == ("YouTube", None)  # a channel's live page names no video to play
    assert (other["platform"], other["embed"]) == ("Douyu", None)  # a link: "Watch on Douyu"
    assert watch.offer([raw("https://www.nimo.tv/esports")])[0]["platform"] == "nimo.tv"  # a platform it doesn't know: by its host


def test_only_a_channels_own_page_or_a_videos_plays_in_the_page():
    def embed(url: str) -> dict | None:
        return watch.offer([raw(url)])[0]["embed"]

    assert embed("https://m.twitch.tv/Gaules") == {"player": "twitch", "id": "Gaules"}
    assert embed("https://www.twitch.tv/directory") is None and embed("https://www.twitch.tv/riotgames/videos") is None
    assert embed("https://youtu.be/dQw4w9WgXcQ") == {"player": "youtube", "id": "dQw4w9WgXcQ"}
    assert embed("https://www.youtube.com/live/dQw4w9WgXcQ?si=x") == {"player": "youtube", "id": "dQw4w9WgXcQ"}
    assert embed("https://www.youtube.com/watch?v=short") is None
    assert embed("https://www.youtube.com/OGNEsports1") is None  # an old channel address, as long as a video id
    assert embed("https://kick.com/browse") is None and embed("https://kick.com/fiesta_cs/videos") is None
    assert embed("https://evil-twitch.tv/riotgames") is None  # twitch.tv's, not a lookalike's
    assert watch.offer([raw("https://evil-twitch.tv/riotgames")])[0]["platform"] == "evil-twitch.tv"


def test_the_organisers_main_broadcasts_come_first_then_english_then_the_rest():
    streams = [
        Stream("https://www.twitch.tv/betboom_ru", "ru"), Stream("https://www.twitch.tv/caedrel", "en"),
        Stream("https://www.twitch.tv/lck", "ko", official=True, main=True), Stream("https://www.twitch.tv/lolesports_de", "de", official=True),
        Stream("https://www.twitch.tv/riotgames", "en", official=True, main=True), Stream("https://www.twitch.tv/lolesports_en2", "en-GB", official=True),
    ]
    assert [s.url.rsplit("/", 1)[1] for s in watch.ordered(streams)] == ["riotgames", "lck", "lolesports_en2", "caedrel", "lolesports_de", "betboom_ru"]


def test_a_page_lists_each_web_address_once_and_no_more_than_it_shows():
    offered = watch.offer([raw("javascript:alert(1)"), raw("kick.com/x"), raw("https://kick.com/a"), raw("https://KICK.com/a"), None, "x",  # type: ignore[list-item]
                           *(raw(f"https://kick.com/{n}") for n in range(20))])
    assert [s["url"] for s in offered][:2] == ["https://kick.com/a", "https://kick.com/0"] and len(offered) == watch.SHOWN


def test_a_language_by_its_name():
    assert [watch.language(c) for c in ("en", "pt-BR", "RU", "xx", "")] == ["English", "Portuguese", "Russian", "XX", ""]
