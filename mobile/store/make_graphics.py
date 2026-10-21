"""Draw the Play Store's graphics from the app's own assets, and fit screenshots to Play's rules.

- icon-512.png: the store icon, the app's badge on a full paper square (Play rounds the corners itself), a
  32-bit PNG as Play asks, opaque. The same picture as web/public/icons/maskable-512.png, drawn by
  web/scripts/make_icons.py's rasterizer.
- feature-1024x500.png: the feature graphic, the badge and the name (Fredoka, lower case, as in the header), the site's
  one-line pitch in the leaning display type (Anton, the app's own slanted copy) and a line of Manrope.
  24-bit PNG with no alpha, as Play asks.
- screenshots/: with --screenshots DIR, every PNG or JPEG in DIR made opaque and brought within Play's limit
  of twice as long as wide: a longer one is set whole on a paper canvas (an emulator's 1080x2400 capture on
  1200x2400, a 60px margin each side; cutting it would take the floating tab bar off the bottom). Named as
  the captures are; what was there before goes.

Run from the repo root with its venv (Pillow is in requirements.txt), after `npm install` in mobile/
(the fonts come from node_modules/@expo-google-fonts):

    python mobile/store/make_graphics.py
    python mobile/store/make_graphics.py --screenshots ~/Desktop/slam-shots
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

STORE = Path(__file__).resolve().parent
MOBILE = STORE.parent
ROOT = MOBILE.parent
sys.path.insert(0, str(ROOT / "web" / "scripts"))
import make_icons  # noqa: E402  the badge's geometry and rasterizer, so the store's badge is the app's

PAPER = (0xF3, 0xF3, 0xEF)  # styles.css --bg
INK = (0x0D, 0x0D, 0x0D)  # --text
MUTED = (0x55, 0x57, 0x5C)  # --muted
FONTS = MOBILE / "node_modules" / "@expo-google-fonts"
FREDOKA = FONTS / "fredoka" / "600SemiBold" / "Fredoka_600SemiBold.ttf"  # the logo's type (.brand)
MANROPE = FONTS / "manrope" / "600SemiBold" / "Manrope_600SemiBold.ttf"  # body text
ANTON_SLANTED = MOBILE / "assets" / "fonts" / "AntonSlanted.ttf"  # the display type's lean, as the app draws it

# The name as the logo writes it: sports_follow/app.json's, in lower case (styles.css .brand).
NAME = json.loads((ROOT / "sports_follow" / "app.json").read_text(encoding="utf-8"))["name"].lower()
PITCH = "Follow any athlete in any sport"  # web/index.html's description, its first clause
LINE = "Live scores, results and alerts for the players you follow. No ads."


def badge(size: int) -> Image.Image:
    """The badge alone, edge to edge, `size` pixels square, with alpha."""
    return Image.open(io.BytesIO(make_icons.render(make_icons.VARIANTS["mark"], size))).convert("RGBA")


def icon() -> Path:
    out = STORE / "icon-512.png"
    Image.open(io.BytesIO(make_icons.render(make_icons.VARIANTS["maskable"], 512))).convert("RGBA").save(out, optimize=True)
    return out


def fitted(path: Path, text: str, size: int, width: int) -> ImageFont.FreeTypeFont:
    """The font at `size`, or smaller until `text` fits in `width`."""
    while size > 8:
        font = ImageFont.truetype(str(path), size)
        if font.getlength(text) <= width:
            return font
        size -= 2
    raise ValueError(f"{text!r} doesn't fit in {width}px")


def feature() -> Path:
    width, height = 1024, 500
    im = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(im)
    for path in (FREDOKA, MANROPE, ANTON_SLANTED):
        if not path.exists():
            raise FileNotFoundError(f"{path}: run `npm install` in mobile/ first")

    # Row 1: the badge and the name, as the header pairs them (.brand: the badge 1.2 times the type's size).
    name_font = ImageFont.truetype(str(FREDOKA), 168)
    mark = badge(200)
    name_w = name_font.getlength(NAME)
    gap = 28
    row_w = mark.width + gap + name_w
    x = (width - row_w) / 2
    top = 64
    im.paste(mark, (round(x), top), mark)
    # Fredoka's x-height sits low in its box: align the word's visual middle with the badge's centre.
    _, name_top, _, name_bottom = name_font.getbbox(NAME)
    name_y = top + mark.height / 2 - (name_top + name_bottom) / 2
    draw.text((x + mark.width + gap, name_y), NAME, font=name_font, fill=INK)

    # Row 2: the pitch, in the leaning display type, upper case as .dsp sets it.
    pitch_font = fitted(ANTON_SLANTED, PITCH.upper(), 60, width - 120)
    pitch_w = pitch_font.getlength(PITCH.upper())
    pitch_y = top + mark.height + 42
    draw.text(((width - pitch_w) / 2, pitch_y), PITCH.upper(), font=pitch_font, fill=INK)

    # Row 3: one plain line.
    line_font = fitted(MANROPE, LINE, 27, width - 120)
    line_w = line_font.getlength(LINE)
    _, _, _, pitch_bottom = pitch_font.getbbox(PITCH.upper())
    draw.text(((width - line_w) / 2, pitch_y + pitch_bottom + 22), LINE, font=line_font, fill=MUTED)

    out = STORE / "feature-1024x500.png"
    im.save(out, optimize=True)
    return out


def screenshots(source: Path) -> list[Path]:
    """Every picture in `source`, opaque and no more than twice as long as wide, under its own name."""
    out_dir = STORE / "screenshots"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    made = []
    for path in sorted(p for p in source.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}):
        im = Image.open(path).convert("RGB")
        w, h = im.size
        if h > 2 * w or w > 2 * h:  # whole, on paper, with the margin that brings it to 2:1
            cw, ch = max(w, -(-h // 2)), max(h, -(-w // 2))
            canvas = Image.new("RGB", (cw, ch), PAPER)
            canvas.paste(im, ((cw - w) // 2, (ch - h) // 2))
            im = canvas
        if min(im.size) < 320 or max(im.size) > 3840:
            raise ValueError(f"{path.name}: {im.size} is outside Play's 320 to 3840px")
        out = out_dir / f"{path.stem}.png"
        im.save(out, optimize=True)
        made.append(out)
    return made


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--screenshots", type=Path, help="a folder of emulator captures to fit for Play")
    args = parser.parse_args()
    for made in (icon(), feature(), *(screenshots(args.screenshots) if args.screenshots else [])):
        with Image.open(made) as im:
            print(f"{made.relative_to(ROOT)}  {im.size[0]}x{im.size[1]} {im.mode}  {made.stat().st_size:,} bytes")
