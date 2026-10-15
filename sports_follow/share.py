"""Link previews: what a shared link to the app shows in WhatsApp, X and the rest.

The app is one page that draws itself in the browser, but a link preview is read by a crawler that runs
no JavaScript. So the server writes each page's Open Graph and Twitter card tags into the page itself
(server.spa): a player's page (/player/<id>) says who they are and what they're doing now, live, last or
next; a game of theirs (/player/<id>?g=<event id>, the link a This week poster shares) says its big moment,
as This week words it (week.game, Sport.poster); every other page says what the app is.

Each has a 1200 × 630 picture, drawn here: a block of the sport's colour (Sport.colour) with the words in
the app's own type (Anton, slanted, for the name and the moment's word; Manrope for the lines) and a paper
band with the app's name in the logo's Fredoka. Nothing else: no photos and no likenesses, no league or
team marks. The fonts are in data/fonts, each with its licence (OFL 1.1).
"""

from __future__ import annotations

import hashlib
import html
import io
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from . import sports
from .config import APP_NAME

FONTS = Path(__file__).resolve().parent / "data" / "fonts"
WIDTH, HEIGHT = 1200, 630  # what WhatsApp, X, Facebook and LinkedIn all show whole
PAD = 64
BAND = 104  # the paper band along the bottom
INK = "#0d0d0d"  # the app's text colour (web/src/styles.css --text): a sport without a colour wears it
PAPER = "#f3f3ef"  # the app's ground (--bg)
BRAND = "#1f5bef"  # the logo's blue, whatever colours the sports wear (web/scripts/make_icons.py)
SLANT = math.tan(math.radians(7))  # the app's display type leans 7° (styles.css .dsp)
TAGLINE = "Follow any athlete, live"
APP_WORD = "Any athlete.\nAny sport."  # the app's own preview
APP_LINE = "Live scores, their numbers while they play, results and alerts."


@dataclass(frozen=True)
class Picture:
    """What a preview's picture shows, and all it shows: the same words are drawn once (png)."""

    ground: str  # the block colour
    ink: str  # the words on it
    tag: str  # the pill at the top: the sport, "Live · Cricket"
    word: str  # in big type: the moment's word ("Hundred"), or the player's name
    name: str = ""  # under it, smaller: the player's name, under a moment's word
    lines: tuple[str, ...] = ()  # then a line or two of plain text: the score, the line, the game

    def digest(self) -> str:
        """A few characters that change whenever the picture would: its address carries them, so a preview
        cached by its address is drawn again once the score moves."""
        return hashlib.blake2b(repr(self).encode(), digest_size=5).hexdigest()


@dataclass(frozen=True)
class Preview:
    """A page's preview: its title and description, its own address, and its picture."""

    title: str
    description: str
    path: str  # "/player/3", "/player/3?g=20"
    picture: Picture
    image: str  # the picture's address, without its digest


def _colours(sport: sports.Sport) -> tuple[str, str]:
    """The sport's block colour and the colour of words on it; ink and paper for a sport without one."""
    if not sport.colour:
        return INK, PAPER
    return sport.colour["sport"], sport.colour["on"]


def _joined(*parts: Any, sep: str = " · ") -> str:
    return sep.join(str(p).strip() for p in parts if p and str(p).strip())


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?" else f"{text}."


def of_card(card: dict[str, Any]) -> Preview | None:
    """A player's page: who they are, and what they're doing now (live), did last (their last game) or do next.
    None for a card with no player's name (a build that never found one)."""
    player = card.get("player") or {}
    name = (player.get("name") or "").strip()
    if not name:
        return None
    sport_name = player.get("sport") or ""
    label = sports.label(sport_name)
    ground, ink = _colours(sports.of(sport_name))
    live = card.get("live") or {}
    results = card.get("recent_results") or []
    upcoming = [u for u in card.get("upcoming") or [] if u.get("status") not in ("final", "postponed")]
    tag = label
    if live.get("is_live"):
        tag = _joined("Live", label)
        lines = (_joined(live.get("event"), live.get("competition")), _joined(live.get("score"), live.get("clock"), live.get("headline")))
        said = f"Live now: {_joined(live.get('event'), live.get('score'), sep=', ')}. {_sentence(live.get('headline') or '')}"
    elif card.get("status") == "ready" and results:
        last = results[0]
        lines = (_joined("Last game", last.get("title"), sep=": "), _joined(last.get("result"), last.get("player_contribution")))
        said = f"Last game: {_joined(last.get('title'), last.get('result'), sep=', ')}. {_sentence(last.get('player_contribution') or '')}"
    elif card.get("status") == "ready" and upcoming:
        nxt = upcoming[0]
        lines = (_joined("Next", nxt.get("title"), sep=": "), nxt.get("competition") or "")
        said = f"Next: {_joined(nxt.get('title'), nxt.get('competition'), sep=', ')}."
    else:
        lines = (_joined(*(player.get("teams") or [])[:2], player.get("role")),)
        said = _sentence(_joined(label, *(player.get("teams") or [])[:2], sep=", "))
    player_id = card.get("player_id")
    return Preview(
        title=f"{name} · {label}" if label else name,
        description=f"{said.strip()} Live scores, results and alerts for {name} on {APP_NAME}.".strip(),
        path=f"/player/{player_id}",
        picture=Picture(ground, ink, tag, name, "", tuple(line for line in lines if line)),
        image=f"/api/players/{player_id}/share.png",
    )


def of_game(card: dict[str, Any], poster: dict[str, Any]) -> Preview | None:
    """A player's game as This week shows it (week.game): its big moment's word in big type ("Hundred"), the
    player's name, and the line ("112 (98) v Australia")."""
    shown = of_card(card)
    if shown is None:
        return None
    name, label = poster["player"], sports.label(poster["sport"])
    ground, ink = _colours(sports.of(poster["sport"]))
    game = _joined(poster.get("title"), poster.get("competition"))
    return Preview(
        title=f"{poster['word']}: {name}",
        description=f"{_sentence(_joined(poster['line'], poster.get('competition'), sep=', '))} Live scores, results and alerts for {name} on {APP_NAME}.",
        path=f"/player/{poster['player_id']}?g={poster['event_id']}",
        picture=Picture(ground, ink, label, poster["word"], name, tuple(line for line in (poster["line"], game) if line)),
        image=f"/api/players/{poster['player_id']}/share.png?g={poster['event_id']}",
    )


APP = Picture(BRAND, "#ffffff", "", APP_WORD, "", (APP_LINE,))


def of_app(description: str) -> Preview:
    """Any other page: the app itself."""
    return Preview(APP_NAME, description, "/", APP, "/api/share.png")


# ---------------------------------------------------------------- the page


def tags(preview: Preview, site: str) -> str:
    """The Open Graph and Twitter card tags for a page, with absolute addresses on `site` ("https://stann.in")."""
    image = f"{site}{preview.image}{'&' if '?' in preview.image else '?'}v={preview.picture.digest()}"
    alt = _joined(preview.picture.word, preview.picture.name, *preview.picture.lines, sep=". ")
    meta = [
        ("property", "og:site_name", APP_NAME),
        ("property", "og:type", "website"),
        ("property", "og:title", preview.title),
        ("property", "og:description", preview.description),
        ("property", "og:url", f"{site}{preview.path}"),
        ("property", "og:image", image),
        ("property", "og:image:type", "image/png"),
        ("property", "og:image:width", str(WIDTH)),
        ("property", "og:image:height", str(HEIGHT)),
        ("property", "og:image:alt", alt),
        ("name", "twitter:card", "summary_large_image"),
        ("name", "twitter:title", preview.title),
        ("name", "twitter:description", preview.description),
        ("name", "twitter:image", image),
        ("name", "twitter:image:alt", alt),
    ]
    return "\n".join(f'    <meta {kind}="{key}" content="{html.escape(value, quote=True)}" />' for kind, key, value in meta)


_DESCRIPTION = re.compile(r'(<meta name="description" content=")[^"]*(")')


def description_of(page: str) -> str:
    """The page's own description (web/index.html's): what the app's preview says."""
    m = re.search(r'<meta name="description" content="([^"]*)"', page)
    return html.unescape(m[1]) if m else ""


def page(index: str, preview: Preview | None, site: str) -> str:
    """The app's page (the build's index.html) with a preview's description and tags in its head; the app's
    own when there's no other. Its title stays the app's: the app doesn't change it, so a player's would
    stay in the tab after the fan moved on."""
    preview = preview or of_app(description_of(index))
    if preview.path != "/":
        index = _DESCRIPTION.sub(lambda m: f"{m[1]}{html.escape(preview.description, quote=True)}{m[2]}", index, count=1)
    return index.replace("</head>", f"{tags(preview, site)}\n  </head>", 1)


# ---------------------------------------------------------------- the picture


@lru_cache(maxsize=128)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """One of the app's typefaces at a size: Anton, Manrope at weight 800, or Fredoka at 600 (the logo's)."""
    if name == "anton":
        return ImageFont.truetype(FONTS / "Anton-Regular.ttf", size)
    font = ImageFont.truetype(FONTS / f"{name.title()}.ttf", size)
    font.set_variation_by_axes([800] if name == "manrope" else [600, 100])  # Fredoka's axes: weight, width
    return font


def _shape(font: ImageFont.FreeTypeFont, char: str) -> bytes:
    img = Image.new("L", (64, 64))
    ImageDraw.Draw(img).text((8, 8), char, font=font, fill=255)
    return img.tobytes()


@lru_cache(maxsize=1024)
def _has(name: str, char: str) -> bool:
    """Does the typeface draw this character? One it lacks comes out as its empty box (.notdef), as a
    character no font has does."""
    font = _font(name, 40)
    return char.isspace() or _shape(font, char) != _shape(font, "\U000effff")


def _drawable(text: str, *names: str) -> tuple[str, str]:
    """The first of the typefaces that draws all of the text, and the text. When none does (a name in a
    script none of them has), the one that draws the most of it, and the text without what it can't."""
    best = max(names, key=lambda name: (sum(_has(name, c) for c in text), -names.index(name)))
    if all(_has(best, c) for c in text):
        return best, text
    return best, " ".join("".join(c for c in text if _has(best, c)).split())


def _wrapped(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """The text in lines no wider than `width`, broken between words and where it has a line break (a word
    wider than that alone on its line)."""
    lines: list[str] = []
    for part in text.split("\n"):
        start = len(lines)
        for word in part.split():
            if len(lines) > start and font.getlength(f"{lines[-1]} {word}") <= width:
                lines[-1] = f"{lines[-1]} {word}"
            else:
                lines.append(word)
    return lines


def _tall(lines: list[str], font: ImageFont.FreeTypeFont) -> float:
    """How tall lines of display type stand, one on another (_slanted)."""
    return sum(font.getbbox(line)[3] - font.getbbox(line)[1] for line in lines) + _gap(font) * (len(lines) - 1)


def _gap(font: ImageFont.FreeTypeFont) -> int:
    return round(font.size * 0.08)


def _fit(text: str, name: str, width: int, height: int, sizes: range, most: int) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """The biggest size the text fits at, in at most `most` lines `width` wide and `height` tall together; at
    the smallest, its first lines, each cut to fit."""
    for size in sizes:
        font = _font(name, size)
        lines = _wrapped(text, font, width)
        if len(lines) <= most and all(font.getlength(line) <= width for line in lines) and _tall(lines, font) <= height:
            return font, lines
    font = _font(name, sizes[-1])
    return font, [_cut(line, font, width) for line in _wrapped(text, font, width)[:most]]


def _cut(text: str, font: ImageFont.FreeTypeFont, width: int) -> str:
    """The text, shortened with an ellipsis until it fits."""
    if font.getlength(text) <= width:
        return text
    while text and font.getlength(f"{text}…") > width:
        text = text[:-1]
    return f"{text.rstrip()}…"


def _slanted(img: Image.Image, x: int, bottom: int, text: str, font: ImageFont.FreeTypeFont, name: str, fill: str) -> int:
    """Draw a line of display type leaning as the app's does, the bottom of its letters at `bottom`; returns
    the top of what it drew. Drawn twice the size and scaled down, so the leaning edges stay smooth."""
    big = _font(name, round(font.size) * 2)
    left, top, right, low = big.getbbox(text)
    w, h = math.ceil(right - left), math.ceil(low - top)
    mask = Image.new("L", (w + math.ceil(h * SLANT) + 4, h))
    ImageDraw.Draw(mask).text((-left, -top), text, font=big, fill=255)
    # The bottom row stays put and each row above moves right by its height times the slant.
    mask = mask.transform(mask.size, Image.Transform.AFFINE, (1, SLANT, -SLANT * h, 0, 1, 0), resample=Image.Resampling.BICUBIC)
    mask = mask.resize((math.ceil(mask.width / 2), math.ceil(h / 2)), Image.Resampling.LANCZOS)
    img.paste(fill, (x, bottom - mask.height), mask)
    return bottom - mask.height


def _pill(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, ground: str, ink: str) -> None:
    """The tag at the top, as the app's posters have it: spaced capitals on a pill."""
    font = _font("manrope", 26)
    text = text.upper()
    spaced = 2
    width = sum(font.getlength(c) + spaced for c in text) - spaced
    draw.rounded_rectangle((x, y, x + width + 44, y + 48), radius=24, fill=ink)
    cx = x + 22
    for c in text:
        draw.text((cx, y + 24), c, font=font, fill=ground, anchor="lm")
        cx += font.getlength(c) + spaced


@lru_cache(maxsize=128)
def png(picture: Picture) -> bytes:
    """The picture as a PNG: the block, the tag, the words from the bottom up (lines, name, big word), and the
    paper band with the app's name."""
    img = Image.new("RGB", (WIDTH, HEIGHT), picture.ground)
    draw = ImageDraw.Draw(img)
    width = WIDTH - 2 * PAD
    if picture.tag and (tag := _drawable(picture.tag, "manrope")[1]):
        _pill(draw, PAD, PAD - 8, tag, picture.ground, picture.ink)
    ceiling = PAD + 80  # under the tag

    bottom = HEIGHT - BAND - 44
    for line in reversed(picture.lines[:2]):
        font = _font("manrope", 34)
        draw.text((PAD, bottom), _cut(_drawable(line, "manrope")[1], font, width), font=font, fill=picture.ink, anchor="ls")
        bottom -= 50
    bottom -= 14

    if picture.name:
        name, text = _drawable(picture.name.upper(), "anton", "manrope")
        font, lines = _fit(text, name, width - 40, 100, range(84, 55, -4), 1)
        bottom = _slanted(img, PAD, bottom, lines[0], font, name, picture.ink) - 18

    name, text = _drawable(picture.word.upper(), "anton", "manrope")  # keeps a line break: a space to the typefaces
    font, lines = _fit(text, name, width - 40, bottom - ceiling, range(220 if picture.name else 180, 47, -4), 2)
    for line in reversed(lines):
        bottom = _slanted(img, PAD, bottom, line, font, name, picture.ink) - _gap(font)

    draw.rectangle((0, HEIGHT - BAND, WIDTH, HEIGHT), fill=PAPER)
    draw.text((PAD, HEIGHT - BAND / 2), APP_NAME, font=_font("fredoka", 60), fill=BRAND, anchor="lm")
    draw.text((WIDTH - PAD, HEIGHT - BAND / 2), TAGLINE, font=_font("manrope", 28), fill=INK, anchor="rm")

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
