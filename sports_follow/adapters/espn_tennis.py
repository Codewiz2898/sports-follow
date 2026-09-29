"""Tennis from ESPN: ATP and WTA singles. Draws, live set scores, rankings.

ESPN has no per-match summary or player schedule for tennis. A tour scoreboard lists the
tournaments running on a day with their whole draws, so a player's matches are found by reading
the current scoreboard plus one day a week back, and a live match is read from its tournament's
scoreboard (cached for a few seconds, so everyone in the draw shares a fetch). Draws only exist a
day or two ahead, so a player's "upcoming" is their next match in a draw already made.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from . import espn
from .base import AdapterError, Fixture, Line, PlayerRef, Snapshot

SPORT_UID = "s:850~"
TOURS = {"851": "atp", "900": "wta"}
WEEKS_BACK = 6  # how far back to look for results

_STATUS = {"pre": "scheduled", "in": "live", "post": "final"}


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _num(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _competitor(c: dict[str, Any]) -> dict[str, Any]:
    athlete = c.get("athlete") or {}
    pair = c.get("roster") or {}  # doubles: the pair, "V. Hruncakova / A. Siskova"
    return {
        "id": str(c.get("id")),
        "name": athlete.get("displayName") or pair.get("displayName") or "TBD",
        "short": athlete.get("shortName") or pair.get("shortDisplayName") or athlete.get("displayName") or "TBD",
        "country": (athlete.get("flag") or {}).get("alt"),
        "seed": (c.get("curatedRank") or {}).get("current"),
        "serving": bool(c.get("possession")),
        "winner": c.get("winner"),
        "sets": [{"games": _num(ls.get("value")), "tiebreak": ls.get("tiebreak"), "won": ls.get("winner")} for ls in c.get("linescores") or []],
    }


def _ordered(competitors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(competitors, key=lambda c: c.get("order") or 0)


def set_scores(me: dict[str, Any], other: dict[str, Any]) -> str:
    """ "6-4 7-6(5)" from `me`'s side; the tiebreak shows the loser's points, as scoreboards do."""
    out = []
    for a, b in zip(me["sets"], other["sets"]):
        text = f"{a['games']}-{b['games']}"
        if a.get("tiebreak") is not None and b.get("tiebreak") is not None:
            text += f"({min(a['tiebreak'], b['tiebreak'])})"
        out.append(text)
    return " ".join(out)


def _sets_won(me: dict[str, Any]) -> int:
    return sum(1 for s in me["sets"] if s.get("won"))


# ---------------------------------------------------------------- parsers (pure)


def parse_scoreboard(data: dict[str, Any], tour: str, day: str) -> list[Fixture]:
    """Every singles match on a tour scoreboard -> fixtures (doubles are left out)."""
    out = []
    for event in data.get("events", []):
        for group in event.get("groupings") or []:
            if not ((group.get("grouping") or {}).get("slug") or "").endswith("singles"):
                continue
            for comp in group.get("competitions") or []:
                players = [_competitor(c) for c in _ordered(comp.get("competitors") or [])]
                if len(players) != 2:
                    continue
                status = _STATUS.get(((comp.get("status") or {}).get("type") or {}).get("state"), "scheduled")
                venue = comp.get("venue") or {}
                out.append(
                    Fixture(
                        source_id=str(comp["id"]),
                        title=f"{players[0]['name']} vs {players[1]['name']}",
                        competition=event.get("name") or "",
                        start_utc=_time(comp.get("date")),
                        venue=", ".join(v for v in (venue.get("court"), venue.get("fullName")) if v) or None,
                        status=status,
                        team_ids=[p["id"] for p in players],
                        team_names=[p["name"] for p in players],
                        locator={"tour": tour, "tournament": str(event.get("id")), "match": str(comp["id"]), "day": day},
                        detail=(comp.get("round") or {}).get("displayName"),
                        result={"players": players, "note": next((n.get("text") for n in comp.get("notes") or [] if n.get("text")), None)} if status == "final" else None,
                    )
                )
    return out


def find_match(data: dict[str, Any], match_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for event in data.get("events", []):
        for group in event.get("groupings") or []:
            for comp in group.get("competitions") or []:
                if str(comp.get("id")) == match_id:
                    return event, comp
    return None


def parse_match(event: dict[str, Any], comp: dict[str, Any]) -> Snapshot:
    """One match from a scoreboard -> snapshot, with each player's side of it as their line."""
    status_obj = comp.get("status") or {}
    kind = status_obj.get("type") or {}
    status = _STATUS.get(kind.get("state"), "scheduled")
    a, b = [_competitor(c) for c in _ordered(comp.get("competitors") or [])][:2]
    score = set_scores(a, b)

    lines = {}
    for me, other in ((a, b), (b, a)):
        won, lost = _sets_won(me), _sets_won(other)
        games = sum(s["games"] for s in me["sets"])
        if status == "final":
            headline = f"{'Won' if me.get('winner') else 'Lost'} in {won + lost} sets"
        elif status == "live":
            headline = "Level in sets" if won == lost else f"{'Leads' if won > lost else 'Trails'} {won}–{lost} in sets"
            if me["serving"]:
                headline += " · serving"
        else:
            headline = f"vs {other['name']}"
        stats = [{"label": "Sets", "value": f"{won}–{lost}"}, {"label": "Games", "value": str(games)}, {"label": "Opponent", "value": other["short"]}]
        if other.get("seed"):
            stats.append({"label": "Opp. seed", "value": str(other["seed"])})
        lines[me["id"]] = Line(headline=headline, stats=stats)

    detail = kind.get("detail") or kind.get("shortDetail") or ""
    note = next((n.get("text") for n in comp.get("notes") or [] if n.get("text")), None)
    return Snapshot(
        status=status,
        score_label=f"{a['short']} {score} {b['short']}".strip() if score else f"{a['name']} vs {b['name']}",
        clock_label=(note or "Final") if status == "final" else detail,
        state={
            "kind": "tennis",
            "players": [a, b],
            "set": status_obj.get("period"),
            "round": (comp.get("round") or {}).get("displayName"),
            "tournament": event.get("name"),
        },
        lines=lines,
        source_url=next((l.get("href") for l in event.get("links") or [] if "summary" in (l.get("rel") or [])), None),
    )


def parse_ranking(data: dict[str, Any], athlete_id: str) -> tuple[dict[str, Any] | None, str | None]:
    table = (data.get("rankings") or [{}])[0]
    row = next((r for r in table.get("ranks") or [] if str((r.get("athlete") or {}).get("id")) == athlete_id), None)
    return row, table.get("update")


def result_label(fixture: Fixture, team_ids: list[str]) -> str:
    """ "Won 6-4 7-6(5)" / "Lost 4-6 3-6", from the followed player's side."""
    players = (fixture.result or {}).get("players") or []
    if len(players) != 2:
        return ""
    me = next((p for p in players if p["id"] in team_ids), players[0])
    other = players[1] if me is players[0] else players[0]
    return f"{'Won' if me.get('winner') else 'Lost'} {set_scores(me, other)}"


# ---------------------------------------------------------------- the adapter


class EspnTennis:
    system = "espn_tennis"
    sports = ("tennis",)
    live_cadence = 10

    def find_player(self, name: str) -> PlayerRef | None:
        for hit in espn.search_athletes(name, SPORT_UID):
            tour = TOURS.get(hit.get("league_id") or "")
            if tour is not None and espn.names_match(name, hit["name"]):
                # Individual sport: the player is their own "team", so result labels and titles work as for teams.
                return PlayerRef(self.system, hit["athlete_id"], hit["name"], [hit["athlete_id"]], [hit["name"]], hit.get("url"), league=tour)
        return None

    def player(self, athlete_id: str, league: str | None = None) -> PlayerRef | None:
        # ESPN serves any tennis player under either tour's path, so the tour can't be read from the
        # record; without one, the player's own search hit says which tour they're on.
        athlete = espn.get_json(f"{espn.WEB}/common/v3/sports/tennis/{league or 'atp'}/athletes/{athlete_id}", ttl=6 * 3600).get("athlete") or {}
        if not athlete.get("id"):
            return None
        name = athlete.get("displayName") or ""
        tour = league if league in TOURS.values() else None
        if tour is None:
            hit = next((h for h in espn.search_athletes(name, SPORT_UID) if h["athlete_id"] == str(athlete["id"])), None)
            tour = TOURS.get((hit or {}).get("league_id") or "")
        if tour is None:
            return None
        return PlayerRef(self.system, str(athlete["id"]), name, [str(athlete["id"])], [name], espn.profile_link(athlete), league=tour, born=espn.birth_date(athlete))

    def _board(self, tour: str, day: str | None, ttl: float) -> dict[str, Any]:
        return espn.get_json(f"{espn.SITE}/tennis/{tour}/scoreboard{f'?dates={day}' if day else ''}", ttl=ttl)

    def fixtures(self, ref: PlayerRef) -> list[Fixture]:
        tour = ref.league or "atp"
        today = datetime.now(timezone.utc)
        days = [None] + [(today - timedelta(weeks=w)).strftime("%Y%m%d") for w in range(1, WEEKS_BACK + 1)]

        def read(day: str | None) -> list[Fixture]:
            try:
                return parse_scoreboard(self._board(tour, day, 600 if day is None else 12 * 3600), tour, day or today.strftime("%Y%m%d"))
            except AdapterError:
                return []

        seen: dict[str, Fixture] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for fixtures in pool.map(read, days):
                for f in fixtures:
                    if ref.athlete_id in f.team_ids:
                        seen.setdefault(f.source_id, f)
        return sorted(seen.values(), key=lambda f: f.start_utc or datetime.max.replace(tzinfo=timezone.utc))

    def snapshot(self, locator: dict[str, Any], final: bool = False) -> Snapshot:
        tour, match_id = locator["tour"], locator["match"]
        # The current scoreboard while the tournament runs; the day it was found on once it has moved on.
        for day, ttl in ((None, 5), (locator.get("day"), 24 * 3600 if final else 5)):
            found = find_match(self._board(tour, day, ttl), match_id)
            if found:
                return parse_match(*found)
        raise AdapterError(f"ESPN has no tennis match {match_id} on the {tour} scoreboard")

    def result_label(self, fixture: Fixture, team_ids: list[str]) -> str:
        return result_label(fixture, team_ids)

    def stats(self, ref: PlayerRef, recent: list[tuple[Fixture, Snapshot]]) -> tuple[list[dict[str, str]], str]:
        tour = ref.league or "atp"
        stats = []
        row, updated = parse_ranking(espn.get_json(f"{espn.SITE}/tennis/{tour}/rankings", ttl=6 * 3600), ref.athlete_id)
        if row:
            stats.append({"label": "Ranking", "value": str(row.get("current"))})
            if row.get("points") is not None:
                stats.append({"label": "Points", "value": f"{int(row['points']):,}"})
            prev = row.get("previous")
            if prev and prev != row.get("current"):
                stats.append({"label": "Last week", "value": str(prev)})
        finals = [f for f in self.fixtures(ref) if f.status == "final"]
        if finals:
            wins = sum(1 for f in finals if result_label(f, ref.team_ids).startswith("Won"))
            stats.append({"label": "Win–loss", "value": f"{wins}–{len(finals) - wins}"})
        note_bits = [f"{tour.upper()} ranking" + (f" of {updated[:10]}" if updated else "") if row else None, f"win–loss over the last {WEEKS_BACK} weeks of singles" if finals else None]
        return stats, "; ".join(b for b in note_bits if b)
