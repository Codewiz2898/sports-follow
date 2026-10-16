"""Battle royale esports from Liquipedia: BGMI and PUBG Mobile, Free Fire and Apex Legends, each a sport of its
own (sports/esports.py). (PUBG itself comes from PUBG's own API, with every player's numbers: pubg_esports.)

A battle royale "match" on Liquipedia is a lobby, not two teams: 12 to 24 teams play several games
(maps) in a row, each team scoring points for its placement and its finishes (kills) in every game,
and the lobby's standings are the totals. One lobby (a match2 record: a day of a group, say) is one
event here. Its state is the standings and each game's winner, and moments are told from the
player's team's side: a chicken dinner (Booyah in Free Fire), each game's placement, going top, and
where the team finished.

Liquipedia's editors record results during and after play, so a lobby is read every few minutes,
not second by second, and all running lobbies are read in one request (structured.poll_lane): the
free key allows 60 requests an hour.

Players are keyed "<wiki>:<page name>" ("pubgmobile:Jonathan"): one adapter reads every battle royale
wiki, and a page name is unique only within its wiki. A team is its page name as MediaWiki keys it
(underscores, a capital first letter). Match records name a team as their wiki wrote it, with spaces or
underscores, so a team's lobbies are asked for in each spelling, as Liquipedia's own MatchTicker does.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from .. import naming, sports
from ..sports import esports
from . import liquipedia
from .base import Adapter, AdapterError, Fixture, Line, PlayerRef, Roster, Rostered, Snapshot

# The battle royale wikis read, and what fans call a game won on each. All store lobbies the same way
# (Lua-Modules: each wiki's MatchGroup/Input/Custom.lua hands them to standardProcessFfaMaps).
log = logging.getLogger("sports_follow.esports")

# Each wiki's game, as the sport it is (sports/esports.py); an Indian player on the PUBG Mobile wiki plays BGMI.
WIKIS = {"pubgmobile": "pubgmobile", "freefire": "freefire", "apexlegends": "apex"}
INDIA = {"pubgmobile": "bgmi"}
WIN_WORD = {"pubgmobile": "Chicken dinner", "freefire": "Booyah", "apexlegends": "Champions"}
# A match's own game field, where a wiki holds more than one ("bgmi" events on the PUBG Mobile wiki).
GAME_FIELD = {"bgmi": "bgmi", "pubgm": "pubgmobile", "pubgmobile": "pubgmobile", "freefire": "freefire", "ffmax": "freefire"}
SHORT = {"pubgmobile": "pubgm", "freefire": "ff", "apexlegends": "apex"}  # registry keys: "lp:pubgm:<page id>"
IMPORT_WIKIS = [w for w in os.environ.get("SPORTS_FOLLOW_LIQUIPEDIA_WIKIS", ",".join(WIKIS)).split(",") if w in WIKIS]
NO_DATE = ("1970-01-01", "0000-01-01")  # Liquipedia's placeholders for a date nobody entered
LIVE_FOR = timedelta(hours=12)  # a lobby not marked finished this long after its start is taken as over
# A lobby entered without a time is stored at midnight UTC of its day, dateexact false. Liquipedia waits
# the whole day before taking one as over; it may be played that evening anywhere, so it gets the day too.
LIVE_FOR_INEXACT = timedelta(days=1) + LIVE_FOR


def team_id(name: str | None) -> str:
    """A team page's key, however a record spells it: "GodLike Esports" and "godLike_Esports" are GodLike_Esports."""
    page = "_".join((name or "").replace("_", " ").split())
    return page[:1].upper() + page[1:]


def team_pages(team: str) -> list[str]:
    """The ways a match record may name a team page: spaces or underscores, the first letter as the team
    template wrote it (it may be lower case) or as MediaWiki keys it."""
    page = team_id(team)
    return list(dict.fromkeys(f for p in (page, page[:1].lower() + page[1:]) for f in (p.replace("_", " "), p)))


def team_name(page: str | None) -> str:
    return (page or "").replace("_", " ").strip()


def _label(sport: str | None) -> str | None:
    return sports.BY_KEY[sport].label if sport else None


def game_name(wiki: str | None, game: str | None = None) -> str | None:
    """A lobby's game as the app names it: its own game field's, else its wiki's ("BGMI", "Free Fire")."""
    return _label(GAME_FIELD.get((game or "").lower()) or WIKIS.get(wiki or ""))


def player_sport(wiki: str | None, country: str | None = None) -> str | None:
    """The game a player on a wiki plays, as its sport: India plays PUBG Mobile as BGMI, its own version."""
    if (country or "").strip().lower() == "india" and wiki in INDIA:
        return INDIA[wiki]
    return WIKIS.get(wiki or "")


def _time(raw: str | None) -> datetime | None:
    if not raw or raw.startswith(NO_DATE):
        return None
    try:
        return datetime.fromisoformat(raw.replace(" ", "T")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int | None:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value) if value else None
        except ValueError:
            return None
    return value


def _opponents(x: dict[str, Any]) -> list[dict[str, Any]]:
    return [o for o in x.get("match2opponents") or [] if o.get("name") and o.get("name").upper() != "TBD"]


def _finished(x: dict[str, Any]) -> bool:
    return str(x.get("finished")).lower() in ("1", "true")


def _exact(x: dict[str, Any]) -> bool:
    """Whether the lobby's time was entered, not only its day."""
    return str(x.get("dateexact", 1)).lower() not in ("0", "false", "")


def _played(x: dict[str, Any]) -> bool:
    """Whether any result is in: a team on points."""
    return any(r["points"] > 0 for r in standings(x))


def _status(x: dict[str, Any], now: datetime) -> str:
    """final when Liquipedia marks it finished (or a long time has passed); live once it has started. Over
    with no points in is postponed, as an abandoned match, not a win on 0 points: Liquipedia marks a lobby
    finished when it wasn't played, or once its time has long passed, results or not. A lobby without a
    time starts with its first result, not at midnight."""
    start, exact, played = _time(x.get("date")), _exact(x), _played(x)
    over = _finished(x) or bool(start and now >= start + (LIVE_FOR if exact else LIVE_FOR_INEXACT))
    if x.get("status") == "notplayed" or (over and not played):
        return "postponed"
    if over:
        return "final"
    if played or (start and exact and now >= start):
        return "live"
    return "scheduled"


def _games(x: dict[str, Any]) -> list[dict[str, Any]]:
    return [g for g in x.get("match2games") or [] if g.get("status") != "notplayed"]


def _game_opponents(g: dict[str, Any]) -> list[dict[str, Any]]:
    return _json(g.get("opponents")) or []


def _game_done(g: dict[str, Any]) -> bool:
    return bool(_int(g.get("winner"))) or any(_int(o.get("placement")) for o in _game_opponents(g))


def _title(x: dict[str, Any]) -> str:
    return x.get("tickername") or x.get("shortname") or team_name(x.get("tournament") or x.get("parent")) or "Lobby"


def _detail(x: dict[str, Any]) -> str | None:
    bracket = x.get("match2bracketdata") or {}
    parts = [x.get("section"), bracket.get("header") or bracket.get("inheritedheader") or bracket.get("title")]
    return " · ".join(dict.fromkeys(team_name(p) for p in parts if p)) or None


# ---------------------------------------------------------------- parsers (pure)


def standings(x: dict[str, Any]) -> list[dict[str, Any]]:
    """Every team in the lobby with its points, placement, finishes, games won and each game's result,
    best first. Placement is Liquipedia's when it has set one (a finished lobby), else by points."""
    teams = _opponents(x)
    games = _games(x)
    rows = []
    for i, o in enumerate(x.get("match2opponents") or []):
        if o not in teams:
            continue
        per_game = []
        for n, g in enumerate(games, start=1):
            mine = (_game_opponents(g) + [{}] * (i + 1))[i]
            place = _int(mine.get("placement"))
            if place is None and mine.get("score") in (None, ""):
                continue
            kills = int(_num((mine.get("scoreBreakdown") or {}).get("kills")))
            per_game.append({"n": n, "place": place, "kills": kills, "points": _num(mine.get("score"))})
        points = _num(o.get("score")) if o.get("score") not in (None, "", -1) else sum(g["points"] for g in per_game)
        rows.append({
            "id": team_id(o.get("name")), "name": team_name(o.get("name")),
            "points": points, "place": _int(o.get("placement")), "kills": sum(g["kills"] for g in per_game),
            "wins": sum(1 for g in per_game if g["place"] == 1), "games": per_game,
        })
    if not all(r["place"] for r in rows):
        ranked = sorted(rows, key=lambda r: -r["points"])
        for r in rows:
            r["place"] = 1 + sum(1 for o in ranked if o["points"] > r["points"])
    return sorted(rows, key=lambda r: (r["place"], -r["points"]))


def parse_fixtures(rows: list[dict[str, Any]], wiki: str, now: datetime | None = None) -> list[Fixture]:
    """Lobbies (match2 records) -> fixtures, with the final standings for finished ones."""
    now = now or datetime.now(timezone.utc)
    out = []
    for x in rows:
        teams = _opponents(x)
        if not x.get("match2id") or not teams:
            continue
        status = _status(x, now)
        result = None
        if status == "final":
            table = standings(x)
            result = {"teams": len(table), "standings": [{k: r[k] for k in ("id", "name", "place", "points", "kills", "wins")} for r in table]}
        out.append(Fixture(
            source_id=f"{SHORT.get(wiki, wiki)}:{x['match2id']}",  # event keys hold 64 characters
            title=_title(x),
            competition=team_name(x.get("tournament") or x.get("parent")),
            start_utc=_time(x.get("date")),
            venue=None,
            status=status,
            team_ids=[team_id(o["name"]) for o in teams],
            team_names=[team_name(o["name"]) for o in teams],
            locator={"wiki": wiki, "match": x["match2id"]},
            detail=_detail(x),
            result=result,
        ))
    return out


def result_label(fixture: Fixture, team_ids: list[str]) -> str:
    """ "3rd of 16 · 87 pts" from the player's team's side."""
    return esports.lobby_result(fixture, team_ids, team_id)


def parse_match(x: dict[str, Any], wiki: str, now: datetime | None = None) -> Snapshot:
    """A lobby -> snapshot: the standings, each game's winner, and each listed player's line (their team's
    standing; a lobby's line-ups name the four who played)."""
    now = now or datetime.now(timezone.utc)
    status = _status(x, now)
    table = standings(x)
    games = _games(x)
    done = sum(1 for g in games if _game_done(g))
    total = max(len(games), _int(x.get("bestof")) or 0)
    ids = [team_id(o.get("name")) for o in x.get("match2opponents") or []]
    game_rows = []
    for n, g in enumerate(games, start=1):
        winner = _int(g.get("winner"))
        if winner is None:
            winner = next((i + 1 for i, o in enumerate(_game_opponents(g)) if _int(o.get("placement")) == 1), None)
        game_rows.append({"n": n, "map": g.get("map") or None, "status": "finished" if _game_done(g) else "not_started", "winner": ids[winner - 1] if winner and winner <= len(ids) else None})
    leader = table[0] if table else None
    if status == "final" and leader:
        score = f"{leader['name']} win · {esports.points(leader['points'])} pts"
        clock = "Final standings"
    elif status == "live" and leader and done:
        score = f"{leader['name']} lead · {esports.points(leader['points'])} pts"
        clock = f"After match {done} of {total}" if total else f"After match {done}"
    elif status == "live":
        score, clock = f"{len(table)} teams", f"Match 1 of {total}" if total else "Under way"
    else:
        score, clock = f"{len(table)} teams", f"{total} matches" if total else ""
    state = {
        "kind": "battle_royale",
        "game": game_name(wiki, x.get("game")),
        "win_word": WIN_WORD.get(wiki, "Win"),
        "stage": _detail(x),
        "standings": table,
        "games": game_rows,
        "total": total,
    }
    lines: dict[str, Line] = {}
    if status in ("live", "final") and done:
        by_team = {r["id"]: r for r in table}
        for o in _opponents(x):
            r = by_team.get(team_id(o["name"]))
            if r is None:
                continue
            line = Line(
                headline=f"{esports.ordinal(r['place'])} · {esports.points(r['points'])} pts",
                stats=[{"label": "Place", "value": esports.ordinal(r["place"])}, {"label": "Points", "value": esports.points(r["points"])}, {"label": "Finishes", "value": str(r["kills"])}, {"label": "Games won", "value": str(r["wins"])}],
            )
            for p in o.get("match2players") or []:
                if p.get("name"):
                    lines[f"{wiki}:{team_id(p['name'])}"] = line
    return Snapshot(status=status, score_label=score, clock_label=clock, state=state, lines=lines)


def parse_player(p: dict[str, Any], wiki: str) -> PlayerRef:
    team = p.get("teampagename") or p.get("team")
    return PlayerRef(
        system="liquipedia_esports",
        athlete_id=f"{wiki}:{team_id(p.get('pagename'))}",
        name=p.get("id") or team_name(p.get("pagename")),
        team_ids=[team_id(team)] if team else [],
        team_names=[team_name(p.get("team") or team)] if team else [],
        profile_url=f"https://liquipedia.net/{wiki}/{team_id(p.get('pagename'))}" if p.get("pagename") else None,
        league=wiki,
        born=None if (p.get("birthdate") or "").startswith(NO_DATE) else p.get("birthdate") or None,
        game=_label(player_sport(wiki, p.get("nationality"))),
    )


ROSTER_WAIT = 3600  # how long a run waits, in all, for live lobbies to leave it some of the hour
ROSTER_FIELDS = "pageid,pagename,id,name,romanizedname,localizedname,nationality,birthdate,status,team,teampagename"


def rostered(players: list[dict[str, Any]], wiki: str) -> list[Rostered]:
    """Liquipedia players on one wiki for the registry (pure). The handle is the name, the real name an alias."""
    out = []
    for p in players:
        if not p.get("pageid") or not p.get("pagename"):
            continue
        ref = parse_player(p, wiki)
        aliases = [p.get(k) for k in ("name", "romanizedname", "localizedname")]
        out.append(Rostered(key=f"lp:{SHORT[wiki]}:{p['pageid']}", ref=ref, aliases=[a for a in aliases if a], country=p.get("nationality") or None, current=p.get("status") == "Active" and bool(ref.team_ids)))
    return out


def team_form(fixtures: list[Fixture], team_ids: list[str], team: str | None) -> tuple[list[dict[str, str]], str]:
    """Lobbies played, average placement, top-three finishes, games won and finishes over these lobbies."""
    ids = {team_id(t) for t in team_ids}
    mine = [s for f in fixtures for s in (f.result or {}).get("standings") or [] if team_id(s["id"]) in ids]
    if not mine:
        return [], ""
    n = len(mine)
    stats = [
        {"label": "Lobbies", "value": str(n)},
        {"label": "Avg place", "value": f"{sum(s['place'] for s in mine) / n:.1f}"},
        {"label": "Top 3", "value": str(sum(1 for s in mine if s["place"] <= 3))},
        {"label": "Games won", "value": str(sum(s.get("wins") or 0 for s in mine))},
        {"label": "Finishes", "value": str(sum(s.get("kills") or 0 for s in mine))},
    ]
    return stats, f"{team or 'Their team'}, last {n} {'lobby' if n == 1 else 'lobbies'}"


# ---------------------------------------------------------------- the adapter


class LiquipediaEsports(Adapter):
    system = "liquipedia_esports"
    name = "Liquipedia"
    provider = liquipedia.PROVIDER
    covers = "BGMI, PUBG Mobile, Free Fire and Apex Legends"
    id_pattern = re.compile(r"[a-z0-9]{1,30}:[^\s\[\]{}|#<>]{1,100}")  # wiki:Page_name
    sports = tuple(dict.fromkeys([*INDIA.values(), *WIKIS.values()]))
    games = tuple(WIKIS)  # its own names for them, beside PandaScore's: players never move between them
    live_cadence = 300  # Liquipedia is edited by hand, and the free key allows 60 requests an hour
    live_note = "Standings update from Liquipedia every few minutes as results are recorded."
    lag_note = "Lobby results appear as Liquipedia's editors record them, often several minutes after each match."
    WINDOW_BACK, WINDOW_AHEAD = 45, 30  # days of lobbies a refresh reads
    HISTORY_DAYS = 180  # how far before that window history.py reads, once: two 90-day pages per team
    MULTI_MAX = 50

    def __init__(self) -> None:
        self._seen: dict[str, dict[str, Any]] = {}  # finished lobbies as the last list returned them

    def spare(self) -> bool:
        return liquipedia.spare()

    def sport_of(self, ref: PlayerRef) -> str:
        """The player's game: the one their ref names (BGMI for an Indian player), else their wiki's."""
        found = (sports.family(ref.game) if ref.game else "") or player_sport(ref.league) or ""
        return found if found in self.sports else self.sports[0]

    def _wikis(self, wanted: set[str] | None = None) -> list[str]:
        """The wikis whose games are wanted (by default, those of its sports that run now)."""
        wanted = set(self.running()) if wanted is None else wanted
        return [w for w in WIKIS if {WIKIS[w], INDIA.get(w, WIKIS[w])} & wanted]

    def _keep(self, rows: list[dict[str, Any]], wiki: str) -> list[dict[str, Any]]:
        for x in rows:
            if x.get("match2id") and _finished(x):
                self._seen[f"{wiki}:{x['match2id']}"] = x
        if len(self._seen) > 2000:
            for key in list(self._seen)[:400]:
                self._seen.pop(key, None)
        return rows

    roster_prefix = "lp:"
    wikidata_property = "P10918"  # Liquipedia ID: "pubgmobile/Jonathan"

    def wikidata_id(self, athlete_id: str) -> str:
        return athlete_id.replace(":", "/", 1)  # "pubgmobile:Jonathan"

    @classmethod
    def available(cls) -> bool:
        return bool(liquipedia.token())

    def roster(self, keep: Callable[[list[Rostered]], int], wanted: set[str] | None = None) -> Roster:
        """The active players of each battle royale wiki of the sports wanted (all of its own by default),
        1,000 a request, only while the hour's reserve for live lobbies holds (liquipedia.spare): the free key
        allows 60 requests an hour. The PUBG Mobile wiki is read once for both its games."""
        complete = True
        read = [w for w in IMPORT_WIKIS if w in self._wikis(set(self.sports) if wanted is None else wanted)]
        for wiki in read:
            offset, waited = 0, 0
            while True:
                if not liquipedia.spare() and waited < ROSTER_WAIT:
                    time.sleep(60)
                    waited += 60
                    continue
                try:
                    players = liquipedia.get("player", 0, wiki=wiki, conditions="[[status::Active]]", query=ROSTER_FIELDS, order="pageid ASC", limit=liquipedia.PAGE, offset=offset)
                except AdapterError as exc:
                    log.warning("registry esports: Liquipedia %s at %d failed: %s", wiki, offset, exc)
                    complete = False
                    break
                total = keep(rostered(players, wiki))
                log.info("registry esports: Liquipedia %s, %d players read, %d kept", wiki, offset + len(players), total)
                if len(players) < liquipedia.PAGE:
                    break
                offset += liquipedia.PAGE
        return Roster(complete=complete, leagues=read)

    def search(self, name: str, league: str | None = None) -> list[PlayerRef]:
        """Players whose handle is exactly this, on every battle royale wiki of a game that runs."""
        found = []
        for wiki in self._wikis():
            for p in liquipedia.get("player", 24 * 3600, wiki=wiki, conditions=f"[[id::{name}]]", limit=10):
                found.append(parse_player(p, wiki))
        return found

    def find_player(self, name: str) -> PlayerRef | None:
        hits = [r for r in self.search(name) if naming.fold(r.name) == naming.fold(name)]
        on_teams = [r for r in hits if r.team_ids]
        pick = hits if len(hits) == 1 else on_teams
        return pick[0] if len(pick) == 1 else None

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        wiki, _, page = athlete_id.partition(":")
        if wiki not in WIKIS or not page:
            return None
        rows = liquipedia.get("player", 6 * 3600, wiki=wiki, conditions=f"[[pagename::{page}]]", limit=1)
        return parse_player(rows[0], wiki) if rows else None

    def _between(self, wiki: str, team: str, start: date, end: date, ttl: float) -> list[Fixture]:
        conditions = f"({liquipedia.any_of('opponent', team_pages(team))}) AND [[date::>{start.isoformat()}]] AND [[date::<{(end + timedelta(days=1)).isoformat()}]]"
        rows = liquipedia.get("match", ttl, wiki=wiki, conditions=conditions, order="date ASC", limit=200)
        return parse_fixtures(self._keep(rows, wiki), wiki)

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        today = datetime.now(timezone.utc).date()
        seen: dict[str, Fixture] = {}
        for team in ref.team_ids:
            for f in self._between(ref.league or "", team, today - timedelta(days=self.WINDOW_BACK), today + timedelta(days=self.WINDOW_AHEAD), ttl=1800):
                seen.setdefault(f.source_id, f)
        return sorted(seen.values(), key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def history_pages(self, ref: PlayerRef, anchor: date) -> list[dict[str, Any]]:
        pages = []
        end = anchor - timedelta(days=self.WINDOW_BACK + 1)
        stop = end - timedelta(days=self.HISTORY_DAYS)
        while end > stop:
            start = max(stop, end - timedelta(days=89))
            pages += [{"team": team, "from": start.isoformat(), "to": end.isoformat()} for team in ref.team_ids]
            end = start - timedelta(days=1)
        return pages

    def history(self, ref: PlayerRef, page: dict[str, Any]) -> list[Fixture]:
        found = self._between(ref.league or "", page["team"], date.fromisoformat(page["from"]), date.fromisoformat(page["to"]), ttl=24 * 3600)
        return [f for f in found if f.status == "final"]

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        wiki, match = locator["wiki"], str(locator["match"])
        if final and f"{wiki}:{match}" in self._seen:
            return parse_match(self._seen[f"{wiki}:{match}"], wiki)
        rows = liquipedia.get("match", 24 * 3600 if final else 240, wiki=wiki, conditions=f"[[match2id::{match}]]", limit=1)
        if not rows:
            raise AdapterError(f"Liquipedia has no match {match} on {wiki}")
        return parse_match(self._keep(rows, wiki)[0], wiki)

    def snapshots(self, locators: dict[Any, dict[str, Any]]) -> dict[Any, Snapshot]:
        """Every running lobby at once: one request a wiki a round, keyed as given."""
        by_wiki: dict[str, set[str]] = {}
        for loc in locators.values():
            by_wiki.setdefault(loc["wiki"], set()).add(str(loc["match"]))
        found: dict[str, Snapshot] = {}
        for wiki, ids in by_wiki.items():
            ordered = sorted(ids)
            for i in range(0, len(ordered), self.MULTI_MAX):
                rows = liquipedia.get("match", 0, wiki=wiki, conditions=liquipedia.any_of("match2id", ordered[i:i + self.MULTI_MAX]), limit=self.MULTI_MAX)
                for x in self._keep(rows, wiki):
                    found[f"{wiki}:{x.get('match2id')}"] = parse_match(x, wiki)
        return {key: found[f"{loc['wiki']}:{loc['match']}"] for key, loc in locators.items() if f"{loc['wiki']}:{loc['match']}" in found}

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        return result_label(fixture, team_ids)

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        """The team's lobbies over the refresh window (the list fixtures() just read, so no new request)."""
        finals = [f for f in self.fixtures(ref) if f.status == "final"] if ref.team_ids else []
        return team_form(finals, ref.team_ids, (ref.team_names or [None])[0])
