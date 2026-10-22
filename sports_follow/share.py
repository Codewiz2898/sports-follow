"""Link previews: what a shared link to the app shows in WhatsApp, X and the rest.

The app is one page that draws itself in the browser, but a link preview is read by a crawler that runs
no JavaScript. So the server writes each page's Open Graph and Twitter card tags into the page itself
(server.spa): a player's page (/player/<id>) says who they are and what they're doing now, live, last or
next; a game of theirs (/player/<id>?g=<event id>, the link a This week poster shares) says its big moment,
as This week words it (week.game, Sport.poster); a campaign page (/<slug>, campaigns.py) says its event, its
next game and who to follow; a league's invite (/j/<code>) says join it, who asked and how many are in it; a
team's page, a clip's and a game's say who, what and where it's at; every other page says what the app is.

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
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from . import campaigns, sports
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
        return hashlib.blake2b(repr((APP_NAME, self)).encode(), digest_size=5).hexdigest()  # the band says the name


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


def _and(names: list[str]) -> str:
    """ "A", "A and B", "A, B and C"."""
    return " and ".join(names) if len(names) < 3 else f"{', '.join(names[:-1])} and {names[-1]}"


def of_campaign(shown: dict[str, Any]) -> Preview:
    """A campaign page (campaigns.page): its title in big type on its sport's colour, the event on the tag,
    then its next game in the home audience's time and who to follow."""
    ground, ink = _colours(sports.of(shown["sport"]))
    nxt = campaigns.next_game(shown)
    game = f"{nxt['label']}: {campaigns.said(nxt['start_utc'])}" if nxt else ""
    names = [r["name"] for r in shown["players"]] or [t["name"] for t in shown["teams"]]
    follow = f"Follow {_and(names)}" if names else ""
    event = shown.get("event") or ""
    said = " ".join(x for x in (_sentence(event) if event else "", _sentence(game) if game else "", *(_sentence(line) for line in shown["lines"][:1]),
                                f"{follow} on {APP_NAME}: live boards and alerts." if follow else f"Live boards and alerts on {APP_NAME}.") if x)
    return Preview(
        title=f"{shown['title']} · {event}" if event else shown["title"],
        description=said,
        path=f"/{shown['slug']}",
        picture=Picture(ground, ink, event or sports.label(shown["sport"]), shown["title"], "", tuple(x for x in (game, follow) if x)),
        image=f"/api/campaigns/{shown['slug']}/share.png",
    )


def of_question(q: dict[str, Any]) -> Preview:
    """A question fans call (predictions.public), as the link a fan shares from its card (/match/<game>?q=<id>):
    once settled, how few called it ("7%", "called it: Sindarov"); before, how fans split ("71%", "say Gukesh D"),
    once enough have picked to show it; with fewer, the question itself, and once settled its answer (no picks that
    count: no share of them, BUG-314). In its sport's colour, as every preview."""
    ground, ink = _colours(sports.of(q["sport"]))
    label = sports.label(q["sport"])
    shown = [o for o in q["options"] if o.get("share") is not None]
    answer = next((o for o in shown if o["key"] == q.get("answer")), None) if q["status"] == "settled" and q["picks"] else None
    top = max(shown, key=lambda o: o["share"]) if shown and q["picks"] else None
    rest = _joined(*(f"{o['label']} {o['share']}%" for o in shown if o is not (answer or top)))
    if answer is not None:
        word, name, lines = f"{answer['share']}%", f"called it: {answer['label']}", (q["game"], f"{q['picks']:,} picks · {rest}" if rest else "")
        title = f"Only {answer['share']}% of {APP_NAME} fans called it: {answer['label']}" if answer["share"] <= 50 else f"{answer['share']}% of {APP_NAME} fans called it: {answer['label']}"
    elif top is not None:
        word, name, lines = f"{top['share']}%", f"say {top['label']}", (_joined(q["title"], q["game"]), _joined(rest, f"{q['picks']:,} picks"))
        title = f"{top['share']}% of {APP_NAME} fans say {top['label']}: {q['title']} {q['game']}"
    else:
        right = next((o["label"] for o in q["options"] if o["key"] == q.get("answer")), None) if q["status"] == "settled" else None
        word, name, lines = q["title"], "", (q["game"], f"Answer: {right}" if right else f"Call it on {APP_NAME} before it starts")
        title = f"{q['title']} {q['game']}"
    return Preview(
        title=title,
        description=f"{_sentence(_joined(q['title'], q['game'], sep=' '))} Call it before it starts, and play with friends on {APP_NAME}. Bragging rights only.",
        path=f"/match/{q['match']}?q={q['id']}",
        picture=Picture(ground, ink, label, word, name, tuple(line for line in lines if line)),
        image=f"/api/predictions/{q['id']}/share.png",
    )


def of_invite(shown: dict[str, Any]) -> Preview:
    """A league's invite (predictions.about), as its page (/j/<code>) says it: "You're invited", the league's name in
    big type, who asked and how many are in it, on the app's own blue (a league calls any sport's games). Its code is
    in the link alone, never in the words or the picture: a screenshot of the preview is no invite."""
    asked = f"{shown['by']} asked you" if shown.get("by") else "You're asked"  # its maker left: still an invite
    together = f"{asked} to call the games together"
    count = f"{shown['members']} in it so far"
    return Preview(
        title=f"Join {shown['name']} on {APP_NAME}",
        description=f"{together} on {APP_NAME}: {count}. Call each game before it starts; bragging rights only.",
        path=f"/j/{shown['code']}",
        picture=Picture(BRAND, "#ffffff", "You're invited", shown["name"], "", (together, count)),
        image=f"/api/leagues/{shown['code']}/share.png",
    )


def of_team(shown: dict[str, Any]) -> Preview | None:
    """A team's page (teams.page), as a player's says theirs: its name in big type on its sport's colour, then its
    game on now, else its last result from its side ("Won 88–80"), else its next game, else what it plays in. None
    for a team with no name."""
    name = (shown.get("name") or "").strip()
    if not name:
        return None
    label = sports.label(shown["sport"])
    ground, ink = _colours(sports.of(shown["sport"]))
    live = shown.get("live") or {}
    recent = shown.get("recent") or []
    upcoming = [u for u in shown.get("upcoming") or [] if u.get("status") not in ("final", "postponed")]
    tag = label
    if live.get("is_live"):
        tag = _joined("Live", label)
        lines = (_joined(live.get("event"), live.get("competition")), _joined(live.get("score"), live.get("clock")))
        said = f"Live now: {_joined(live.get('event'), live.get('score'), sep=', ')}."
    elif recent:
        last = recent[0]
        lines = (_joined("Last game", last.get("title"), sep=": "), last.get("result") or "")
        said = f"Last game: {_joined(last.get('title'), last.get('result'), sep=', ')}."
    elif upcoming:
        nxt = upcoming[0]
        lines = (_joined("Next", nxt.get("title"), sep=": "), nxt.get("competition") or "")
        said = f"Next: {_joined(nxt.get('title'), nxt.get('competition'), sep=', ')}."
    else:
        lines = (shown.get("detail") or "",)
        said = _sentence(_joined(label, shown.get("detail"), sep=", "))
    return Preview(
        title=f"{name} · {label}" if label else name,
        description=f"{said} Live scores, results and alerts for {name} on {APP_NAME}.".strip(),
        path=f"/team/{shown['team_id']}",
        picture=Picture(ground, ink, tag, name, "", tuple(line for line in lines if line)),
        image=f"/api/teams/{shown['team_id']}/share.png",
    )


def of_clip(clip: dict[str, Any]) -> Preview:
    """A clip (clips.one, /clip/<id>): its caption in big type, else its moment, on its sport's colour (ink for a fan's
    own game, of no sport), then the moment and its game. Drawn, as every preview is: never a frame of the clip."""
    sport = sports.get(clip.get("sport"))
    ground, ink = _colours(sports.of(clip.get("sport")))
    word = clip.get("caption") or clip.get("line") or clip.get("game_title") or f"A clip on {APP_NAME}"  # its title as shared (web/src/share.ts)
    lines = tuple(x for x in (clip.get("line") or "", _joined(clip.get("game_title"), clip.get("competition"))) if x and x != word)
    return Preview(
        title=word,
        description=f"{_sentence(_joined(*lines, sep=', '))} Watch it on {APP_NAME}.".strip(),
        path=f"/clip/{clip['id']}",
        picture=Picture(ground, ink, _joined("Clip", sports.label(sport.key) if sport else ""), word, "", lines),
        image=f"/api/clips/{clip['id']}/share.png",
    )


def of_match(shown: dict[str, Any]) -> Preview | None:
    """A game's page (match.view, /match/<source>/<game>): its title in big type on its sport's colour, its
    competition, and where it's at: the score while it's on, who won once it's over, else when it starts in the home
    audience's time (campaigns.said). None for a game with no title."""
    title = (shown.get("title") or "").strip()
    if not title:
        return None
    sport = sports.get(shown.get("sport"))
    label = sports.label(sport.key) if sport else ""
    ground, ink = _colours(sports.of(shown.get("sport")))
    board = shown.get("live") or {}
    tag = label
    if shown.get("status") == "live":
        tag = _joined("Live", label)
        where = _joined(board.get("score"), board.get("clock"))
    elif shown.get("status") == "final":
        where = shown.get("verdict") or _joined(board.get("score"), "Final")
    elif shown.get("status") == "postponed":
        where = "Postponed"
    else:
        where = campaigns.said(shown["start_utc"]) if shown.get("start_utc") else ""
    lines = tuple(x for x in (_joined(shown.get("competition"), shown.get("detail")), where) if x)
    return Preview(
        title=_joined(title, shown.get("competition")),
        description=f"{_sentence(_joined(*lines, sep=', '))} Live scores, key moments and the result on {APP_NAME}.".strip(),
        path=f"/match/{shown['match']}",
        picture=Picture(ground, ink, tag, title, "", lines),
        image=f"/api/match/{shown['match']}/share.png",
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
    """The first of the typefaces that draws all of the text, and the text. When none does, the one that draws
    the most of it, and the text without the symbols it lacks (an emoji); but none of the text where it lacks a
    letter (words in a script none of them has, Devanagari): drawn without them, "Follow गुकेश and Sindarov" read
    "Follow and Sindarov"."""
    best = max(names, key=lambda name: (sum(_has(name, c) for c in text), -names.index(name)))
    if all(_has(best, c) for c in text):
        return best, text
    if any(unicodedata.category(c)[0] in "LM" and not _has(best, c) for c in text):
        return best, ""
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
    spaced = 2

    def wide(t: str) -> float:
        return sum(font.getlength(c) + spaced for c in t) - spaced

    text, most = text.upper(), WIDTH - 2 * PAD - 44  # within the picture's margins: a long event's name is cut
    if wide(text) > most:
        while text and wide(f"{text}…") > most:
            text = text[:-1]
        text = f"{text.rstrip()}…"
    width = wide(text)
    draw.rounded_rectangle((x, y, x + width + 44, y + 48), radius=24, fill=ink)
    cx = x + 22
    for c in text:
        draw.text((cx, y + 24), c, font=font, fill=ground, anchor="lm")
        cx += font.getlength(c) + spaced


KEPT = 256  # pictures kept drawn, the newest used: about 50 KB each
_drawn: OrderedDict[Picture, bytes] = OrderedDict()
_kept = threading.Lock()


def drawn(picture: Picture) -> bytes | None:
    """The picture as a PNG, if it was drawn lately: a page shared widely is asked for again and again."""
    with _kept:
        if (data := _drawn.get(picture)) is not None:
            _drawn.move_to_end(picture)
        return data


def png(picture: Picture) -> bytes:
    """The picture as a PNG, drawn once and kept (drawn)."""
    if (data := drawn(picture)) is not None:
        return data
    data = _draw(picture)
    with _kept:
        _drawn[picture] = data
        while len(_drawn) > KEPT:
            _drawn.popitem(last=False)
    return data


def _draw(picture: Picture) -> bytes:
    """The block, the tag, the words from the bottom up (lines, name, big word), and the paper band with the
    app's name."""
    img = Image.new("RGB", (WIDTH, HEIGHT), picture.ground)
    draw = ImageDraw.Draw(img)
    width = WIDTH - 2 * PAD
    if picture.tag and (tag := _drawable(picture.tag, "manrope")[1]):
        _pill(draw, PAD, PAD - 8, tag, picture.ground, picture.ink)
    ceiling = PAD + 80  # under the tag

    bottom = HEIGHT - BAND - 44
    font = _font("manrope", 34)
    for line in reversed([shown for line in picture.lines[:2] if (shown := _drawable(line, "manrope")[1])]):  # one it can't draw goes
        draw.text((PAD, bottom), _cut(line, font, width), font=font, fill=picture.ink, anchor="ls")
        bottom -= 50
    bottom -= 14

    name, text = _drawable(picture.name.upper(), "anton", "manrope")
    if text:  # not a name none of the typefaces can draw any of
        font, lines = _fit(text, name, width - 40, 100, range(84, 55, -4), 1)
        bottom = _slanted(img, PAD, bottom, lines[0], font, name, picture.ink) - 18

    name, text = _drawable(picture.word.upper(), "anton", "manrope")  # keeps a line break: a space to the typefaces
    font, lines = _fit(text, name, width - 40, bottom - ceiling, range(220 if picture.name else 180, 47, -4), 2)
    for line in reversed(lines):
        bottom = _slanted(img, PAD, bottom, line, font, name, picture.ink) - _gap(font)

    draw.rectangle((0, HEIGHT - BAND, WIDTH, HEIGHT), fill=PAPER)
    draw.text((PAD, HEIGHT - BAND / 2), APP_NAME.lower(), font=_font("fredoka", 60), fill=BRAND, anchor="lm")  # as the logo writes it
    draw.text((WIDTH - PAD, HEIGHT - BAND / 2), TAGLINE, font=_font("manrope", 28), fill=INK, anchor="rm")

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
