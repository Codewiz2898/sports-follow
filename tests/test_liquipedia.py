"""Battle royale esports from Liquipedia: lobby parsers, standings, alerts, form, the registry's rows,
search, credits, the shared hourly budget and the lane's pace. Against small made-up records in the
shape LiquipediaDB stores matches (Liquipedia/Lua-Modules: Match.lua, MatchGroup/Input/Util.lua), as
for the other sources: none of theirs are committed."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from functools import partial
from types import SimpleNamespace

import httpx
import pytest

from sports_follow import credits, form, moments, registry, search, structured
from sports_follow.adapters import liquipedia, liquipedia_esports as lp
from sports_follow.adapters import KNOWN, budget
from sports_follow.adapters.base import Snapshot
from sports_follow.sports import esports
from sports_follow.models import Athlete, Event, EventState, EventTeam, MatchFollow, Player, PlayerLine, SourceBinding

from .test_structured import FakeSource, lane, redis  # noqa: F401 (redis: the fixture)

WIKI = "pubgmobile"
NOW = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
TEAMS = ["GodLike_Esports", "Team_SouL", "Revenant_XSpark", "Orangutan"]
SPACED = [t.replace("_", " ") for t in TEAMS]  # match records name a team either way (MatchTicker asks for both)
# Placement and finishes for each team in each game (team order as above).
GAMES = [[(1, 12), (3, 5), (2, 7), (4, 1)], [(4, 2), (1, 9), (2, 6), (3, 3)], [(1, 8), (4, 0), (2, 11), (3, 4)]]
# Totals after each game: GodLike 22, 28, 46; Team SouL 10, 29, 33; Revenant 13, 25, 42; Orangutan 5, 13, 22.
POINTS = {1: 10, 2: 6, 3: 5, 4: 4}  # placement points; a finish is a point


def _game(results: list[tuple[int, int]] | None) -> dict:
    if results is None:
        return {"map": "Erangel", "status": "", "winner": "", "opponents": []}
    winner = next(i + 1 for i, (place, _) in enumerate(results) if place == 1)
    return {
        "map": "Erangel", "status": "", "winner": str(winner),
        "opponents": [{"placement": place, "score": POINTS[place] + kills, "scoreBreakdown": {"kills": kills, "placePoints": POINTS[place], "killPoints": kills}} for place, kills in results],
    }


def _lobby(played: int, finished: bool = False, names: list[str] = TEAMS, **extra) -> dict:
    games = [_game(GAMES[i] if i < played else None) for i in range(3)]
    totals = [sum(POINTS[GAMES[g][t][0]] + GAMES[g][t][1] for g in range(played)) for t in range(4)]
    order = sorted(range(4), key=lambda t: -totals[t])
    return {
        "match2id": "aBcDeFgHiJ_0003", "date": "2026-10-12 13:30:00", "dateexact": 1, "finished": 1 if finished else 0,
        "status": "", "bestof": 3, "tickername": "BMPS 2026", "tournament": "Battlegrounds Mobile India Pro Series/2026",
        "section": "Semi Finals", "game": "bgmi", "match2bracketdata": {"header": "Day 3"},
        "match2opponents": [{
            "name": name, "template": name.replace(" ", "_").split("_")[0][:4].upper(), "type": "team", "score": totals[t] if played else -1,
            "placement": (order.index(t) + 1) if finished else None,
            "match2players": [{"name": "Jonathan" if t == 0 else f"{TEAMS[t]}_p1", "displayname": "Jonathan" if t == 0 else "p1"}],
        } for t, name in enumerate(names)],
        "match2games": games,
        **extra,
    }


def test_standings_total_each_team_and_rank_by_points_until_liquipedia_places_them():
    table = lp.standings(_lobby(3, finished=True))
    assert [(r["name"], r["place"], r["points"], r["kills"], r["wins"]) for r in table] == [
        ("GodLike Esports", 1, 46.0, 22, 2), ("Revenant XSpark", 2, 42.0, 24, 0), ("Team SouL", 3, 33.0, 14, 1), ("Orangutan", 4, 22.0, 8, 0)]
    assert table[0]["games"][0] == {"n": 1, "place": 1, "kills": 12, "points": 22.0}
    live = lp.standings(_lobby(1))  # no placements yet: ranked by points
    assert [(r["name"], r["place"]) for r in live][:2] == [("GodLike Esports", 1), ("Revenant XSpark", 2)]


@pytest.mark.parametrize("names", [TEAMS, SPACED])
def test_a_finished_lobby_is_a_fixture_with_final_standings(names):
    [f] = lp.parse_fixtures([_lobby(3, finished=True, names=names)], WIKI, NOW)
    assert (f.source_id, f.title, f.competition, f.detail, f.status) == ("pubgm:aBcDeFgHiJ_0003", "BMPS 2026", "Battlegrounds Mobile India Pro Series/2026", "Semi Finals · Day 3", "final")
    assert f.locator == {"wiki": WIKI, "match": "aBcDeFgHiJ_0003"} and f.team_ids == TEAMS
    assert f.start_utc == datetime(2026, 10, 12, 13, 30, tzinfo=timezone.utc)
    assert lp.result_label(f, ["Team_SouL"]) == lp.result_label(f, ["Team SouL"]) == "3rd of 4 · 33 pts"
    assert lp.result_label(f, ["Someone_Else"]) == ""
    assert len(f"liquipedia_esports:{f.source_id}") <= 64  # the event key


def test_a_team_is_asked_for_in_every_spelling_and_found_in_any(monkeypatch):
    # LPDB keeps a match's opponent as its wiki wrote it: spaces (usually) or underscores, the first letter
    # as the team template has it. Liquipedia's MatchTicker asks for each; so does a player's fixtures read.
    assert lp.team_id("GodLike Esports") == lp.team_id("godLike_Esports") == lp.team_id(" GodLike  Esports ") == "GodLike_Esports"
    asked = []
    monkeypatch.setattr(liquipedia, "get", lambda datatype, ttl, **params: asked.append(params["conditions"]) or [_lobby(3, finished=True, names=SPACED)])
    ref = lp.parse_player({"pagename": "Owais", "id": "Owais", "teampagename": "Team SouL", "team": "Team SouL"}, WIKI)
    [f] = lp.LiquipediaEsports().fixtures(ref)
    assert asked[0].startswith("([[opponent::Team SouL]] OR [[opponent::Team_SouL]] OR [[opponent::team SouL]] OR [[opponent::team_SouL]]) AND [[date::>")
    assert ref.team_ids == ["Team_SouL"] and f.team_ids == TEAMS and lp.result_label(f, ref.team_ids) == "3rd of 4 · 33 pts"


def test_a_running_lobby_shows_the_leader_and_the_matches_played():
    snap = lp.parse_match(_lobby(1), WIKI, NOW)
    assert (snap.status, snap.score_label, snap.clock_label) == ("live", "GodLike Esports lead · 22 pts", "After match 1 of 3")
    assert snap.state["kind"] == "battle_royale" and snap.state["game"] == "BGMI" and snap.state["win_word"] == "Chicken dinner"
    assert [g["winner"] for g in snap.state["games"]] == ["GodLike_Esports", None, None]
    # The four who played get their team's standing as their line.
    line = snap.lines["pubgmobile:Jonathan"]
    assert line.headline == "1st · 22 pts" and {"label": "Finishes", "value": "12"} in line.stats
    upcoming = lp.parse_match(_lobby(0, date="2026-10-12 18:00:00"), WIKI, NOW)
    assert (upcoming.status, upcoming.score_label, upcoming.clock_label, upcoming.lines) == ("scheduled", "4 teams", "3 matches", {})
    over = lp.parse_match(_lobby(3, finished=True), WIKI, NOW)
    assert (over.status, over.score_label, over.clock_label) == ("final", "GodLike Esports win · 46 pts", "Final standings")
    # Never marked finished, half a day after it began: over.
    assert lp.parse_match(_lobby(2, date="2026-10-11 13:30:00"), WIKI, NOW).status == "final"
    assert lp.parse_match(_lobby(0, status="notplayed"), WIKI, NOW).status == "postponed"


def test_a_lobby_over_with_no_points_in_is_postponed_never_a_win():
    # Liquipedia marks a lobby finished when it wasn't played, or once its time has long passed, results or
    # not; half a day on with nothing in is the same. None of them is "GodLike Esports win · 0 pts".
    for x in (_lobby(0, finished=True), _lobby(0, finished=True, status="notplayed"), _lobby(0, date="2026-10-11 13:30:00")):
        snap = lp.parse_match(x, WIKI, NOW)
        [f] = lp.parse_fixtures([x], WIKI, NOW)
        assert (snap.status, f.status, f.result, snap.score_label) == ("postponed", "postponed", None, "4 teams")
        assert moments.detect("Esports", "Jonathan", "pubgmobile:Jonathan", _seen(_lobby(0)), _seen(x)) == []


def test_a_lobby_without_a_time_waits_for_its_first_result():
    # A lobby nobody entered a time for is stored at midnight UTC of its day, dateexact false. It may be played
    # that evening in India, or after midnight UTC in the Americas: midnight isn't its start, nor noon its end.
    day = {"date": "2026-10-12 00:00:01", "dateexact": 0}
    owais = "pubgmobile:Team_SouL_p1"
    at = [datetime(2026, 10, 12, h, 5, tzinfo=timezone.utc) for h in (0, 12, 23)]
    assert [lp.parse_match(_lobby(0, **day), WIKI, t).status for t in at] == ["scheduled"] * 3
    [f] = lp.parse_fixtures([_lobby(0, **day)], WIKI, at[1])
    assert (f.status, f.result, lp.result_label(f, ["Team_SouL"])) == ("scheduled", None, "")
    assert moments.detect("Esports", "Owais", owais, _seen(_lobby(0, **day), ["Team_SouL"], owais, at[0]), _seen(_lobby(0, **day), ["Team_SouL"], owais, at[1])) == []
    # It starts with its first result, and runs past midnight; finished, or a day and a half on, it's over.
    [start, dinner] = moments.detect("Esports", "Jonathan", "pubgmobile:Jonathan", _seen(_lobby(0, **day), now=at[1]), _seen(_lobby(1, **day), now=at[1]))
    assert (start.kind, dinner.title) == ("start", "Chicken dinner: GodLike Esports")
    assert lp.parse_match(_lobby(2, **day), WIKI, datetime(2026, 10, 13, 3, 0, tzinfo=timezone.utc)).status == "live"
    assert lp.parse_match(_lobby(3, finished=True, **day), WIKI, at[2]).status == "final"
    assert lp.parse_match(_lobby(3, **day), WIKI, datetime(2026, 10, 13, 12, 5, tzinfo=timezone.utc)).status == "final"
    assert lp.parse_match(_lobby(0, **day), WIKI, datetime(2026, 10, 13, 12, 5, tzinfo=timezone.utc)).status == "postponed"


def test_a_lobby_without_a_time_first_seen_late_in_its_day_is_coming_up_and_armed():
    # BUG-205: stored at midnight UTC, a lobby with no time first seen after 06:00 UTC (a fan following at noon in
    # India) was past the arming window, and after noon UTC not even coming up: it was first polled once its results
    # were in, so its start and first games were never told. On its day, nothing in yet, its source says it's next
    # (Fixture.next_up): it's coming up, and armed from the refresh that reads it, so its start is seen when it comes.
    day = {"date": "2026-10-12 00:00:01", "dateexact": 0}
    late = datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc)
    [f] = lp.parse_fixtures([_lobby(0, **day)], WIKI, late)
    assert (f.status, f.next_up) == ("scheduled", True)
    assert structured.upcoming([f], late) == [f]
    event = Event(key=f"liquipedia_esports:{f.source_id}", sport="BGMI", status="scheduled")
    structured.reschedule(None, event, f.start_utc, f.status, "BGMI", f.next_up)  # type: ignore[arg-type]  (no question to move)
    assert event.status == "armed"
    # Not the day before (armed by the clock, as any game), nor once under way, nor a lobby with a time.
    before = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)
    assert [lp.parse_fixtures([x], WIKI, t)[0].next_up for x, t in ((_lobby(0, **day), before), (_lobby(1, **day), late), (_lobby(0), late))] == [False] * 3


def test_players_are_keyed_by_wiki_and_page():
    ref = lp.parse_player({"pagename": "Jonathan", "id": "JONATHAN", "teampagename": "GodLike Esports", "team": "GodLike Esports", "birthdate": "1970-01-01"}, WIKI)
    assert (ref.system, ref.athlete_id, ref.name, ref.team_ids, ref.team_names, ref.league, ref.born) == (
        "liquipedia_esports", "pubgmobile:Jonathan", "JONATHAN", ["GodLike_Esports"], ["GodLike Esports"], WIKI, None)
    assert ref.profile_url == "https://liquipedia.net/pubgmobile/Jonathan"
    assert esports.ordinal(11) == "11th" and esports.ordinal(21) == "21st" and esports.ordinal(2) == "2nd"


def test_team_form_over_the_lobbies():
    fixtures = lp.parse_fixtures([_lobby(3, finished=True), {**_lobby(3, finished=True), "match2id": "x_0004"}], WIKI, NOW)
    stats, note = lp.team_form(fixtures, ["Team_SouL"], "Team SouL")
    assert stats == [{"label": "Lobbies", "value": "2"}, {"label": "Avg place", "value": "3.0"}, {"label": "Top 3", "value": "2"}, {"label": "Games won", "value": "2"}, {"label": "Finishes", "value": "28"}]
    assert note == "Team SouL, last 2 lobbies"
    assert lp.team_form(fixtures, ["Team SouL"], "Team SouL") == (stats, note)


def test_the_lane_reads_every_running_lobby_in_one_request(monkeypatch):
    calls = []

    started = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")  # the lane reads the real clock

    def fake(datatype, ttl, **params):
        calls.append((datatype, params))
        return [_lobby(1, date=started), {**_lobby(2, date=started), "match2id": "other_0001"}]

    monkeypatch.setattr(liquipedia, "get", fake)
    adapter = lp.LiquipediaEsports()
    snaps = adapter.snapshots({1: {"wiki": WIKI, "match": "aBcDeFgHiJ_0003"}, 2: {"wiki": WIKI, "match": "other_0001"}, 3: {"wiki": WIKI, "match": "gone"}})
    assert calls == [("match", {"wiki": WIKI, "conditions": "[[match2id::aBcDeFgHiJ_0003]] OR [[match2id::gone]] OR [[match2id::other_0001]]", "limit": 50})]
    assert snaps[1].clock_label.startswith("After match 1") and snaps[2].clock_label.startswith("After match 2") and 3 not in snaps


# ---------------------------------------------------------------- moments


def _seen(x: dict, teams: list[str] | None = None, athlete: str = "pubgmobile:Jonathan", now: datetime = NOW) -> moments.Seen:
    """The lobby as one player's poll sees it: their line, if the line-ups name them (as apply_poll)."""
    snap = lp.parse_match(x, WIKI, now)
    line = snap.lines.get(athlete)
    line = {"headline": line.headline, "stats": line.stats} if line else None
    return moments.Seen(snap.status, snap.state, line, snap.score_label, snap.clock_label, lineups=bool(snap.lines), teams=["GodLike_Esports"] if teams is None else teams)


@pytest.mark.parametrize("names", [TEAMS, SPACED])
def test_a_chicken_dinner_each_placement_and_going_top(names):
    lobby = partial(_lobby, names=names)
    before = _seen(lobby(0, date="2026-10-12 18:00:00"))  # not started yet
    started = _seen(lobby(0))
    [start] = moments.detect("Esports", "Jonathan", "pubgmobile:Jonathan", before, started)
    assert (start.kind, start.level, start.title, start.body) == ("start", "key", "Jonathan is playing", "Semi Finals · Day 3 · 3 matches · BGMI")
    one = _seen(lobby(1))
    [dinner] = moments.detect("Esports", "Jonathan", "pubgmobile:Jonathan", started, one)
    assert (dinner.kind, dinner.level, dinner.title, dinner.detail, dinner.priority) == ("dinner", "key", "Chicken dinner: GodLike Esports", "g1", 90)
    assert dinner.body == "Jonathan · Match 1 · 12 finishes · 1st overall, 22 pts"
    [fourth] = moments.detect("Esports", "Jonathan", "pubgmobile:Jonathan", one, _seen(lobby(2)))
    assert (fourth.kind, fourth.level, fourth.title) == ("game", "minor", "Jonathan: GodLike Esports 4th in match 2")
    # Team SouL win match 2 and go top: their chicken dinner, then the lead.
    soul = moments.detect("Esports", "Owais", "pubgmobile:Team_SouL_p1", _seen(lobby(1), ["Team_SouL"], "pubgmobile:Team_SouL_p1"), _seen(lobby(2), ["Team_SouL"], "pubgmobile:Team_SouL_p1"))
    assert [(f.kind, f.level, f.title) for f in soul] == [("dinner", "key", "Chicken dinner: Team SouL"), ("lead", "key", "Owais: Team SouL go top")]
    assert soul[1].body == "29 pts after 2 matches"
    assert moments.detect("Esports", "Jonathan", "pubgmobile:Jonathan", None, one) == []  # a first look is the baseline


def test_a_placing_a_poll_after_its_points_is_still_told():
    # Editors enter match 2's points, then its placings: Team SouL go top on the points, their chicken dinner
    # comes with the placing.
    owais = "pubgmobile:Team_SouL_p1"
    points_only = _lobby(2)
    points_only["match2games"][1]["winner"] = ""
    for o in points_only["match2games"][1]["opponents"]:
        o["placement"] = None
    one, points, placed = (_seen(x, ["Team_SouL"], owais) for x in (_lobby(1), points_only, _lobby(2)))
    assert [(f.kind, f.title) for f in moments.detect("Esports", "Owais", owais, one, points)] == [("lead", "Owais: Team SouL go top")]
    assert [(f.kind, f.title) for f in moments.detect("Esports", "Owais", owais, points, placed)] == [("dinner", "Chicken dinner: Team SouL")]


@pytest.mark.parametrize("names", [TEAMS, SPACED])
def test_where_the_team_finished_the_lobby(names):
    lobby = partial(_lobby, names=names)
    two = _seen(lobby(2))
    [won] = moments.detect("Esports", "Jonathan", "pubgmobile:Jonathan", two, _seen(lobby(3, finished=True)))
    assert (won.kind, won.title, won.priority) == ("final", "Jonathan: GodLike Esports win the lobby · 46 pts", 90)
    assert won.body == "4 teams · 2 won · 22 finishes · Semi Finals · Day 3"
    owais = "pubgmobile:Team_SouL_p1"
    [third] = moments.detect("Esports", "Owais", owais, _seen(lobby(2), ["Team_SouL"], owais), _seen(lobby(3, finished=True), ["Team_SouL"], owais))
    assert third.title == "Owais: Team SouL finish 3rd · 33 pts" and third.priority == 80


def test_a_player_left_out_of_the_lobby_hears_nothing():
    # The line-ups name who played; a benched teammate has no line, so no result for them.
    bench = "pubgmobile:Bench"
    assert moments.detect("Esports", "Bench", bench, _seen(_lobby(2), athlete=bench), _seen(_lobby(3, finished=True), athlete=bench)) == []
    assert moments.detect("Esports", "Jonathan", "pubgmobile:Jonathan", _seen(_lobby(2)), _seen(_lobby(3, finished=True)))


class PollSession:
    """The rows apply_poll reads and writes for one followed player in one lobby, kept in memory. Nobody
    follows the lobby by itself (no match follow), nor a team in it (no team's game)."""

    def __init__(self, event: Event, player: Player, team_ids: list[str]):
        self.event, self.player, self.team_ids = event, player, team_ids
        self.rows: dict[type, list] = {EventState: [], PlayerLine: [], MatchFollow: [], EventTeam: []}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, model, key):
        return self.event if model is Event else None  # no card to update

    def execute(self, stmt):
        model = stmt.column_descriptions[0]["entity"]
        if model is Player:
            return [(self.player.id, self.player.name, self.player.sport)]
        if model is EventState:  # when a lobby found over ended (structured.found_over), for its result: not asked here
            return SimpleNamespace(all=list)
        assert model is SourceBinding  # the team ids moments are told from
        return [(self.player.id, {"team_ids": self.team_ids})]

    def scalar(self, stmt):
        rows = self.rows[stmt.column_descriptions[0]["entity"]]
        return rows[-1] if rows else None

    def add(self, row):
        self.rows[type(row)].append(row)


def test_the_poller_tells_a_lobby_from_the_players_team(monkeypatch, redis):
    # The lane's path, not Seen built by hand: the poller reads the player's team from their binding.
    owais = "pubgmobile:Team_SouL_p1"
    db = PollSession(Event(id=1, status="armed"), Player(id=7, name="Owais", sport="BGMI"), ["Team_SouL"])
    told: dict[str, moments.Found] = {}

    def record(db, player_id, event_id, found):
        for f in found:
            told.setdefault(f"{f.kind}:{f.detail}", f)  # one per key, as notify.record stores them
        return []

    monkeypatch.setattr(structured, "session", lambda: db)
    monkeypatch.setattr(structured.notify, "record", record)
    monkeypatch.setattr(structured.notify, "told", lambda db, player_id, event_id: [])
    monkeypatch.setattr(structured.notify, "dispatch", lambda ids: None)
    binding = {"adapter": "liquipedia_esports", "locator": {"wiki": WIKI, "match": "aBcDeFgHiJ_0003"}, "cadence": 300}
    for x in (_lobby(0, date="2026-10-12 18:00:00"), _lobby(1), _lobby(2), _lobby(3), _lobby(3, finished=True)):
        structured.apply_poll(1, binding, lp.LiquipediaEsports(), [(7, owais)], lp.parse_match(x, WIKI, NOW))
    assert [(f.kind, f.title) for f in told.values()] == [
        ("start", "Owais is playing"), ("game", "Owais: Team SouL 3rd in match 1"), ("dinner", "Chicken dinner: Team SouL"),
        ("lead", "Owais: Team SouL go top"), ("game", "Owais: Team SouL 4th in match 3"), ("final", "Owais: Team SouL finish 3rd · 33 pts")]


def test_form_reads_placings_from_lobby_results():
    items = [({"result": "1st of 16 · 112 pts"}, None), ({"result": "6th of 16 · 54 pts"}, None), ({"result": "3rd of 16 · 80 pts"}, None)]
    stats, note = form.summarize("Esports", items, ["GodLike Esports"])
    assert stats == [{"label": "Avg place", "value": "3.3"}, {"label": "Top 3", "value": "2"}, {"label": "Avg points", "value": "82"}]
    assert note == "Last 3 lobbies"


# ---------------------------------------------------------------- registry, search, credits, scheduling


PLAYER = {"pageid": 123456, "pagename": "Jonathan", "id": "JONATHAN", "name": "Jonathan Amaral", "nationality": "India", "birthdate": "1998-09-21", "status": "Active", "team": "GodLike Esports", "teampagename": "GodLike Esports"}


def test_registry_rows_from_liquipedia_players():
    rows = registry.roster_rows(KNOWN["liquipedia_esports"], lp.rostered([PLAYER, {**PLAYER, "pageid": None}, {**PLAYER, "pageid": 2, "pagename": "Retired", "id": "Ghost", "status": "Retired", "team": None, "teampagename": None}], WIKI))
    one, ghost = rows
    assert one["qid"] == "lp:pubgm:123456" and len(one["qid"]) <= 20
    # An Indian player on the PUBG Mobile wiki plays BGMI, its own sport; another, PUBG Mobile.
    assert (one["sport"], one["name"], one["aliases"], one["teams"], one["league"], one["country"], one["current"]) == ("bgmi", "JONATHAN", ["Jonathan Amaral"], ["GodLike Esports"], WIKI, "India", True)
    assert ghost["sport"] == "bgmi" and registry.roster_rows(KNOWN["liquipedia_esports"], lp.rostered([{**PLAYER, "nationality": "Nepal"}], WIKI))[0]["sport"] == "pubgmobile"
    assert one["ids"] == {"liquipedia_esports": "pubgmobile:Jonathan"} and str(one["birth_date"]) == "1998-09-21"
    assert ghost["current"] is False


def test_a_liquipedia_row_is_a_search_result_keyed_by_its_page(monkeypatch):
    monkeypatch.setitem(search.ADAPTERS, "liquipedia_esports", lp.LiquipediaEsports())
    row = registry.roster_rows(KNOWN["liquipedia_esports"], lp.rostered([PLAYER], WIKI))[0]
    r = search.from_athlete(Athlete(**{k: v for k, v in row.items() if k != "search_text"}), "jonathan")
    assert (r.key, r.system, r.athlete_id, r.qid) == ("liquipedia_esports:pubgmobile:Jonathan", "liquipedia_esports", "pubgmobile:Jonathan", None)
    assert r.sport == "BGMI" and r.detail == "GodLike Esports · India" and r.live_scores  # India plays PUBG Mobile as BGMI


def test_the_credits_name_liquipedia_and_its_licence():
    [entry] = credits.credits({"liquipedia_esports": 1}, stockfish=False)["data"]
    assert entry["name"] == "Liquipedia" and entry["what"].startswith("BGMI, PUBG Mobile, Free Fire and Apex Legends: battle royale standings")
    assert (entry["licence"], entry["licence_url"]) == ("CC BY-SA 3.0", "https://creativecommons.org/licenses/by-sa/3.0/")


def test_esports_players_stay_with_the_source_that_covers_their_game(monkeypatch):
    monkeypatch.setitem(structured.ADAPTERS, "liquipedia_esports", lp.LiquipediaEsports())
    football = type("Sportmonks", (), {"system": "sportmonks_football"})()
    esports = type("PandaScore", (), {"system": "pandascore_esports"})()
    assert structured.rebind(None, esports)  # no source yet: look them up
    assert structured.rebind("espn_soccer", football)  # their sport moved to a better source
    assert not structured.rebind("liquipedia_esports", esports)  # a BGMI pro isn't moved to PandaScore


def test_a_lobby_lane_waits_its_own_pace_when_nothing_moves(monkeypatch):
    source = FakeSource()
    source.live_cadence = 300
    lane(monkeypatch, source, {1: {"fixture": "a"}}, changed=False)
    assert structured.lane_round(source, {1: time.monotonic() - 120}, set()) == 300  # not every minute


# ---------------------------------------------------------------- the client


class FakeRedis:
    def __init__(self):
        self.counts: dict[str, int] = {}

    def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key, seconds):
        pass

    def get(self, key):
        return self.counts.get(key)


def test_the_key_goes_in_a_header_and_the_hour_is_shared_and_never_overspent(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": [{"match2id": "a"}]})

    monkeypatch.setattr(liquipedia, "_client", httpx.Client(transport=httpx.MockTransport(handler), headers=liquipedia._client.headers))
    monkeypatch.setattr(liquipedia, "token", lambda: "lp-key")
    monkeypatch.setattr(liquipedia, "BUDGET", budget.Budget("liquipedia", windows=(budget.Window(3600, 8, margin=4),), reserve=2, fan=1))
    with budget.in_lane("live"):
        assert liquipedia.get("match", 0, wiki=WIKI, conditions="[[match2id::a]]") == [{"match2id": "a"}]
    assert seen[0].headers["authorization"] == "Apikey lp-key" and "lp-key" not in str(seen[0].url)
    assert "sports-follow" in seen[0].headers["user-agent"]
    assert liquipedia.spare()  # 8 an hour, 4 kept back, 2 reserved: one more background read
    liquipedia.get("match", 0, wiki=WIKI)  # a refresh
    assert not liquipedia.spare()
    with pytest.raises(liquipedia.RateLimited):
        liquipedia.get("match", 0, wiki=WIKI)  # another refresh: into the reserve
    with budget.in_lane("fan"):
        liquipedia.get("match", 0, wiki=WIKI)  # a fan following a pro: their share is one an hour
        with pytest.raises(liquipedia.RateLimited):
            liquipedia.get("match", 0, wiki=WIKI)
    with budget.in_lane("live"):
        liquipedia.get("match", 0, wiki=WIKI)  # the 4th of 8: live games may use what's kept for them
        with pytest.raises(liquipedia.RateLimited):
            liquipedia.get("match", 0, wiki=WIKI)  # the 5th: the margin is never spent, even live
    assert len(seen) == 4
    assert liquipedia.any_of("match2id", ["a", "b"]) == "[[match2id::a]] OR [[match2id::b]]"


def test_without_redis_each_process_counts_its_own(monkeypatch):
    # Redis down: the budget falls back to this process's count instead of stopping every source.
    def broken():
        raise ConnectionError("redis down")

    monkeypatch.setattr(budget, "STORE", budget.RedisStore())
    monkeypatch.setattr(budget, "_fallback", budget.MemoryStore())
    monkeypatch.setattr(budget, "_redis", broken)
    small = budget.Budget("liquipedia", windows=(budget.Window(3600, 6, margin=4),))
    budget.take(small, lane_name="live")
    budget.take(small, lane_name="live")
    with pytest.raises(budget.RateLimited):
        budget.take(small, lane_name="live")


def test_snapshot_of_a_finished_lobby_needs_no_request(monkeypatch):
    calls = []
    monkeypatch.setattr(liquipedia, "get", lambda datatype, ttl, **params: calls.append(params) or [_lobby(3, finished=True)])
    adapter = lp.LiquipediaEsports()
    adapter.snapshots({1: {"wiki": WIKI, "match": "aBcDeFgHiJ_0003"}})
    snap = adapter.snapshot({"wiki": WIKI, "match": "aBcDeFgHiJ_0003"}, final=True)
    assert isinstance(snap, Snapshot) and snap.status == "final" and len(calls) == 1


def test_a_player_whose_game_it_cant_tell_is_esports_never_another_games():
    adapter = lp.LiquipediaEsports()
    assert adapter.sport_of(lp.PlayerRef("liquipedia_esports", "x:Y", "Y", [], [])) == "esports"
    assert adapter.sport_of(lp.PlayerRef("liquipedia_esports", "x:Y", "Y", [], [], league="dota2")) == "esports"  # a wiki it doesn't read
    assert adapter.sport_of(lp.parse_player(PLAYER, WIKI)) == "bgmi" and adapter.sport_of(lp.parse_player({**PLAYER, "nationality": "Nepal"}, WIKI)) == "pubgmobile"


def test_only_the_wikis_of_games_that_run_are_read_and_the_pubg_mobile_wiki_once_for_both(monkeypatch):
    from sports_follow import sports

    asked: list[str] = []
    monkeypatch.setattr(liquipedia, "get", lambda datatype, ttl, **params: asked.append(params["wiki"]) or [])
    monkeypatch.setattr(liquipedia, "spare", lambda: True)
    adapter = lp.LiquipediaEsports()
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key in ("football", "freefire")))
    adapter.search("Nobru")
    assert asked == ["freefire"]  # BGMI, PUBG Mobile and Apex off: their wikis aren't asked
    asked.clear()
    adapter.roster(lambda rows: 0, {"bgmi", "pubgmobile"})
    assert asked == ["pubgmobile"]  # both games' players are on one wiki: read once
    asked.clear()
    adapter.roster(lambda rows: 0, {"bgmi"})
    assert asked == ["pubgmobile"]
    asked.clear()
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key == "football"))
    assert adapter.search("Nobru") == [] and asked == []  # none of its games runs: not one request
