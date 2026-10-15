"""Esports from PandaScore: parsers, the batched lane fetch, series and map moments, form, the registry
import's rows and search results. Against small made-up responses in PandaScore's documented shapes
(developers.pandascore.co), as for Sportmonks: none of theirs are committed."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import httpx

from sports_follow import credits, form, moments, registry, search, sports, structured
from sports_follow.sports.esports import game_of_league
from sports_follow.adapters import ADAPTERS, KNOWN, SearchResult, budget, pandascore, pandascore_esports as ps
from sports_follow.adapters.base import PlayerRef
from sports_follow.models import Athlete

SEN, NRG = 1234, 5678
TENZ = 31001


def _team(team_id: int, name: str, acronym: str) -> dict:
    return {"type": "Team", "opponent": {"id": team_id, "name": name, "acronym": acronym, "slug": name.lower(), "location": "US"}}


def _game(position: int, status: str, winner: int | None = None) -> dict:
    return {"id": 900 + position, "position": position, "status": status, "finished": status == "finished", "winner": {"id": winner, "type": "Team"}, "winner_type": "Team"}


def _match(status: str, sen: int = 0, nrg: int = 0, games: list[dict] | None = None, **extra) -> dict:
    return {
        "id": 777,
        "name": "Upper bracket final: SEN vs NRG",
        "status": status,
        "scheduled_at": "2026-10-11T19:00:00Z",
        "begin_at": None if status == "not_started" else "2026-10-11T19:12:00Z",
        "number_of_games": 3,
        "match_type": "best_of",
        "forfeit": False,
        "draw": False,
        "opponents": [_team(SEN, "Sentinels", "SEN"), _team(NRG, "NRG", "NRG")],
        "results": [{"team_id": SEN, "score": sen}, {"team_id": NRG, "score": nrg}],
        "winner_id": None,
        "winner_type": "Team",
        "games": games or [_game(1, "not_started"), _game(2, "not_started"), _game(3, "not_started")],
        "league": {"id": 1, "name": "VCT Americas"},
        "serie": {"id": 2, "full_name": "Stage 2 2026"},
        "tournament": {"id": 3, "name": "Playoffs"},
        "videogame": {"id": 26, "name": "Valorant", "slug": "valorant"},
        **extra,
    }


FINAL = _match("finished", 2, 1, [_game(1, "finished", SEN), _game(2, "finished", NRG), _game(3, "finished", SEN)], winner_id=SEN)
LIVE = _match("running", 1, 0, [_game(1, "finished", SEN), _game(2, "running"), _game(3, "not_started")])
PLAYER = {
    "id": TENZ, "name": "TenZ", "first_name": "Tyson", "last_name": "Ngo", "nationality": "CA", "birthday": None, "active": True,
    "role": None, "slug": "tenz", "current_team": {"id": SEN, "name": "Sentinels", "acronym": "SEN"},
    "current_videogame": {"id": 26, "name": "Valorant", "slug": "valorant"},
}


def test_fixtures_read_both_teams_the_stage_and_the_result():
    upcoming = {**_match("not_started"), "id": 778, "name": "SEN vs NRG", "opponents": [_team(SEN, "Sentinels", "SEN")]}
    finished, scheduled = ps.parse_fixtures([FINAL, upcoming])
    assert finished.title == "Sentinels vs NRG"
    assert finished.competition == "VCT Americas Stage 2 2026"
    assert finished.detail == "Upper bracket final · Bo3"
    assert (finished.status, finished.team_ids, finished.locator) == ("final", [str(SEN), str(NRG)], {"match": "777"})
    assert finished.start_utc == datetime(2026, 10, 11, 19, 12, tzinfo=timezone.utc)  # when it began
    assert finished.result["teams"][0] == {"id": str(SEN), "name": "Sentinels", "score": 2, "winner": True}
    # A bracket slot still to be decided, and no prefix in the name: the tournament is the stage.
    assert (scheduled.title, scheduled.detail, scheduled.result) == ("Sentinels vs TBD", "Playoffs · Bo3", None)
    assert scheduled.start_utc == datetime(2026, 10, 11, 19, 0, tzinfo=timezone.utc)
    # A 1v1 title's match (players, not teams) isn't a fixture for a team.
    assert ps.parse_fixtures([{**FINAL, "opponents": [{"type": "Player", "opponent": {"id": 1, "name": "Serral"}}]}]) == []


def test_result_from_the_players_team_side():
    [f] = ps.parse_fixtures([FINAL])
    assert ps.result_label(f, [str(SEN)]) == "Won 2–1"
    assert ps.result_label(f, [str(NRG)]) == "Lost 1–2"
    assert ps.result_label(f, ["999"]) == "Sentinels 2–1 NRG"
    [forfeit] = ps.parse_fixtures([{**FINAL, "forfeit": True, "results": [{"team_id": SEN, "score": 0}, {"team_id": NRG, "score": 0}]}])
    assert ps.result_label(forfeit, [str(SEN)]) == "Won by forfeit"
    assert ps.result_label(forfeit, [str(NRG)]) == "Lost by forfeit"


def test_a_running_series_shows_the_map_being_played():
    snap = ps.parse_match(LIVE)
    assert (snap.status, snap.score_label, snap.clock_label) == ("live", "Sentinels 1 – 0 NRG", "Map 2")
    assert snap.state["kind"] == "esports" and snap.state["game"] == "Valorant" and snap.state["format"] == "Bo3"
    assert [m["winner"] for m in snap.state["maps"]] == [str(SEN), None, None]
    assert snap.lines == {}  # no player numbers on the free plan
    between = ps.parse_match(_match("running", 1, 1, [_game(1, "finished", SEN), _game(2, "finished", NRG), _game(3, "not_started")]))
    assert between.clock_label == "Between maps"


def test_a_finished_series_leaves_out_maps_never_played():
    two_nil = _match("finished", 2, 0, [_game(1, "finished", SEN), _game(2, "finished", SEN), _game(3, "not_played")], winner_id=SEN)
    snap = ps.parse_match(two_nil)
    assert (snap.status, snap.score_label, snap.clock_label) == ("final", "Sentinels 2 – 0 NRG", "Final")
    assert len(snap.state["maps"]) == 2
    assert [t["winner"] for t in snap.state["teams"]] == [True, False]
    scheduled = ps.parse_match(_match("not_started"))
    assert (scheduled.score_label, scheduled.clock_label) == ("Sentinels vs NRG", "Bo3")


def test_players_by_handle_with_their_team_and_game():
    ref = ps.parse_player(PLAYER)
    assert (ref.system, ref.athlete_id, ref.name, ref.team_ids, ref.team_names, ref.league) == ("pandascore_esports", str(TENZ), "TenZ", [str(SEN)], ["Sentinels"], "valorant")
    assert ps.full_name(PLAYER) == "Tyson Ngo"
    free_agent = ps.parse_player({**PLAYER, "current_team": None})
    assert (free_agent.team_ids, free_agent.team_names) == ([], [])


def test_find_player_takes_the_one_pro_with_the_handle(monkeypatch):
    adapter = ps.PandascoreEsports()
    monkeypatch.setattr(adapter, "search", lambda name: [ps.parse_player(PLAYER), ps.parse_player({**PLAYER, "id": 2, "name": "TenZin"})])
    assert adapter.find_player("tenz").athlete_id == str(TENZ)
    retired = ps.parse_player({**PLAYER, "id": 3, "current_team": None})
    monkeypatch.setattr(adapter, "search", lambda name: [ps.parse_player(PLAYER), retired])
    assert adapter.find_player("TenZ").athlete_id == str(TENZ)  # of two, the one on a team
    monkeypatch.setattr(adapter, "search", lambda name: [ps.parse_player(PLAYER), ps.parse_player({**PLAYER, "id": 4})])
    assert adapter.find_player("TenZ") is None  # two pros on teams: the agent decides


def test_the_lane_fetches_every_series_in_one_request(monkeypatch):
    calls = []

    def fake(path, ttl, **params):
        calls.append((path, params))
        return [LIVE, {**FINAL, "id": 779}]

    monkeypatch.setattr(pandascore, "get_json", fake)
    adapter = ps.PandascoreEsports()
    snaps = adapter.snapshots({1: {"match": "777"}, 2: {"match": "779"}, 3: {"match": "780"}})
    assert calls == [("/matches", {"filter[id]": "777,779,780", "page[size]": 100})]
    assert snaps[1].status == "live" and snaps[2].status == "final" and 3 not in snaps
    # The finished one is kept: its scorecard is served without another request.
    assert adapter.snapshot({"match": "779"}, final=True).score_label == "Sentinels 2 – 1 NRG"
    assert len(calls) == 1


def test_team_form_over_the_window():
    lost = {**FINAL, "id": 780, "results": [{"team_id": SEN, "score": 0}, {"team_id": NRG, "score": 2}], "winner_id": NRG}
    forfeit = {**FINAL, "id": 781, "forfeit": True, "results": [{"team_id": SEN, "score": 1}, {"team_id": NRG, "score": 0}]}
    stats, note = ps.team_form(ps.parse_fixtures([FINAL, lost, forfeit]), [str(SEN)], "Sentinels")
    assert stats == [{"label": "Series", "value": "2–1"}, {"label": "Series won", "value": "67%"}, {"label": "Maps", "value": "2–3"}]
    assert note == "Sentinels, last 3 series"
    assert ps.team_form([], [str(SEN)], "Sentinels") == ([], "")


# ---------------------------------------------------------------- moments


def _seen(match: dict, teams: list[str] | None = None) -> moments.Seen:
    snap = ps.parse_match(match)
    return moments.Seen(snap.status, snap.state, None, snap.score_label, snap.clock_label, teams=[str(SEN)] if teams is None else teams)


def test_a_series_starting_and_each_map_from_the_teams_side():
    before = _seen(_match("not_started"))
    started = _seen(_match("running", 0, 0, [_game(1, "running"), _game(2, "not_started"), _game(3, "not_started")]))
    [start] = moments.detect("Esports", "TenZ", str(TENZ), before, started)
    assert (start.kind, start.level, start.title, start.body) == ("start", "key", "TenZ's series has started", "Sentinels v NRG · Bo3 · Upper bracket final")
    one_nil = _seen(LIVE)
    [won] = moments.detect("Esports", "TenZ", str(TENZ), started, one_nil)
    assert (won.kind, won.level, won.title, won.body, won.detail) == ("map", "minor", "TenZ: Sentinels win map 1", "Sentinels 1–0 NRG", "map1")
    one_all = _seen(_match("running", 1, 1, [_game(1, "finished", SEN), _game(2, "finished", NRG), _game(3, "not_started")]))
    [lost] = moments.detect("Esports", "TenZ", str(TENZ), one_nil, one_all)
    assert lost.title == "TenZ: Sentinels lose map 2"
    assert moments.detect("Esports", "TenZ", str(TENZ), None, one_all) == []  # a first look is the baseline


def test_the_series_result_from_the_teams_side():
    one_all = _seen(_match("running", 1, 1, [_game(1, "finished", SEN), _game(2, "finished", NRG), _game(3, "running")]))
    [final] = moments.detect("Esports", "TenZ", str(TENZ), one_all, _seen(FINAL))
    assert (final.kind, final.level, final.title, final.body) == ("final", "key", "TenZ: Sentinels win 2–1", "v NRG · Upper bracket final")
    [nrg] = moments.detect("Esports", "s0m", "1", one_all, _seen(FINAL, teams=[str(NRG)]))
    assert nrg.title == "s0m: NRG lose 1–2"
    [forfeit] = moments.detect("Esports", "TenZ", str(TENZ), _seen(_match("not_started")), _seen({**FINAL, "forfeit": True}))
    assert forfeit.title == "TenZ: Sentinels win by forfeit"
    # Moved to another team since: the plain score.
    [other] = moments.detect("Esports", "TenZ", str(TENZ), one_all, _seen(FINAL, teams=["42"]))
    assert other.title == "TenZ: Sentinels 2 – 1 NRG"


def test_a_forfeit_is_a_result():
    # As PandaScore sends every forfeit (all 100 in /matches/past?filter[forfeit]=true on 2026-10-13):
    # "canceled", with forfeit set, the series awarded (winner_id) and no map played. It was stored as
    # postponed, "Cancelled", with no result.
    unplayed = [_game(1, "not_started"), _game(2, "not_started"), _game(3, "not_started")]
    forfeit = _match("canceled", games=unplayed, forfeit=True, winner_id=SEN, begin_at=None)
    [f] = ps.parse_fixtures([forfeit])
    assert (f.status, f.start_utc) == ("final", datetime(2026, 10, 11, 19, 0, tzinfo=timezone.utc))
    assert (ps.result_label(f, [str(SEN)]), ps.result_label(f, [str(NRG)])) == ("Won by forfeit", "Lost by forfeit")
    snap = ps.parse_match(forfeit)
    assert (snap.status, snap.clock_label, snap.state["forfeit"], snap.state["maps"]) == ("final", "Forfeit", True, [])
    assert [t["winner"] for t in snap.state["teams"]] == [True, False]
    [final] = moments.detect("Esports", "TenZ", str(TENZ), _seen(_match("not_started")), _seen(forfeit))
    assert (final.kind, final.title) == ("final", "TenZ: Sentinels win by forfeit")
    adapter = ps.PandascoreEsports()
    adapter._keep([forfeit])
    assert "777" in adapter._seen  # a finished series' snapshot needs no request of its own
    # Cancelled with the series awarded to nobody is still off.
    cancelled = ps.parse_match(_match("canceled", games=unplayed, begin_at=None))
    assert (cancelled.status, cancelled.clock_label) == ("postponed", "Cancelled")


def test_a_pros_sport_is_their_game():
    assert {sports.family(s) for s in ("Valorant", "CS2", "BGMI", "Free Fire", "League of Legends", "Esports")} == {"esports"}
    assert sports.family("Cricket") == "cricket"
    assert [sports.label(s) for s in ("CS2", "League of Legends", "PUBG Mobile", "cricket", "esports")] == ["CS2", "League of Legends", "PUBG Mobile", "Cricket", "Esports"]
    assert (game_of_league("cs-go"), game_of_league("pubgmobile", "India"), game_of_league("pubgmobile", "Nepal"), game_of_league("freefire")) == ("CS2", "BGMI", "PUBG Mobile", "Free Fire")
    assert (game_of_league("mlbb"), game_of_league("kog"), game_of_league("apexlegends"), game_of_league("pubg")) == ("Mobile Legends", "King of Glory", "Apex Legends", "PUBG")  # PUBG's own API
    assert {sports.family(s) for s in ("Mobile Legends", "Apex Legends", "PUBG", "Rainbow Six Siege")} == {"esports"}
    adapter = ps.PandascoreEsports()
    assert structured.sport_name(adapter, ps.parse_player(PLAYER)) == "Valorant"
    stored_before = PlayerRef("pandascore_esports", "1", "amster", ["138811"], ["6666"], league="cs-go")  # a binding saved without a game
    assert structured.sport_name(adapter, stored_before) == "CS2"
    # The rules read the family: a CS2 pro's series ends like any esports series.
    [final] = moments.detect("CS2", "TenZ", str(TENZ), _seen(LIVE), _seen(FINAL))
    assert final.title == "TenZ: Sentinels win 2–1"
    assert form.summarize("Valorant", [({"result": "Won 2–1"}, None)], [])[1] == "Last 1 series"


def test_form_counts_series_and_maps():
    items = [({"result": "Won 2–1"}, None), ({"result": "Lost 0–2"}, None), ({"result": "Won by forfeit"}, None)]
    stats, note = form.summarize("Esports", items, ["Sentinels"])
    assert stats == [{"label": "W–L", "value": "2–1"}, {"label": "Maps", "value": "2–3"}]
    assert note == "Last 3 series"


# ---------------------------------------------------------------- registry, search, credits, scheduling


def test_registry_rows_from_pandascore_players():
    rows = registry.roster_rows("pandascore_esports", ps.rostered([PLAYER, {**PLAYER, "id": 5, "name": "Serral", "current_videogame": {"slug": "starcraft-2"}}, {**PLAYER, "id": 6, "name": "  "}]))
    [row] = rows
    assert row["qid"] == f"pandascore:{TENZ}" and len(row["qid"]) <= 20
    assert (row["sport"], row["name"], row["aliases"], row["teams"], row["league"], row["country"]) == ("esports", "TenZ", ["Tyson Ngo"], ["Sentinels"], "valorant", "Canada")
    assert row["ids"] == {"pandascore_esports": str(TENZ)} and row["current"] is True
    assert row["search_text"] == " tenz | tyson ngo |"


def test_an_esports_row_is_a_search_result_keyed_by_its_pandascore_id(monkeypatch):
    monkeypatch.setitem(ADAPTERS, "pandascore_esports", KNOWN["pandascore_esports"])  # it runs only with its token
    monkeypatch.setattr(registry, "live_system", lambda sport: "pandascore_esports" if sport == "esports" else None)
    row = registry.roster_rows("pandascore_esports", ps.rostered([PLAYER]))[0]
    r = search.from_athlete(Athlete(**{k: v for k, v in row.items() if k != "search_text"}), "tyson ngo")
    assert (r.key, r.system, r.athlete_id, r.sport) == (f"pandascore_esports:{TENZ}", "pandascore_esports", str(TENZ), "Valorant")  # the game, not "Esports"
    assert r.qid is None  # following goes by the PandaScore id; the server takes only Wikidata ids as qids
    assert r.detail == "Sentinels · Canada" and r.exact and r.live_scores


def test_a_pro_on_a_team_ranks_before_a_teamless_namesake():
    # PandaScore gives no popularity, and calls long-retired players active: being on a team decides.
    veteran = SearchResult(key="pandascore_esports:7", name="Move", sport="Esports", detail="League of Legends", live_scores=True, inactive=True)
    pro = SearchResult(key="pandascore_esports:9001", name="Move", sport="Esports", detail="T1 · League of Legends", live_scores=True)
    assert [r.key for r in search.rank("move", [], [veteran, pro])] == ["pandascore_esports:9001", "pandascore_esports:7"]


def test_the_credits_name_pandascore_when_it_runs():
    page = credits.credits({"pandascore_esports": 1, "lichess_chess": 1}, stockfish=False)
    assert [c["name"] for c in page["data"]] == ["Lichess", "PandaScore"]
    assert page["data"][1]["what"].startswith("Valorant, CS2, League of Legends, Dota 2 and other team games: series schedules and results")


def test_an_armed_series_moved_hours_later_is_disarmed():
    assert structured.next_status("armed", "scheduled", moved_later=True) == "scheduled"
    assert structured.next_status("armed", "scheduled") == "armed"  # a schedule minutes old
    assert structured.next_status("live", "scheduled", moved_later=True) == "live"


def test_a_series_without_player_lines_still_has_a_baseline():
    live = ps.parse_match(LIVE)
    earlier = SimpleNamespace(status="live", state=ps.parse_match(_match("running", 0, 0)).state, score_label="Sentinels 0 – 0 NRG", clock_label="Map 1")
    before = structured.baseline(earlier, None, live)
    assert before is not None and before.state == earlier.state  # so map 1 is news on this poll
    assert structured.baseline(None, None, live) is None  # nothing seen yet: a first look
    # A source with lines: no earlier line for the player while it was live is still a first look.
    with_lines = SimpleNamespace(**{**live.__dict__, "lines": {"9": object()}})
    assert structured.baseline(earlier, None, with_lines) is None


def test_a_series_cancelled_last_week_isnt_upcoming():
    cancelled = {**_match("canceled"), "id": 1, "scheduled_at": "2026-10-06T18:45:00Z"}
    later = {**_match("canceled"), "id": 2, "scheduled_at": "2026-10-20T18:45:00Z"}
    rows = ps.parse_fixtures([cancelled, LIVE, {**_match("not_started"), "id": 3, "scheduled_at": "2026-10-14T11:00:00Z"}, later])
    ahead = structured.upcoming(rows, datetime(2026, 10, 12, 19, 30, tzinfo=timezone.utc))
    assert [(f.source_id, f.status) for f in ahead] == [("777", "live"), ("3", "scheduled"), ("2", "postponed")]


def test_history_pages_go_back_a_year_before_the_window():
    pages = ps.PandascoreEsports().history_pages(PlayerRef("pandascore_esports", str(TENZ), "TenZ", [str(SEN)], ["Sentinels"]), date(2026, 10, 13))
    assert pages[0] == {"team": str(SEN), "from": "2026-05-16", "to": "2026-08-13"}
    assert len(pages) == 4 and pages[-1]["from"] == "2025-08-19"


# ---------------------------------------------------------------- the client


def test_the_token_goes_in_a_header_and_a_429_pauses_reads(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "page" in request.url.params.get("filter[id]", ""):
            return httpx.Response(429, json={"error": "Too many requests"})
        return httpx.Response(200, json=[{"id": 1}], headers={"x-rate-limit-remaining": "412"})

    monkeypatch.setattr(pandascore, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(pandascore, "token", lambda: "secret-token")
    monkeypatch.setattr(pandascore, "_cache", {})
    assert pandascore.get_json("/matches", 0, **{"filter[id]": "1"}) == [{"id": 1}]
    assert seen[0].headers["authorization"] == "Bearer secret-token" and "secret-token" not in str(seen[0].url)
    assert budget.left(pandascore.BUDGET) == 412  # what PandaScore says is left, shared by every process
    try:
        pandascore.get_json("/matches", 0, **{"filter[id]": "page"})
        raise AssertionError("expected a rate limit")
    except pandascore.RateLimited:
        pass
    try:
        pandascore.get_json("/matches", 0, **{"filter[id]": "1"})
        raise AssertionError("expected the pause")
    except pandascore.RateLimited:
        assert len(seen) == 2  # paused: nothing sent


def test_live_now_lists_every_series_on_by_its_teams(monkeypatch):
    asked = []
    monkeypatch.setattr(pandascore, "get_json", lambda path, ttl, **params: asked.append(path) or [LIVE, _match("finished", 2, 0, id=99)])
    [game] = ps.PandascoreEsports().live_now()
    assert asked == ["/matches/running"]
    assert game.snapshot.status == "live" and game.players == [] and len(game.teams) == 2  # a match names no players: live.py asks the registry
