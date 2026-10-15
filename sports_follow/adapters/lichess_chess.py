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

import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .. import naming
from ..config import USER_AGENT
from . import budget
from .base import Adapter, AdapterError, Fixture, Line, LiveSearch, PlayerRef, Provider, SearchResult, Snapshot

# With the slash: the budgets below match requests by this prefix, so every address is API + path.
API = "https://lichess.org/api/"
DAYS_BACK = 60
DAYS_AHEAD = 21
MAX_TOURS = 6  # the most recent tournaments a player is followed through
PAGE_TTL = 6 * 3600  # a player's Lichess FIDE page: their tournaments change a few times a month
STALE_PAGE = 7 * 24 * 3600  # the last copy read, while Lichess refuses a new one

_client = httpx.Client(headers={"user-agent": USER_AGENT, "accept": "application/json"}, timeout=20, follow_redirects=True)
_cache: dict[str, tuple[float, Any]] = {}
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
# Lichess counts broadcast reads in three-minute windows, so after a 429 nothing asks for three minutes.
BUDGET = budget.Budget("lichess", windows=(budget.Window(60, 120, margin=10) if token() else budget.Window(60, 10, margin=1),), urls=() if token() else (API,), pause=180)
# Lichess's FIDE player pages and its player endpoint ignore the token: one limit, about 60 requests a
# minute, holds every caller from a datacenter together, a server like ours included, so others can spend
# it (lila: Fide.scala, Limiters.enumeration.fidePlayer). Requests there are counted apart and kept few,
# and a 429 they get pauses only them, not the broadcasts live boards read.
FIDE_URLS = ("https://lichess.org/fide/", f"{API}fide/player/")
FIDE = budget.Budget("lichess-fide", windows=(budget.Window(60, 10, margin=1),), urls=FIDE_URLS)
# The research agent's requests (tools.fetch_url) carry no token, so Lichess holds them to its limit
# without one: with a token they're counted apart, and a 429 they get doesn't pause our own polls.
ANONYMOUS = budget.Budget("lichess-anonymous", windows=(budget.Window(60, 10, margin=1),), urls=(API,)) if token() else BUDGET


def _spends(url: str) -> budget.Budget:
    """The budget a request spends: FIDE's shared limit for its player pages and endpoint, else ours."""
    return FIDE if url.startswith(FIDE_URLS) else BUDGET


def _headers(url: str, text: bool) -> dict[str, str]:
    headers = {"accept": "*/*"} if text else {}
    if url.startswith("https://lichess.org/") and token():
        headers["authorization"] = f"Bearer {token()}"  # only ever sent to Lichess
    return headers


# Lichess said slow down, or the minute is spent. Unlike a missing round, this must fail the whole read: a
# fixture list with holes would drop results from the card until the next refresh.
RateLimited = budget.RateLimited


def get_json(url: str, ttl: float, text: bool = False) -> Any:
    """GET a Lichess API document (or, with text=True, a page), cached for ttl seconds."""
    now = time.time()
    hit = _cache.get(url)
    if hit and now - hit[0] < ttl:
        return hit[1]
    with _lock:
        hit = _cache.get(url)  # another thread may have fetched it while this one waited
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        spends = _spends(url)
        budget.take(spends)
        time.sleep(max(0.0, _state["last"] + SPACING - time.time()))
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
        _cache[url] = (time.time(), data)
        if len(_cache) > 1000:
            for key, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:250]:
                _cache.pop(key, None)
        return data


def peek(url: str, ttl: float) -> Any:
    """A cached response younger than ttl seconds, or None, without waiting for the request line."""
    hit = _cache.get(url)
    return hit[1] if hit and time.time() - hit[0] < ttl else None


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


def _points(result: str, white: bool) -> float:
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
        rounds.setdefault(round_id, []).append({"id": game_id, "players": players, "status": tags.get("Result", "*"), "fen": ""})
    return rounds


def games_of(round_data: dict[str, Any], fide_id: str) -> list[dict[str, Any]]:
    """The player's games in a round, in board order. Knockout rounds hold a whole mini-match
    (Esports World Cup "Grand Final": eight games between the same two players)."""
    return [g for g in round_data.get("games") or [] if any(str(p.get("fideId")) == fide_id for p in g.get("players") or [])]


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
            points += _points(result, _is_white(g, fide_id))
            decided += 1
    return points, decided


def fmt_points(x: float) -> str:
    whole = int(x)
    return f"{whole if whole or x == 0 else ''}{'½' if x % 1 else ''}"


def round_status(round_info: dict[str, Any], games: list[dict[str, Any]], now: datetime) -> str:
    if games and all(_result(g.get("status")) for g in games):
        # A single game is over when it's decided; a match only when its round is (a tiebreak can follow).
        return "final" if len(games) == 1 or round_info.get("finished") or not round_info.get("ongoing") else "live"
    if round_info.get("finished"):
        return "final" if games else "postponed"
    starts = _dt(round_info.get("startsAt"))
    if round_info.get("ongoing") or (games and starts and starts <= now):
        return "live"
    return "scheduled"


def parse_board(round_data: dict[str, Any], fide_id: str, now: datetime | None = None) -> Snapshot:
    """One player's board in a broadcast round -> snapshot: the game in play (or the last one), and
    over a multi-game round the match score. Lines for both players, keyed by FIDE id."""
    now = now or datetime.now(timezone.utc)
    rnd, tour = round_data.get("round") or {}, round_data.get("tour") or {}
    games = games_of(round_data, fide_id)
    status = round_status(rnd, games, now)
    game = current_game(games)
    if game is None:
        return Snapshot(
            status=status,
            score_label=f"{tour.get('name', '')} · {rnd.get('name', '')}",
            clock_label="Pairings not published yet" if status == "scheduled" else "Not paired this round",
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
            headline = f"{word} the match {score} vs {other['name']}" if is_match else f"{word} with {colour} vs {other['name']}"
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
            clock_label = f"{leader} won the match ({decided} games)" if leader else f"Match drawn ({decided} games)"
        else:
            clock_label = f"Game {decided + (0 if result else 1)} · move {move} · {turn.capitalize()} to play" if move and not result else f"Game {decided} done"
    elif result:
        who = white["name"] if result == "1-0" else black["name"] if result == "0-1" else None
        score_label = f"{white['name']} {result.replace('-', '–')} {black['name']}"
        clock_label = f"{who} won" if who else "Draw"
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
            "match": {"games": decided, "score": f"{fmt_points(pts)}–{fmt_points(decided - pts)}"} if is_match else None,
        },
        lines=lines,
        source_url=f"{rnd['url']}/{game['id']}" if rnd.get("url") and game.get("id") else rnd.get("url"),
    )


def fixture_for(tour: dict[str, Any], rnd: dict[str, Any], games: list[dict[str, Any]], fide_id: str, now: datetime) -> Fixture:
    """A player's fixture in one round: their game (or match) once paired, the round itself before that."""
    status = round_status(rnd, games, now)
    title = f"{tour.get('name', '')} · {rnd.get('name', '')}"
    result = None
    if games:
        white, black = [_side(p) for p in (games[0].get("players") or [{}, {}])[:2]]
        title = f"{white['name']} vs {black['name']}"
        if status == "final":
            pts, decided = match_score(games, fide_id)
            opponent = black if white["id"] == fide_id else white
            result = {"points": pts, "games": decided, "opponent": opponent["name"], "colour": ("White" if _is_white(games[0], fide_id) else "Black") if len(games) == 1 else None}
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
    )


def result_label(fixture: Fixture, team_ids: list[str]) -> str:
    """ "Won 1–0" for a game, "Won 6–2" for a match; always from the followed player's side (a chess
    fixture belongs to one player)."""
    r = fixture.result
    if not r or not r.get("games"):
        return ""
    pts, other = r["points"], r["games"] - r["points"]
    word = "Won" if pts > other else "Lost" if pts < other else "Drew"
    return f"{word} {fmt_points(pts)}–{fmt_points(other)}"


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


def recognise(query: str, adapters: list[Adapter]) -> tuple[Adapter, PlayerRef] | None:
    """A titled or 2200+ active player with this name on FIDE's list."""
    chess = adapters[0]
    try:
        ref = chess.find_player(query)
        if ref:
            record = fide_record(ref.athlete_id) or {}
            if not record.get("inactive") and (record.get("title") in CHESS_TITLES or (record.get("standard") or 0) >= 2200):
                return chess, ref
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
    budgets=(FIDE, BUDGET, ANONYMOUS),  # FIDE first: the research agent's FIDE pages spend it, not ANONYMOUS
)


# ---------------------------------------------------------------- the adapter


class LichessChess(Adapter):
    system = "lichess_chess"
    sport = "chess"
    live_cadence = 15  # moves come slower than goals; Lichess prefers light polling
    name = "Lichess"
    provider = PROVIDER
    covers = "Chess"
    # A chess player's tournaments take a dozen rate-limited Lichess reads to find; the preview shows
    # ratings only and the page finds the tournaments once followed.
    preview_fixtures = False

    def credits_also(self) -> list[dict[str, Any]]:
        return [{
            "name": "FIDE",
            "url": "https://ratings.fide.com",
            "what": "Chess titles and ratings, looked up player by player through Lichess.",
            "note": "Ratings are FIDE's.",
        }]

    def find_player(self, name: str) -> PlayerRef | None:
        # FIDE search matches the name as typed; "Gukesh Dommaraju" only finds FIDE's "Gukesh, D" by
        # its given name, so search each long part too and let pick_player decide.
        queries = [name, *[t for t in _tokens(name) if len(t) >= 4]]
        results: dict[int, dict[str, Any]] = {}
        for q in dict.fromkeys(queries):
            found = get_json(f"{API}fide/player?q={quote(q)}", ttl=24 * 3600)
            for r in found if isinstance(found, list) else []:
                results.setdefault(r["id"], r)
            if pick_player(list(results.values()), name):
                break
        best = pick_player(list(results.values()), name)
        return ref_for(best) if best else None

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        record = fide_record(athlete_id)
        return ref_for(record) if record else None

    def _page_rounds(self, ref: PlayerRef) -> list[str]:
        """A round of each of the player's "Recent tournaments" on their Lichess FIDE page, newest first.

        The page is behind the limit every datacenter shares (FIDE), so it's read a few times a day, and
        when Lichess refuses it the last copy read stands in. With none, the player's events on now and
        to come are still found by search; a past one only the page lists comes back once it's read.
        """
        if not ref.profile_url:
            return []
        try:
            page = get_json(ref.profile_url, ttl=PAGE_TTL, text=True)
        except RateLimited:
            page = peek(ref.profile_url, ttl=STALE_PAGE)
        except AdapterError:
            page = None
        return list(dict.fromkeys(re.findall(r'href="/broadcast/[^/"]+/[^/"]+/([A-Za-z0-9]{8})"', page or "")))[:MAX_TOURS]

    def _tours(self, ref: PlayerRef, now: datetime) -> list[dict[str, Any]]:
        """Broadcast tournaments the player is likely in: the "Recent tournaments" on their Lichess
        FIDE page, broadcast search on their surname, and what's running now that names them.

        The FIDE page is the only place Lichess lists a player's events; the API has no endpoint
        for it, and search misses events whose player list doesn't name them (Carlsen's Global
        Chess League final).
        """
        surname = naming.fold(ref.name).split()[-1]
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
        search = get_json(f"{API}broadcast/search?q={quote(surname)}", ttl=3600)
        for entry in search.get("currentPageResults") or []:
            found.setdefault(entry["tour"]["id"], entry["tour"])
        top = get_json(f"{API}broadcast/top", ttl=600)
        for entry in [*(top.get("active") or []), *(top.get("upcoming") or [])]:
            tour = entry["tour"]
            text = naming.fold(f"{tour.get('name', '')} {(tour.get('info') or {}).get('players', '')}")
            if surname in text.split() or surname in text.replace(",", " ").split():
                found.setdefault(tour["id"], tour)
        lo, hi = now - timedelta(days=DAYS_BACK), now + timedelta(days=DAYS_AHEAD)

        def in_window(tour: dict[str, Any]) -> bool:
            dates = [d for d in (_dt(x) for x in tour.get("dates") or []) if d]
            return bool(dates) and dates[-1] >= lo and dates[0] <= hi

        tours = sorted((t for t in found.values() if in_window(t)), key=lambda t: (t.get("dates") or [0])[0], reverse=True)
        return tours[:MAX_TOURS]

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        now = datetime.now(timezone.utc)
        surname = naming.fold(ref.name).split()[-1]
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
                if not (rnd.get("finished") or rnd.get("ongoing") or (starts and starts <= now + timedelta(hours=12))):
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
                mine = games_of(data, ref.athlete_id)
                if mine:
                    games.append(fixture_for(tour, rnd, mine, ref.athlete_id, now))
                elif not data.get("games") and not rnd.get("finished"):
                    unpaired.append(rnd)  # starting soon, pairings not out
                # else: paired without them (a bye, or not in this section)
            # Rounds not yet paired count as the player's only if they've played in this event or it lists them.
            listed = surname in naming.fold((tour.get("info") or {}).get("players", "")).replace(",", " ").split()
            if games or listed:
                out += games + [fixture_for(tour, rnd, [], ref.athlete_id, now) for rnd in unpaired[:3]]
        return sorted(out, key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def history_pages(self, ref: PlayerRef, anchor: date) -> list[dict[str, Any]]:
        return []  # 60 days of broadcasts already cost a minute or two at Lichess's one request a second

    def history(self, ref: PlayerRef, page: dict[str, Any]) -> list[Fixture]:
        return []

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        data = get_json(f"{API}broadcast/-/-/{locator['round']}", ttl=24 * 3600 if final else 5)
        return parse_board(data, locator["fide"])

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
