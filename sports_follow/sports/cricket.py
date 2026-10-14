"""Cricket, as every cricket source shares it: a line's innings, and form over a run of matches."""

from __future__ import annotations

import re
from typing import Any


def _int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


_BAT = re.compile(r"(\d+)(\*?) \((\d+)\)")
_BOWL = re.compile(r"(\d+)/(\d+) \(([\d.]+) ov\)")


def innings(headline: str | None) -> tuple[list[tuple[int, bool, int]], list[tuple[int, int, str]]]:
    """Every innings in a cricket line's headline ("103 (152) & 48* (60) · 3/45 (20 ov)"): batting as
    (runs, out, balls), bowling as (wickets, runs conceded, overs)."""
    bats, bowls = [], []
    for part in re.split(r" & | · ", headline or ""):
        if m := _BAT.fullmatch(part):
            bats.append((int(m[1]), not m[2], int(m[3])))
        elif m := _BOWL.fullmatch(part):
            bowls.append((int(m[1]), int(m[2]), m[3]))
    return bats, bowls


def batting_bowling(lines: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Runs, average, strike rate, highest, 100s/50s, wickets and economy over some matches' lines
    ({"headline", "stats"}), every innings counted: a line's stats are only its latest innings, so a
    Test's two come from the headline."""
    runs = balls = outs = batted = hundreds = fifties = 0
    best = None
    wickets = conceded = 0
    balls_bowled = 0
    for line in lines:
        bats, bowls = innings(line.get("headline"))
        values = {s["label"]: s["value"] for s in line.get("stats") or []}
        if not bats and "Runs" in values:
            bats = [(_int(values["Runs"].rstrip("*")), not values["Runs"].endswith("*"), _int(values.get("Balls")))]
        if not bowls and "Wickets" in values:
            bowls = [(_int(values["Wickets"]), _int(values.get("Runs conceded")), str(values.get("Overs", "0")))]
        for r, out, b in bats:
            batted += 1
            runs += r
            balls += b
            outs += out
            hundreds += r >= 100
            fifties += 50 <= r < 100
            best = max(best or 0, r)
        for w, c, overs in bowls:
            wickets += w
            conceded += c
            whole, _, part = overs.partition(".")
            balls_bowled += _int(whole) * 6 + _int(part or 0)
    stats = []
    if batted:
        stats += [
            {"label": "Runs", "value": str(runs)},
            {"label": "Average", "value": f"{runs / outs:.2f}" if outs else "–"},
            {"label": "Strike rate", "value": f"{runs * 100 / balls:.1f}" if balls else "–"},
            {"label": "Highest", "value": str(best)},
            {"label": "100s / 50s", "value": f"{hundreds} / {fifties}"},
        ]
    if balls_bowled:
        stats += [
            {"label": "Wickets", "value": str(wickets)},
            {"label": "Economy", "value": f"{conceded * 6 / balls_bowled:.2f}"},
        ]
    return stats
