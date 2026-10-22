"""Chess from Lichess: FIDE players, over-the-board broadcasts, live boards, FIDE ratings.

Lichess relays most elite and many open tournaments as broadcasts, through a public, documented
API (https://lichess.org/api). Players are identified by FIDE id. A player's tournaments are
found with broadcast search on their surname plus the broadcasts running now; a "fixture" is one
game of one player's in a round of a tournament ("<round id>-<FIDE id>", the round itself before
it's paired), and its snapshot is that game's board. A knockout's round holds a mini-match, several
games between the same two players: each is a fixture of its own, game 2 on ("<round id>-<FIDE
id>-2"), and the one that may follow is armed, unlisted, till it starts or its wait is over.

Lichess asks API clients to make one request at a time and to back off after a 429, so requests
here are serialized. Its limits differ by endpoint, and so do the budgets here: broadcast reads
spend the token's allowance, and a 429 pauses them for Lichess's three-minute window; its FIDE
player pages share one limit with every caller from a datacenter, so they have a budget of their
own, a 429 there pauses only them, and FIDE records are read through FIDE search, which has none.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import httpx

from .. import bus, naming
from ..config import USER_AGENT
from . import budget, cache
from .base import Adapter, AdapterError, Fixture, Line, LiveGame, LiveSearch, PlayerRef, Provider, SearchResult, Snapshot

log = logging.getLogger(__name__)

# With the slash: the budgets below match requests by this prefix, so every address is API + path.
API = "https://lichess.org/api/"
DAYS_BACK = 60
DAYS_AHEAD = 21
MAX_TOURS = 6  # the most recent tournaments a player is followed through
PAGE_TTL = 6 * 3600  # a player's Lichess FIDE page: their tournaments change a few times a month
STALE_PAGE = 7 * 24 * 3600  # what the last copy read listed, kept in Redis while Lichess refuses a new one
LIVE_MOVES_KEEP = 6 * 3600  # a live board's moves so far, kept in Redis this long after the last move added
# A player's games over in a round, where their pairing's next game may follow (may_follow): the next game's chapter
# shows only as it starts, a break of ten or twenty minutes in a tiebreak or before an armageddon, so its fixture
# (next_game) waits this long after the last one ended for it before it's off, whatever Lichess says of the round: it
# calls a round finished as soon as every game in it has a result, and on again as the next game's first moves come.
GRACE = 30 * 60
OVER_KEEP = 24 * 3600  # when such a game was first seen over, kept in Redis this long
# A tournament's rounds and its PGN as a refresh reads them (fixtures), a copy up to this old: a round over less than
# GRACE and this ago is read from its own games instead (just_over), as its polls read them.
ROUNDS_TTL = 600
# A round set to start after the one before it (no start time of its own: a blitz day's rounds) is next while that one
# is on, and this long after it ended: its fixture says so (Fixture.next_up), and it's armed and polled till it starts.
AFTER_PREVIOUS = timedelta(hours=3)
# Words a knockout's event or round goes by ("8-player Knockout", "Tiebreaks", "Rapid tie-breakers", "Playoffs", "Sudden
# death"). Not "rapid" or "blitz": a rapid event's rounds are a round robin's too. Nor "armageddon": a round robin's
# format says it too (Norway Chess's, an armageddon after each draw), which only a draw is followed by (may_follow).
KNOCKOUT = re.compile(r"\b(?:knock-?outs?|ko|tie[- ]?break(?:er)?s?|play-?offs?|sudden[- ]death)\b", re.I)
# What an armageddon game goes by: its chapter's name, its round's, or in a tournament's PGN its event's (armageddon).
ARMAGEDDON = re.compile(r"\barmageddon\b", re.I)

_client = httpx.Client(headers={"user-agent": USER_AGENT, "accept": "application/json"}, timeout=20, follow_redirects=True)
_lock = threading.Lock()  # one request at a time from each process, as Lichess asks
_state = {"last": 0.0}
SPACING = 1.0  # seconds between requests; bursts of ~25 at 0.3 s drew a 429


# This server's Lichess API token (scope study:read). Lichess now wants broadcast reads authenticated:
# 120 a minute with a token, 10 without, "soon to be zero". Kept outside the repo.
TOKEN_FILE = Path(os.environ.get("SPORTS_FOLLOW_LICHESS_TOKEN_FILE", Path.home() / ".config" / "sports-follow" / "lichess-token"))


@lru_cache(maxsize=1)
def token() -> str | None:
    value = os.environ.get("SPORTS_FOLLOW_LICHESS_TOKEN", "").strip()
    if not value and TOKEN_FILE.is_file():
        value = TOKEN_FILE.read_text().strip()
    return value or None


# Every process's requests together, a minute: what the token allows, or what Lichess allows without one.
# Lichess counts broadcast reads in three-minute windows, so after a 429 nothing asks for three minutes. A
# quarter of each minute is kept for followed players' live boards: background reads (refreshes, Live now, match
# pages) stop short of it, and so does polling a board nobody follows a player in (matches.py, spare()). Fans'
# chess searches, previews and follows take at most half of it, so the boards keep the rest whatever fans do: a
# preview reads the player's FIDE record and no games (preview_fixtures), one request, which a follow then reuses.
def _budget(with_token: bool) -> budget.Budget:
    if with_token:
        return budget.Budget("lichess", windows=(budget.Window(60, 120, margin=10),), pause=180, reserve=30, fan=15)
    return budget.Budget("lichess", windows=(budget.Window(60, 10, margin=1),), urls=(API,), pause=180, reserve=3, fan=1)


BUDGET = _budget(token() is not None)
# Lichess's FIDE player pages and its player endpoint ignore the token: one limit, about 60 requests a
# minute, holds every caller from a datacenter together, a server like ours included, so others can spend
# it (lila: Fide.scala, Limiters.enumeration.fidePlayer). Requests there are counted apart and kept few,
# and a 429 they get pauses only them, not the broadcasts live boards read.
FIDE_URLS = ("https://lichess.org/fide/", f"{API}fide/player/")
FIDE = budget.Budget("lichess-fide", windows=(budget.Window(60, 10, margin=1),), urls=FIDE_URLS)
# The research agent's requests (tools.fetch_url) carry no token, so Lichess holds them to its limit
# without one: with a token they're counted apart, and a 429 they get doesn't pause our own polls.
ANONYMOUS = budget.Budget("lichess-anonymous", windows=(budget.Window(60, 10, margin=1),), urls=(API,)) if token() else BUDGET
# Every response, under the main budget's name whichever budget it spent (cache.py).
_cache = cache.Cache(BUDGET.name, size=1000)


def _spends(url: str) -> budget.Budget:
    """The budget a request spends: FIDE's shared limit for its player pages and endpoint, else ours."""
    return FIDE if url.startswith(FIDE_URLS) else BUDGET


def _headers(url: str, text: bool) -> dict[str, str]:
    headers = {"accept": "*/*"} if text else {}
    if url.startswith("https://lichess.org/") and token():
        headers["authorization"] = f"Bearer {token()}"  # only ever sent to Lichess
    return headers


# A broadcast read refused (Lichess said slow down, or the minute is spent). Unlike a missing round, this
# must fail the whole read: a fixture list with holes would drop results from the card until the next
# refresh. A refused FIDE page doesn't (LichessChess._page_rounds).
RateLimited = budget.RateLimited


def get_json(url: str, ttl: float, text: bool = False) -> Any:
    """GET a Lichess API document (or, with text=True, a page), cached for ttl seconds (0: fetched every
    time and never kept), in Redis too (cache.py)."""
    return _cache.read(url, ttl, lambda: _fetch(url, text))


def _fetch(url: str, text: bool) -> Any:
    with _lock:
        spends = _spends(url)
        budget.take(spends)
        time.sleep(max(0.0, _state["last"] + SPACING - time.time()))
        cache.sending()  # its answer is as old as now, not as the wait for the request line
        try:
            res = _client.get(url, headers=_headers(url, text))
        except httpx.HTTPError as exc:
            raise AdapterError(f"Lichess unreachable: {exc}") from exc
        finally:
            _state["last"] = time.time()
        if res.status_code == 429:
            budget.pause(spends)
            # Where, and Lichess's own words: they say which of its limits it was.
            said = " ".join(res.text.split())[:160]
            raise RateLimited(f"Lichess rate limit (429) on {url.removeprefix('https://lichess.org')}; {spends.name} paused for {spends.pause} s: {said}")
        if res.status_code == 401 and token():
            raise AdapterError("Lichess rejected the API token (HTTP 401); check ~/.config/sports-follow/lichess-token")
        if res.status_code != 200:
            raise AdapterError(f"Lichess returned HTTP {res.status_code} for {url}")
        try:
            data = res.text if text else res.json()
        except ValueError as exc:  # a captive portal's or a CDN's error page, with a 200
            raise AdapterError(f"Lichess returned non-JSON for {url}") from exc
        if not text and not isinstance(data, (dict, list)):
            raise AdapterError(f"Lichess returned {type(data).__name__} for {url}")
        return data


def peek(url: str, ttl: float) -> Any:
    """A cached response younger than ttl seconds, or None, without waiting for the request line."""
    return _cache.peek(url, ttl)


def _dt(ms: Any) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if isinstance(ms, (int, float)) else None


def display_name(fide_name: str) -> str:
    """FIDE writes "Carlsen, Magnus"; people say "Magnus Carlsen"."""
    last, _, first = (fide_name or "").partition(",")
    return f"{first.strip()} {last.strip()}".strip() if first else (fide_name or "").strip()


def _clock(centis: Any) -> str | None:
    if not isinstance(centis, (int, float)):
        return None
    seconds = int(centis) // 100
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _side(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(p.get("fideId") or ""),
        "name": display_name(p.get("name") or ""),
        "title": p.get("title"),
        "rating": p.get("rating"),
        "fed": p.get("fed"),
        "clock": _clock(p.get("clock")),
    }


def _result(status: str | None) -> str | None:
    # Round JSON writes a draw "½-½", PGN "1/2-1/2".
    return {"1-0": "1-0", "0-1": "0-1", "½-½": "½-½", "1/2-1/2": "½-½"}.get(status or "")


def _points(result: str, white: bool, armageddon: bool = False) -> float:
    if armageddon and result == "½-½":
        return 0.0 if white else 1.0  # an armageddon drawn is Black's win
    return {"1-0": 1.0 if white else 0.0, "0-1": 0.0 if white else 1.0, "½-½": 0.5}[result]


# ---------------------------------------------------------------- parsers (pure)


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z]+", naming.fold(name)) if t]


def name_score(wanted: str, fide_name: str) -> int:
    """How many of the wanted name's parts the FIDE name has, in any order, an initial matching a
    whole part ("Gukesh Dommaraju" and FIDE's "Gukesh, D" score 2; "R Praggnanandhaa" and
    "Praggnanandhaa, R" score 2)."""
    have = _tokens(fide_name)
    score = 0
    for w in _tokens(wanted):
        if any(h == w or (len(h) == 1 and w.startswith(h)) or (len(w) == 1 and h.startswith(w)) for h in have):
            score += 1
    return score


def known_as(ref: PlayerRef) -> list[str]:
    """The words a player's events name them by, folded, each one searched for (LichessChess._tours). FIDE writes most
    names "Surname, Given", which the display name turns around (display_name: "Carlsen, Magnus" is "Magnus Carlsen"),
    so it ends with the surname, the one word to go by: a given name is anyone's. A name FIDE writes with no comma
    ("Gukesh D", "Praggnanandhaa R", "Nihal Sarin") is shown as it is, and an event's players line may give either part
    ("Gukesh", "Nihal"): each of them, longest first, but never an initial ("D", "R", "Vr"), which would find every
    "Group D". Which it is, the profile address says: it keeps FIDE's order (ref_for)."""
    words = naming.fold(ref.name).split()
    if not words:
        return []
    fide = naming.fold(unquote((ref.profile_url or "").rstrip("/").rpartition("/")[2]).replace("_", " ")).split()
    if fide != words and len(words[-1]) > 1:
        return [words[-1]]
    parts = sorted(dict.fromkeys(w for w in words if len(w) > 2), key=len, reverse=True)
    return parts or [max(words, key=len)]


def names_them(tour: dict[str, Any], names: list[str]) -> bool:
    """Whether an event's name or players line ("Carlsen, Gukesh, Praggnanandhaa") has one of the words a player is
    known by (known_as)."""
    text = naming.fold(f"{tour.get('name', '')} {(tour.get('info') or {}).get('players', '')}").replace(",", " ").split()
    return any(name in text for name in names)


def pick_player(results: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """The FIDE record a name means: every part of the name must match (initials count), then an
    active player before an inactive one, then the higher rating ("Carlsen" alone is Magnus)."""
    need = len(_tokens(name))
    candidates = [r for r in results if need and name_score(name, r.get("name", "")) == need]
    if not candidates:
        return None
    return sorted(candidates, key=lambda r: (bool(r.get("inactive")), -(r.get("standard") or 0)))[0]


RECORD_TTL = 24 * 3600  # FIDE publishes its ratings monthly


def fide_record(fide_id: str) -> dict[str, Any] | None:
    """A player's FIDE record (name, title, ratings, inactive), by FIDE id. FIDE search answers an id with
    that one player, the same record as the player endpoint, and unlike it isn't held to the limit every
    datacenter shares; the endpoint is asked only if search stops answering ids (an unknown id: its 404)."""
    found = get_json(f"{API}fide/player?q={quote(fide_id)}", ttl=RECORD_TTL)
    record = next((r for r in found if str(r.get("id")) == fide_id), None) if isinstance(found, list) else None
    if record is None:
        record = get_json(f"{API}fide/player/{quote(fide_id)}", ttl=RECORD_TTL)
    return record if isinstance(record, dict) and record.get("id") else None


def ref_for(record: dict[str, Any]) -> PlayerRef:
    """A FIDE record (search hit or /fide/player/{id}) as a PlayerRef; the player is their own "team"."""
    fide_id = str(record["id"])
    full = display_name(record["name"])
    slug = record["name"].replace(", ", "_").replace(" ", "_")
    return PlayerRef("lichess_chess", fide_id, full, [fide_id], [full], f"https://lichess.org/fide/{fide_id}/{quote(slug)}")


# A PGN tag: [Name "value"], where the value escapes a quote or a backslash with a backslash
# ([Event "The \"Alcalá\" Open"]).
_TAG = r'\[(\w+) "((?:[^"\\]|\\.)*)"\]'
_MOVED = re.compile(r"(?:^|\s)\d+\.")  # a move number in a game's moves: it had one


def _pgn_player(tags: dict[str, str], colour: str) -> dict[str, Any]:
    rating = tags.get(f"{colour}Elo")
    return {"name": tags.get(colour, ""), "fideId": int(tags[f"{colour}FideId"]) if (tags.get(f"{colour}FideId") or "").isdigit() else None,
            "rating": int(rating) if (rating or "").isdigit() else None, "title": tags.get(f"{colour}Title")}


def parse_pgn_rounds(pgn: str) -> dict[str, list[dict[str, Any]]]:
    """A tournament's PGN export -> its games grouped by round id, shaped like the round JSON's games.

    One request for a whole event, instead of one per round: Lichess rate-limits round reads hard.
    Each game's GameURL ends in /{roundId}/{gameId}. One its tags say wasn't played (Termination "Unplayed"), or with
    no move, is marked so (unplayed).
    """
    rounds: dict[str, list[dict[str, Any]]] = {}
    blocks = list(re.finditer(rf"(?:^{_TAG}\n?)+", pgn, flags=re.M))
    for block, after in zip(blocks, [*blocks[1:], None]):
        tags = {name: re.sub(r"\\(.)", r"\1", value) for name, value in re.findall(_TAG, block[0])}
        parts = (tags.get("GameURL") or "").rstrip("/").split("/")
        if len(parts) < 2:
            continue
        round_id, game_id = parts[-2], parts[-1]
        players = [_pgn_player(tags, "White"), _pgn_player(tags, "Black")]
        game = {"id": game_id, "players": players, "status": tags.get("Result", "*"), "fen": ""}
        if ARMAGEDDON.search(" ".join(tags.get(k) or "" for k in ("Event", "ChapterName"))):
            game["armageddon"] = True
        moves = re.sub(r"\{[^}]*\}", " ", pgn[block.end():after.start() if after else len(pgn)])  # its moves, comments out
        if (tags.get("Termination") or "").lower() == "unplayed" or not _MOVED.search(moves):
            game["unplayed"] = True
        rounds.setdefault(round_id, []).append(game)
    return rounds


_NUMBER = re.compile(r"(\d+)(\.+)(.*)")  # a move number, "12." (White's) or "12..." (Black's), maybe with the move on it
_RESULTS = {"1-0", "0-1", "1/2-1/2", "½-½", "*"}
_ENDINGS = {"normal", "unterminated", ""}  # Termination tags that say nothing more than the result


def parse_pgn_game(pgn: str) -> dict[str, Any]:
    """One game's PGN -> its moves, each move number's as [n, White's, Black's] ([1, "d4", "Nf6"]; None for one
    not played: Black's first in a game set up with Black to move), placed by the PGN's own move numbers, so a
    move written some other way ("e8Q", a null move "--") can't put the rest on the wrong side; castling written
    with zeros ("0-0") as O-O. Its opening ("Queen's Gambit Declined: Ragozin Defense") and how it ended where
    that says more than the result ("resigned", "Time forfeit"). Comments, side lines, annotation glyphs ($1)
    and the result are left out."""
    tags = {name: re.sub(r"\\(.)", r"\1", value) for name, value in re.findall(_TAG, pgn)}
    body = re.sub(r"\{[^}]*\}|;[^\n]*", " ", re.sub(_TAG, " ", pgn))
    while (bare := re.sub(r"\([^()]*\)", " ", body)) != body:  # side lines nest: the innermost go first
        body = bare
    moves: dict[int, list[str | None]] = {}
    n, black = 1, False
    for token in body.split():
        if number := _NUMBER.fullmatch(token):
            n, black, token = int(number[1]), len(number[2]) > 1, number[3]
        if not token or token in _RESULTS or token.startswith("$"):
            continue
        moves.setdefault(n, [None, None])[black] = re.sub(r"^0-0(-0)?", lambda c: c[0].replace("0", "O"), token)
        n, black = (n + 1, False) if black else (n, True)
    ending = tags.get("Termination") or ""
    return {"moves": [[k, *pair] for k, pair in sorted(moves.items())], "opening": tags.get("Opening") or None,
            "ending": None if ending.lower() in _ENDINGS else ending}


def fen_ply(fen: str | None) -> int | None:
    """How many moves (plies: White's and Black's each one) a game has had at a position, by its FEN's move
    number and side to move ("… b - - 4 25": White's 25 and Black's 24, 49); None for a FEN that doesn't say."""
    fields = (fen or "").split()
    if len(fields) < 6 or fields[1] not in ("w", "b") or not fields[5].isdigit():
        return None
    return (int(fields[5]) - 1) * 2 + (fields[1] == "b")


def moves_ply(moves: list[list[Any]]) -> int:
    """How many plies a game had after its moves (parse_pgn_game's rows): by the last one's number and side, so a
    game set up from a position counts from there as its FEN does; 0 with none."""
    if not moves:
        return 0
    n, _, black = moves[-1]
    return (int(n) - 1) * 2 + (2 if black else 1)


def moves_to(kept: dict[str, Any] | None, fen: str | None, last_move: str | None) -> dict[str, Any] | None:
    """A live board's moves kept so far ({"moves": parse_pgn_game's rows, "more": moves added since, as the
    board gives its last one, "d5e3"; "fen": the board's position when they last reached it) brought to its
    position: as they are when they reach it, with its last move added when they're one short and it's that move on
    from where they reached (one_move_on), or as they are when they're ahead of it with its last move theirs at its
    place (a look at an older copy of the round than the one they were kept up by). None when they can't be: none
    kept, a move or more missed in between, or a position they don't lead to (a relay correcting a move, or taking
    one back): its PGN is read again."""
    ply = fen_ply(fen)
    if kept is None or ply is None:
        return None
    have = moves_ply(kept.get("moves") or []) + len(kept.get("more") or [])
    if have > ply:
        return kept if _theirs_at(kept, ply, last_move) else None
    at = _squares(kept.get("fen"))
    if have == ply:
        if at is not None and at != _squares(fen):
            return None
        return kept if kept.get("fen") == fen else {**kept, "fen": fen}
    if have + 1 == ply and last_move and one_move_on(kept.get("fen"), fen, last_move):
        return {**kept, "more": [*(kept.get("more") or []), last_move], "fen": fen}
    return None


def _squares(fen: str | None) -> dict[str, str] | None:
    """A FEN's pieces by square ({"e1": "K", "e8": "k", …}); None for one that doesn't say (none kept)."""
    rows = (fen or "").split(" ")[0].split("/")
    if len(rows) != 8:
        return None
    board: dict[str, str] = {}
    for rank, row in zip("87654321", rows):
        file = 0
        for c in row:
            if c.isdigit():
                file += int(c)
                continue
            if file > 7:
                return None
            board["abcdefgh"[file] + rank] = c
            file += 1
    return board


def one_move_on(before: str | None, after: str | None, move: str) -> bool:
    """Whether a board (`after`, its FEN) is one move (`move`, as a board gives its last one: "g1f3") on from another
    (`before`): the piece gone from where it moved and one of its side's where it went, every square the move doesn't
    touch as it was (a castling's rook and an en passant's pawn it does). True where either doesn't say."""
    was, now = _squares(before), _squares(after)
    squares = re.fullmatch(r"([a-h][1-8])([a-h][1-8])[qrbn]?", move or "")
    if was is None or now is None or squares is None:
        return True
    start, to = squares[1], squares[2]
    piece = was.get(start)
    if piece is None:
        return False
    touched = {start, to}
    if piece in "Kk":
        touched |= {file + start[1] for file in "abcdefgh"}  # castling: its rook moves along the rank too
    elif piece in "Pp" and start[0] != to[0] and to not in was:
        touched.add(to[0] + start[1])  # en passant: the pawn taken is beside where it went
    if any(was.get(sq) != now.get(sq) for sq in was.keys() | now.keys() if sq not in touched):
        return False
    landed = now.get(to)
    return piece in "Kk" or (start not in now and landed is not None and landed.isupper() == piece.isupper())


def _theirs_at(kept: dict[str, Any], ply: int, last_move: str | None) -> bool:
    """Whether moves kept ahead of a board have its last move as the one that made its ply: as the board gives it,
    where it's one of those added since; by the square it went to, where the PGN wrote it ("a6", "Nxe5+"). True where
    nothing says otherwise (no last move: the start; a move no square says, castling)."""
    if not last_move or ply < 1:
        return True
    moves = kept.get("moves") or []
    base = moves_ply(moves)
    if ply > base:
        return (kept.get("more") or [])[ply - base - 1] == last_move
    row = next((m for m in moves if int(m[0]) == (ply + 1) // 2), None)
    written = row[1 if ply % 2 else 2] if row else None
    went = re.findall(r"[a-h][1-8]", written or "")
    return not went or went[-1] == last_move[2:4]


def games_of(round_data: dict[str, Any], fide_id: str) -> list[dict[str, Any]]:
    """The player's games in a round, in board order, each with its number in their pairing's games there ("n": 1, 2,
    …), an armageddon marked so (its "armageddon"), and none that wasn't played (unplayed), though it keeps its number,
    so a game's number stays its own whichever of those before it turns out not to be played. Knockout rounds hold a
    whole mini-match (Esports World Cup "Grand Final": eight games between the same two players)."""
    mine = [g for g in round_data.get("games") or [] if any(str(p.get("fideId")) == fide_id for p in g.get("players") or [])]
    return [{**g, "n": n, **({"armageddon": True} if armageddon(round_data, g) else {})} for n, g in enumerate(mine, 1) if not unplayed(g)]


def fixture_id(round_id: str, fide_id: str, n: int = 1) -> str:
    """A player's fixture of their nth game in a round: "<round id>-<FIDE id>" for the first, as the round's own before
    it's paired, so a round to come is the game it becomes; "<round id>-<FIDE id>-<n>" for a mini-match's next ones."""
    return f"{round_id}-{fide_id}" + (f"-{n}" if n > 1 else "")


def unplayed(game: dict[str, Any]) -> bool:
    """Whether a game in a round has a result but wasn't played, so it's no game of its players', not a point in a
    match's score nor a result in their form (the Esports World Cup's third-place match, won 3–1 after four games, lists
    a fifth "1-0" with Termination "Unplayed"; a forfeit): its PGN says so, or has no move (parse_pgn_rounds), or, in a
    round's JSON, which doesn't say how a game ended, no move was made, its board at the start."""
    return bool(_result(game.get("status"))) and (bool(game.get("unplayed")) or (fen_ply(game.get("fen")) == 0 and not game.get("lastMove")))


def unstarted(game: dict[str, Any]) -> bool:
    """Whether a game listed in a round hasn't started: no result and no move made, as unplayed tells (a board made
    ahead for a game that may be needed: a mini-match's next, an armageddon). No game under way."""
    return not _result(game.get("status")) and (bool(game.get("unplayed")) or (fen_ply(game.get("fen")) == 0 and not game.get("lastMove")))


def last_over(games: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The last of a player's games in a round to have its result, where none of them is under way (each decided, or
    not started): their pairing between games, or done. None while one is being played, or before any is decided."""
    if any(not _result(g.get("status")) and not unstarted(g) for g in games):
        return None
    return next((g for g in reversed(games) if _result(g.get("status"))), None)


def armageddon(round_data: dict[str, Any], game: dict[str, Any]) -> bool:
    """Whether a game is an armageddon, where a draw is Black's win: its chapter's name or its round's says so
    (Norway Chess's armageddon after a classical draw, a knockout's tiebreak), so does its event's in a tournament's
    PGN (parse_pgn_rounds), or its round is scored so (Lichess's custom scoring: a draw worth more to Black)."""
    rnd = round_data.get("round") or {}
    if game.get("armageddon") or ARMAGEDDON.search(f"{game.get('name') or ''} {rnd.get('name') or ''}"):
        return True
    white, black = (((rnd.get("customScoring") or {}).get(side) or {}).get("draw") for side in ("white", "black"))
    return isinstance(white, (int, float)) and isinstance(black, (int, float)) and black > white


def _is_white(game: dict[str, Any], fide_id: str) -> bool:
    return str(((game.get("players") or [{}])[0]).get("fideId")) == fide_id


def match_score(games: list[dict[str, Any]], fide_id: str) -> tuple[float, int]:
    """(points, decided games) for the player over these games of theirs in a round."""
    points, decided = 0.0, 0
    for g in games:
        result = _result(g.get("status"))
        if result:
            points += _points(result, _is_white(g, fide_id), bool(g.get("armageddon")))
            decided += 1
    return points, decided


def fmt_points(x: float) -> str:
    whole = int(x)
    return f"{whole if whole or x == 0 else ''}{'½' if x % 1 else ''}"


def _names(round_data: dict[str, Any]) -> str:
    """What a round's event and the round itself are called, and Lichess's format for the event."""
    rnd, tour = round_data.get("round") or {}, round_data.get("tour") or {}
    return " ".join(str(x or "") for x in (tour.get("name"), rnd.get("name"), (tour.get("info") or {}).get("format")))


def may_follow(round_data: dict[str, Any], games: list[dict[str, Any]]) -> bool:
    """Whether a player's game in a round may be followed by another with the same opponent in it: a mini-match's next
    game, a tiebreak, an armageddon, each shown only once the one before is over. So in a knockout's round: its event's
    or round's name, or Lichess's format for the event, says so (KNOCKOUT: a World Championship's "Tiebreaks"), or a
    pairing in it already has a second game that's no armageddon. Where draws go to an armageddon (Norway Chess's round
    robin: its names say so, or a pairing in it has one), only after a draw. Never after an armageddon, which decides;
    not in a round robin's or a Swiss's, nor in a match's classical game ("Game 5", its pairing's alone), which is over
    once it's decided."""
    last = next((g for g in reversed(games) if _result(g.get("status"))), None)
    if last is not None and armageddon(round_data, last):
        return False
    names = _names(round_data)
    if KNOCKOUT.search(names):
        return True
    pairs: dict[frozenset[str], list[dict[str, Any]]] = {}
    for g in round_data.get("games") or games:
        pairs.setdefault(pairing(g), []).append(g)
    if any(not armageddon(round_data, g) for later in pairs.values() for g in later[1:]):
        return True  # a mini-match in it
    if ARMAGEDDON.search(names) or any(len(later) > 1 for later in pairs.values()):
        return last is not None and _result(last.get("status")) == "½-½"
    return False


def pairing(game: dict[str, Any]) -> frozenset[str]:
    """A game's two players by FIDE id, or by name where they have none (an open's unrated boards are each a pairing of
    their own)."""
    return frozenset(str(p.get("fideId") or p.get("name") or "") for p in game.get("players") or [])


MATCH = re.compile(r"\bmatch\b", re.I)  # Lichess's format for an event of two players' games ("14-game match")


def a_match(tour: dict[str, Any], pairings: set[frozenset[str]], fide_id: str) -> bool:
    """Whether an event is a match between two players, the player one of them (a World Championship), so each of its
    rounds is their meeting, before its pairing's out too: every round of it read so far paired the same two, or, with
    none read yet, its players line names two and its format says it's a match."""
    if pairings:
        return len(pairings) == 1 and fide_id in next(iter(pairings))
    info = tour.get("info") or {}
    return len([p for p in str(info.get("players") or "").split(",") if p.strip()]) == 2 and bool(MATCH.search(str(info.get("format") or "")))


def finished_for(round_info: dict[str, Any], now: datetime) -> float | None:
    """How long ago, in seconds, Lichess called a round finished (its finishedAt); None while it doesn't. One said
    finished without saying when (not as Lichess serves it) is taken at its word: long since."""
    if not round_info.get("finished"):
        return None
    ended = _dt(round_info.get("finishedAt"))
    return (now - ended).total_seconds() if ended is not None else float("inf")


def round_status(round_info: dict[str, Any], games: list[dict[str, Any]], now: datetime) -> str:
    """A player's game in a round (`games`: it, or none before they're paired): scheduled, live, final, or postponed.
    Decided, it's over at once, a mini-match's game too: the one that may follow it is a fixture of its own (next_game).
    A game under way in a round Lichess calls finished is a copy of its games older than the round's, or one never given
    its result: over."""
    if games and all(_result(g.get("status")) for g in games):
        return "final"
    if round_info.get("finished"):
        return "final" if games else "postponed"
    starts = _dt(round_info.get("startsAt"))
    if games and (round_info.get("ongoing") or (starts and starts <= now)):
        return "live"
    return "scheduled"  # a round on without a board of theirs (yet): their game hasn't started


def game_status(round_info: dict[str, Any], game: dict[str, Any] | None, now: datetime) -> str:
    """A player's game in a round (games_of's; None before they're paired) as round_status has it, but a mini-match's
    game after the first that's under way in a round Lichess called finished less than GRACE ago is live: Lichess calls
    the round finished once the game before it was decided, and on again only as this one's first moves reach it."""
    status = round_status(round_info, [game] if game else [], now)
    if status == "final" and game and game["n"] > 1 and not _result(game.get("status")) and not unstarted(game):
        since = finished_for(round_info, now)
        return "live" if since is not None and since < GRACE else status
    return status


def next_game(round_data: dict[str, Any], games: list[dict[str, Any]], now: datetime, waited: float | None = None) -> int | None:
    """The number of a player's game that may follow their last in a round (games_of's), while it may: their pairing's
    next may follow (may_follow), none of theirs is under way, and it's less than GRACE since the last one ended, by the
    longer of how long ago it was first seen over (`waited`, in seconds: LichessChess._waited; None for not known yet)
    and how long ago Lichess called the round finished (finished_for), whatever Lichess says of the round, which it calls
    finished as soon as every game in it has a result. A board made ahead for it is it; with none, the one after the
    last. None once it may not."""
    started = [g for g in games if not unstarted(g)]
    if not started or last_over(games) is None or not may_follow(round_data, games):
        return None
    since = finished_for(round_data.get("round") or {}, now)
    if max((s for s in (waited, since) if s is not None), default=0.0) >= GRACE:
        return None
    last = started[-1]["n"]
    return next((g["n"] for g in games if g["n"] > last and unstarted(g)), last + 1)


def to_follow(round_data: dict[str, Any], games: list[dict[str, Any]], n: int, now: datetime, waited: float | None = None) -> str:
    """A mini-match's game that may follow, before it starts (`n`: its number; next_game): scheduled while it may still
    come (one before it under way, or it's the next while that may follow), else postponed, never needed: its fixture is
    shown nowhere (Fixture.unlisted) and isn't polled again."""
    if any(g["n"] < n and not unstarted(g) and not _result(g.get("status")) for g in games):
        return "scheduled"
    return "scheduled" if next_game(round_data, games, now, waited) == n else "postponed"


def game_label(games: list[dict[str, Any]], game: dict[str, Any]) -> str | None:
    """What a game of a mini-match is called beside its round's name, where its pairing has more than one in the round
    (games_of's): "Game 2", "Armageddon". The game before an armageddon (Norway Chess's classical, after which a draw
    goes to one) is its round's alone. None for a round's one game."""
    if len(games) < 2:
        return None
    if game.get("armageddon"):
        return "Armageddon"
    return f"Game {game['n']}" if any(not g.get("armageddon") for g in games if g["n"] != game["n"]) else None


def mini_match(games: list[dict[str, Any]], game: dict[str, Any], label: str | None) -> dict[str, Any] | None:
    """A mini-match's game's place in it, for its board (state["mini_match"]): its number and what it's called
    (game_label), and the match's score as it stands, over the games decided up to it (this one's too once decided): the
    leader's points first, the leader by FIDE id ("ahead"; None while level), and how many games it counts. None for a
    round's one game, or the game an armageddon follows (game_label)."""
    if not label:
        return None
    upto = [g for g in games if g["n"] < game["n"] or (g["n"] == game["n"] and _result(g.get("status")))]
    ids = [str(p.get("fideId") or "") for p in (game.get("players") or [])[:2]]
    if len(ids) < 2:
        return None
    pts, decided = match_score(upto, ids[0])
    other = decided - pts
    ahead = ids[0] if pts > other else ids[1] if other > pts else None
    return {"game": game["n"], "label": label, "games": decided, "score": f"{fmt_points(max(pts, other))}–{fmt_points(min(pts, other))}", "ahead": ahead}


def next_up(rounds: list[dict[str, Any]], rnd: dict[str, Any], now: datetime) -> bool:
    """Whether a round with no start time of its own, set to start once the one before it is over (Lichess's
    startsAfterPrevious: a blitz day's rounds, a tiebreak), is next: the one before it is on, or ended within
    AFTER_PREVIOUS."""
    if rnd.get("startsAt") or not rnd.get("startsAfterPrevious") or rnd.get("finished") or rnd.get("ongoing"):
        return False
    at = next((i for i, r in enumerate(rounds) if r.get("id") == rnd.get("id")), 0)
    before = rounds[at - 1] if at else {}
    ended = _dt(before.get("finishedAt"))
    return bool(before.get("ongoing") or (before.get("finished") and (ended is None or now - ended < AFTER_PREVIOUS)))


LIVE_TOURS = 3  # the broadcasts on now whose round is read for live_now(), highest tier first: a request each, on the one-at-a-time line
LIVE_BOARDS = 2  # and the boards of each round shown: the highest rated pairs still playing
# A board's average rating to be one of the big games on now: an elite event's, not the top board of a
# seniors' or an open's section (Lichess's tiers rank an event, not who plays in it). None on a quiet day.
LIVE_RATING = 2550


def live_boards(round_data: dict[str, Any], system: str, now: datetime | None = None) -> list[LiveGame]:
    """A broadcast round on now -> its highest rated boards still being played whose players average
    LIVE_RATING or more, each a game with both players, under each one's fixture of it (a mini-match's game its own)."""
    now = now or datetime.now(timezone.utc)
    rnd, tour = round_data.get("round") or {}, round_data.get("tour") or {}
    playing = [g for g in round_data.get("games") or [] if g.get("status") == "*" and len(g.get("players") or []) >= 2 and all(p.get("fideId") for p in g["players"][:2])
               and sum(p.get("rating") or 0 for p in g["players"][:2]) >= 2 * LIVE_RATING]
    playing.sort(key=lambda g: -sum(p.get("rating") or 0 for p in g["players"][:2]))
    out = []
    for g in playing[:LIVE_BOARDS]:
        sides = g["players"][:2]
        n = next((x["n"] for x in games_of(round_data, str(sides[0]["fideId"])) if x.get("id") == g.get("id")), 1)
        snap = parse_board(round_data, str(sides[0]["fideId"]), now, game=n)
        if snap.status != "live":
            continue
        refs = [PlayerRef(system=system, athlete_id=str(p["fideId"]), name=display_name(p.get("name") or ""), team_ids=[str(p["fideId"])], team_names=[display_name(p.get("name") or "")]) for p in sides]
        out.append(LiveGame(
            source_id=f"{rnd.get('id')}:{g.get('id')}",
            title=f"{refs[0].name} v {refs[1].name}",
            competition=" · ".join(x for x in (tour.get("name"), rnd.get("name"), (snap.state.get("mini_match") or {}).get("label")) if x),
            start_utc=_dt(rnd.get("startsAt")),
            snapshot=snap,
            players=refs,
            fixtures=[fixture_id(str(rnd.get("id")), p.athlete_id, n) for p in refs],  # as fixtures() names each player's
        ))
    return out


def parse_game(round_data: dict[str, Any], fide_id: str, system: str, now: datetime | None = None, waited: float | None = None, game: int = 1) -> LiveGame | None:
    """A broadcast round -> one player's game in it (`game`: its number, games_of's) for its match page, with both
    players and the fixture each has it under; None when they have no such game in it (not paired, or a mini-match's
    game that hasn't come). `waited` as parse_board's."""
    rnd, tour = round_data.get("round") or {}, round_data.get("tour") or {}
    if not rnd.get("id") or not any(g["n"] == game for g in games_of(round_data, fide_id)):
        return None
    snap = parse_board(round_data, fide_id, now, waited, game)
    refs = [PlayerRef(system=system, athlete_id=s["id"], name=s["name"], team_ids=[s["id"]], team_names=[s["name"]]) for s in (snap.state.get("white"), snap.state.get("black")) if s and s.get("id")]
    return LiveGame(
        source_id=fixture_id(rnd["id"], fide_id, game),
        title=" v ".join(r.name for r in refs),
        competition=" · ".join(x for x in (tour.get("name"), rnd.get("name"), (snap.state.get("mini_match") or {}).get("label")) if x),
        start_utc=_dt(rnd.get("startsAt")),
        snapshot=snap,
        players=refs,
        fixtures=[fixture_id(rnd["id"], r.athlete_id, game) for r in refs],  # as fixtures() names each player's
    )


def parse_board(round_data: dict[str, Any], fide_id: str, now: datetime | None = None, waited: float | None = None, game: int = 1) -> Snapshot:
    """One player's game in a broadcast round (`game`: its number, games_of's; the round itself before they're paired)
    -> snapshot: its board, and of a mini-match's game, its place in the match and the match's score as it stands
    (mini_match). Lines for both players, keyed by FIDE id. A mini-match's game that may follow, before it starts, is
    to come while it may, else off (to_follow: `waited`, LichessChess._waited)."""
    now = now or datetime.now(timezone.utc)
    rnd, tour = round_data.get("round") or {}, round_data.get("tour") or {}
    games = games_of(round_data, fide_id)
    g = next((x for x in games if x["n"] == game), None)
    if game > 1 and (g is None or unstarted(g)):
        status = to_follow(round_data, games, game, now, waited)
    else:
        status = game_status(rnd, g, now)
    if g is None:
        if game > 1:
            clock = f"Game {game}, if it's played"
        else:
            clock = "Pairings not published yet" if status == "scheduled" and not round_data.get("games") else "Not paired this round"
        return Snapshot(
            status=status,
            score_label=f"{tour.get('name', '')} · {rnd.get('name', '')}",
            clock_label=clock,
            state={"kind": "chess", "round": rnd.get("name"), "tournament": tour.get("name")},
            source_url=rnd.get("url"),
        )
    white, black = [_side(p) for p in (g.get("players") or [{}, {}])[:2]]
    fen = g.get("fen") or ""
    fields = fen.split()
    turn = "white" if len(fields) < 2 or fields[1] == "w" else "black"
    move = int(fields[5]) if len(fields) > 5 and fields[5].isdigit() else None
    result = _result(g.get("status"))
    mm = mini_match(games, g, game_label(games, g))
    upto = [x for x in games if x["n"] < g["n"] or (x["n"] == g["n"] and result)]  # the match's games decided so far
    decider = " on armageddon" if result and g.get("armageddon") else ""  # where a draw is Black's win

    lines = {}
    for me, other, colour in ((white, black, "White"), (black, white, "Black")):
        if not me["id"]:
            continue
        stats = [{"label": "Colour", "value": colour}, {"label": "Opponent", "value": other["name"]}]
        if other.get("rating"):
            stats.append({"label": "Opp. rating", "value": str(other["rating"])})
        if mm:
            pts, decided = match_score(upto, me["id"])
            stats.append({"label": "Match", "value": f"{fmt_points(pts)}–{fmt_points(decided - pts)}"})
        if result:
            pts = _points(result, colour == "White", bool(g.get("armageddon")))
            word = "Won" if pts > 0.5 else "Lost" if pts < 0.5 else "Drew"
            headline = f"{word} with {colour} vs {other['name']}{decider}"
        else:
            headline = f"{colour} vs {other['name']}"
            if me.get("clock"):
                stats.append({"label": "Clock", "value": me["clock"]})
        lines[me["id"]] = Line(headline=headline, stats=stats)

    if result:
        who = white["name"] if result == "1-0" else black["name"] if result == "0-1" or g.get("armageddon") else None
        score_label = f"{white['name']} {result.replace('-', '–')} {black['name']}"
        clock_label = f"{who} won{decider}" if who else "Draw"
    else:
        score_label = f"{white['name']} vs {black['name']}"
        clock_label = f"Move {move} · {turn.capitalize()} to play" if move and status == "live" else ("Starts soon" if status == "scheduled" else "In play")
    return Snapshot(
        status=status,
        score_label=score_label,
        clock_label=clock_label,
        state={
            "kind": "chess",
            "round": rnd.get("name"),
            "tournament": tour.get("name"),
            "game_id": g.get("id"),
            "white": white,
            "black": black,
            "fen": fen,
            "last_move": g.get("lastMove"),
            "turn": turn,
            "move": move,
            "result": result,
            **({"armageddon": True} if g.get("armageddon") else {}),  # a draw is Black's win (Chess.answer)
            **({"mini_match": mm} if mm else {}),
        },
        lines=lines,
        source_url=f"{rnd['url']}/{g['id']}" if rnd.get("url") and g.get("id") else rnd.get("url"),
    )


def fixture_for(tour: dict[str, Any], rnd: dict[str, Any], game: dict[str, Any] | None, fide_id: str, now: datetime, label: str | None = None,
                upnext: bool = False, meeting: bool = False) -> Fixture:
    """A player's fixture of one game of theirs in a round (`game`, numbered: games_of; `label` what it's called in its
    mini-match, game_label), or of the round itself before they're paired (None). `upnext`, a round with no start time
    of its own that's next (next_up); `meeting`, a round of a match between two players (a_match), whose board before its
    pairing's out is the round, the same under both players' fixtures, so Coming up shows it once."""
    status = game_status(rnd, game, now)
    n = game["n"] if game else 1
    title = f"{tour.get('name', '')} · {rnd.get('name', '')}"
    result = None
    if game:
        white, black = [_side(p) for p in (game.get("players") or [{}, {}])[:2]]
        title = f"{white['name']} vs {black['name']}"
        if status == "final":
            pts, decided = match_score([game], fide_id)
            white_side = _is_white(game, fide_id)
            result = {"points": pts, "games": decided, "opponent": (black if white_side else white)["name"], "colour": "White" if white_side else "Black",
                      **({"armageddon": True} if game.get("armageddon") and decided else {})}
    return Fixture(
        source_id=fixture_id(rnd["id"], fide_id, n),
        title=title,
        competition=tour.get("name") or "",
        start_utc=_dt(rnd.get("startsAt")),
        venue=(tour.get("info") or {}).get("location"),
        status=status,
        team_ids=[fide_id],
        team_names=[],
        locator={"round": rnd["id"], "fide": fide_id, "game": n},
        detail=" · ".join(x for x in (rnd.get("name"), label) if x) if game else "Pairings not published yet",
        result=result,
        board=str((game or {}).get("id") or "") or (f"round:{rnd['id']}" if meeting else ""),  # the game parse_board will show, under each player's fixture
        next_up=upnext,
    )


def following_fixture(tour: dict[str, Any], rnd: dict[str, Any], games: list[dict[str, Any]], n: int, fide_id: str) -> Fixture:
    """A player's fixture of the game that may follow their last in a round's mini-match (`n`: next_game), before it
    starts: armed while it may come, so its start is seen when it does, but shown nowhere till then (unlisted), as it may
    never be played. Its board's, where one is made ahead for it; else the last game's players, colours changed, as a
    mini-match's games go."""
    ahead = next((g for g in games if g["n"] == n), None)
    last = [g for g in games if not unstarted(g)][-1]
    white, black = [_side(p) for p in ((ahead or last).get("players") or [{}, {}])[:2]]
    if ahead is None:
        white, black = black, white
    return Fixture(
        source_id=fixture_id(rnd["id"], fide_id, n),
        title=f"{white['name']} vs {black['name']}",
        competition=tour.get("name") or "",
        start_utc=_dt(rnd.get("startsAt")),
        venue=(tour.get("info") or {}).get("location"),
        status="scheduled",
        team_ids=[fide_id],
        team_names=[],
        locator={"round": rnd["id"], "fide": fide_id, "game": n},
        detail=" · ".join(x for x in (rnd.get("name"), "Armageddon" if (ahead or {}).get("armageddon") else f"Game {n}") if x),
        board=str((ahead or {}).get("id") or ""),
        next_up=True,
        unlisted=True,
    )


def round_fixtures(tour: dict[str, Any], rnd: dict[str, Any], round_data: dict[str, Any], games: list[dict[str, Any]], fide_id: str, now: datetime,
                   waited: float | None = None, upnext: bool = False) -> list[Fixture]:
    """A player's fixtures in a round they're paired in (`games`: games_of's): one for each of their games, the first
    (the round's own, before) and those under way or over, a mini-match's each its own; and the one that may follow, while
    it may (next_game: `waited` as its), unlisted till it starts. A board made ahead for a game that may never be needed
    (an armageddon) is that one, not a game of theirs yet."""
    out = [fixture_for(tour, rnd, g, fide_id, now, game_label(games, g), upnext and g["n"] == 1) for g in games if g["n"] == 1 or not unstarted(g)]
    n = next_game(round_data, games, now, waited)
    if n is not None:
        out.append(following_fixture(tour, rnd, games, n, fide_id))
    return out


def result_label(fixture: Fixture, team_ids: list[str]) -> str:
    """ "Won 6–2" for a match, from the followed player's side (a chess fixture belongs to one player), its colours
    changing game to game; a game's result as chess writes it, White's points first, from either side ("Lost 1–0":
    a loss with Black), so the score alone (the app's results drop the word) never says the other side won."""
    r = fixture.result
    if not r or not r.get("games"):
        return ""
    pts, other = r["points"], r["games"] - r["points"]
    word = "Won" if pts > other else "Lost" if pts < other else "Drew"
    white, black = (other, pts) if r.get("games") == 1 and r.get("colour") == "Black" else (pts, other)
    return f"{word} {fmt_points(white)}–{fmt_points(black)}" + (" on armageddon" if r.get("armageddon") else "")


# ---------------------------------------------------------------- search and recognising a name

CHESS_LIMIT = 4  # FIDE rows among all sports; a chess-only search shows more
# FIDE's list holds about a million players, most of them amateurs; a typed name is only taken to
# mean a chess player straight away if they're titled or rated 2200+.
CHESS_TITLES = {"GM", "IM", "WGM", "WIM", "FM", "WFM"}
# FIDE federation codes for the countries most players come from; others show as the code.
FEDERATIONS = {
    "NOR": "Norway", "IND": "India", "USA": "United States", "CHN": "China", "RUS": "Russia", "FID": "FIDE", "NED": "Netherlands",
    "GER": "Germany", "FRA": "France", "ESP": "Spain", "ENG": "England", "ITA": "Italy", "POL": "Poland", "UKR": "Ukraine",
    "ARM": "Armenia", "AZE": "Azerbaijan", "HUN": "Hungary", "UZB": "Uzbekistan", "IRI": "Iran", "DEN": "Denmark", "SWE": "Sweden",
    "CZE": "Czechia", "ROU": "Romania", "SRB": "Serbia", "TUR": "Türkiye", "ISR": "Israel", "CAN": "Canada", "BRA": "Brazil",
    "ARG": "Argentina", "PER": "Peru", "VIE": "Vietnam", "PHI": "Philippines", "GEO": "Georgia", "KAZ": "Kazakhstan", "AUS": "Australia",
}


def parse_fide(records: list[dict[str, Any]], query: str, limit: int) -> list[SearchResult]:
    """FIDE records as results: active, titled and higher-rated players first. Amateurs only show
    up when the name is exactly what was typed or the search is chess only (limit > CHESS_LIMIT)."""
    out = []
    for r in records:
        if not r.get("id") or not r.get("name"):
            continue
        name = display_name(r["name"])
        if not naming.prefix_match(query, name):
            continue  # FIDE search matches any one word: "max verstappen" returns every titled Max
        exact = naming.same(query, name)
        notable = bool(r.get("title")) or (r.get("standard") or 0) >= 2000
        if not (notable or exact or limit > CHESS_LIMIT):
            continue
        fed = FEDERATIONS.get(r.get("federation") or "", r.get("federation") or "")
        rating = f"{r['standard']} classical" if r.get("standard") else ""
        detail = " · ".join(x for x in (r.get("title") or "", fed, rating, "inactive" if r.get("inactive") else "") if x)
        out.append(SearchResult(
            key=f"lichess_chess:{r['id']}",
            name=name,
            sport="Chess",
            detail=detail,
            system="lichess_chess",
            athlete_id=str(r["id"]),
            live_scores=True,
            inactive=bool(r.get("inactive")),
            exact=exact,
            popularity=5 if r.get("title") and not r.get("inactive") else 0,
            _rank=(bool(r.get("title")) and not r.get("inactive"), r.get("standard") or 0),
        ))
    out.sort(key=lambda x: (x.inactive, -x._rank[1]))
    return out[:limit]


_fide_lock = threading.Lock()


def search(query: str, sport: str | None, adapters: list[Adapter]) -> list[SearchResult]:
    """FIDE's list, for a chess-only search: the registry has the titled players, this the other 1.9 million."""
    # Lichess asks for one request at a time; a burst of keystrokes shouldn't queue up behind each
    # other, so a search that can't get the line within a second gives up on chess this time.
    url = f"{API}fide/player?q={quote(query)}"
    records = peek(url, ttl=24 * 3600)
    if records is None:
        if not _fide_lock.acquire(timeout=1.0):
            raise AdapterError("chess search busy")
        try:
            records = get_json(url, ttl=24 * 3600)
        finally:
            _fide_lock.release()
    return parse_fide(records if isinstance(records, list) else [], query, 10)


def find_record(name: str) -> dict[str, Any] | None:
    """The FIDE record a name means (pick_player), from FIDE search."""
    # FIDE search matches the name as typed; "Gukesh Dommaraju" only finds FIDE's "Gukesh, D" by its
    # given name, so search each long part too and let pick_player decide.
    queries = [name, *[t for t in _tokens(name) if len(t) >= 4]]
    results: dict[int, dict[str, Any]] = {}
    for q in dict.fromkeys(queries):
        found = get_json(f"{API}fide/player?q={quote(q)}", ttl=24 * 3600)
        for r in found if isinstance(found, list) else []:
            results.setdefault(r["id"], r)
        if pick_player(list(results.values()), name):
            break
    return pick_player(list(results.values()), name)


def recognise(query: str, adapters: list[Adapter]) -> tuple[Adapter, PlayerRef] | None:
    """A titled or 2200+ active player with this name on FIDE's list."""
    chess = adapters[0]
    try:
        record = find_record(query)
        if record and not record.get("inactive") and (record.get("title") in CHESS_TITLES or (record.get("standard") or 0) >= 2200):
            return chess, ref_for(record)
    except AdapterError:
        pass
    return None


PROVIDER = Provider(
    name="Lichess",
    url="https://lichess.org",
    what="broadcasts of over-the-board events (games, moves and clocks), shown with each organiser's delay.",
    licence="Broadcast games: CC BY-SA 4.0",
    licence_url="https://creativecommons.org/licenses/by-sa/4.0/",
    note="The games are shown unchanged; the evaluations beside them are ours.",
    search=LiveSearch(find=search, unfiltered=False, min_length=3),
    recognise=recognise,
    research=lambda adapters: f"{API}broadcast/top",  # the broadcasts on now
    budgets=(BUDGET, ANONYMOUS, FIDE),
)


# ---------------------------------------------------------------- the adapter


class LichessChess(Adapter):
    system = "lichess_chess"
    sports = ("chess",)
    live_cadence = 15  # moves come slower than goals; Lichess prefers light polling
    name = "Lichess"
    provider = PROVIDER
    covers = "Chess"
    # A chess player's tournaments take a dozen rate-limited Lichess reads to find; the preview shows
    # ratings only and the page finds the tournaments once followed.
    preview_fixtures = False
    # A player's game in a round: "<round id>-<FIDE id>", and a mini-match's next ones "<round id>-<FIDE id>-<n>" (fixture_id).
    fixture_pattern = re.compile(r"([A-Za-z0-9]{8})-([0-9]{1,12})(?:-([2-9]|[1-9][0-9]))?")

    def credits_also(self) -> list[dict[str, Any]]:
        return [{
            "name": "FIDE",
            "url": "https://ratings.fide.com",
            "what": "Chess titles and ratings, looked up player by player through Lichess.",
            "note": "Ratings are FIDE's.",
        }]

    def find_player(self, name: str) -> PlayerRef | None:
        best = find_record(name)
        return ref_for(best) if best else None

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        record = fide_record(athlete_id)
        return ref_for(record) if record else None

    def _page_rounds(self, ref: PlayerRef) -> list[str]:
        """A round of each of the player's "Recent tournaments" on their Lichess FIDE page, newest first.

        The page is behind the limit every datacenter shares (FIDE), so it's read a few times a day, and
        when Lichess refuses it what the last copy listed stands in, kept in Redis through restarts. With
        none (the page never read), the player's events on now and to come are still found by search.
        """
        if not ref.profile_url:
            return []
        key = f"lichess:fide-page:{ref.athlete_id}"
        try:
            page = get_json(ref.profile_url, ttl=PAGE_TTL, text=True)
        except RateLimited:
            try:
                raw: Any = bus.sync_redis().get(key)  # synchronous: a value, whatever redis-py's types allow
            except Exception:
                raw = None
            return json.loads(raw) if raw else []
        except AdapterError:
            return []
        rounds = list(dict.fromkeys(re.findall(r'href="/broadcast/[^/"]+/[^/"]+/([A-Za-z0-9]{8})"', page)))[:MAX_TOURS]
        if rounds:
            try:
                bus.sync_redis().set(key, json.dumps(rounds), ex=STALE_PAGE)
            except Exception:
                pass  # Redis down: the next read keeps them
        return rounds

    def _tours(self, ref: PlayerRef, now: datetime) -> list[dict[str, Any]]:
        """Broadcast tournaments the player is likely in: the "Recent tournaments" on their Lichess
        FIDE page, broadcast search on each word they're known by (known_as), and what's running now
        that names them. Those on their page or naming them come first, then the newest: search's
        other hits (an event that only mentions them) don't push their own out.

        The FIDE page is the only place Lichess lists a player's events; the API has no endpoint
        for it, and search misses events whose player list doesn't name them (Carlsen's Global
        Chess League final).
        """
        names = known_as(ref)
        found: dict[str, dict[str, Any]] = {}
        for round_id in self._page_rounds(ref):
            try:
                tour = get_json(f"{API}broadcast/-/-/{round_id}", ttl=24 * 3600).get("tour") or {}
            except RateLimited:
                raise
            except AdapterError:
                continue  # search below still finds the well-known events
            if tour.get("id"):
                found.setdefault(tour["id"], tour)
        theirs = set(found)
        for name in names:
            hits = get_json(f"{API}broadcast/search?q={quote(name)}", ttl=3600)
            for entry in hits.get("currentPageResults") or []:
                found.setdefault(entry["tour"]["id"], entry["tour"])
        top = get_json(f"{API}broadcast/top", ttl=600)
        for entry in [*(top.get("active") or []), *(top.get("upcoming") or [])]:
            tour = entry["tour"]
            if names_them(tour, names):
                found.setdefault(tour["id"], tour)
        lo, hi = now - timedelta(days=DAYS_BACK), now + timedelta(days=DAYS_AHEAD)

        def in_window(tour: dict[str, Any]) -> bool:
            dates = [d for d in (_dt(x) for x in tour.get("dates") or []) if d]
            return bool(dates) and dates[-1] >= lo and dates[0] <= hi

        tours = sorted((t for t in found.values() if in_window(t)), key=lambda t: (t["id"] in theirs or names_them(t, names), (t.get("dates") or [0])[0]), reverse=True)
        return tours[:MAX_TOURS]

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        now = datetime.now(timezone.utc)
        names = known_as(ref)
        out: list[Fixture] = []
        for tour in self._tours(ref, now):
            try:
                rounds = get_json(f"{API}broadcast/{tour['id']}", ttl=ROUNDS_TTL).get("rounds") or []
            except RateLimited:
                raise
            except AdapterError:
                continue
            games: list[Fixture] = []
            unpaired: list[dict[str, Any]] = []
            pairings: set[frozenset[str]] = set()  # every pairing in its rounds read: one, in a match between two players
            finished = {}

            def just_over(r: dict[str, Any]) -> bool:
                """A round Lichess called finished less than GRACE ago, its rounds' copy's age too: read from its own
                games, as its polls read them, not the event's PGN, whose copy, up to ROUNDS_TTL old, may be from
                before its last game ended. The read that first sees a mini-match's game over arms the next one
                (next_game), and Lichess calls a round finished between them."""
                since = finished_for(r, now)
                return since is not None and since < GRACE + ROUNDS_TTL

            if any(r.get("finished") and not just_over(r) for r in rounds):
                # Kept up to a day once every round is over and has been long enough to be done with (Lichess calls a
                # round finished between a tiebreak's games, and its event's last round may go on: next_game), but
                # only a copy read after the last of them ended, its rounds' copy up to ROUNDS_TTL late to say so: one
                # read before that may lack its last games (a refresh skipped, a 429, a restart in between).
                over = min(finished_for(r, now) or 0 for r in rounds)
                ttl = min(24 * 3600, over - ROUNDS_TTL) if over >= GRACE + ROUNDS_TTL else ROUNDS_TTL
                try:
                    finished = parse_pgn_rounds(get_json(f"{API}broadcast/{tour['id']}.pgn", ttl=ttl, text=True))
                except RateLimited:
                    raise
                except AdapterError:
                    continue
            for rnd in rounds:
                starts = _dt(rnd.get("startsAt"))
                # A round to start after the one before, which is on or just over, is read as one starting soon is.
                if not (rnd.get("finished") or rnd.get("ongoing") or (starts and starts <= now + timedelta(hours=12)) or next_up(rounds, rnd, now)):
                    unpaired.append(rnd)
                    continue
                here = rnd
                if rnd.get("finished") and not just_over(rnd):
                    data = {"games": finished.get(rnd["id"], [])}
                else:
                    try:
                        data = get_json(f"{API}broadcast/-/-/{rnd['id']}", ttl=60)
                    except RateLimited:
                        raise
                    except AdapterError:
                        continue
                    if isinstance(data.get("round"), dict):
                        # Whether it's on or over as its own read says, a minute old at most, not the rounds' copy.
                        here = {**{k: v for k, v in rnd.items() if k not in ("ongoing", "finished", "finishedAt")}, **data["round"]}
                read = {**data, "round": here, "tour": tour}  # a finished round's games come from the PGN, without it
                pairings.update(pairing(g) for g in read.get("games") or [])
                mine = games_of(read, ref.athlete_id)
                if mine:
                    games += round_fixtures(tour, here, read, mine, ref.athlete_id, now, self._waited(read, ref.athlete_id), next_up(rounds, rnd, now))
                elif not data.get("games") and not here.get("finished"):
                    unpaired.append(rnd)  # starting soon, pairings not out
                # else: paired without them (a bye, or not in this section)
            # Rounds not yet paired count as the player's only if they've played in this event or it names them.
            if games or names_them(tour, names):
                meeting = a_match(tour, pairings, ref.athlete_id)
                out += games + [fixture_for(tour, rnd, None, ref.athlete_id, now, upnext=next_up(rounds, rnd, now), meeting=meeting) for rnd in unpaired[:3]]
        return sorted(out, key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def history_pages(self, ref: PlayerRef, anchor: date) -> list[dict[str, Any]]:
        return []  # 60 days of broadcasts already cost a minute or two at Lichess's one request a second

    def history(self, ref: PlayerRef, page: dict[str, Any]) -> list[Fixture]:
        return []

    def live_now(self) -> list[LiveGame]:
        """The broadcasts with a round under way (broadcast/top, read every ten minutes at most), highest tier
        first and of equals in Lichess's order, and in each of the first LIVE_TOURS of those rounds, its
        strongest boards still being played (live_boards): a request a round."""
        now = datetime.now(timezone.utc)
        top = get_json(f"{API}broadcast/top", ttl=600)
        on = [e for e in top.get("active") or [] if (e.get("round") or {}).get("id") and e["round"].get("ongoing")]  # one not started yet has no boards
        on.sort(key=lambda e: -((e.get("tour") or {}).get("tier") or 0))
        out: list[LiveGame] = []
        for entry in on[:LIVE_TOURS]:
            out.extend(live_boards(get_json(f"{API}broadcast/-/-/{entry['round']['id']}", ttl=60), self.system, now))
        return out

    def spare(self) -> bool:
        """Whether a background read, or a board nobody follows a player in, leaves the minute's reserve for
        followed players' live boards."""
        return budget.spare(BUDGET)

    def locator(self, source_id: str) -> dict[str, Any] | None:
        """A game polled by its address alone ("<round id>-<FIDE id>", a mini-match's next ones "-<n>" on: fixture_id),
        as its player's fixture of it is (matches.py)."""
        found = self.fixture_pattern.fullmatch(source_id)
        return {"round": found[1], "fide": found[2], "game": int(found[3] or 1)} if found else None

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        def read(ttl: float) -> Snapshot:  # the round match() and Live now read, too
            data = get_json(f"{API}broadcast/-/-/{locator['round']}", ttl=ttl)
            # A locator from before a mini-match's games were each a fixture says no game: its first.
            return parse_board(data, locator["fide"], waited=self._waited(data, locator["fide"]), game=int(locator.get("game") or 1))

        snap = self.read_snapshot(read, final, 5)
        if snap.status == "live":
            self._moves_so_far(locator["round"], snap.state, read=False)  # one move on: added to its page's list
        return snap

    @staticmethod
    def _waited(round_data: dict[str, Any], fide_id: str) -> float | None:
        """How long ago, in seconds, the last of a player's games in a round was first seen over, with none under way
        since (last_over), where their pairing's next game may follow (may_follow): what the fixture of the one that may
        follow waits GRACE on (next_game), whatever Lichess says of the round. The first look at it over starts the
        clock (a poll of the game, or of the one to follow, a refresh's read), kept in Redis for the worker and the API
        alike. None where nothing waits, or where Lichess says the round ended GRACE ago or more (next_game reads that
        itself); with Redis down, GRACE: off now, rather than polled with no end."""
        rnd = round_data.get("round") or {}
        games = games_of(round_data, fide_id)
        last = last_over(games)
        if last is None or not last.get("id") or not may_follow(round_data, games):
            return None
        since = finished_for(rnd, datetime.now(timezone.utc))
        if since is not None and since >= GRACE:
            return None
        key, now = f"lichess:over:{last['id']}", time.time()
        try:
            r: Any = bus.sync_redis()
            first = r.get(key)
            if first is None:
                r.set(key, str(now), ex=OVER_KEEP)
            return now - float(first or now)
        except Exception:
            return GRACE

    @staticmethod
    def _moves_so_far(round_id: str, state: dict[str, Any], read: bool) -> dict[str, Any] | None:
        """A live board's moves so far (moves_to's), kept in Redis for the worker and the API alike, under its
        game: each poll of the board, and each read of its match page, that finds it a move on adds that move,
        at no request, so a game whose page someone opened is read once and kept up by every look after.
        Moves kept that can't be brought to the board (moves missed between looks: a fast exchange, time
        trouble; or a move the relay has corrected or taken back since) are dropped, by any look, so none adds to a
        list that may be wrong; with `read` (the match page's own read, at most once a minute a game while it's
        open), its PGN is read where there are none. None where there are none (a poll before anyone opened the
        page, or a PGN Lichess wouldn't give: the page shows the board alone till the next read)."""
        game_id, fen = state.get("game_id"), state.get("fen")
        if not game_id or fen_ply(fen) is None:
            return None
        key = f"lichess:moves:{game_id}"
        try:
            r: Any = bus.sync_redis()
            raw = r.get(key)
            kept = json.loads(raw) if raw else None
        except Exception:  # Redis down: a read still brings them, nothing is kept
            r, kept = None, None
        now = moves_to(kept, fen, state.get("last_move"))
        if now is None and kept is not None:
            kept = None
            try:
                r.delete(key)
            except Exception:
                pass  # Redis down: the next look drops them
        if now is None and read:
            try:
                # Without clocks, comments and side lines: a smaller answer, and not the finished game's copy
                # (match(), kept a day), which a live read's must never stand in for.
                pgn = parse_pgn_game(get_json(f"{API}study/{round_id}/{game_id}.pgn?clocks=false&comments=false&variations=false", ttl=10, text=True))
            except AdapterError as exc:  # refused or unreachable (RateLimited too): the board alone this time
                log.info("lichess: no live moves for %s/%s: %s", round_id, game_id, exc)
                return None
            read_ = {"moves": pgn["moves"], "more": [], "opening": pgn["opening"], "fen": None}
            now = moves_to(read_, fen, state.get("last_move")) or read_
        if now is not None and now != kept and r is not None:
            try:
                r.set(key, json.dumps(now), ex=LIVE_MOVES_KEEP)
            except Exception:
                pass  # Redis down: the next look reads them again
        return now

    def match(self, source_id: str) -> LiveGame | None:
        """One game by a fixture of it ("<round id>-<FIDE id>", a mini-match's next ones "-<n>" on: fixture_id),
        whoever follows it, for its match page: its position and clocks, a mini-match's game its place in the match
        and the match's score, and both players. One round request, the one Live now and a followed player's live
        poll make; while the game is on, its moves so far (_moves_so_far: its PGN once, then kept up a move at a time
        by every look at the board); and once it's over, one more for its moves (its PGN: a broadcast round is a study,
        each board a chapter). A game whose moves couldn't be read, or over with its round still on (a mini-match's
        game, whose page says which game of the match it is once the next comes), is partial: match.py reads it again
        later, and keeps it a week once the round is over (or, if Lichess never says so, once it has waited long
        enough: match.PARTIAL). None for an address that's no game (yet)."""
        at = self.locator(source_id)
        if at is None:
            return None
        round_id, fide = at["round"], at["fide"]
        data = get_json(f"{API}broadcast/-/-/{round_id}", ttl=30)
        game = parse_game(data, fide, self.system, waited=self._waited(data, fide), game=at["game"])
        state = game.snapshot.state if game else {}
        if game and game.snapshot.status == "live":
            # The moves so far, as the page's detail: it shows under whichever board is newest (match.py), a
            # followed player's poll's too. Of this game, by its id.
            moves = self._moves_so_far(round_id, state, read=True)
            if moves and (moves.get("moves") or moves.get("more")):
                state["page_detail"] = {"game_id": state.get("game_id"), "moves": moves.get("moves") or [], "more": moves.get("more") or [], "opening": moves.get("opening")}
        if game and game.snapshot.status == "final":
            game.partial = not (data.get("round") or {}).get("finished")
            try:
                if state.get("game_id"):
                    state.update(parse_pgn_game(get_json(f"{API}study/{round_id}/{state['game_id']}.pgn", ttl=24 * 3600, text=True)))
            except RateLimited:
                raise  # the read fails, and is asked for again
            except AdapterError as exc:
                game.partial = True  # the board and its result say it till the next read
                log.info("lichess: no moves for %s yet: %s", source_id, exc)
        return game

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        return result_label(fixture, team_ids)

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        record = fide_record(ref.athlete_id) or {}
        stats = [{"label": label, "value": str(record[key])} for key, label in (("standard", "Classical"), ("rapid", "Rapid"), ("blitz", "Blitz")) if record.get(key)]
        if record.get("title"):
            stats.append({"label": "Title", "value": record["title"]})
        rounds = [f.result for f, _ in recent if f.result and f.result.get("games")]
        played = sum(r["games"] for r in rounds)
        if played:
            stats.append({"label": "Recent score", "value": f"{fmt_points(sum(r['points'] for r in rounds))}/{played}"})
        note = "FIDE ratings via Lichess" + (f"; score over the last {played} broadcast game{'s' if played != 1 else ''}" if played else "")
        return stats, note
