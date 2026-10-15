"""Draw the app icon: stann's fan badge, a blue scalloped pin with a white "s", on the app's paper ground.

The "s" is Fredoka's, the logo's typeface (the header pairs the badge with "stann" in it, styles.css
.brand). Writes public/icons/*.svg, public/favicon.svg and the PNG sizes Android, Chrome and iOS ask for,
from one geometry, with no image library (a tiny anti-aliased rasterizer and PNG writer). Run from web/:

    python3 scripts/make_icons.py
"""

from __future__ import annotations

import math
import re
import struct
import zlib
from pathlib import Path

PUBLIC = Path(__file__).resolve().parent.parent / "public"
OUT = PUBLIC / "icons"
PAPER = (0xF3, 0xF3, 0xEF)
BLUE = (0x1F, 0x5B, 0xEF)  # stann's own blue, fixed whatever colours the sports wear
WHITE = (0xFF, 0xFF, 0xFF)

# Fredoka's "s" at weight 600 (SIL Open Font License 1.1, (c) The Fredoka Project Authors), in font units
# with y up, as fontTools' SVGPathPen writes it from the variable font's wght=600 instance. Keeping the
# outline here means the icons don't need the font.
S_PATH = (
    "M220.75 -17Q194 -17 162.25 -11.0Q130.5 -5 100.75 6.5Q71 18 50.75 33.625Q30.5 49.25 27.75 69.25"
    "Q26 79.25 28.625 90.375Q31.25 101.5 37.875 114.25Q44.5 127 55.25 141Q63.25 151.5 72.875 154.75"
    "Q82.5 158 96.25 153.75Q112 151.25 128.25 143.5Q144.5 135.75 161.25 127.125Q178 118.5 195.875 112.125"
    "Q213.75 105.75 233 105.75Q261 105.75 276.625 114.125Q292.25 122.5 292.25 137.75Q292.25 148.5 285.625 155.875"
    "Q279 163.25 267.375 168.375Q255.75 173.5 240.625 177.625Q225.5 181.75 208.125 186.125Q190.75 190.5 172.5 196.25"
    "Q148.25 203.25 124.875 214.0Q101.5 224.75 82.875 242.25Q64.25 259.75 53.0 285.25Q41.75 310.75 41.75 348.25"
    "Q41.75 396.75 64.625 430.0Q87.5 463.25 131.5 480.75Q175.5 498.25 238.75 498.25Q257.25 498.25 275.0 495.875"
    "Q292.75 493.5 310.25 488.75Q327.75 484 344.75 476.75Q361.75 469.5 378.5 460.25Q409.25 445.5 410.25 421.875"
    "Q411.25 398.25 392.75 372.5Q381.5 355.75 369.625 348.25Q357.75 340.75 345.25 343.5Q329.25 347.5 310.625 356.625"
    "Q292 365.75 271.625 373.75Q251.25 381.75 230.5 381.75Q214.25 381.75 202.125 377.5Q190 373.25 183.875 365.5"
    "Q177.75 357.75 177.75 347.5Q177.75 335 185.5 327.25Q193.25 319.5 206.125 314.5Q219 309.5 236.125 305.75"
    "Q253.25 302 272.75 298.25Q299.75 293.25 328.125 284.5Q356.5 275.75 380.125 259.875Q403.75 244 418.25 216.75"
    "Q432.75 189.5 432.75 145.75Q432.75 67 377.75 25.0Q322.75 -17 220.75 -17Z"
)
S_LEFT, S_RIGHT, S_BOTTOM, S_TOP = 27.05, 432.75, -17, 498.25  # its bounds

# The badge, as fractions of its outer radius: a disc with 14 scallops round its edge, and the s's height.
SCALLOPS, RING, SCALLOP, CORE, S_HEIGHT = 14, 0.786, 0.214, 0.81, 0.62

# On a 512 grid, "radius" being the badge's outer radius. "icon" sits on a rounded paper tile; "maskable"
# fills the square and keeps the badge well inside the 40%-radius safe zone launchers never crop;
# "monochrome" is the badge alone in white with the s cut out, for themed icons and the notification
# badge; "mark" is the badge alone in colour, edge to edge, for the header's logo. The Android app's
# adaptive icon (mobile/app.config.ts) is a layer on a clear ground that a launcher masks to a shape
# 72 of its 108dp wide, so its badge is smaller, inside Android's 66dp safe circle.
VARIANTS = {
    "icon": {"ground": 112, "radius": 215, "mono": False},
    "maskable": {"ground": 0, "radius": 176, "mono": False},
    "monochrome": {"ground": None, "radius": 176, "mono": True},
    "mark": {"ground": None, "radius": 256, "mono": False},
    "adaptive": {"ground": None, "radius": 136, "mono": False},
    "adaptive-monochrome": {"ground": None, "radius": 136, "mono": True},
}
# badge-96: the small white mark Android shows in the status bar for a notification.
PNGS = [("icon", 192), ("icon", 512), ("maskable", 192), ("maskable", 512), ("monochrome", 512), ("maskable", 180, "apple-touch-icon"), ("monochrome", 96, "badge-96"),
        ("adaptive", 432), ("adaptive-monochrome", 432), ("mark", 192)]


def circles(v: dict, scale: float = 1.0) -> list[tuple[float, float, float]]:
    """The badge as circles (x, y, radius): the core, then the scallops round it."""
    c, outer = 256 * scale, v["radius"] * scale
    ring = [(c + RING * outer * math.cos(a), c + RING * outer * math.sin(a), SCALLOP * outer) for a in (i / SCALLOPS * math.tau for i in range(SCALLOPS))]
    return [(c, c, CORE * outer), *ring]


def s_outline(v: dict, scale: float = 1.0) -> list[tuple[str, list[tuple[float, float]]]]:
    """The s as (command, points) with M, L, Q and Z, centred on the badge (times scale)."""
    k = S_HEIGHT * v["radius"] / (S_TOP - S_BOTTOM)
    left, top = 256 - (S_RIGHT - S_LEFT) * k / 2, 256 - S_HEIGHT * v["radius"] / 2

    def place(x: float, y: float) -> tuple[float, float]:
        return (left + (x - S_LEFT) * k) * scale, (top + (S_TOP - y) * k) * scale

    out, pen = [], (0.0, 0.0)
    tokens = re.findall(r"[MLHVQZ]|-?\d+(?:\.\d+)?", S_PATH)
    i = 0
    while i < len(tokens):
        cmd = tokens[i]
        i += 1
        if cmd in "ML":
            pen = (float(tokens[i]), float(tokens[i + 1]))
            i += 2
            out.append((cmd, [place(*pen)]))
        elif cmd in "HV":
            pen = (float(tokens[i]), pen[1]) if cmd == "H" else (pen[0], float(tokens[i]))
            i += 1
            out.append(("L", [place(*pen)]))
        elif cmd == "Q":
            ctrl, pen = (float(tokens[i]), float(tokens[i + 1])), (float(tokens[i + 2]), float(tokens[i + 3]))
            i += 4
            out.append(("Q", [place(*ctrl), place(*pen)]))
        elif cmd == "Z":
            out.append(("Z", []))
        else:  # S_PATH must stay absolute M, L, H, V, Q and Z, one command per segment, as SVGPathPen writes it
            raise ValueError(f"S_PATH: unexpected {cmd!r}")
    return out


def svg(v: dict) -> str:
    hexes = lambda c: "#%02X%02X%02X" % c  # noqa: E731
    rounded = f' rx="{v["ground"]}"' if v["ground"] else ""
    ground = "" if v["ground"] is None else f'<rect width="512" height="512"{rounded} fill="{hexes(PAPER)}"/>'
    d = "".join(cmd + " ".join(f"{x:.1f} {y:.1f}" for x, y in pts) for cmd, pts in s_outline(v))
    fill = "#FFFFFF" if v["mono"] else hexes(BLUE)
    badge = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{fill}"/>' for x, y, r in circles(v))
    if v["mono"]:  # the s is cut out, so it shows through a themed icon
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
            f'<mask id="m">{badge}<path d="{d}" fill="#000000"/></mask>'
            '<rect width="512" height="512" fill="#FFFFFF" mask="url(#m)"/></svg>\n'
        )
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">{ground}{badge}<path d="{d}" fill="{hexes(WHITE)}"/></svg>\n'


def coverage(signed: float) -> float:
    """How much of a pixel a shape covers, from the signed distance of its centre to the edge."""
    return min(1.0, max(0.0, 0.5 - signed))


def rounded_rect_distance(x: float, y: float, x0: float, y0: float, w: float, h: float, radius: float) -> float:
    q = [abs(x - (x0 + w / 2)) - (w / 2 - radius), abs(y - (y0 + h / 2)) - (h / 2 - radius)]
    outside = math.hypot(max(q[0], 0), max(q[1], 0))
    return outside + min(max(q[0], q[1]), 0) - radius


def fill(outline: list[tuple[str, list[tuple[float, float]]]], size: int, rows: int = 8) -> list[list[float]]:
    """Each pixel's coverage by a closed outline (non-zero winding): the curves flattened to edges, then
    `rows` scanlines a pixel, each adding the share of every pixel its spans cross."""
    edges, start, pen = [], (0.0, 0.0), (0.0, 0.0)
    for cmd, pts in outline:
        if cmd == "M":
            start = pen = pts[0]
        elif cmd == "L":
            edges.append((pen, pts[0]))
            pen = pts[0]
        elif cmd == "Q":
            (cx, cy), (ex, ey) = pts
            steps, prev = 12, pen
            for n in range(1, steps + 1):
                t = n / steps
                point = ((1 - t) ** 2 * pen[0] + 2 * (1 - t) * t * cx + t * t * ex, (1 - t) ** 2 * pen[1] + 2 * (1 - t) * t * cy + t * t * ey)
                edges.append((prev, point))
                prev = point
            pen = (ex, ey)
        else:
            edges.append((pen, start))
            pen = start
    cover = [[0.0] * size for _ in range(size)]
    for py in range(size):
        line = cover[py]
        for sub in range(rows):
            y = py + (sub + 0.5) / rows
            crossings = sorted(
                (x0 + (y - y0) * (x1 - x0) / (y1 - y0), 1 if y1 > y0 else -1)
                for (x0, y0), (x1, y1) in edges
                if (y0 <= y < y1) or (y1 <= y < y0)
            )
            winding, span = 0, 0.0
            for x, direction in crossings:
                if winding == 0:
                    span = x
                winding += direction
                if winding == 0:
                    add_span(line, span, x, 1 / rows, size)
    return [[min(1.0, c) for c in line] for line in cover]


def add_span(line: list[float], a: float, b: float, weight: float, size: int) -> None:
    a, b = max(a, 0.0), min(b, float(size))
    for px in range(int(a), min(int(math.ceil(b)), size)):
        line[px] += weight * (min(b, px + 1) - max(a, px))


def render(v: dict, size: int) -> bytes:
    s = size / 512
    badge = circles(v, s)
    letter = fill(s_outline(v, s), size)
    rows = []
    for py in range(size):
        row = bytearray([0])  # PNG filter: none
        for px in range(size):
            x, y = px + 0.5, py + 0.5
            color, alpha = (0.0, 0.0, 0.0), 0.0  # premultiplied
            layers = []
            if v["ground"] is not None:
                layers.append((PAPER, coverage(rounded_rect_distance(x, y, 0, 0, size, size, v["ground"] * s))))
            pin = coverage(min(math.hypot(x - cx, y - cy) - r for cx, cy, r in badge))  # the union of the circles
            hole = letter[py][px]
            if v["mono"]:  # the s is cut out, so it shows through a themed icon
                layers.append((WHITE, pin * (1 - hole)))
            else:
                layers += [(BLUE, pin), (WHITE, hole)]
            for rgb, a in layers:  # "over" compositing, premultiplied
                color = tuple(ch / 255 * a + cc * (1 - a) for ch, cc in zip(rgb, color))
                alpha = a + alpha * (1 - a)
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
