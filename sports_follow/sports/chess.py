"""Chess: one game a round, a game turning as our engine sees it, FIDE titles, form as a score."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .base import Found, Line, Poster, Seen, Sport, stat

if TYPE_CHECKING:
    from ..models import Athlete

TITLES = {
    "Grandmaster": "GM", "International Master": "IM", "FIDE Master": "FM", "Candidate Master": "CM",
    "Woman Grandmaster": "WGM", "Woman International Master": "WIM", "Woman FIDE Master": "WFM", "Woman Candidate Master": "WCM",
}
TITLE_ORDER = ["GM", "IM", "WGM", "FM", "WIM", "CM", "WFM", "WCM"]  # highest first

# An engine evaluation (engine.py) falls in a band, from White's side: 2 winning, 1 better, 0 level,
# -1 worse, -2 losing. +3.0 and +1.4 are 75% and 63% winning chances in Lichess's model; a forced
# mate is winning. Leaving a band takes MARGIN more, so a score sitting on a line doesn't flap.
WINNING, BETTER, MARGIN = 300, 140, 30


def _level(cp: int) -> int:
    return 2 if cp >= WINNING else 1 if cp >= BETTER else -2 if cp <= -WINNING else -1 if cp <= -BETTER else 0


def band(ev: dict[str, Any], current: int = 0) -> int:
    if ev.get("mate") is not None:
        return 2 if ev["mate"] > 0 else -2
    cp = ev.get("cp") or 0
    new = _level(cp)
    if new > current:
        return max(current, _level(cp - MARGIN))
    if new < current:
        return min(current, _level(cp + MARGIN))
    return new


def settle(before: dict[str, Any] | None, ev: dict[str, Any]) -> dict[str, Any]:
    """An evaluation with its settled band. The band moves only when two evaluations in a row agree,
    so a broadcast glitch (a board sending a wrong move for a few seconds) isn't a swing.

    "told" is the winning band (2 or -2) the game last moved into, kept until it's level again or the
    other side is better: an evaluation hovering on the winning line (+2.6, +3.4, +2.7…) moves the band
    back and forth, and the player is winning once, not at every climb."""
    if before and before.get("at") == ev.get("at"):
        return before
    settled = before.get("band", 0) if before else 0
    raw = band(ev, settled)
    moved = before is None or raw == before.get("raw")
    new = raw if moved else settled
    told = before.get("told", 0) if before else 0
    if before is not None and abs(new) == 2 and new != settled:
        told = new
    elif new * told <= 0:
        told = 0
    return {**ev, "move": _fullmove(ev.get("fen")), "raw": raw, "band": new, "told": told}


def _fullmove(fen: str | None) -> int | None:
    fields = (fen or "").split()
    return int(fields[5]) if len(fields) > 5 and fields[5].isdigit() else None


def _score(ev: dict[str, Any], side: int) -> str:
    """An evaluation from the player's side: "+3.6", "mate in 4", "facing mate in 4"."""
    if ev.get("mate") is not None:
        mate = ev["mate"] * side
        return f"mate in {mate}" if mate > 0 else f"facing mate in {-mate}"
    return f"{(ev.get('cp') or 0) * side / 100:+.1f}"


def _points(result: str) -> tuple[float, float] | None:
    """ "Won 3½–2½" -> (3.5, 2.5)."""
    match = re.search(r"(\d*½?)–(\d*½?)", result or "")
    if not match:
        return None
    value = lambda s: (int(s.rstrip("½")) if s.rstrip("½") else 0) + (0.5 if s.endswith("½") else 0)  # noqa: E731
    return value(match.group(1)), value(match.group(2))


def _won(text: str, priority: int = 0) -> Poster | None:
    """A win as a poster, from a result's line at the end of `text` ("Won with Black vs Firouzja", or a
    moment's "Gukesh D: Won the match 2–1 vs So")."""
    if m := re.search(r"(?:^|: )Won with (\w+) vs (.+)$", text):
        return Poster("Win", f"with {m[1]} v {m[2]}", priority=priority)
    if m := re.search(r"(?:^|: )Won the match (\S+) vs (.+)$", text):
        return Poster("Match won", f"{m[1]} v {m[2]}", priority=priority)
    return None


class Chess(Sport):
    key = "chess"
    label = "Chess"
    kinds = ("chess",)
    wikidata = {"lichess_chess": "P1440"}
    athletes = 24_247
    wikidata_title = "P2962"
    engine = True
    draws = True
    source_line_first = True
    colour = {"sport": "#7c3aed", "on": "#ffffff", "on_soft": "#ffffff", "ink": "#a98bff"}
    pattern = r"chess"
    alerts = {
        "key": ("Games turning and results", "A game starting, turning (winning, in trouble, a turnaround) and the result"),
        "more": ("every edge", "a game edging better or worse, and milestones"),
    }

    def title_of(self, label: str) -> str | None:
        return TITLES.get(label)

    def better_title(self, a: str, b: str) -> bool:
        """A player holds every title they've earned; the highest is shown."""
        return TITLE_ORDER.index(a) < TITLE_ORDER.index(b)

    def search_detail(self, athlete: Athlete) -> list[str]:
        return [athlete.title or "", athlete.country or ""]

    def starts_unlisted(self, now: Seen) -> bool:
        return True

    def start(self, who: str, now: Seen) -> Found:
        rnd = now.state.get("round") or "Their game"
        opp = stat(now.line, "Opponent")
        return Found("start", "key", f"{who} is playing", f"{rnd}{f' · vs {opp}' if opp else ''}")

    def final(self, who: str, now: Seen) -> Found:
        head = (now.line or {}).get("headline") or ""
        return Found("final", "key", f"{who}: {head or 'result'}", now.score)

    def settle(self, before: dict[str, Any] | None, ev: dict[str, Any]) -> dict[str, Any]:
        return settle(before, ev)

    def during(self, who: str, athlete_id: str, before: Seen, now: Seen) -> list[Found]:
        """A followed player's game turning, as the engine sees it (from their side)."""
        was, got = before.state.get("eval"), now.state.get("eval")
        if now.status != "live" or not was or not got or before.state.get("game_id") != now.state.get("game_id"):
            return []
        colour = stat(now.line, "Colour")
        side = 1 if colour == "White" else -1 if colour == "Black" else 0
        old, new = was["band"] * side, got["band"] * side
        if not side or old == new:
            return []
        if abs(new) == 2 and (was.get("told") or 0) * side == new:
            return []  # told already, and the game hasn't been level or turned since: back over the line
        if new == 2:
            title, level = f"{who} is winning", "key"
        elif new == -2:
            title, level = f"{who} is in trouble", "key"
        elif new == 1 and old <= 0:
            title, level = f"{who} is better", "minor"
        elif new == -1 and old >= 0:
            title, level = f"{who} is worse", "minor"
        elif new == 0 and abs(old) == 2:
            title, level = (f"{who} is back level" if old < 0 else f"{who}'s advantage is gone"), "minor"
        else:
            return []  # drifting toward level on the same side
        turnaround = old * new < 0
        if turnaround:
            title, level = f"Turnaround: {title}", "key"
        opponent = stat(now.line, "Opponent")
        body = " · ".join(x for x in (f"Engine {_score(got, side)}", f"move {got['move']}" if got.get("move") else None, f"vs {opponent}" if opponent else None) if x)
        return [Found("swing", level, title, body, f"b{new}m{got.get('move') or 0}", 85 if turnaround else 70 if level == "key" else 30)]

    def match_page(self, state: dict[str, Any], events: list[dict[str, Any]], lines: dict[str, Line], names: dict[str, tuple[str, str]]) -> dict[str, Any]:
        """The board says the game; once it's over, the moves that led there, in pairs ("1. d4 Nf6"), its
        opening and how it ended, where its source's read of the finished game brought them. Of a mini-match,
        the last game's, the one on the board (game: its number)."""
        moves = [str(m) for m in state.get("moves") or []]
        if not moves:
            return {}
        pairs = [f"{n // 2 + 1}. {' '.join(moves[n:n + 2])}" for n in range(0, len(moves), 2)]
        return {"moves": pairs, "opening": state.get("opening"), "ending": state.get("ending"), "game": (state.get("match") or {}).get("games")}

    def verdict(self, state: dict[str, Any], score: str, clock: str) -> str:
        return clock  # its source's words for a finished board: "Magnus Carlsen won", "Draw", "Match drawn (4 games)"

    def high(self, f: Found, versus: str) -> Poster | None:
        """A win, as the result's line has it: "Won with Black vs Firouzja", "Won the match 2–1 vs So"."""
        return _won(f.title)

    def finished_poster(self, line: Line, result: str, versus: str) -> Poster | None:
        """A win, off a finished game's line, as high() words its result's moment (whose title is the line)."""
        return _won(line.get("headline") or "", 80)  # a result's priority (moments.PRIORITY)

    def form(self, items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
        scored = [p for p in (_points(r.get("result", "")) for r in items) if p]
        if not scored:
            return []
        mine, games = sum(p for p, _ in scored), sum(p + q for p, q in scored)
        fmt = lambda x: (str(int(x)) if x == int(x) else f"{int(x)}½" if x > 1 else "½")  # noqa: E731
        return [{"label": "Score", "value": f"{fmt(mine)}/{fmt(games)}"}, {"label": "Score %", "value": f"{100 * mine / games:.0f}%"}]


SPORT = Chess()
