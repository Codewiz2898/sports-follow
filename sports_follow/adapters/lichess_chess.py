"""Chess from Lichess: FIDE players, over-the-board broadcasts, live boards, FIDE ratings.

Lichess relays most elite and many open tournaments as broadcasts, through a public, documented
API (https://lichess.org/api). Players are identified by FIDE id. A player's tournaments are
found with broadcast search on their surname plus the broadcasts running now; a "fixture" is one
round of one tournament for one player, and its snapshot is that player's board in that round.

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
# A player's one game over in a round still on, where their pairing's next game may follow it (may_follow): the next
# game's chapter shows only as it starts, a break of ten or twenty minutes in a tiebreak or before an armageddon, so the
# board waits this long for it before it's over, where Lichess doesn't say the round is first.
GRACE = 30 * 60
OVER_KEEP = 24 * 3600  # when such a game was first seen over, kept in Redis this long
# A round set to start after the one before it (no start time of its own: a blitz day's rounds) is next while that one
# is on, and this long after it ended: its fixture says so (Fixture.next_up), and it's armed and polled till it starts.
AFTER_PREVIOUS = timedelta(hours=3)
# Words a knockout's event or round goes by ("8-player Knockout", "Tiebreaks", "Armageddon", "Playoffs").
KNOCKOUT = re.compile(r"\b(?:knock-?outs?|ko|tie-?breaks?|tie breaks?|armageddon|play-?offs?)\b", re.I)
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
# chess searches, previews and follows take at most half of it, so the boards keep the rest whatever fans do.
BUDGET = budget.Budget("lichess", windows=(budget.Window(60, 120, margin=10) if token() else budget.Window(60, 10, margin=1),), urls=() if token() else (API,), pause=180,
                       reserve=30 if token() else 3, fan=15 if token() else 1)
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


def _pgn_player(tags: dict[str, str], colour: str) -> dict[str, Any]:
    rating = tags.get(f"{colour}Elo")
    return {"name": tags.get(colour, ""), "fideId": int(tags[f"{colour}FideId"]) if (tags.get(f"{colour}FideId") or "").isdigit() else None,
            "rating": int(rating) if (rating or "").isdigit() else None, "title": tags.get(f"{colour}Title")}


def parse_pgn_rounds(pgn: str) -> dict[str, list[dict[str, Any]]]:
    """A tournament's PGN export -> its games grouped by round id, shaped like the round JSON's games.

    One request for a whole event, instead of one per round: Lichess rate-limits round reads hard.
    Each game's GameURL ends in /{roundId}/{gameId}.
    """
    rounds: dict[str, list[dict[str, Any]]] = {}
    for block in re.finditer(rf"(?:^{_TAG}\n?)+", pgn, flags=re.M):
        tags = {name: re.sub(r"\\(.)", r"\1", value) for name, value in re.findall(_TAG, block[0])}
        parts = (tags.get("GameURL") or "").rstrip("/").split("/")
        if len(parts) < 2:
            continue
        round_id, game_id = parts[-2], parts[-1]
        players = [_pgn_player(tags, "White"), _pgn_player(tags, "Black")]
        game = {"id": game_id, "players": players, "status": tags.get("Result", "*"), "fen": ""}
        if ARMAGEDDON.search(" ".join(tags.get(k) or "" for k in ("Event", "ChapterName"))):
            game["armageddon"] = True
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
    board gives its last one, "d5e3"; "at": the board's last move when they last reached it) brought to its
    position: as they are when they reach it, with its last move added when they're one short, or as they are
    when they're ahead of it (a look at an older copy of the round than the one they were kept up by). None when
    they can't be: none kept, a move or more missed in between, or the board's last move not the one they
    reached it with (a relay correcting a move): its PGN is read again."""
    ply = fen_ply(fen)
    if kept is None or ply is None:
        return None
    have = moves_ply(kept.get("moves") or []) + len(kept.get("more") or [])
    if have > ply:
        return kept
    if have == ply:
        at = kept.get("at")
        if at is not None and last_move and at != last_move:
            return None
        return kept if at == last_move or not last_move else {**kept, "at": last_move}
    if have + 1 == ply and last_move:
        return {**kept, "more": [*(kept.get("more") or []), last_move], "at": last_move}
    return None


def games_of(round_data: dict[str, Any], fide_id: str) -> list[dict[str, Any]]:
    """The player's games in a round, in board order, an armageddon marked so (its "armageddon"). Knockout rounds hold
    a whole mini-match (Esports World Cup "Grand Final": eight games between the same two players)."""
    return [{**g, "armageddon": True} if armageddon(round_data, g) else g
            for g in round_data.get("games") or [] if any(str(p.get("fideId")) == fide_id for p in g.get("players") or [])]


def armageddon(round_data: dict[str, Any], game: dict[str, Any]) -> bool:
    """Whether a game is an armageddon, where a draw is Black's win: its chapter's name or its round's says so
    (Norway Chess's armageddon after a classical draw, a knockout's tiebreak), so does its event's in a tournament's
    PGN (parse_pgn_rounds), or its round is scored so (Lichess's custom scoring: a draw worth more to Black)."""
    rnd = round_data.get("round") or {}
    if game.get("armageddon") or ARMAGEDDON.search(f"{game.get('name') or ''} {rnd.get('name') or ''}"):
        return True
    white, black = (((rnd.get("customScoring") or {}).get(side) or {}).get("draw") for side in ("white", "black"))
    return isinstance(white, (int, float)) and isinstance(black, (int, float)) and black > white


def _decided_on_armageddon(games: list[dict[str, Any]]) -> bool:
    """Whether an armageddon among these games has its result: what decided them."""
    return any(g.get("armageddon") and _result(g.get("status")) for g in games)


def current_game(games: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The game in play, else the last one."""
    return next((g for g in games if not _result(g.get("status"))), games[-1] if games else None)


def _is_white(game: dict[str, Any], fide_id: str) -> bool:
    return str(((game.get("players") or [{}])[0]).get("fideId")) == fide_id


def match_score(games: list[dict[str, Any]], fide_id: str) -> tuple[float, int]:
    """(points, decided games) for the player over their games in a round."""
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


def may_follow(round_data: dict[str, Any], games: list[dict[str, Any]]) -> bool:
    """Whether a player's game in a round may be followed by another with the same opponent in it: a mini-match's next
    game, a tiebreak, an armageddon, each shown only once the one before is over. So in a knockout's round (its event's
    or round's name, or Lichess's format for the event, says so; or a pairing in it already has several games) and in a
    round of their pairing alone (a match's: a World Championship's tiebreak); not in a round robin's or a Swiss's."""
    rnd, tour = round_data.get("round") or {}, round_data.get("tour") or {}
    if KNOCKOUT.search(" ".join(str(x or "") for x in (tour.get("name"), rnd.get("name"), (tour.get("info") or {}).get("format")))):
        return True
    pairs: dict[frozenset[str], int] = {}
    for g in round_data.get("games") or games:
        pair = frozenset(str(p.get("fideId")) for p in g.get("players") or [])
        pairs[pair] = pairs.get(pair, 0) + 1
    return len(pairs) <= 1 or max(pairs.values()) > 1


def round_status(round_info: dict[str, Any], games: list[dict[str, Any]], now: datetime, follows: bool = False, waited: float | None = None) -> str:
    """A player's board in a round: scheduled, live, final, or postponed. Their games all decided, it's over once the
    round is, as a mini-match is (a tiebreak can follow); one game, at once, unless its pairing's next may follow it
    (`follows`: may_follow): then GRACE after it was first seen over (`waited`, in seconds; None for not known yet)."""
    if games and all(_result(g.get("status")) for g in games):
        if round_info.get("finished") or not round_info.get("ongoing"):
            return "final"
        if len(games) > 1 or (follows and (waited is None or waited < GRACE)):
            return "live"
        return "final"
    if round_info.get("finished"):
        return "final" if games else "postponed"
    starts = _dt(round_info.get("startsAt"))
    if games and (round_info.get("ongoing") or (starts and starts <= now)):
        return "live"
    return "scheduled"  # a round on without a board of theirs (yet): their game hasn't started


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
    LIVE_RATING or more, each a game with both players."""
    now = now or datetime.now(timezone.utc)
    rnd, tour = round_data.get("round") or {}, round_data.get("tour") or {}
    playing = [g for g in round_data.get("games") or [] if g.get("status") == "*" and len(g.get("players") or []) >= 2 and all(p.get("fideId") for p in g["players"][:2])
               and sum(p.get("rating") or 0 for p in g["players"][:2]) >= 2 * LIVE_RATING]
    playing.sort(key=lambda g: -sum(p.get("rating") or 0 for p in g["players"][:2]))
    out = []
    for g in playing[:LIVE_BOARDS]:
        sides = g["players"][:2]
        snap = parse_board(round_data, str(sides[0]["fideId"]), now)
        if snap.status != "live":
            continue
        refs = [PlayerRef(system=system, athlete_id=str(p["fideId"]), name=display_name(p.get("name") or ""), team_ids=[str(p["fideId"])], team_names=[display_name(p.get("name") or "")]) for p in sides]
        out.append(LiveGame(
            source_id=f"{rnd.get('id')}:{g.get('id')}",
            title=f"{refs[0].name} v {refs[1].name}",
            competition=" · ".join(x for x in (tour.get("name"), rnd.get("name")) if x),
            start_utc=_dt(rnd.get("startsAt")),
            snapshot=snap,
            players=refs,
            fixtures=[f"{rnd.get('id')}-{p.athlete_id}" for p in refs],  # as fixture_for names each player's
        ))
    return out


def parse_game(round_data: dict[str, Any], fide_id: str, system: str, now: datetime | None = None, waited: float | None = None) -> LiveGame | None:
    """A broadcast round -> one player's board in it as a game for its match page, with both players and the
    fixture each has it under; None when they aren't paired in it. `waited` as parse_board's."""
    rnd, tour = round_data.get("round") or {}, round_data.get("tour") or {}
    if not rnd.get("id") or not games_of(round_data, fide_id):
        return None
    snap = parse_board(round_data, fide_id, now, waited)
    refs = [PlayerRef(system=system, athlete_id=s["id"], name=s["name"], team_ids=[s["id"]], team_names=[s["name"]]) for s in (snap.state.get("white"), snap.state.get("black")) if s and s.get("id")]
    return LiveGame(
        source_id=f"{rnd['id']}-{fide_id}",
        title=" v ".join(r.name for r in refs),
        competition=" · ".join(x for x in (tour.get("name"), rnd.get("name")) if x),
        start_utc=_dt(rnd.get("startsAt")),
        snapshot=snap,
        players=refs,
        fixtures=[f"{rnd['id']}-{r.athlete_id}" for r in refs],  # as fixture_for names each player's
    )


def parse_board(round_data: dict[str, Any], fide_id: str, now: datetime | None = None, waited: float | None = None) -> Snapshot:
    """One player's board in a broadcast round -> snapshot: the game in play (or the last one), and
    over a multi-game round the match score, who's ahead in it and its first game (the board its
    questions were asked of: Chess.question_board). Lines for both players, keyed by FIDE id. A
    game over whose pairing's next may follow it in the round is live till GRACE after it was
    first seen over (`waited`: LichessChess._waited) or the round is (round_status)."""
    now = now or datetime.now(timezone.utc)
    rnd, tour = round_data.get("round") or {}, round_data.get("tour") or {}
    games = games_of(round_data, fide_id)
    status = round_status(rnd, games, now, may_follow(round_data, games), waited)
    game = current_game(games)
    if game is None:
        return Snapshot(
            status=status,
            score_label=f"{tour.get('name', '')} · {rnd.get('name', '')}",
            clock_label="Pairings not published yet" if status == "scheduled" and not round_data.get("games") else "Not paired this round",
            state={"kind": "chess", "round": rnd.get("name"), "tournament": tour.get("name")},
            source_url=rnd.get("url"),
        )
    white, black = [_side(p) for p in (game.get("players") or [{}, {}])[:2]]
    fen = game.get("fen") or ""
    fields = fen.split()
    turn = "white" if len(fields) < 2 or fields[1] == "w" else "black"
    move = int(fields[5]) if len(fields) > 5 and fields[5].isdigit() else None
    result = _result(game.get("status"))
    is_match = len(games) > 1
    decider = " on armageddon" if status == "final" and _decided_on_armageddon(games) else ""  # where a draw was Black's win

    lines = {}
    for me, other, colour in ((white, black, "White"), (black, white, "Black")):
        if not me["id"]:
            continue
        pts, decided = match_score(games, me["id"])
        score = f"{fmt_points(pts)}–{fmt_points(decided - pts)}"
        stats = [{"label": "Colour", "value": colour}, {"label": "Opponent", "value": other["name"]}]
        if other.get("rating"):
            stats.append({"label": "Opp. rating", "value": str(other["rating"])})
        if is_match:
            stats.append({"label": "Match", "value": score})
        if status == "final":
            word = "Won" if pts * 2 > decided else "Lost" if pts * 2 < decided else "Drew"
            headline = (f"{word} the match {score} vs {other['name']}" if is_match else f"{word} with {colour} vs {other['name']}") + decider
        else:
            headline = f"{colour} vs {other['name']}" + (f" · game {decided + 1}, match {score}" if is_match and not result else "")
            if me.get("clock") and not result:
                stats.append({"label": "Clock", "value": me["clock"]})
        lines[me["id"]] = Line(headline=headline, stats=stats)

    me_side, opp_side = (white, black) if _is_white(game, fide_id) else (black, white)
    pts, decided = match_score(games, fide_id)
    if is_match:
        score_label = f"{me_side['name']} {fmt_points(pts)}–{fmt_points(decided - pts)} {opp_side['name']}"
        if status == "final":
            leader = me_side["name"] if pts * 2 > decided else opp_side["name"] if pts * 2 < decided else None
            won = "won on armageddon" if decider else "won the match"
            clock_label = f"{leader} {won} ({decided} games)" if leader else f"Match drawn ({decided} games)"
        else:
            clock_label = f"Game {decided + (0 if result else 1)} · move {move} · {turn.capitalize()} to play" if move and not result else f"Game {decided} done"
    elif result:
        who = white["name"] if result == "1-0" else black["name"] if result == "0-1" or game.get("armageddon") else None
        score_label = f"{white['name']} {result.replace('-', '–')} {black['name']}"
        clock_label = f"{who} won{' on armageddon' if game.get('armageddon') else ''}" if who else "Draw"
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
            "game_id": game.get("id"),
            "white": white,
            "black": black,
            "fen": fen,
            "last_move": game.get("lastMove"),
            "turn": turn,
            "move": move,
            "result": result,
            **({"armageddon": True} if game.get("armageddon") else {}),  # a draw is Black's win (Chess.answer)
            "match": {"games": decided, "score": f"{fmt_points(pts)}–{fmt_points(decided - pts)}", "first": str(games[0].get("id") or ""),
                      "ahead": me_side["id"] if pts * 2 > decided else opp_side["id"] if pts * 2 < decided else None} if is_match else None,
        },
        lines=lines,
        source_url=f"{rnd['url']}/{game['id']}" if rnd.get("url") and game.get("id") else rnd.get("url"),
    )


def fixture_for(tour: dict[str, Any], rnd: dict[str, Any], games: list[dict[str, Any]], fide_id: str, now: datetime,
                follows: bool = False, waited: float | None = None, upnext: bool = False) -> Fixture:
    """A player's fixture in one round: their game (or match) once paired, the round itself before that.
    `follows` and `waited` as round_status's: a refresh calls a game over no sooner than its polls do; `upnext`, a
    round with no start time of its own that's next (next_up)."""
    status = round_status(rnd, games, now, follows, waited)
    title = f"{tour.get('name', '')} · {rnd.get('name', '')}"
    result = None
    if games:
        white, black = [_side(p) for p in (games[0].get("players") or [{}, {}])[:2]]
        title = f"{white['name']} vs {black['name']}"
        if status == "final":
            pts, decided = match_score(games, fide_id)
            opponent = black if white["id"] == fide_id else white
            result = {"points": pts, "games": decided, "opponent": opponent["name"], "colour": ("White" if _is_white(games[0], fide_id) else "Black") if len(games) == 1 else None,
                      **({"armageddon": True} if _decided_on_armageddon(games) else {})}
    return Fixture(
        source_id=f"{rnd['id']}-{fide_id}",
        title=title,
        competition=tour.get("name") or "",
        start_utc=_dt(rnd.get("startsAt")),
        venue=(tour.get("info") or {}).get("location"),
        status=status,
        team_ids=[fide_id],
        team_names=[],
        locator={"round": rnd["id"], "fide": fide_id},
        detail=rnd.get("name") if games else "Pairings not published yet",
        result=result,
        board=str((current_game(games) or {}).get("id") or ""),  # the game parse_board will show, under each player's fixture
        next_up=upnext,
    )


def result_label(fixture: Fixture, team_ids: list[str]) -> str:
    """ "Won 1–0" for a game, "Won 6–2" for a match; always from the followed player's side (a chess
    fixture belongs to one player)."""
    r = fixture.result
    if not r or not r.get("games"):
        return ""
    pts, other = r["points"], r["games"] - r["points"]
    word = "Won" if pts > other else "Lost" if pts < other else "Drew"
    return f"{word} {fmt_points(pts)}–{fmt_points(other)}" + (" on armageddon" if r.get("armageddon") else "")


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
    fixture_pattern = re.compile(r"[A-Za-z0-9]{8}-[0-9]{1,12}")  # a player's board in a round: "<round id>-<FIDE id>"

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
                rounds = get_json(f"{API}broadcast/{tour['id']}", ttl=600).get("rounds") or []
            except RateLimited:
                raise
            except AdapterError:
                continue
            games: list[Fixture] = []
            unpaired: list[dict[str, Any]] = []
            finished = {}
            if any(r.get("finished") for r in rounds):
                done = all(r.get("finished") for r in rounds)
                try:
                    finished = parse_pgn_rounds(get_json(f"{API}broadcast/{tour['id']}.pgn", ttl=24 * 3600 if done else 600, text=True))
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
                if rnd.get("finished"):
                    data = {"games": finished.get(rnd["id"], [])}
                else:
                    try:
                        data = get_json(f"{API}broadcast/-/-/{rnd['id']}", ttl=60)
                    except RateLimited:
                        raise
                    except AdapterError:
                        continue
                read = {**data, "round": rnd, "tour": tour}  # a finished round's games come from the PGN, without it
                mine = games_of(read, ref.athlete_id)
                if mine:
                    games.append(fixture_for(tour, rnd, mine, ref.athlete_id, now, may_follow(read, mine), self._waited(read, ref.athlete_id), next_up(rounds, rnd, now)))
                elif not data.get("games") and not rnd.get("finished"):
                    unpaired.append(rnd)  # starting soon, pairings not out
                # else: paired without them (a bye, or not in this section)
            # Rounds not yet paired count as the player's only if they've played in this event or it names them.
            if games or names_them(tour, names):
                out += games + [fixture_for(tour, rnd, [], ref.athlete_id, now, upnext=next_up(rounds, rnd, now)) for rnd in unpaired[:3]]
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
        """A board polled by its address alone ("<round id>-<FIDE id>"), as its player's fixture of it is (matches.py)."""
        round_id, _, fide = source_id.partition("-")
        return {"round": round_id, "fide": fide} if self.fixture_pattern.fullmatch(source_id) else None

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        def read(ttl: float) -> Snapshot:  # the round match() and Live now read, too
            data = get_json(f"{API}broadcast/-/-/{locator['round']}", ttl=ttl)
            return parse_board(data, locator["fide"], waited=self._waited(data, locator["fide"]))

        snap = self.read_snapshot(read, final, 5)
        if snap.status == "live":
            self._moves_so_far(locator["round"], snap.state, read=False)  # one move on: added to its page's list
        return snap

    @staticmethod
    def _waited(round_data: dict[str, Any], fide_id: str) -> float | None:
        """How long ago, in seconds, a player's one game in a round still on was first seen over, where their pairing's
        next game may follow it (may_follow): what round_status waits GRACE on. The first look at it over starts the
        clock, kept in Redis for the worker and the API alike. None where nothing waits; with Redis down, GRACE: the
        game's over now, as before, rather than polled with no end."""
        rnd = round_data.get("round") or {}
        games = games_of(round_data, fide_id)
        if len(games) != 1 or not games[0].get("id") or not _result(games[0].get("status")) or rnd.get("finished") or not rnd.get("ongoing"):
            return None
        if not may_follow(round_data, games):
            return None
        key, now = f"lichess:over:{games[0]['id']}", time.time()
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
        With `read` (the match page's own read, at most once a minute a game while it's open), its PGN is read
        where they can't be brought to the board (none kept yet, or moves missed between looks: a fast
        exchange, time trouble). None where there are none (a poll before anyone opened the page, or a PGN
        Lichess wouldn't give: the page shows the board alone till the next read)."""
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
        if now is None and read:
            try:
                # Without clocks, comments and side lines: a smaller answer, and not the finished game's copy
                # (match(), kept a day), which a live read's must never stand in for.
                pgn = parse_pgn_game(get_json(f"{API}study/{round_id}/{game_id}.pgn?clocks=false&comments=false&variations=false", ttl=10, text=True))
            except AdapterError as exc:  # refused or unreachable (RateLimited too): the board alone this time
                log.info("lichess: no live moves for %s/%s: %s", round_id, game_id, exc)
                return kept
            read_ = {"moves": pgn["moves"], "more": [], "opening": pgn["opening"], "at": None}
            now = moves_to(read_, fen, state.get("last_move")) or read_
        if now is not None and now != kept and r is not None:
            try:
                r.set(key, json.dumps(now), ex=LIVE_MOVES_KEEP)
            except Exception:
                pass  # Redis down: the next look reads them again
        return now if now is not None else kept

    def match(self, source_id: str) -> LiveGame | None:
        """One board by a fixture of it ("<round id>-<FIDE id>", one player's game or mini-match in a round),
        whoever follows it, for its match page: its position, clocks and match score, and both players. One
        round request, the one Live now and a followed player's live poll make; while the game is on, its moves
        so far (_moves_so_far: its PGN once, then kept up a move at a time by every look at the board); and once
        it's over, one more for its moves (its PGN: a broadcast round is a study, each board a chapter). A game
        over that its pairing's next may follow is live till GRACE after it was first seen over (_waited); a board
        whose moves couldn't be read, or over with its round still on, is partial: match.py reads it again later,
        and keeps it a week once the round is over (or, if Lichess never says so, once it has waited long enough:
        match.PARTIAL)."""
        round_id, _, fide = source_id.partition("-")
        data = get_json(f"{API}broadcast/-/-/{round_id}", ttl=30)
        game = parse_game(data, fide, self.system, waited=self._waited(data, fide))
        state = game.snapshot.state if game else {}
        if game and game.snapshot.status == "live":
            # The moves so far, as the page's detail: it shows under whichever board is newest (match.py), a
            # followed player's poll's too.
            # Of this game: a mini-match's next game is another, and a newer board of it mustn't take these.
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
