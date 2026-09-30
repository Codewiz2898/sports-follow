"""Chess from Lichess: FIDE players, over-the-board broadcasts, live boards, FIDE ratings.

Lichess relays most elite and many open tournaments as broadcasts, through a public, documented
API (https://lichess.org/api). Players are identified by FIDE id. A player's tournaments are
found with broadcast search on their surname plus the broadcasts running now; a "fixture" is one
round of one tournament for one player, and its snapshot is that player's board in that round.

Lichess asks API clients to make one request at a time and to back off for a minute after a 429,
so requests here are serialized and a 429 pauses this adapter.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from . import espn
from .base import AdapterError, Fixture, Line, PlayerRef, Snapshot

API = "https://lichess.org/api"
DAYS_BACK = 60
DAYS_AHEAD = 21
MAX_TOURS = 6  # the most recent tournaments a player is followed through

_client = httpx.Client(headers={"user-agent": espn.USER_AGENT, "accept": "application/json"}, timeout=20, follow_redirects=True)
_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()  # one request at a time, as Lichess asks
_state = {"last": 0.0, "paused_until": 0.0}
SPACING = 1.0  # seconds between requests; bursts of ~25 at 0.3 s drew a 429


class RateLimited(AdapterError):
    """Lichess said slow down. Unlike a missing round, this must fail the whole read: a fixture
    list with holes would drop results from the card until the next refresh."""


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
        if time.time() < _state["paused_until"]:
            raise RateLimited("Lichess asked us to slow down; paused for a minute")
        time.sleep(max(0.0, _state["last"] + SPACING - time.time()))
        try:
            res = _client.get(url, headers={"accept": "*/*"} if text else None)
        except httpx.HTTPError as exc:
            raise AdapterError(f"Lichess unreachable: {exc}") from exc
        finally:
            _state["last"] = time.time()
        if res.status_code == 429:
            _state["paused_until"] = time.time() + 60
            raise RateLimited("Lichess rate limit (429); paused for a minute")
        if res.status_code != 200:
            raise AdapterError(f"Lichess returned HTTP {res.status_code} for {url}")
        data = res.text if text else res.json()
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
    return [t for t in re.split(r"[^a-z]+", espn.fold(name)) if t]


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


def ref_for(record: dict[str, Any]) -> PlayerRef:
    """A FIDE record (search hit or /fide/player/{id}) as a PlayerRef; the player is their own "team"."""
    fide_id = str(record["id"])
    full = display_name(record["name"])
    slug = record["name"].replace(", ", "_").replace(" ", "_")
    return PlayerRef("lichess_chess", fide_id, full, [fide_id], [full], f"https://lichess.org/fide/{fide_id}/{quote(slug)}")


def parse_pgn_rounds(pgn: str) -> dict[str, list[dict[str, Any]]]:
    """A tournament's PGN export -> its games grouped by round id, shaped like the round JSON's games.

    One request for a whole event, instead of one per round: Lichess rate-limits round reads hard.
    Each game's GameURL ends in /{roundId}/{gameId}.
    """
    rounds: dict[str, list[dict[str, Any]]] = {}
    for block in re.findall(r'(?:^\[\w+ "[^"]*"\]\n?)+', pgn, flags=re.M):
        tags = dict(re.findall(r'\[(\w+) "([^"]*)"\]', block))
        parts = (tags.get("GameURL") or "").rstrip("/").split("/")
        if len(parts) < 2:
            continue
        round_id, game_id = parts[-2], parts[-1]

        def player(colour: str) -> dict[str, Any]:
            rating = tags.get(f"{colour}Elo")
            return {"name": tags.get(colour, ""), "fideId": int(tags[f"{colour}FideId"]) if (tags.get(f"{colour}FideId") or "").isdigit() else None,
                    "rating": int(rating) if (rating or "").isdigit() else None, "title": tags.get(f"{colour}Title")}

        rounds.setdefault(round_id, []).append({"id": game_id, "players": [player("White"), player("Black")], "status": tags.get("Result", "*"), "fen": ""})
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


# ---------------------------------------------------------------- the adapter


class LichessChess:
    system = "lichess_chess"
    sports = ("chess",)
    live_cadence = 15  # moves come slower than goals; Lichess prefers light polling

    def find_player(self, name: str) -> PlayerRef | None:
        # FIDE search matches the name as typed; "Gukesh Dommaraju" only finds FIDE's "Gukesh, D" by
        # its given name, so search each long part too and let pick_player decide.
        queries = [name, *[t for t in _tokens(name) if len(t) >= 4]]
        results: dict[int, dict[str, Any]] = {}
        for q in dict.fromkeys(queries):
            found = get_json(f"{API}/fide/player?q={quote(q)}", ttl=24 * 3600)
            for r in found if isinstance(found, list) else []:
                results.setdefault(r["id"], r)
            if pick_player(list(results.values()), name):
                break
        best = pick_player(list(results.values()), name)
        return ref_for(best) if best else None

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        record = get_json(f"{API}/fide/player/{quote(athlete_id)}", ttl=12 * 3600)
        return ref_for(record) if isinstance(record, dict) and record.get("id") else None

    def _tours(self, ref: PlayerRef, now: datetime) -> list[dict[str, Any]]:
        """Broadcast tournaments the player is likely in: the "Recent tournaments" on their Lichess
        FIDE page, broadcast search on their surname, and what's running now that names them.

        The FIDE page is the only place Lichess lists a player's events; the API has no endpoint
        for it, and search misses events whose player list doesn't name them (Carlsen's Global
        Chess League final).
        """
        surname = espn.fold(ref.name).split()[-1]
        found: dict[str, dict[str, Any]] = {}
        if ref.profile_url:
            try:
                page = get_json(ref.profile_url, ttl=3600, text=True)
                for round_id in list(dict.fromkeys(re.findall(r'href="/broadcast/[^/"]+/[^/"]+/([A-Za-z0-9]{8})"', page)))[:MAX_TOURS]:
                    tour = get_json(f"{API}/broadcast/-/-/{round_id}", ttl=24 * 3600).get("tour") or {}
                    if tour.get("id"):
                        found.setdefault(tour["id"], tour)
            except RateLimited:
                raise
            except AdapterError:
                pass  # search below still finds the well-known events
        search = get_json(f"{API}/broadcast/search?q={quote(surname)}", ttl=3600)
        for entry in search.get("currentPageResults") or []:
            found.setdefault(entry["tour"]["id"], entry["tour"])
        top = get_json(f"{API}/broadcast/top", ttl=600)
        for entry in [*(top.get("active") or []), *(top.get("upcoming") or [])]:
            tour = entry["tour"]
            text = espn.fold(f"{tour.get('name', '')} {(tour.get('info') or {}).get('players', '')}")
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
        surname = espn.fold(ref.name).split()[-1]
        out: list[Fixture] = []
        for tour in self._tours(ref, now):
            try:
                rounds = get_json(f"{API}/broadcast/{tour['id']}", ttl=600).get("rounds") or []
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
                    finished = parse_pgn_rounds(get_json(f"{API}/broadcast/{tour['id']}.pgn", ttl=24 * 3600 if done else 600, text=True))
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
                        data = get_json(f"{API}/broadcast/-/-/{rnd['id']}", ttl=60)
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
            listed = surname in espn.fold((tour.get("info") or {}).get("players", "")).replace(",", " ").split()
            if games or listed:
                out += games + [fixture_for(tour, rnd, [], ref.athlete_id, now) for rnd in unpaired[:3]]
        return sorted(out, key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def history_pages(self, ref: PlayerRef, anchor: date) -> list[dict[str, Any]]:
        return []  # 60 days of broadcasts already cost a minute or two at Lichess's one request a second

    def history(self, ref: PlayerRef, page: dict[str, Any]) -> list[Fixture]:
        return []

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        data = get_json(f"{API}/broadcast/-/-/{locator['round']}", ttl=24 * 3600 if final else 5)
        return parse_board(data, locator["fide"])

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        return result_label(fixture, team_ids)

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        record = get_json(f"{API}/fide/player/{ref.athlete_id}", ttl=12 * 3600)
        stats = [{"label": label, "value": str(record[key])} for key, label in (("standard", "Classical"), ("rapid", "Rapid"), ("blitz", "Blitz")) if record.get(key)]
        if record.get("title"):
            stats.append({"label": "Title", "value": record["title"]})
        rounds = [f.result for f, _ in recent if f.result and f.result.get("games")]
        played = sum(r["games"] for r in rounds)
        if played:
            stats.append({"label": "Recent score", "value": f"{fmt_points(sum(r['points'] for r in rounds))}/{played}"})
        note = "FIDE ratings via Lichess" + (f"; score over the last {played} broadcast game{'s' if played != 1 else ''}" if played else "")
        return stats, note
