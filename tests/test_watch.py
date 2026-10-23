"""Where a game can be watched (watch.py): each stream named by the platform its address is on, with what the
app's own player plays where it has one, and in the order a fan wants them. The site's /watch page, which the
Android app plays in its match page, is one of the app's own players alone, playing only what's of its shape, and
may hold that player's frame and nothing else. Till it's played, a stream shows its platform's picture of it, once
the platform has said there is one."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from html import unescape
from pathlib import Path

import httpx
import pytest

from sports_follow import server, watch
from sports_follow.adapters import Stream

from .test_api import PUBLIC, api  # noqa: F401  (the fixture)

ROOT = Path(__file__).resolve().parent.parent


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
                      "embed": {"player": "twitch", "id": "riotgames", "poster": "/api/watch/twitch/riotgames/poster"}}
    assert (kick["platform"], kick["embed"]) == ("Kick", {"player": "kick", "id": "cct_cs2", "poster": None})  # Kick has no address for a picture
    assert (video["platform"], video["embed"]) == ("YouTube", {"player": "youtube", "id": "dQw4w9WgXcQ", "poster": "/api/watch/youtube/dQw4w9WgXcQ/poster"})
    assert (channel["platform"], channel["embed"]) == ("YouTube", None)  # a channel's live page names no video to play
    assert (other["platform"], other["embed"]) == ("Douyu", None)  # a link: "Watch on Douyu"
    assert watch.offer([raw("https://www.nimo.tv/esports")])[0]["platform"] == "nimo.tv"  # a platform it doesn't know: by its host


def test_only_a_channels_own_page_or_a_videos_plays_in_the_page():
    def embed(url: str) -> dict | None:
        found = watch.offer([raw(url)])[0]["embed"]
        return found and {k: v for k, v in found.items() if k != "poster"}

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
    "/watch/kick/a%22%20onload%3D%22x", "/watch/twitch/%3Cscript%3E",
])
def test_a_player_the_app_doesnt_have_or_what_isnt_of_its_shape_is_a_404(api, path):
    got = api.call("GET", path)
    assert got.status_code == 404 and got.json() == {"detail": "Not Found"}


def test_a_nul_in_a_players_address_is_refused_before_the_page(api):
    """As in any address (server.NoNul, BUG-096): a plain 400, never the player."""
    assert api.call("GET", "/watch/youtube/dQw4w9WgXc%00").status_code == 400


def test_every_stream_the_app_plays_has_a_watch_page_and_a_host_that_isnt_one_none():
    offered = watch.offer([raw(u) for u in ("https://www.twitch.tv/riotgames", "https://m.twitch.tv/Gaules", "https://kick.com/united21_en",
                                            "https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/live/abcDEF_-123")])
    assert all(s["embed"] and watch.page(s["embed"]["player"], s["embed"]["id"], "stann.in") for s in offered)
    assert set(watch.PLAYERS) == {"twitch", "kick", "youtube"}
    assert watch.page("twitch", "riotgames", 'stann.in"><script>') is None and watch.page("twitch", "riotgames", "") is None
    assert frames(watch.page("twitch", "riotgames", "10.0.2.2")[0]) == ["https://player.twitch.tv/?channel=riotgames&parent=10.0.2.2&autoplay=true"]


def test_the_web_app_makes_each_players_address_as_the_watch_page_does_and_the_site_may_frame_it():
    # The web app frames a player itself (components/Watch.tsx's playerUrl), the site's CSP allowing its host.
    web = (ROOT / "web" / "src" / "components" / "Watch.tsx").read_text()
    made = dict(re.findall(r"embed\.player === '(\w+)'\) return `([^`]+)`", web))
    assert set(made) == set(watch.PLAYERS)
    for name, plays in (("twitch", "riotgames"), ("kick", "cct_cs2"), ("youtube", "dQw4w9WgXcQ")):
        address = made[name].replace("${id}", plays).replace("${encodeURIComponent(window.location.hostname)}", "stann.in")
        assert address == watch.PLAYERS[name].address(plays, "stann.in"), name
    policy = re.search(r'header @app Content-Security-Policy "([^"]+)"', (ROOT / "deploy" / "caddy" / "Caddyfile").read_text())
    framed = re.search(r"frame-src ([^;]+);", policy[1] if policy else "")
    assert framed and {p.origin for p in watch.PLAYERS.values()} <= set(framed[1].split())


def test_the_web_plays_a_stream_in_the_page_at_any_width_and_its_players_own_full_screen_makes_it_bigger(api):
    # Bharath, 21 Oct (BHA-387): a phone's column plays it in the page too, though that's under Twitch's stated
    # 400×300, and nothing is drawn over the whole page instead. The /watch page's player has no full screen of its
    # own: the app's Expand is the way to a bigger one, as the WebView's own would turn the screen upright.
    web = (ROOT / "web" / "src" / "components" / "Watch.tsx").read_text()
    played = re.findall(r"<iframe [^>]*/>", web)
    assert len(played) == 1 and " allowFullScreen " in played[0] and "fullscreen" in re.findall(r'allow="([^"]*)"', played[0])[0]
    assert "createPortal" not in web
    page = api.call("GET", "/watch/twitch/riotgames").text
    assert "allowfullscreen" not in page.lower() and "fullscreen" not in re.findall(r'allow="([^"]*)"', page)[0]


# ---------------------------------------------------------------- its picture till it's played (embed.poster)


def test_a_twitch_channel_and_a_youtube_video_have_their_platforms_picture_and_kick_none():
    assert watch.poster("twitch", "Gaules") == "https://static-cdn.jtvnw.net/previews-ttv/live_user_gaules-640x360.jpg"  # Twitch's names are lower case there
    assert watch.poster("youtube", "dQw4w9WgXcQ") == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
    assert watch.poster("kick", "cct_cs2") is None  # Kick's is in its API alone
    assert watch.poster("twitch", "riot.games") is None and watch.poster("vimeo", "riotgames") is None and watch.poster("youtube", "short") is None
    assert {name for name, p in watch.PLAYERS.items() if p.poster} == {"twitch", "youtube"}


def pictured(answer: httpx.Response | Exception) -> tuple[bool, list[httpx.Request]]:
    """watch.pictured, with the platform answering this for the poster's address, and Twitch's grey "offline" picture
    there as an image, as it is (so a client that followed Twitch's redirect would call that a picture); and what it
    was asked. The client is watch's own, with only where it sends swapped."""
    asked: list[httpx.Request] = []

    def platform(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        if request.url.path.startswith("/ttv-static/404_preview"):
            return httpx.Response(200, headers={"content-type": "image/jpeg"})
        if isinstance(answer, Exception):
            raise answer
        return answer

    real = watch._client
    made = real()
    asyncio.run(made.aclose())
    assert made.follow_redirects is False  # the offline check is that Twitch's redirect isn't followed
    watch._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(platform), headers=made.headers, timeout=made.timeout,
                                              follow_redirects=made.follow_redirects)
    try:
        return asyncio.run(watch.pictured("https://static-cdn.jtvnw.net/previews-ttv/live_user_riotgames-640x360.jpg")), asked
    finally:
        watch._client = real


def test_a_picture_is_one_only_where_the_platform_serves_an_image_and_not_elsewhere():
    there, asked = pictured(httpx.Response(200, headers={"content-type": "image/jpeg"}))
    assert there and [(r.method, r.headers["user-agent"]) for r in asked] == [("HEAD", watch.USER_AGENT)]  # asked, not fetched
    # A Twitch channel that isn't live: its address sends to Twitch's grey "offline" picture, which isn't of it.
    offline, asked = pictured(httpx.Response(302, headers={"location": "https://static-cdn.jtvnw.net/ttv-static/404_preview-640x360.jpg"}))
    assert not offline and len(asked) == 1  # not followed there
    assert not pictured(httpx.Response(404, headers={"content-type": "image/jpeg"}))[0]  # a YouTube video that isn't there
    assert not pictured(httpx.Response(200, headers={"content-type": "text/html"}))[0]
    assert not pictured(httpx.ConnectTimeout("too slow"))[0]  # a platform that can't be reached: none for now


def platform_says(monkeypatch, there: bool) -> list[str]:
    asked: list[str] = []

    async def pictured(address: str) -> bool:
        asked.append(address)
        return there

    monkeypatch.setattr(watch, "pictured", pictured)
    return asked


def test_the_poster_sends_the_page_to_the_platforms_picture_and_asks_the_platform_once_for_everyone(api, monkeypatch):
    asked = platform_says(monkeypatch, True)
    kept, put = {}, api.redis.set

    async def set(key, value, nx=False, ex=None):
        kept[key] = ex
        return await put(key, value, nx=nx, ex=ex)

    monkeypatch.setattr(api.redis, "set", set)
    for fan in ("one", "two"):
        got = api.call("GET", "/api/watch/twitch/riotgames/poster", fan=fan)
        assert got.status_code == 302 and got.headers["location"] == "https://static-cdn.jtvnw.net/previews-ttv/live_user_riotgames-640x360.jpg"
        assert got.headers["cache-control"] == f"public, max-age={watch.POSTER_FOR}"
    assert asked == ["https://static-cdn.jtvnw.net/previews-ttv/live_user_riotgames-640x360.jpg"]
    assert kept == {"watch:poster:" + asked[0]: watch.POSTER_FOR}  # asked again once it's that old
    offered = watch.offer([raw("https://www.twitch.tv/riotgames")])[0]["embed"]["poster"]
    assert api.call("GET", offered).status_code == 302  # a stream's embed.poster is this


def test_a_channel_that_isnt_live_has_no_poster_and_the_page_keeps_its_own(api, monkeypatch):
    asked = platform_says(monkeypatch, False)
    for _ in range(2):
        got = api.call("GET", "/api/watch/twitch/riotgames/poster")
        assert got.status_code == 404 and "location" not in got.headers
        assert got.headers["cache-control"] == f"public, max-age={watch.POSTER_FOR}"
    assert len(asked) == 1


@pytest.mark.parametrize("path", [
    "/api/watch/kick/cct_cs2/poster",  # a player without pictures
    "/api/watch/vimeo/riotgames/poster", "/api/watch/Twitch/riotgames/poster",  # a player the app hasn't
    "/api/watch/twitch/riot.games/poster", "/api/watch/youtube/short/poster", "/api/watch/twitch/%3Cscript%3E/poster",  # not of its shape
])
def test_no_poster_for_what_the_app_doesnt_play_or_has_no_picture_of_and_the_platform_isnt_asked(api, monkeypatch, path):
    asked = platform_says(monkeypatch, True)
    assert api.call("GET", path).status_code == 404 and asked == []


def test_one_address_has_the_platform_asked_so_many_times_an_hour_and_what_was_asked_still_answers(api, monkeypatch):
    asked = platform_says(monkeypatch, True)
    monkeypatch.setattr(server, "POSTERS_PER_ADDRESS", 2)
    got = [api.call("GET", f"/api/watch/twitch/name{n}/poster", address=PUBLIC).status_code for n in range(3)]
    assert got == [302, 302, 429] and len(asked) == 2
    assert api.call("GET", "/api/watch/twitch/name0/poster", address=PUBLIC).status_code == 302  # kept: nothing asked
    assert api.call("GET", "/api/watch/twitch/name2/poster", address="81.2.69.143").status_code == 302  # another address isn't held up


def test_the_apps_ask_for_a_poster_anew_as_often_as_the_server_asks_the_platform():
    # endpoints.ts's watchPoster: the Android app keeps a picture by its address for good, so the address changes
    # every watch.POSTER_FOR seconds (the server ignores what it adds).
    web = (ROOT / "web" / "src" / "endpoints.ts").read_text()
    every = re.search(r"export const watchPoster = .*Math\.floor\(opened / ([\d_]+)\)", web)
    assert every and int(every[1].replace("_", "")) == watch.POSTER_FOR * 1000
