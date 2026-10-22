"""Team logos (logos.py, BHA-666), with no database: where each source keeps a team's logo and the hosts it may come
from, what a downloaded logo is kept as, the colours read from it, an operator's colours, the switches that take
logos down, and the Credits page's word on them. Logos are drawn here with Pillow; the network is made up."""

from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image, ImageDraw

from sports_follow import controls, credits, logos
from sports_follow.adapters import pandascore_esports, sportmonks_cricket, sportmonks_football
from sports_follow.adapters.base import Adapter

BLUE, GARNET, GOLD, RED, WHITE, BLACK = (0, 77, 152), (165, 0, 68), (216, 174, 7), (227, 60, 62), (255, 255, 255), (13, 13, 13)


def drawn(*stripes: tuple[tuple[int, int, int], float], size: int = 120, ring: tuple[int, int, int] | None = None, mode: str = "PNG") -> bytes:
    """A logo of vertical stripes, each a colour and its share of the width, on a clear ground; with a ring round it."""
    pic = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(pic)
    x = 0.0
    for colour, share in stripes:
        draw.rectangle([round(x), 10, round(x + share * size) - 1, size - 11], fill=(*colour, 255))
        x += share * size
    if ring:
        draw.rectangle([0, 0, size - 1, size - 1], outline=(*ring, 255), width=16)
    out = io.BytesIO()
    (pic.convert("RGB") if mode == "JPEG" else pic).save(out, format=mode)
    return out.getvalue()


# ---------------------------------------------------------------- what a source says


def test_each_source_says_where_a_teams_logo_is_and_its_logos_hosts_hold_it():
    league = {"name": "La Liga"}
    football = sportmonks_football.parse_teams([{"id": 83, "name": "FC Barcelona", "short_code": "BAR", "image_path": "https://cdn.sportmonks.com/images/soccer/teams/19/83.png"}], league)
    cricket = sportmonks_cricket.parse_teams({"teams": [{"id": 10, "name": "India", "code": "IND", "national_team": True, "image_path": "https://cdn.sportmonks.com/images/cricket/teams/10/10.png"}]}, {"name": "T20I"})
    esports = pandascore_esports.parse_team({"id": 2882, "name": "Gen.G", "acronym": "GEN", "current_videogame": {"slug": "league-of-legends"},
                                             "image_url": "https://cdn-api.pandascore.co/images/team/image/2882/gen.png"})
    assert esports is not None
    for adapter, team in ((sportmonks_football.SportmonksFootball, football[0]), (sportmonks_cricket.SportmonksCricket, cricket[0]), (pandascore_esports.PandascoreEsports, esports)):
        assert team.logo and logos.allowed(adapter.system, team.logo), adapter.system
    # A team without one has none, rather than an address that isn't one.
    assert sportmonks_football.parse_teams([{"id": 1, "name": "Placeholder FC"}], league)[0].logo == ""


def test_a_logo_comes_from_its_own_sources_hosts_over_https_only():
    assert logos.allowed("sportmonks_football", "https://cdn.sportmonks.com/images/soccer/teams/19/83.png")
    assert logos.allowed("pandascore_esports", "https://cdn-api.pandascore.co/images/team/image/1/a.png")
    assert not logos.allowed("sportmonks_football", "http://cdn.sportmonks.com/images/soccer/teams/19/83.png")  # not HTTPS
    assert not logos.allowed("sportmonks_football", "https://cdn-api.pandascore.co/images/team/image/1/a.png")  # another source's
    assert not logos.allowed("sportmonks_football", "https://cdn.sportmonks.com.evil.example/x.png")
    assert not logos.allowed("sportmonks_football", "https://169.254.169.254/latest/meta-data")
    assert not logos.allowed("no_such_source", "https://cdn.sportmonks.com/x.png")
    assert Adapter.logo_hosts == ()  # a source says its own; none by default


def network(monkeypatch, handler) -> None:
    monkeypatch.setattr(logos, "_client", httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))


def test_a_download_is_refused_when_it_strays_is_missing_or_too_big(monkeypatch):
    png = drawn((BLUE, 1.0))

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/ok.png":
            return httpx.Response(200, content=png)
        if path == "/away.png":
            return httpx.Response(302, headers={"location": "https://elsewhere.example/x.png"})
        if path == "/big.png":
            return httpx.Response(200, content=b"x" * (logos.MAX_BYTES + 1))
        return httpx.Response(404)

    network(monkeypatch, handler)
    base = "https://cdn.sportmonks.com"
    assert logos.download("sportmonks_football", f"{base}/ok.png") == png
    for path, why in (("/away.png", "redirected away"), ("/big.png", "over"), ("/gone.png", "HTTP 404")):
        with pytest.raises(logos.Refused, match=why):
            logos.download("sportmonks_football", f"{base}{path}")
    with pytest.raises(logos.Refused, match="not one of"):
        logos.download("sportmonks_football", "https://elsewhere.example/x.png")


# ---------------------------------------------------------------- what's kept


def test_a_logo_is_kept_as_a_png_drawn_again_at_most_size_pixels():
    png, colours = logos.redraw(drawn((BLUE, 0.7), (GARNET, 0.3), size=600, mode="JPEG"))
    with Image.open(io.BytesIO(png)) as kept:
        assert kept.format == "PNG" and max(kept.size) <= logos.SIZE
    assert colours[0]["hex"].startswith("#") and not colours[0]["grey"]


def test_what_isnt_a_picture_or_is_too_big_a_one_isnt_kept():
    with pytest.raises(logos.Refused, match="not an image"):
        logos.redraw(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
    with pytest.raises(logos.Refused, match="not an image"):
        logos.redraw(b"\x89PNG\r\n\x1a\n not really")
    huge = io.BytesIO()
    Image.new("1", (5000, 4000)).save(huge, format="PNG")  # a small file of many pixels
    with pytest.raises(logos.Refused, match="pixels"):
        logos.redraw(huge.getvalue())


# ---------------------------------------------------------------- its colours


def colours_of(data: bytes) -> dict | None:
    return logos.auto(logos.redraw(data)[1])


def near(hex_: str, rgb: tuple[int, int, int]) -> bool:
    return sum(abs(a - b) for a, b in zip(logos._rgb(hex_), rgb)) < 40


def test_a_teams_colours_are_its_logos_two_biggest():
    found = colours_of(drawn((BLUE, 0.6), (GARNET, 0.4)))
    assert found and near(found["fill"], BLUE) and near(found["accent"], GARNET)


def test_a_crests_gold_trim_isnt_taken_for_its_colour():
    """Barcelona's border, Real Madrid's crown: gold covers much of a crest without being the team's colour."""
    found = colours_of(drawn((RED, 0.5), (BLUE, 0.5), ring=GOLD))
    assert found and not near(found["fill"], GOLD) and not near(found["accent"], GOLD)


def test_a_logo_in_one_colour_or_in_greys_alone_still_has_two():
    one = colours_of(drawn((GOLD, 1.0)))  # Gen.G: gold alone, with black to show on it
    assert one and near(one["fill"], GOLD) and one["accent"] == "#0d0d0d"
    greys = colours_of(drawn((BLACK, 0.6), (WHITE, 0.4)))  # G2, Invictus: drawn on white
    assert greys == {"fill": "#ffffff", "accent": "#0d0d0d"}
    assert logos.auto([]) is None  # a logo that's all clear


def test_an_operators_colours_are_checked():
    assert logos.colours_of("#004D98", "#A50044") == {"fill": "#004d98", "accent": "#a50044"}
    assert logos.colours_of("#ffffff") == {"fill": "#ffffff", "accent": "#0d0d0d"}  # an accent that shows on it
    assert logos.colours_of("#004d98") == {"fill": "#004d98", "accent": "#ffffff"}
    assert logos.colours_of("auto") is None and logos.colours_of(None) is None
    for wrong in ("blue", "#00f", "#00zz00", "url(x)"):
        with pytest.raises(ValueError):
            logos.colours_of(wrong)


# ---------------------------------------------------------------- taking them down


def test_the_switches_take_logos_down_by_sport_or_by_team():
    team = type("T", (), {"system": "sportmonks_football", "external_id": "83", "sport": "football"})()
    assert logos.shown(team)  # pyright: ignore[reportArgumentType]
    controls.turn_logos("football", False)
    assert not logos.shown(team)  # pyright: ignore[reportArgumentType]
    controls.turn_logos("football", True)
    controls.hide_logo("sportmonks_football:83", True)
    assert not logos.shown(team) and controls.current().logos_hidden == {"sportmonks_football:83"}  # pyright: ignore[reportArgumentType]
    controls.hide_logo("sportmonks_football:83", False)
    assert logos.shown(team)  # pyright: ignore[reportArgumentType]


def test_the_credits_page_says_whose_logos_they_are():
    said = " ".join(credits.credits(sources={}, stockfish=False, agent=False, photos=[], on=[])["notices"])
    assert "Team logos belong to their owners" in said and "take one down" in said
