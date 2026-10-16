"""Make the app's display face: Anton, leaning forward 7 degrees as the site's .dsp does (styles.css).

The site leans its display type with a CSS skew (`transform: skewX(-7deg)`), which Android can't do to text:
it draws a skewed Text upright. So the lean is put into the letters instead. This writes a copy of Anton
whose outlines (and accent anchors) are skewed the same 7 degrees about the same line the site's skew turns
on: the middle of a .dsp box, (ascent + descent) / 2 above the baseline.

A leaning letter reaches past its own width (its top to the right, its foot to the left), and Android lays a
line out by the ink: React Native sizes a Text to the letters' widths, then Android puts a last word that
pokes out of that box on a line of its own, which the box hides. So each letter's width is grown to hold
its ink with a pixel to spare, and a kerning lookup takes the growth back between two letters:
inside a word the letters sit as
Anton sets them, as on the site, while a word's first and last letters (next to a space, or at either end)
keep the room their ink needs.

Anton is under the SIL Open Font License 1.1 with no reserved name; the copy keeps its copyright and licence
(assets/fonts/AntonSlanted.LICENSE) and is named AntonSlanted, the family the app asks for (theme.ts) and,
on Android, its file name. Run from mobile/ after `npm install`:

    uv run --no-project --with fonttools python scripts/slant_font.py
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

from fontTools.otlLib.builder import buildLookup, buildPairPosClassesSubtable, buildValue
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables import otTables

MOBILE = Path(__file__).resolve().parent.parent
SOURCE = MOBILE / "node_modules/@expo-google-fonts/anton/400Regular/Anton_400Regular.ttf"
LICENCE = MOBILE / "node_modules/@expo-google-fonts/anton/LICENSE_FONT"
OUT = MOBILE / "assets/fonts"
NAME = "AntonSlanted"
ANGLE = 7  # degrees, as .dsp's skewX(-7deg)
STEP = 8  # font units: a letter's growth is rounded up to this, so the kerning needs few classes
MARGIN = 48  # font units (0.023 em) of room inside a letter's width: a pixel at the smallest .dsp, 19 dp
HINTING = ("fpgm", "prep", "cvt ", "hdmx", "LTSH", "VDMX")  # written for upright outlines, so dropped


def main() -> None:
    font = TTFont(SOURCE)
    slope = math.tan(math.radians(ANGLE))
    hhea = font["hhea"]
    pivot = (hhea.ascent + hhea.descent) / 2  # the middle of a .dsp box, above the baseline

    glyphs = font.getGlyphSet()
    glyf = font["glyf"]
    hmtx = font["hmtx"]
    order = font.getGlyphOrder()
    leaned = {}
    for name in order:
        recorded = DecomposingRecordingPen(glyphs)  # components leaned once, as outlines
        glyphs[name].draw(recorded)
        pen = TTGlyphPen(None)
        recorded.replay(TransformPen(pen, (1, 0, slope, 1, -slope * pivot, 0)))  # x + slope * (y - pivot)
        leaned[name] = pen.glyph()

    # How much each letter grows on each side so its leaning ink stays MARGIN inside its width (Android rounds
    # a glyph's box out to whole pixels when it fits a line, so ink at the very edge doesn't fit), rounded up
    # to STEP. Marks (no width of their own) and blank glyphs (spaces) keep their widths.
    def up(units: float) -> int:
        return math.ceil(max(0.0, units) / STEP) * STEP

    left: dict[str, int] = {}
    right: dict[str, int] = {}
    for name in order:
        glyph = leaned[name]
        glyph.recalcBounds(glyf)
        advance, _ = hmtx[name]
        if advance and glyph.numberOfContours:
            left[name], right[name] = up(MARGIN - glyph.xMin), up(glyph.xMax - (advance - MARGIN))

    shift: dict[str, int] = {}
    for name in order:
        glyph = leaned[name]
        advance, _ = hmtx[name]
        grow_left, grow_right = left.get(name, 0), right.get(name, 0)
        if grow_left:
            glyph.coordinates.translate((grow_left, 0))
        shift[name] = grow_left
        glyf[name] = glyph
        glyph.recalcBounds(glyf)
        hmtx[name] = (advance + grow_left + grow_right, glyph.xMin if glyph.numberOfContours else 0)

    # A space isn't kerned (a line may break there), so the letters either side of it keep their growth;
    # the space gives back what a word's end and start grow on average, so words sit as far apart as upright.
    cmap = font.getBestCmap()
    sample = [cmap[ord(c)] for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" if ord(c) in cmap]
    tighten = round((sum(right.get(g, 0) for g in sample) + sum(left.get(g, 0) for g in sample)) / len(sample))
    for code in (0x20, 0xA0):  # space, no-break space
        if code in cmap:
            advance, bearing = hmtx[cmap[code]]
            hmtx[cmap[code]] = (advance - tighten, bearing)

    lean_anchors(font, slope, pivot, shift)
    kern_back(font, left, right)

    for tag in HINTING:
        if tag in font:
            del font[tag]
    font["post"].italicAngle = -float(ANGLE)
    hhea.caretSlopeRise = 1000
    hhea.caretSlopeRun = round(1000 * slope)

    names = font["name"]
    for record_id in (16, 17):
        names.removeNames(nameID=record_id)
    for record_id, value in {
        1: NAME,
        3: f"{NAME}-Regular",
        4: NAME,
        6: f"{NAME}-Regular",
        10: f"Anton, leaning forward {ANGLE} degrees for stann's display type (mobile/scripts/slant_font.py).",
    }.items():
        names.setName(value, record_id, 3, 1, 0x409)
        names.setName(value, record_id, 1, 0, 0)

    OUT.mkdir(parents=True, exist_ok=True)
    font.save(OUT / f"{NAME}.ttf")
    shutil.copyfile(LICENCE, OUT / f"{NAME}.LICENSE")
    print(f"wrote {OUT / f'{NAME}.ttf'}: {len(leaned)} glyphs, {ANGLE} degrees about {pivot:g} units")


def lean_anchors(font: TTFont, slope: float, pivot: float, shift: dict[str, int]) -> None:
    """Move the points accents attach to (GPOS mark, mkmk) with their glyphs: leaned, and shifted as the glyph."""

    def moved(anchor: otTables.Anchor, glyph: str) -> otTables.Anchor:
        new = otTables.Anchor()
        new.Format = 1
        new.XCoordinate = round(anchor.XCoordinate + slope * (anchor.YCoordinate - pivot) + shift.get(glyph, 0))
        new.YCoordinate = anchor.YCoordinate
        return new

    for lookup in font["GPOS"].table.LookupList.Lookup:
        for sub in lookup.SubTable:
            if lookup.LookupType == 4:  # mark to base
                for glyph, record in zip(sub.MarkCoverage.glyphs, sub.MarkArray.MarkRecord, strict=True):
                    record.MarkAnchor = moved(record.MarkAnchor, glyph)
                for glyph, record in zip(sub.BaseCoverage.glyphs, sub.BaseArray.BaseRecord, strict=True):
                    record.BaseAnchor = [a and moved(a, glyph) for a in record.BaseAnchor]
            elif lookup.LookupType == 5:  # mark to ligature
                for glyph, record in zip(sub.MarkCoverage.glyphs, sub.MarkArray.MarkRecord, strict=True):
                    record.MarkAnchor = moved(record.MarkAnchor, glyph)
                for glyph, attach in zip(sub.LigatureCoverage.glyphs, sub.LigatureArray.LigatureAttach, strict=True):
                    for component in attach.ComponentRecord:
                        component.LigatureAnchor = [a and moved(a, glyph) for a in component.LigatureAnchor]
            elif lookup.LookupType == 6:  # mark to mark
                for glyph, record in zip(sub.Mark1Coverage.glyphs, sub.Mark1Array.MarkRecord, strict=True):
                    record.MarkAnchor = moved(record.MarkAnchor, glyph)
                for glyph, record in zip(sub.Mark2Coverage.glyphs, sub.Mark2Array.Mark2Record, strict=True):
                    record.Mark2Anchor = [a and moved(a, glyph) for a in record.Mark2Anchor]


def kern_back(font: TTFont, left: dict[str, int], right: dict[str, int]) -> None:
    """Between two letters, take back what each grew on the side that faces the other, so a word is set as
    Anton sets it; a space isn't kerned, so a word's ends keep their room. Added to the font's 'kern' feature,
    after its own kerning."""
    by_right: dict[int, list[str]] = {}
    by_left: dict[int, list[str]] = {}
    for name in sorted(right):
        by_right.setdefault(right[name], []).append(name)
    for name in sorted(left):
        by_left.setdefault(left[name], []).append(name)
    pairs = {}
    for grow_right, firsts in by_right.items():
        for grow_left, seconds in by_left.items():
            if grow_right + grow_left:
                pairs[(tuple(firsts), tuple(seconds))] = (buildValue({"XAdvance": -(grow_right + grow_left)}), None)
    glyph_map = font.getReverseGlyphMap()
    lookup = buildLookup([buildPairPosClassesSubtable(pairs, glyph_map)])
    gpos = font["GPOS"].table
    gpos.LookupList.Lookup.append(lookup)
    gpos.LookupList.LookupCount = len(gpos.LookupList.Lookup)
    index = gpos.LookupList.LookupCount - 1
    for record in gpos.FeatureList.FeatureRecord:
        if record.FeatureTag == "kern":
            record.Feature.LookupListIndex.append(index)
            record.Feature.LookupCount = len(record.Feature.LookupListIndex)


if __name__ == "__main__":
    main()
