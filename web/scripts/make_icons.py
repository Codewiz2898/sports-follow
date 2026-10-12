"""Draw the app icon: four leaning player tiles, as on the Today screen, on the app's paper ground.

Three tiles wear sport colours (basketball, cricket, football); the fourth is ink with the red "live"
dot. Writes public/icons/*.svg, public/favicon.svg and the PNG sizes Android, Chrome and iOS ask
for, from one geometry, with no image library (a tiny anti-aliased rasterizer and PNG writer). Run
from web/:

    python3 scripts/make_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

PUBLIC = Path(__file__).resolve().parent.parent / "public"
OUT = PUBLIC / "icons"
PAPER = (0xF3, 0xF3, 0xEF)
INK = (0x0D, 0x0D, 0x0D)
LIVE = (0xDC, 0x20, 0x16)
WHITE = (0xFF, 0xFF, 0xFF)
# Top left, top right, bottom left, bottom right: the colours styles.css gives [data-sport].
TILES = [(0xFF, 0x7A, 0x1A), (0x1F, 0x5B, 0xEF), (0x12, 0xC4, 0x6C), INK]
LEAN = math.tan(math.radians(7))  # the tiles lean like the app's Anton type (skewX(-7deg))

# On a 512 grid. "any" is a rounded tile; "maskable" fills the square and keeps the mark inside the
# 40%-radius safe zone launchers never crop; "monochrome" is the mark alone, for themed icons and the
# notification badge, with the live dot cut out of the last tile.
VARIANTS = {
    "icon": {"ground": 112, "tile": 140, "gap": 20, "radius": 34, "dot": 30, "mono": False},
    "maskable": {"ground": 0, "tile": 121, "gap": 18, "radius": 30, "dot": 26, "mono": False},
    "monochrome": {"ground": None, "tile": 121, "gap": 18, "radius": 30, "dot": 26, "mono": True},
}
# badge-96: the small white mark Android shows in the status bar for a notification.
PNGS = [("icon", 192), ("icon", 512), ("maskable", 192), ("maskable", 512), ("monochrome", 512), ("maskable", 180, "apple-touch-icon"), ("monochrome", 96, "badge-96")]


def tiles(v: dict) -> list[tuple[float, float]]:
    """Each tile's top-left corner, upright (the lean is applied around the centre)."""
    start = 256 - (2 * v["tile"] + v["gap"]) / 2
    step = v["tile"] + v["gap"]
    return [(start + col * step, start + row * step) for row in (0, 1) for col in (0, 1)]


def dot_centre(v: dict) -> tuple[float, float]:
    """The middle of the last tile once it leans. The dot itself stays round, like the app's live dot."""
    x, y = tiles(v)[3]
    cx, cy = x + v["tile"] / 2, y + v["tile"] / 2
    return cx - (cy - 256) * LEAN, cy


def svg(v: dict) -> str:
    hexes = lambda c: "#%02X%02X%02X" % c  # noqa: E731
    t, r = v["tile"], v["radius"]
    corners = tiles(v)
    dot_x, dot_y = dot_centre(v)
    ground = "" if v["ground"] is None else f'<rect width="512" height="512"{f" rx=\"{v["ground"]}\"" if v["ground"] else ""} fill="{hexes(PAPER)}"/>'
    lean = 'transform="translate(256 256) skewX(-7) translate(-256 -256)"'
    dot = f'<circle cx="{dot_x:.2f}" cy="{dot_y:g}" r="{v["dot"]}"'
    if v["mono"]:
        rects = "".join(f'<rect x="{x:g}" y="{y:g}" width="{t}" height="{t}" rx="{r}" fill="#FFFFFF"/>' for x, y in corners)
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
            f'<mask id="m"><g {lean}>{rects}</g>{dot} fill="#000000"/></mask>'
            '<rect width="512" height="512" fill="#FFFFFF" mask="url(#m)"/></svg>\n'
        )
    rects = "".join(f'<rect x="{x:g}" y="{y:g}" width="{t}" height="{t}" rx="{r}" fill="{hexes(c)}"/>' for (x, y), c in zip(corners, TILES))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        f'{ground}<g {lean}>{rects}</g>{dot} fill="{hexes(LIVE)}"/></svg>\n'
    )


def coverage(signed: float) -> float:
    """How much of a pixel a shape covers, from the signed distance of its centre to the edge."""
    return min(1.0, max(0.0, 0.5 - signed))


def rounded_rect_distance(x: float, y: float, x0: float, y0: float, w: float, h: float, radius: float) -> float:
    q = [abs(x - (x0 + w / 2)) - (w / 2 - radius), abs(y - (y0 + h / 2)) - (h / 2 - radius)]
    outside = math.hypot(max(q[0], 0), max(q[1], 0))
    return outside + min(max(q[0], q[1]), 0) - radius


def render(v: dict, size: int) -> bytes:
    s = size / 512
    c = size / 2
    t, r, dot = v["tile"] * s, v["radius"] * s, v["dot"] * s
    corners = [(x * s, y * s) for x, y in tiles(v)]
    dot_x, dot_y = (p * s for p in dot_centre(v))
    rows = []
    for py in range(size):
        row = bytearray([0])  # PNG filter: none
        for px in range(size):
            x, y = px + 0.5, py + 0.5
            ux = x - (c - y) * LEAN  # undo the lean: the top of a tile sits further right
            color, alpha = (0.0, 0.0, 0.0), 0.0  # premultiplied
            layers = []
            if v["ground"] is not None:
                layers.append((PAPER, coverage(rounded_rect_distance(x, y, 0, 0, size, size, v["ground"] * s))))
            for (x0, y0), rgb in zip(corners, TILES):
                layers.append((WHITE if v["mono"] else rgb, coverage(rounded_rect_distance(ux, y, x0, y0, t, t, r))))
            hole = coverage(math.hypot(x - dot_x, y - dot_y) - dot)
            if not v["mono"]:
                layers.append((LIVE, hole))
            for rgb, a in layers:  # "over" compositing, premultiplied
                color = tuple(ch / 255 * a + cc * (1 - a) for ch, cc in zip(rgb, color))
                alpha = a + alpha * (1 - a)
            if v["mono"]:  # the dot is cut out, so it shows through a themed icon
                color, alpha = tuple(cc * (1 - hole) for cc in color), alpha * (1 - hole)
            out = [round(cc / alpha * 255) if alpha else 0 for cc in color]
            row += bytes([*out, round(alpha * 255)])
        rows.append(bytes(row))
    return png(size, b"".join(rows))


def png(size: int, raw: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, v in VARIANTS.items():
        (OUT / f"{name}.svg").write_text(svg(v))
    (PUBLIC / "favicon.svg").write_text(svg(VARIANTS["icon"]))
    for name, size, *alias in PNGS:
        path = OUT / f"{alias[0] if alias else f'{name}-{size}'}.png"
        path.write_bytes(render(VARIANTS[name], size))
        print(path.name, path.stat().st_size, "bytes")
