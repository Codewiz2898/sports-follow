"""Esports from PandaScore: parsers, the batched lane fetch, series and map moments, form, the registry
import's rows and search results. Against small made-up responses in PandaScore's documented shapes
(developers.pandascore.co), as for Sportmonks: none of theirs are committed."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import httpx

from sports_follow import credits, form, moments, registry, search, sports, structured
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
        "tournament": {"id": 3, "name": "Playoffs", "tier": "a"},  # PandaScore's tiers: "s" to "d", or "unranked"
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
    assert finished.sport == "valorant"  # each title is a sport of its own
    assert ps.parse_fixtures([{**FINAL, "videogame": {"id": 99, "slug": "unknown"}}])[0].sport == ""
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
    assert [sports.family(s) for s in ("Valorant", "CS2", "Counter-Strike", "League of Legends", "LoL", "Esports")] == ["valorant", "cs2", "cs2", "lol", "lol", "esports"]
    assert sports.family("Cricket") == "cricket"
    assert [sports.label(s) for s in ("CS2", "cs2", "League of Legends", "PUBG Mobile", "cricket", "esports")] == ["CS2", "CS2", "League of Legends", "PUBG Mobile", "Cricket", "Esports"]
    assert [sports.family(s) for s in ("Mobile Legends", "King of Glory", "Honor of Kings", "Rainbow Six Siege", "Free Fire MAX")] == ["mlbb", "hok", "hok", "r6siege", "freefire"]
    adapter = ps.PandascoreEsports()
    assert {s: adapter.sport_of(PlayerRef("pandascore_esports", "1", "x", [], [], league=s)) for s in ("cs-go", "kog", "league-of-legends")} == {"cs-go": "cs2", "kog": "hok", "league-of-legends": "lol"}
    assert structured.sport_name(adapter, ps.parse_player(PLAYER)) == "Valorant"
    stored_before = PlayerRef("pandascore_esports", "1", "amster", ["138811"], ["6666"], league="cs-go")  # a binding saved without a game
    assert structured.sport_name(adapter, stored_before) == "CS2"
    assert structured.sport_name(adapter, PlayerRef("pandascore_esports", "1", "amster", [], [], game="King of Glory")) == "Honor of Kings"  # its game's name now
    # The rules are esports', every game's: a CS2 pro's series ends like any esports series.
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
    rows = registry.roster_rows(KNOWN["pandascore_esports"], ps.rostered([PLAYER, {**PLAYER, "id": 5, "name": "Serral", "current_videogame": {"slug": "starcraft-2"}}, {**PLAYER, "id": 6, "name": "  "}]))
    [row] = rows
    assert row["qid"] == f"pandascore:{TENZ}" and len(row["qid"]) <= 20
    assert (row["sport"], row["name"], row["aliases"], row["teams"], row["league"], row["country"]) == ("valorant", "TenZ", ["Tyson Ngo"], ["Sentinels"], "valorant", "Canada")
    # Each pro under their game; a run of some games keeps only theirs.
    cs = {**PLAYER, "id": 7, "name": "s1mple", "current_videogame": {"slug": "cs-go"}}
    assert [r["sport"] for r in registry.roster_rows(KNOWN["pandascore_esports"], ps.rostered([PLAYER, cs]))] == ["valorant", "cs2"]
    assert [r["qid"] for r in registry.roster_rows(KNOWN["pandascore_esports"], ps.rostered([PLAYER, cs]), {"cs2"})] == ["pandascore:7"]
    assert row["ids"] == {"pandascore_esports": str(TENZ)} and row["current"] is True
    assert row["search_text"] == " tenz | tyson ngo |"


def test_an_esports_row_is_a_search_result_keyed_by_its_pandascore_id(monkeypatch):
    monkeypatch.setitem(ADAPTERS, "pandascore_esports", KNOWN["pandascore_esports"])  # it runs only with its token
    monkeypatch.setattr(registry, "live_system", lambda sport: "pandascore_esports" if sport == "valorant" else None)
    row = registry.roster_rows(KNOWN["pandascore_esports"], ps.rostered([PLAYER]))[0]
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


def test_live_now_lists_a_series_on_by_its_teams(monkeypatch):
    asked = []
    monkeypatch.setattr(pandascore, "get_json", lambda path, ttl, **params: asked.append((path, sorted(params))) or [LIVE, _match("finished", 2, 0, id=99)])
    [game] = ps.PandascoreEsports().live_now()
    # By game, as /matches/running names its filter: it answers "filter[videogame_id]" with HTTP 400.
    assert asked == [("/matches/running", ["filter[videogame]", "page[size]"])]
    assert game.snapshot.status == "live" and game.players == [] and len(game.teams) == 2  # a match names no players: live.py asks the registry


def test_live_now_keeps_the_big_tournaments_series_biggest_tier_first(monkeypatch):
    def on(match_id: int, league: str, tier: str | None) -> dict:
        return {**LIVE, "id": match_id, "league": {"id": match_id, "name": league}, "tournament": {"id": match_id, "name": "Playoffs", "tier": tier}}

    running = [on(1, "DreamLeague Closed Qualifier", "c"), on(2, "North America League", "a"), on(3, "ESEA Advanced", "d"), on(4, "CBLOL", "b"),
               on(5, "Worlds", "s"), on(6, "Thunderpick World Championship", "a"), on(7, "Streamers League", "unranked"), on(8, "New Cup", None),
               {**on(9, "No Tournament", None), "tournament": None}]
    asked = []
    monkeypatch.setattr(pandascore, "get_json", lambda path, ttl, **params: asked.append(path) or running)
    games = ps.PandascoreEsports().live_now()
    assert asked == ["/matches/running"]  # still the one request
    # S, then the two A's in PandaScore's order, then B; C, D, unranked and untiered series aren't the big games.
    assert [(g.source_id, g.tier) for g in games] == [("5", 3), ("2", 2), ("6", 2), ("4", 1)]
    assert games[0].competition == "Worlds Stage 2 2026"
    monkeypatch.setattr(pandascore, "get_json", lambda path, ttl, **params: [on(1, "DreamLeague Closed Qualifier", "c"), on(3, "ESEA Advanced", "d")])
    assert ps.PandascoreEsports().live_now() == []  # a quiet day: esports shows nothing


def test_the_schedule_is_the_big_tournaments_series_between_two_days_each_with_its_tier(monkeypatch):
    """The S and A tournaments first (on now, starting, ended: PandaScore filters tournaments by tier, not
    series), then their series in the window."""
    asked = []
    tours = {"/tournaments/running": [{"id": 3, "tier": "a"}, {"id": 4, "tier": "b"}], "/tournaments/upcoming": [{"id": 9, "tier": "s"}], "/tournaments/past": []}

    def get_json(path, ttl, **params):
        asked.append((path, params))
        if path in tours:
            return tours[path]
        return [{**FINAL, "tournament_id": 3}, _match("not_started", id=778, tournament_id=9, tournament={"id": 9, "name": "Playoffs", "tier": "s"}), _match("not_started", id=779, tournament_id=4)]

    monkeypatch.setattr(pandascore, "get_json", get_json)
    games = ps.PandascoreEsports().schedule(date(2026, 10, 9), date(2026, 10, 30))
    assert [(g.source_id, g.status, g.tier) for g in games] == [("777", "final", 2), ("778", "scheduled", 3), ("779", "scheduled", 0)]
    by_path = {path: params for path, params in asked}
    assert [path for path, _ in asked] == ["/tournaments/running", "/tournaments/upcoming", "/tournaments/past", "/matches"]
    assert by_path["/tournaments/running"]["filter[tier]"] == "s,a" and "filter[videogame_id]" in by_path["/tournaments/running"]
    assert by_path["/tournaments/upcoming"]["range[begin_at]"] == by_path["/tournaments/past"]["range[end_at]"] == "2026-10-09T00:00:00Z,2026-10-30T23:59:59Z"
    assert by_path["/matches"]["filter[tournament_id]"] == "3,9" and by_path["/matches"]["range[scheduled_at]"] == "2026-10-09T00:00:00Z,2026-10-30T23:59:59Z"


def test_no_big_tournament_no_series_asked_for(monkeypatch):
    asked = []
    monkeypatch.setattr(pandascore, "get_json", lambda path, ttl, **params: asked.append(path) or [])
    assert ps.PandascoreEsports().schedule(date(2026, 10, 9), date(2026, 10, 30)) == []
    assert "/matches" not in asked


def test_a_pro_whose_game_it_cant_tell_is_esports_never_another_games(monkeypatch):
    """No title, a title it doesn't cover (StarCraft), or an old binding with neither: the esports group, so a
    refresh never rewrites the pro's sport to Valorant."""
    adapter = ps.PandascoreEsports()
    for ref in (PlayerRef("pandascore_esports", "1", "x", [], []), PlayerRef("pandascore_esports", "1", "x", [], [], league="starcraft-2"),
                ps.parse_player({**PLAYER, "current_videogame": None})):
        assert adapter.sport_of(ref) == "esports", ref
        assert structured.sport_name(adapter, ref) == "Esports"


def test_only_the_games_that_run_are_read_and_none_when_none_do(monkeypatch):
    """Each title is a sport the admin panel turns off on its own: PandaScore asks for the running ones' ids only
    (Valorant's 26 off here), and nothing at all when none of its games runs."""
    asked: list[tuple[str, dict]] = []

    def get_json(path, ttl, **params):
        asked.append((path, params))
        return [{"id": 3, "tier": "s"}] if path.startswith("/tournaments") else []

    monkeypatch.setattr(pandascore, "get_json", get_json)
    monkeypatch.setattr(pandascore, "get_all", lambda path, ttl, pages=2, **params: asked.append((path, params)) or [])
    monkeypatch.setattr(pandascore, "spare", lambda: True)
    adapter = ps.PandascoreEsports()
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key != "valorant"))
    adapter.live_now(), adapter.schedule(date(2026, 10, 9), date(2026, 10, 30)), adapter.search("tenz"), adapter.find_teams("T1")
    ids = [params.get("filter[videogame]") or params.get("filter[videogame_id]") for _, params in asked if "filter[videogame]" in params or "filter[videogame_id]" in params]
    assert len(ids) == 6 and all("26" not in i.split(",") and "3" in i.split(",") for i in ids), ids  # CS2's 3, never Valorant's 26
    asked.clear()
    adapter.roster(lambda rows: 0, {"cs2"})  # the registry's run of one game: only its pros
    assert [params["filter[videogame_id]"] for _, params in asked] == ["3"]
    asked.clear()
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key not in adapter.sports))
    assert (adapter.live_now(), adapter.schedule(date(2026, 10, 9), date(2026, 10, 30)), adapter.search("tenz"), adapter.find_teams("T1")) == ([], [], [], [])
    assert asked == []  # not one request


def test_a_match_page_reads_one_series_by_its_teams(monkeypatch):
    asked = []
    monkeypatch.setattr(pandascore, "get_json", lambda path, ttl, **params: asked.append(path) or LIVE)
    game = ps.PandascoreEsports().match("777")
    assert asked == ["/matches/777"]
    assert game is not None and (game.source_id, game.teams, game.league, game.tier, game.players) == ("777", ["Sentinels", "NRG"], "valorant", 2, [])
    assert game.snapshot.state["maps"][1]["status"] == "running"

    def missing(path, ttl, **params):
        raise pandascore.NotFound("PandaScore has nothing at /matches/1")

    monkeypatch.setattr(pandascore, "get_json", missing)
    assert ps.PandascoreEsports().match("1") is None


def test_a_match_page_reads_a_finished_series_map_by_map_and_who_won(monkeypatch):
    monkeypatch.setattr(pandascore, "get_json", lambda path, ttl, **params: FINAL)  # a made-up series, over
    game = ps.PandascoreEsports().match("777")
    assert game is not None and (game.snapshot.status, game.snapshot.score_label) == ("final", "Sentinels 2 – 1 NRG")
    esports = sports.get("Valorant")
    state = game.snapshot.state
    assert esports.verdict(state, game.snapshot.score_label, game.snapshot.clock_label) == "Sentinels won 2–1"
    assert [(m["n"], m["winner"]) for m in esports.match_page(state, [], {}, {})["maps"]] == [(1, "Sentinels"), (2, "NRG"), (3, "Sentinels")]
