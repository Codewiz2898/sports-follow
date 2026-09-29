"""Draw the app icon: the brand's orange "live" dot with a faint ring, on the app's dark ground.

Writes public/icons/*.svg and the PNG sizes Android, Chrome and iOS ask for, from one geometry, with
no image library (a tiny anti-aliased rasterizer and PNG writer). Run from web/:

    python3 scripts/make_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "public" / "icons"
GROUND = (0x0B, 0x0E, 0x13)
LIVE = (0xFF, 0x6B, 0x3D)
WHITE = (0xFF, 0xFF, 0xFF)

# On a 512 grid. "any" is a rounded tile; "maskable" fills the square and keeps the mark inside the
# 40%-radius safe zone launchers never crop; "monochrome" is the mark alone, for themed icons.
VARIANTS = {
    "icon": {"tile": 112, "ring": (164, 20, 0.35), "dot": 104, "ink": LIVE},
    "maskable": {"tile": 0, "ring": (136, 16, 0.35), "dot": 86, "ink": LIVE},
    "monochrome": {"tile": None, "ring": (136, 16, 0.5), "dot": 86, "ink": WHITE},
}
PNGS = [("icon", 192), ("icon", 512), ("maskable", 192), ("maskable", 512), ("monochrome", 512), ("maskable", 180, "apple-touch-icon")]


def svg(v: dict) -> str:
    hexes = lambda c: "#%02X%02X%02X" % c  # noqa: E731
    r, w, a = v["ring"]
    tile = "" if v["tile"] is None else f'<rect width="512" height="512"{f" rx=\"{v["tile"]}\"" if v["tile"] else ""} fill="{hexes(GROUND)}"/>'
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        f'{tile}<circle cx="256" cy="256" r="{r}" fill="none" stroke="{hexes(v["ink"])}" stroke-width="{w}" opacity="{a}"/>'
        f'<circle cx="256" cy="256" r="{v["dot"]}" fill="{hexes(v["ink"])}"/></svg>\n'
    )


def coverage(signed: float) -> float:
    """How much of a pixel a shape covers, from the signed distance of its centre to the edge."""
    return min(1.0, max(0.0, 0.5 - signed))


def rounded_rect_distance(x: float, y: float, size: float, radius: float) -> float:
    q = [abs(x - size / 2) - (size / 2 - radius), abs(y - size / 2) - (size / 2 - radius)]
    outside = math.hypot(max(q[0], 0), max(q[1], 0))
    return outside + min(max(q[0], q[1]), 0) - radius


def render(v: dict, size: int) -> bytes:
    s = size / 512
    ring_r, ring_w, ring_a = v["ring"][0] * s, v["ring"][1] * s, v["ring"][2]
    dot_r, c = v["dot"] * s, size / 2
    rows = []
    for py in range(size):
        row = bytearray([0])  # PNG filter: none
        for px in range(size):
            x, y = px + 0.5, py + 0.5
            color, alpha = (0.0, 0.0, 0.0), 0.0  # premultiplied
            layers = []
            if v["tile"] is not None:
                layers.append((GROUND, coverage(rounded_rect_distance(x, y, size, v["tile"] * s))))
            d = math.hypot(x - c, y - c)
            layers.append((v["ink"], ring_a * coverage(abs(d - ring_r) - ring_w / 2)))
            layers.append((v["ink"], coverage(d - dot_r)))
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
    for name, size, *alias in PNGS:
        path = OUT / f"{alias[0] if alias else f'{name}-{size}'}.png"
        path.write_bytes(render(VARIANTS[name], size))
        print(path.name, path.stat().st_size, "bytes")
