"""Chess: one game a round, a game turning as our engine sees it, FIDE titles, form as a score."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .base import DRAW, Ask, Fired, Found, Glance, Line, Played, Poster, Seen, Sport, outcome, stat, words

if TYPE_CHECKING:
    from ..models import Athlete

RESULTS = {"1-0": "white", "0-1": "black", "½-½": DRAW}  # a board's result: whose id answers it

TITLES = {
    "Grandmaster": "GM", "International Master": "IM", "FIDE Master": "FM", "Candidate Master": "CM",
    "Woman Grandmaster": "WGM", "Woman International Master": "WIM", "Woman FIDE Master": "WFM", "Woman Candidate Master": "WCM",
}
TITLE_ORDER = ["GM", "IM", "WGM", "FM", "WIM", "CM", "WFM", "WCM"]  # highest first

# The engine of a match page's Play from here, on the fan's own device (web/src/sports/engine/, its README): the
# commit of Stockfish.js that published its build, the board's "source" and "GPL-3.0" links, as
# web/src/sports/facts/chessEngineCore.ts has them (tests/test_credits.py holds the two to one commit).
ENGINE_BUILT_FROM = "93c994592dcf3b4b21052ab925e9b534df9c0918"
ENGINE_SOURCE = f"https://github.com/nmrugg/stockfish.js/tree/{ENGINE_BUILT_FROM}"
ENGINE_LICENCE = f"https://github.com/nmrugg/stockfish.js/blob/{ENGINE_BUILT_FROM}/Copying.txt"

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
    """An evaluation with its settled band. The band goes by each position's first look ("look", the quick
    one every position gets; engine.py) and moves only when the first looks of two positions in a row
    agree, so a broadcast glitch (a board sending a wrong move for a few seconds) isn't a swing. "since" is
    the move it last moved at and "since_look" that move's first look, so a swing is the same swing, in
    the same words, whichever poll of the board first sees it; "prev" is the band and "told" it was
    settled from, the board's own before this one, which a poll joining the board's settles holds it
    against (engine.settled).

    While someone has the game open, the engine goes on deepening the position it's on. A deeper reading of
    the same position is shown (cp, mate and depth, for the board's bar) but never moves the band, nor what
    the next position is held against ("raw"), and neither does a reading with no first look (one the
    engine had to stop: its score is the machine's). A position scores the same whether anyone watches the
    game, and alerts don't hang on how far a search had got when a poll came.

    "told" is the winning band (2 or -2) the game last moved into, kept until it's level again or the
    other side is better: an evaluation hovering on the winning line (+2.6, +3.4, +2.7…) moves the band
    back and forth, and the player is winning once, not at every climb."""
    if before and before.get("at") == ev.get("at"):
        return before
    move = _fullmove(ev.get("fen"))
    held = before or {}
    prev = {"band": held.get("band", 0), "told": held.get("told", 0)}
    if not ev.get("look") or (before is not None and ev.get("fen") is not None and before.get("fen") == ev.get("fen")):
        # Shown, and the band as the last first look settled it.
        return {**ev, "move": held.get("move", move), "raw": held.get("raw"), "band": held.get("band", 0), "told": held.get("told", 0),
                "since": held.get("since"), "since_look": held.get("since_look"), "prev": prev}
    settled = prev["band"]
    raw = band(ev["look"], settled)
    moved = before is None or raw == before.get("raw")
    new = raw if moved else settled
    told = prev["told"]
    if before is not None and abs(new) == 2 and new != settled:
        told = new
    elif new * told <= 0:
        told = 0
    turned = before is None or new != settled
    return {**ev, "move": move, "raw": raw, "band": new, "told": told, "since": move if turned else held.get("since"),
            "since_look": ev["look"] if turned else held.get("since_look"), "prev": prev}


def _fullmove(fen: str | None) -> int | None:
    fields = (fen or "").split()
    return int(fields[5]) if len(fields) > 5 and fields[5].isdigit() else None


def _score(ev: dict[str, Any], side: int) -> str:
    """An evaluation from the player's side: "+3.6", "0.0", "+12" (past ten pawns, whole ones), "mate in 4",
    "facing mate in 4". Halves round away from zero, counted in whole centipawns as the app's evaluation bar
    counts them (evalScore in web/src/sports/facts/chess.ts), so the two agree: a float's own rounding made
    -1.35 "-1.4" but 1.25 "+1.2"."""
    if ev.get("mate") is not None:
        mate = ev["mate"] * side
        return f"mate in {mate}" if mate > 0 else f"facing mate in {-mate}"
    cp = int(ev.get("cp") or 0) * side
    tenths = (abs(cp) + 5) // 10
    if not tenths:
        return "0.0"
    pawns = str((tenths + 5) // 10) if tenths >= 100 else f"{tenths // 10}.{tenths % 10}"
    return f"{'-' if cp < 0 else '+'}{pawns}"


def _points(result: str) -> tuple[float, float] | None:
    """ "Won 3½–2½" -> (3.5, 2.5): the player's points and their opponent's. A match's result is put from the player's
    side, but one game's is the board's, White's first ("Won 0–1", a win with Black: lichess_chess.result_label), so
    a game's point is its word's."""
    match = re.search(r"(\d*½?)–(\d*½?)", result or "")
    if not match:
        return None
    value = lambda s: (int(s.rstrip("½")) if s.rstrip("½") else 0) + (0.5 if s.endswith("½") else 0)  # noqa: E731
    mine, theirs = value(match.group(1)), value(match.group(2))
    if mine + theirs == 1:
        mine = {"W": 1.0, "D": 0.5, "L": 0.0}.get(outcome(result) or "", mine)
        theirs = 1 - mine
    return mine, theirs


def _sides(now: Seen) -> tuple[str, str, str]:
    """The board's players by their FIDE ids, as the player's line puts them: theirs, their opponent's, and White's
    ("" before the pairing's out)."""
    white, black = (str((now.state.get(c) or {}).get("id") or "") for c in ("white", "black"))
    colour = stat(now.line, "Colour")
    mine, theirs = (white, black) if colour == "White" else (black, white) if colour == "Black" else ("", "")
    return mine, theirs, white


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
    profile_role = ("P2962",)  # their title: "Grandmaster"
    honours = {"Q10290970": "World Chess Champion", "Q141044426": "Women's World Chess Champion", "Q752000": "Chess Oscar"}
    engine = True
    draws = True
    source_line_first = True
    colour = {"sport": "#7c3aed", "on": "#ffffff", "on_soft": "#ffffff", "ink": "#a98bff"}
    pattern = r"chess"
    alerts = {
        "key": ("Games turning and results", "A game starting, turning (winning, in trouble, a turnaround) and the result"),
        "more": ("every edge", "a game edging better or worse, and milestones"),
    }

    game_alerts = {
        "key": ("The game turning and the result", "The game starting, turning (a side winning, a turnaround) and the result"),
        "more": ("every edge", "the game edging one way or back to level"),
    }

    def credits_also(self) -> list[dict[str, Any]]:
        return [{
            "name": "Stockfish.js",
            "url": ENGINE_SOURCE,
            "what": "The chess engine of a game's Play from here, on the fan's own phone or browser: Stockfish 18, its lite single-threaded WebAssembly build by Nathan Rugg and Chess.com. The link is its source, the commit that built it.",
            "licence": "GPL-3.0",
            "licence_url": ENGINE_LICENCE,
        }]

    def title_of(self, label: str) -> str | None:
        return TITLES.get(label)

    def better_title(self, a: str, b: str) -> bool:
        """A player holds every title they've earned; the highest is shown."""
        return TITLE_ORDER.index(a) < TITLE_ORDER.index(b)

    def search_detail(self, athlete: Athlete) -> list[str]:
        return [athlete.title or "", athlete.country or ""]

    def starts_unlisted(self, now: Seen) -> bool:
        return True

    # A board is a fixture of each of its players, so each of its moments is found twice, once from each side; a fan
    # who follows both hears it from one (Found.voice): a swing from the side it went toward, the result from the
    # winner's, the start and a draw from White's.

    def start(self, who: str, now: Seen) -> Found:
        rnd = now.state.get("round") or "Their game"
        opp = stat(now.line, "Opponent")
        return Found("start", "key", f"{who} is playing", f"{rnd}{f' · vs {opp}' if opp else ''}", voice=_sides(now)[2])

    def final(self, who: str, now: Seen) -> Found:
        head = (now.line or {}).get("headline") or ""
        mine, theirs, white = _sides(now)
        voice = mine if head.startswith("Won") else theirs if head.startswith("Lost") else white
        return Found("final", "key", f"{who}: {head or 'result'}", now.score, voice=voice)

    def settle(self, before: dict[str, Any] | None, ev: dict[str, Any]) -> dict[str, Any]:
        return settle(before, ev)

    def during(self, who: str, athlete_id: str, before: Seen, now: Seen) -> list[Found]:
        """A followed player's game turning, as the engine sees it (from their side)."""
        was, got = before.state.get("eval"), now.state.get("eval")
        if now.status != "live" or now.state.get("result") or not was or not got or before.state.get("game_id") != now.state.get("game_id"):
            return []  # a decided game (a mini-match is live after it) turns no more
        if got.get("joined"):
            # Its first evaluation from the board's settles (engine.settled): held against the board's band before it,
            # not its own last one, which may be stale (a deploy, a lost key), so only a real turn is told.
            was = {**was, **(got.get("prev") or {"band": got["band"], "told": got.get("told", 0)})}
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
        # The move the band moved at (settle's "since"), not the one the poll came at: a look can land between the
        # two sides' polls, and each side's word for the swing is still the same play.
        move = got.get("since") or got.get("move")
        score = got.get("since_look") or got.get("look") or got  # that move's first look
        body = " · ".join(x for x in (f"Engine {_score(score, side)}", f"move {move}" if move else None, f"vs {opponent}" if opponent else None) if x)
        mine, theirs, _ = _sides(now)
        # Its game, its band from White's side and its move: the same from either side of the board, so each side's
        # word for a swing is told as one play (alerts.voiced_by), and one game of a mini-match isn't taken for another.
        return [Found("swing", level, title, body, f"{self.board(now.state)}w{got['band']}m{move or 0}", 85 if turnaround else 70 if level == "key" else 30,
                      voice=mine if new > old else theirs)]  # the side it went toward: level again, the one that came back

    def game_during(self, name: str, before: Played, now: Played, told: list[tuple[str, str]]) -> list[Found]:
        """The board turning, as the engine sees it, told once, in during's words from the side it moved
        toward ("Gukesh D is winning", "Turnaround: Carlsen is winning"), or, level again, from the side that
        came back ("Carlsen is back level"); about both players. A fan of either hears it as their player's."""
        was, got = before.state.get("eval"), now.state.get("eval")
        if not was or not got or was.get("band") == got.get("band"):
            return []
        old, new = was["band"], got["band"]
        sides = [now.state.get("white") or {}, now.state.get("black") or {}]
        if not all(p.get("name") for p in sides):
            return []
        # The side it moved toward; level again, the side that came back.
        towards = 0 if (new > 0 or (new == 0 and old < 0)) else 1
        me, other = sides[towards], sides[1 - towards]
        line = {"headline": "", "stats": [{"label": "Colour", "value": ("White", "Black")[towards]}, {"label": "Opponent", "value": other["name"]}]}
        found = self.during(me["name"], str(me.get("id") or ""), Seen(before.status, before.state, line), Seen(now.status, now.state, line))
        for f in found:
            f.title, f.about = f"{name}: {f.title}", tuple(str(p.get("id") or "") for p in sides)
        return found

    def match_page(self, state: dict[str, Any], events: list[dict[str, Any]], lines: dict[str, Line], names: dict[str, tuple[str, str]]) -> dict[str, Any]:
        """The board says the game; beside it, the moves that led there, a move number's pair to a line ("1. d4
        Nf6", "1... e5" where Black moved first). Once it's over, from its source's read of the finished game
        (state["moves"]: [n, White's, Black's] each), with its opening and how it ended; of a mini-match, the
        last game's, the one on the board (game: its number). While it's on, the moves so far as its source's
        reads keep them (state["page_detail"], under whichever board is newest, but only the same game's: a
        mini-match's next one is another), with its opening, and those added since as the board gives its last
        move ("d5e3": more), which the app writes as moves; a game kept from its first move has those alone."""
        told = {} if state.get("moves") else state.get("page_detail") or {}
        if told.get("game_id") != state.get("game_id"):
            told = {}
        pairs = []
        for row in state.get("moves") or told.get("moves") or []:
            if isinstance(row, list) and len(row) == 3 and (row[1] or row[2]):
                n, white, black = row
                pairs.append(f"{n}. {white} {black or ''}".rstrip() if white else f"{n}... {black}")
        more = [m for m in told.get("more") or [] if isinstance(m, str)]
        if not pairs and not more:
            return {}
        match = state.get("match") or {}
        if told:
            game = match["games"] + (0 if state.get("result") else 1) if isinstance(match.get("games"), int) else None  # the one being played
            return {"moves": pairs, "more": more, "opening": told.get("opening"), "ending": None, "game": game}
        return {"moves": pairs, "opening": state.get("opening"), "ending": state.get("ending"), "game": match.get("games")}

    def glance(self, state: dict[str, Any], score: str, clock: str, status: str | None, events: list[dict[str, Any]]) -> Glance:
        """White and Black by name, each with their points: of a mini-match so far (state["match"]: its score is
        from the side its score line names first), else of the game once it has a result ("1-0", "½-½"). Where it's
        at: the move, or the game's result; of a mini-match, its game and move ("Game 2 · move 34"), "Final" once
        it's decided."""
        white, black = state.get("white") or {}, state.get("black") or {}
        if not (white.get("name") and black.get("name")):
            return Glance([], clock or "")
        match, result, move = state.get("match") or {}, state.get("result") or "", state.get("move")
        points = ["", ""]
        if match.get("score") and "–" in match["score"]:
            first, _, second = match["score"].partition("–")
            points = [second, first] if score.startswith(black["name"]) and not score.startswith(white["name"]) else [first, second]
        elif "-" in result:
            points = result.split("-", 1)
        if match:
            where = "Final" if status == "final" else f"Game {(match.get('games') or 0) + (0 if result else 1)}" + (f" · move {move}" if move and not result else "")
        else:
            where = result.replace("-", "–") if result else f"Move {move}" if move else clock or ""
        return Glance([(white["name"], points[0]), (black["name"], points[1])], where)

    def verdict(self, state: dict[str, Any], score: str, clock: str) -> str:
        return clock  # its source's words for a finished board: "Magnus Carlsen won", "Draw", "Match drawn (4 games)"

    def high(self, f: Found, versus: str) -> Poster | None:
        """A win, as the result's line has it: "Won with Black vs Firouzja", "Won the match 2–1 vs So"."""
        return _won(f.title)

    def finished_poster(self, line: Line, result: str, versus: str) -> Poster | None:
        """A win, off a finished game's line, as high() words its result's moment (whose title is the line)."""
        return _won(line.get("headline") or "", 80)  # a result's priority (moments.PRIORITY)

    # ---- predictions (predictions.py): the game's result, called before the first move

    predicts = True

    def board(self, state: dict[str, Any]) -> str:
        """The game's id at its source: each player's fixture shows the same board, and asks one question."""
        return str(state.get("game_id") or "")

    def board_players(self, state: dict[str, Any]) -> tuple[str, ...]:
        """White and Black by their FIDE ids, the same all through a mini-match's games."""
        return tuple(str(p["id"]) for p in (state.get("white") or {}, state.get("black") or {}) if p.get("id"))

    def pairing(self, state: dict[str, Any]) -> str:
        """Its tournament's and round's names and its two players: a round is one meeting, its mini-match's games
        and all."""
        players, tournament, rnd = sorted(self.board_players(state)), state.get("tournament"), state.get("round")
        return "|".join([str(tournament), str(rnd), *players]) if len(players) == 2 and tournament and rnd else ""

    def question_board(self, state: dict[str, Any]) -> str:
        """A mini-match's questions are its first game's board's: the round showed that game alone when they were
        asked, before anyone could know more would follow (asks), and its next games, each a board of its own, find
        them there."""
        return str((state.get("match") or {}).get("first") or "") or self.board(state)

    def asks(self, state: dict[str, Any]) -> list[Ask]:
        """Who wins, or a draw, once the round's pairing is out: the two players by their FIDE ids, White first. A
        knockout round's mini-match (several games on one board) asks nothing more: each of its games shows only
        once the one before is over, too late to call. The question its first game was asked, shown alone before
        the start, is the match's (question_board, answer)."""
        white, black = state.get("white") or {}, state.get("black") or {}
        if state.get("match") or not (state.get("game_id") and white.get("id") and black.get("id") and white.get("name") and black.get("name")):
            return []
        return [Ask("result", "Who wins?", ((str(white["id"]), white["name"]), (DRAW, "Draw"), (str(black["id"]), black["name"])))]

    def closed(self, ask: str, status: str | None, state: dict[str, Any]) -> bool:
        return status in ("live", "final") or bool(state.get("result"))

    def answer(self, ask: str, status: str | None, state: dict[str, Any]) -> str | None:
        """The winner's FIDE id, or a draw, once the board's over: of a mini-match, who's ahead in it (state["match"]);
        of one game, its result, where an armageddon drawn is Black's win. Its source keeps a game live while its
        pairing's next may follow it in the round, so no game of a tiebreak answers for its match."""
        if status != "final":
            return None
        match = state.get("match")
        if match:
            if "ahead" not in match:
                return None  # a board read before it said who's ahead: the next read says
            return str(match["ahead"]) if match["ahead"] else DRAW
        side = RESULTS.get(state.get("result") or "")
        if side is None:
            return None
        if side == DRAW and state.get("armageddon"):
            side = "black"
        return DRAW if side == DRAW else str((state.get(side) or {}).get("id") or "") or None

    def form(self, items: list[dict[str, Any]], lines: list[Line]) -> list[dict[str, str]]:
        scored = [p for p in (_points(r.get("result", "")) for r in items) if p]
        if not scored:
            return []
        mine, games = sum(p for p, _ in scored), sum(p + q for p, q in scored)
        fmt = lambda x: (str(int(x)) if x == int(x) else f"{int(x)}½" if x > 1 else "½")  # noqa: E731
        return [{"label": "Score", "value": f"{fmt(mine)}/{fmt(games)}"}, {"label": "Score %", "value": f"{100 * mine / games:.0f}%"}]

    # ---- the Feed's content judge (curate.py)

    restream_after = None  # analysis and players' own streams run for hours: the judge's model tells re-uploads

    def content_hints(self) -> str:
        return """\
A milestone: a tournament win, a title or norm (GM, IM), a rating landmark (2700, 2800), a World Championship or \
Candidates result, beating a much higher-rated player. A match moment: a brilliant sacrifice, a blunder, a \
time-scramble finish. Long videos are normal here: game analysis and a player's own streams run for an hour or \
more and are interview_or_analysis, not restreams; a copied official broadcast is a restream. Cheating claims \
without a ruling from FIDE or the platform are misinformation."""

    # ---- the feed (curator.py): the only channels its players' videos come from (each feed answered, 21 Oct 2026)
    #
    # Official ones (leagues, federations, clubs, broadcasters, tournaments) and a few top creators: every video,
    # whichever stream found it (a channel's poll, a moment's search, a sweep), is kept only from these (candidates.keep).

    video_channels = (
        "UC9B47GnzCRFHTT1BIBWvStQ",  # FIDE
        "UC5kS0l76kC0xOzMPtOmSFGw",  # Chess.com
        "UCIsEhwBMPkRHsEgqYAPQHsA",  # ChessBase India
        "UCM-ONC2bCHytG2mYtKDmIeA",  # Saint Louis Chess Club
        "UCX_tAmRRY4yniFKRMZ4h1hg",  # Norway Chess
        "UCENNtCRTPTdH_IGXs42LuHQ",  # Esports World Cup: its chess
        "UCQHX6ViZmPsWiYSFAyS0a3Q",  # GothamChess: game analysis (8M subscribers)
        "UCweCc7bSMX5J4jEH7HFImng",  # GMHikaru: his games and others' (3M subscribers)
        "UCL5YbN5WLFD8dLIegT5QAbA",  # agadmator's Chess Channel: game analysis (1.3M subscribers)
    )
    title_words = (  # kept as written in a title put in sentence case (titles.py)
        "FIDE", "GM", "IM", "WGM", "FM", "Chess.com", "Norway Chess", "Tata Steel",
        "Olympiad", "Grand Swiss", "Freestyle", "Hikaru", "Magnus", "India", "Norway")
    news_feeds = {
        "https://www.chess.com/rss/news": "Chess.com",
        "https://en.chessbase.com/feed": "ChessBase",
        "https://www.fide.com/feed": "FIDE",
        "https://www.theguardian.com/sport/chess/rss": "The Guardian",
    }

    def video_words(self) -> str:
        return "chess"

    def search_words(self, fired: Fired) -> str | None:
        """A win, in its game ("wins game 4"): its fixture's title names the round."""
        if fired.kind != "final" or ": won" not in fired.title.lower():
            return None
        return words("wins", fired.game or fired.competition)


SPORT = Chess()
