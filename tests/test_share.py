"""Link previews (share.py): a shared link to a player's page, or to a game of theirs, shows the player, the
moment and the score in WhatsApp and X, to crawlers that run no JavaScript. The page is served with its own
Open Graph and Twitter card tags, and each has a picture drawn in the sport's colour; any other page has the
app's own. A preview that can't be made never stops the page opening. Redis and the database are fakes.
"""

from __future__ import annotations

import io
import json
import re
from html import unescape

import pytest
from PIL import Image

from sports_follow import bus, campaigns, server, share, sports, week
from sports_follow.config import APP_NAME

from .test_api import api  # noqa: F401  (the fixture)

INDEX = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="description" content="Follow any athlete in any sport: live scores, results and news." />
    <title>{APP_NAME}</title>
    <script type="module" crossorigin src="/assets/index-abc.js"></script>
  </head>
  <body><div id="root"></div></body>
</html>
"""

KOHLI = {
    "player_id": 1, "version": 12, "status": "ready",
    "player": {"name": "Virat Kohli", "sport": "Cricket", "teams": ["India"]},
    "live": {"is_live": True, "event": "India v Australia", "competition": "2nd ODI", "score": "IND 245/3 (42.1 ov)", "clock": "", "headline": "82* (71)"},
    "recent_results": [{"title": "India v England", "result": "India won by 5 wkts", "player_contribution": "54 (60)"}],
}
HUNDRED = {
    "id": 9, "player_id": 1, "player": "Virat Kohli", "sport": "Cricket", "event_id": 5, "word": "Hundred",
    "line": "112 (98) v Australia", "url": "/player/1", "at": "2026-10-14T10:00:00+00:00", "title": "India v Australia", "competition": "2nd ODI",
}


def meta(page: str) -> dict[str, str]:
    """A page's Open Graph and Twitter tags, by name."""
    return {m[1]: unescape(m[2]) for m in re.finditer(r'<meta (?:property|name)="((?:og|twitter):[^"]+)" content="([^"]*)" />', page)}


def title(page: str) -> str:
    return unescape(re.search(r"<title>(.*?)</title>", page)[1])


def picture(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


# ---------------------------------------------------------------- the tags


def test_a_live_players_page_says_who_and_the_score_with_the_picture_on_the_site():
    page = share.page(INDEX, share.of_card(KOHLI), "https://stann.in")
    tags = meta(page)
    assert tags["og:title"] == "Virat Kohli · Cricket" and title(page) == APP_NAME  # the app keeps one title
    assert tags["og:description"].startswith("Live now: India v Australia, IND 245/3 (42.1 ov). 82* (71).")
    assert f'<meta name="description" content="{tags["og:description"]}" />' in page  # the page's own, replaced
    assert tags["og:url"] == "https://stann.in/player/1"
    assert re.fullmatch(r"https://stann\.in/api/players/1/share\.png\?v=[0-9a-f]{10}", tags["og:image"])
    assert tags["twitter:image"] == tags["og:image"]
    assert (tags["twitter:card"], tags["og:image:width"], tags["og:image:height"], tags["og:site_name"]) == ("summary_large_image", "1200", "630", APP_NAME)
    assert page.index("og:title") < page.index("</head>") and page.count("</head>") == 1


def test_a_games_link_says_its_big_moment_in_the_sports_words():
    tags = meta(share.page(INDEX, share.of_game(KOHLI, HUNDRED), "https://stann.in"))
    assert tags["og:title"] == "Hundred: Virat Kohli"
    assert tags["og:description"].startswith("112 (98) v Australia, 2nd ODI.")
    assert tags["og:url"] == "https://stann.in/player/1?g=5"
    assert re.fullmatch(r"https://stann\.in/api/players/1/share\.png\?g=5&v=[0-9a-f]{10}", tags["og:image"])


def test_a_player_not_playing_shows_their_last_game_else_their_next():
    last = share.of_card({**KOHLI, "live": {"is_live": False}})
    assert last.picture.lines == ("Last game: India v England", "India won by 5 wkts · 54 (60)")
    upcoming = [{"title": "India v South Africa", "competition": "1st Test", "status": "scheduled"}]
    nxt = share.of_card({**KOHLI, "live": {"is_live": False}, "recent_results": [], "upcoming": upcoming})
    assert nxt.picture.lines == ("Next: India v South Africa", "1st Test") and nxt.description.startswith("Next: India v South Africa, 1st Test.")
    building = share.of_card({"player_id": 4, "status": "building", "player": {"name": "Somebody New", "sport": ""}, "live": {}})
    assert building.title == "Somebody New" and building.picture.word == "Somebody New"
    assert share.of_card({"player_id": 4, "status": "building", "player": {}}) is None  # no name to show


def test_the_picture_wears_the_sports_colour_and_a_sport_no_module_knows_wears_ink():
    cricket = sports.of("cricket").colour
    assert (share.of_card(KOHLI).picture.ground, share.of_card(KOHLI).picture.ink) == (cricket["sport"], cricket["on"])
    golf = share.of_card({**KOHLI, "player": {"name": "Aditi Ashok", "sport": "Golf"}})
    assert (golf.picture.ground, golf.picture.ink, golf.picture.tag) == (share.INK, share.PAPER, "Live · Golf")


def test_names_are_escaped_in_the_tags():
    card = {**KOHLI, "player": {"name": 'Evil "</title><script>alert(1)</script>', "sport": "Cricket"}}
    page = share.page(INDEX, share.of_card(card), "https://stann.in")
    assert "<script>alert" not in page and page.count("<title>") == 1
    assert meta(page)["og:title"].startswith('Evil "</title><script>')  # as it was, once unescaped


def test_every_other_page_has_the_apps_own_preview():
    page = share.page(INDEX, None, "https://stann.in")
    tags = meta(page)
    assert (tags["og:title"], tags["og:url"], title(page)) == (APP_NAME, "https://stann.in/", APP_NAME)
    assert tags["og:description"] == "Follow any athlete in any sport: live scores, results and news."
    assert re.fullmatch(r"https://stann\.in/api/share\.png\?v=[0-9a-f]{10}", tags["og:image"])


def test_the_pictures_address_changes_when_the_score_does_and_only_then():
    before = share.of_card(KOHLI)
    again = share.of_card({**KOHLI, "version": 13})  # a new card that shows the same
    moved = share.of_card({**KOHLI, "live": {**KOHLI["live"], "score": "IND 251/3 (43 ov)"}})
    assert before.picture.digest() == again.picture.digest() != moved.picture.digest()


# ---------------------------------------------------------------- the picture


@pytest.mark.parametrize("name", [
    "Virat Kohli",
    "Rahmanullah Gurbaz Zadran Mohammad Abdulrahman Khan Wolfeschlegelsteinhausenbergerdorff",  # cut to fit
    "Novak Đoković",
    "Uzi 简自豪",  # a script none of the app's typefaces has: what they can draw
    "简自豪",  # nothing they can draw
])
def test_any_name_draws_a_picture_of_the_size_previews_show(name):
    # On a player's picture the name is the big word; on a game's, the name under the moment's word.
    for shown in (share.of_card({**KOHLI, "player": {"name": name, "sport": "Cricket"}}), share.of_game(KOHLI, {**HUNDRED, "player": name})):
        assert shown is not None
        data = share.png(shown.picture)
        img = picture(data)
        assert (img.format, img.size) == ("PNG", (1200, 630))
        assert len(data) < 300_000  # WhatsApp shows a picture up to about this


def test_a_picture_is_drawn_once_and_kept_for_the_next_ask(monkeypatch):
    drawn = []
    monkeypatch.setattr(share, "_draw", lambda p: drawn.append(p) or b"png")
    monkeypatch.setattr(share, "_drawn", share.OrderedDict())
    monkeypatch.setattr(share, "KEPT", 2)
    first, second, third = (share.Picture("#000000", "#ffffff", "", word) for word in ("One", "Two", "Three"))
    assert share.drawn(first) is None and share.png(first) == b"png" and share.png(first) == b"png"
    share.png(second), share.png(third)  # the oldest goes
    assert drawn == [first, second, third] and share.drawn(first) is None and share.drawn(third) == b"png"


def test_the_picture_is_the_sports_block_with_the_paper_band():
    img = picture(share.png(share.of_card(KOHLI).picture)).convert("RGB")
    assert img.getpixel((1190, 10)) == (0x1F, 0x5B, 0xEF)  # cricket's blue, from the sport
    assert img.getpixel((600, 625)) == (0xF3, 0xF3, 0xEF)  # the paper band


# ---------------------------------------------------------------- through the server


@pytest.fixture
def built(api, monkeypatch, tmp_path):  # noqa: F811
    """A web build with its page and service worker, and Kohli's card in the (fake) Redis."""
    (tmp_path / "index.html").write_text(INDEX)
    (tmp_path / "sw.js").write_text("self.addEventListener('fetch', () => {})")
    monkeypatch.setattr(server, "WEB_DIST", tmp_path)
    monkeypatch.setattr(server, "SITE_URL", "")
    api.redis.data[bus.card_key(1)] = json.dumps(KOHLI)
    return api


def test_a_crawler_gets_a_players_page_with_its_preview(built, monkeypatch):
    got = built.call("GET", "/player/1?s=share")
    assert got.status_code == 200 and got.headers["content-type"].startswith("text/html") and got.headers["cache-control"] == "no-cache"
    tags = meta(got.text)
    assert tags["og:title"] == "Virat Kohli · Cricket" and tags["og:url"] == "http://test/player/1"  # the share tag isn't the page's
    assert tags["og:image"].startswith("http://test/api/players/1/share.png?v=")
    monkeypatch.setattr(server, "SITE_URL", "https://stann.in")  # the public address, whatever the request's
    assert meta(built.call("GET", "/player/1").text)["og:url"] == "https://stann.in/player/1"


def test_a_crawler_gets_a_games_page_with_its_moment(built, monkeypatch):
    asked = []
    monkeypatch.setattr(week, "game", lambda db, player_id, event_id: asked.append((player_id, event_id)) or HUNDRED)
    assert meta(built.call("GET", "/player/1?g=5&s=share").text)["og:title"] == "Hundred: Virat Kohli"
    assert asked == [(1, 5)]
    monkeypatch.setattr(week, "game", lambda db, player_id, event_id: None)  # nothing to show of that game
    assert meta(built.call("GET", "/player/1?g=6").text)["og:title"] == "Virat Kohli · Cricket"
    for odd in ("0", "-1", "abc", "١٢", "99999999999"):  # not a game id: the player's page, and no query
        assert meta(built.call("GET", f"/player/1?g={odd}").text)["og:title"] == "Virat Kohli · Cricket"
    assert asked == [(1, 5)]


@pytest.mark.parametrize("path", ["/", "/following", "/player/2", "/player/99999999999", "/player/1/extra", "/athlete/espn_cricket/253802"])
def test_any_other_page_or_a_player_no_one_has_gets_the_apps_preview(built, path):
    got = built.call("GET", path)
    assert got.status_code == 200 and meta(got.text)["og:title"] == APP_NAME


def test_a_preview_that_fails_never_stops_the_page_opening(built, monkeypatch):
    async def down(key):
        raise ConnectionError("Redis is down")

    monkeypatch.setattr(built.redis, "get", down)
    got = built.call("GET", "/player/1")
    assert got.status_code == 200 and meta(got.text)["og:title"] == APP_NAME


def test_the_builds_own_files_are_served_as_they_are(built):
    assert built.call("GET", "/sw.js").text.startswith("self.addEventListener")
    assert "og:title" in built.call("GET", "/index.html").text  # the page itself is the app's, with its tags


@pytest.mark.parametrize("path", ["/api", "/api/", "/api/admin", "/api/me/nothing", "/api/players/1/nothing"])
def test_an_api_path_that_isnt_one_is_a_json_404_not_the_app(built, path):
    got = built.call("GET", path)
    assert got.status_code == 404 and got.headers["content-type"] == "application/json" and got.json() == {"detail": "Not Found"}


def test_without_a_build_there_is_no_page(api, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(server, "WEB_DIST", tmp_path / "dist")
    assert api.call("GET", "/player/1").status_code == 404


def test_the_picture_routes_draw_the_preview(built, monkeypatch):
    player = built.call("GET", "/api/players/1/share.png?v=abc")
    assert player.status_code == 200 and player.headers["content-type"] == "image/png"
    assert player.headers["cache-control"] == "public, max-age=300" and picture(player.content).size == (1200, 630)
    monkeypatch.setattr(week, "game", lambda db, player_id, event_id: HUNDRED)
    game = built.call("GET", "/api/players/1/share.png?g=5")
    assert game.status_code == 200 and game.content != player.content
    assert built.call("GET", "/api/share.png").content == share.png(share.APP)
    assert built.call("GET", "/api/players/2/share.png").status_code == 404  # no such player
    assert built.call("GET", "/api/players/1/share.png?g=0").status_code == 422


GUKESH = {"slug": "gukesh", "sport": "chess", "title": "Gukesh v Sindarov", "event": "World Chess Championship", "lines": ["Fourteen games."],
          "players": [{"name": "Gukesh D"}, {"name": "Javokhir Sindarov"}], "teams": [], "games": [{"start_utc": "2099-11-24T09:30:00+00:00", "label": "Game 1"}]}


def test_a_crawler_gets_a_campaign_page_with_its_preview_and_picture(built, monkeypatch):
    asked = []
    monkeypatch.setattr(campaigns, "page", lambda db, slug, fan: asked.append((slug, fan)) or (GUKESH if slug == "gukesh" else None))
    tags = meta(built.call("GET", "/gukesh?utm_source=whatsapp").text)
    assert tags["og:title"] == "Gukesh v Sindarov · World Chess Championship" and tags["og:url"] == "http://test/gukesh"
    assert tags["og:image"].startswith("http://test/api/campaigns/gukesh/share.png?v=") and "Game 1: Tue 24 Nov" in tags["og:description"]
    got = built.call("GET", "/api/campaigns/gukesh/share.png")
    assert got.status_code == 200 and picture(got.content).size == (1200, 630) and got.content == share.png(share.of_campaign(GUKESH).picture)
    # No such campaign, or an address that isn't one: the app's own preview, and no picture.
    assert meta(built.call("GET", "/nothing-here").text)["og:title"] == APP_NAME
    assert built.call("GET", "/api/campaigns/nothing-here/share.png").status_code == 404
    assert built.call("GET", "/api/campaigns/Not%20One/share.png").status_code == 404
    assert asked == [("gukesh", None), ("gukesh", None), ("nothing-here", None), ("nothing-here", None)]  # never a fan's rows in a preview
