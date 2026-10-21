"""Where a game can be watched (watch.py): each stream named by the platform its address is on, with what the
app's own player plays where it has one, and in the order a fan wants them. The site's /watch page, which the
Android app shows, is one of the app's own players alone, playing only what's of its shape, and may hold that
player's frame and nothing else."""

from __future__ import annotations

import base64
import hashlib
import re
from html import unescape

import pytest

from sports_follow import watch
from sports_follow.adapters import Stream

from .test_api import api  # noqa: F401  (the fixture)


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
    assert embed("https://kick.com/about") is None and embed("https://www.kick.com/united21_en") == {"player": "kick", "id": "united21_en"}
    assert embed("https://clips.twitch.tv/AwkwardHelplessSalamander") is None  # a clip on Twitch's other site, not a channel
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


# ---------------------------------------------------------------- the /watch page (the Android app's player)


def frames(page: str) -> list[str]:
    return [unescape(src) for src in re.findall(r'<iframe src="([^"]*)"', page)]


def test_the_watch_page_is_the_streams_player_alone_told_the_pages_own_host(api):
    got = api.call("GET", "/watch/twitch/riotgames")
    assert got.status_code == 200 and got.headers["content-type"].startswith("text/html")
    assert frames(got.text) == ["https://player.twitch.tv/?channel=riotgames&parent=test&autoplay=true"]  # Twitch's parent: this page's host
    assert got.headers["x-robots-tag"] == "noindex, nofollow" and got.headers["cache-control"] == "public, max-age=3600"
    assert frames(api.call("GET", "/watch/kick/cct_cs2").text) == ["https://player.kick.com/cct_cs2?autoplay=true"]
    assert frames(api.call("GET", "/watch/youtube/dQw4w9WgXcQ").text) == ["https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?autoplay=1&rel=0&playsinline=1"]


def test_the_watch_page_may_hold_its_players_frame_and_nothing_else(api):
    for player, plays, origin in (("twitch", "riotgames", "https://player.twitch.tv"), ("kick", "cct_cs2", "https://player.kick.com"),
                                  ("youtube", "dQw4w9WgXcQ", "https://www.youtube-nocookie.com")):
        got = api.call("GET", f"/watch/{player}/{plays}")
        policy = dict(d.strip().split(" ", 1) for d in got.headers["content-security-policy"].split(";"))
        style = re.findall(r"<style>(.*?)</style>", got.text)
        assert len(style) == 1 and "<script" not in got.text and " style=" not in got.text  # nothing to run, one style
        assert policy == {
            "default-src": "'none'", "style-src": f"'sha256-{base64.b64encode(hashlib.sha256(style[0].encode()).digest()).decode()}'",
            "frame-src": origin, "base-uri": "'none'", "form-action": "'none'", "frame-ancestors": "'none'",
        }


@pytest.mark.parametrize("path", [
    "/watch/vimeo/riotgames", "/watch/Twitch/riotgames", "/watch/embed/riotgames",  # only a player the app has, by its name
    "/watch/twitch/riot.games", "/watch/twitch/" + "a" * 26, "/watch/youtube/short", "/watch/youtube/dQw4w9WgXcQx",  # only what it plays
    "/watch/kick/a%22%20onload%3D%22x", "/watch/twitch/%3Cscript%3E", "/watch/youtube/dQw4w9WgXc%00",
])
def test_a_player_the_app_doesnt_have_or_what_isnt_of_its_shape_is_a_404(api, path):
    got = api.call("GET", path)
    assert got.status_code == 404 and got.json() == {"detail": "Not Found"}


def test_every_stream_the_app_plays_has_a_watch_page_and_a_host_that_isnt_one_none():
    offered = watch.offer([raw(u) for u in ("https://www.twitch.tv/riotgames", "https://m.twitch.tv/Gaules", "https://kick.com/united21_en",
                                            "https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/live/abcDEF_-123")])
    assert all(s["embed"] and watch.page(s["embed"]["player"], s["embed"]["id"], "stann.in") for s in offered)
    assert set(watch.PLAYERS) == {"twitch", "kick", "youtube"}
    assert watch.page("twitch", "riotgames", 'stann.in"><script>') is None and watch.page("twitch", "riotgames", "") is None
    assert frames(watch.page("twitch", "riotgames", "10.0.2.2")[0]) == ["https://player.twitch.tv/?channel=riotgames&parent=10.0.2.2&autoplay=true"]
