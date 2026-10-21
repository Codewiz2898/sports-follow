"""Dota 2 inside the map, from OpenDota, laid over a PandaScore series: matching teams and players, the
live map, finished maps' numbers, the series board's state, alerts, and the client's budget, against small
made-up responses in OpenDota's shapes (checked against its live list, proMatches and a match, 13 Oct
2026). And each finished map's part of the match page, against a recorded series: the BLAST Slam VIII grand
final, 11 Oct 2026, Team Spirit 1-3 Team Yandex (tests/fixtures/opendota: OpenDota's four matches, fetched
21 Oct, trimmed to the fields stann reads; its heroes and items, only those the series has)."""

from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from sports_follow import credits, moments, structured
from sports_follow.adapters import dota, opendota, pandascore, pandascore_esports as ps
from sports_follow.adapters import budget, cache
from sports_follow.adapters.base import AdapterError
from sports_follow.models import Event

SPIRIT, LIQUID = 1669, 1652
BEGIN = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
ROSTER = [{"id": 101, "name": "Yatoro"}, {"id": 102, "name": "Collapse"}, {"id": 201, "name": "miCKe"}, {"id": 202, "name": "Boxi"}]
PROS = [
    {"account_id": 321580662, "name": "Yatoro", "team_name": "Team Spirit"},
    {"account_id": 113331514, "name": "Collapse", "team_name": "Team Spirit"},
    {"account_id": 152962063, "name": "miCKe", "team_name": "Team Liquid"},
    {"account_id": 77490514, "name": "Boxi", "team_name": "Team Liquid"},
    {"account_id": 5, "name": "Boxi", "team_name": "Some Other Boxi"},
]
HEROES = {41: "Faceless Void", 129: "Mars", 8: "Juggernaut", 26: "Lion"}
TEAMS = [{"id": str(SPIRIT), "name": "Team Spirit"}, {"id": str(LIQUID), "name": "Team Liquid"}]


def _player(account: int, hero: int, kills: int, deaths: int, assists: int, radiant: bool) -> dict:
    return {"account_id": account, "hero_id": hero, "kills": kills, "deaths": deaths, "assists": assists, "gold_per_min": 600 + kills, "last_hits": 300, "isRadiant": radiant}


def _details(radiant_win: bool, yatoro=(12, 2, 9)) -> dict:
    # Spirit on Radiant: Yatoro and Collapse; Liquid on Dire: miCKe and Boxi.
    return {"radiant_win": radiant_win, "version": 22, "players": [_player(321580662, 41, *yatoro, True), _player(113331514, 129, 3, 4, 20, True), _player(152962063, 8, 7, 6, 5, False), _player(77490514, 26, 1, 8, 11, False)]}


LIVE_GAME = {
    "league_id": 17000, "match_id": 9043,  "game_time": 2232, "radiant_lead": -18118, "radiant_score": 19, "dire_score": 24,
    "team_name_radiant": "Liquid", "team_name_dire": "Spirit",  # sides swapped this map, names shortened
    "players": [{"account_id": 321580662, "name": "Yatoro", "hero_id": 8}, {"account_id": 152962063, "name": "miCKe", "hero_id": 41}],
}
PRO_MATCHES = [
    {"match_id": 9001, "start_time": int(BEGIN.timestamp()) + 60, "radiant_name": "Team Spirit", "dire_name": "Team Liquid"},
    {"match_id": 9002, "start_time": int(BEGIN.timestamp()) + 3000, "radiant_name": "Team Liquid", "dire_name": "Team Spirit"},
    {"match_id": 8000, "start_time": int(BEGIN.timestamp()) - 86400, "radiant_name": "Team Spirit", "dire_name": "Team Liquid"},  # yesterday's series
    {"match_id": 9500, "start_time": int(BEGIN.timestamp()) + 100, "radiant_name": "Tundra Esports", "dire_name": "Team Liquid"},
]


def test_teams_match_without_their_filler_words():
    assert dota.same_team("Team Spirit", "Spirit") and dota.same_team("Tundra Esports", "tundra") and dota.same_team("Gaimin Gladiators", "Gaimin Gladiators")
    assert not dota.same_team("Team Spirit", "Team Liquid") and not dota.same_team("", "Team Spirit")


def test_players_are_matched_to_steam_accounts_by_name_and_team():
    found = dota.accounts(ROSTER, ["Team Spirit", "Team Liquid"], PROS)
    assert found == {321580662: "101", 113331514: "102", 152962063: "201", 77490514: "202"}  # the other Boxi isn't on either team
    # A player OpenDota's pro list doesn't know is found in the live game by name.
    assert dota.accounts([{"id": 301, "name": "Newbie"}], ["Team Spirit"], [], [{"account_id": 999, "name": "newbie"}]) == {999: "301"}


def test_players_whose_names_differ_only_in_spacing_case_or_marks_are_matched():
    roster = [{"id": 1, "name": "Chira JUNIOR"}, {"id": 2, "name": "not me"}, {"id": 3, "name": "watson"}, {"id": 4, "name": "ATF"}]
    pros = [{"account_id": 11, "name": "CHIRA_JUNIOR", "team_name": "Team Yandex"}, {"account_id": 12, "name": "not_me", "team_name": "Team Spirit"},
            {"account_id": 13, "name": "医者watson`", "team_name": "Team Yandex"}, {"account_id": 14, "name": "AMMAR_THE_F", "team_name": "Team Yandex"}]
    assert dota.accounts(roster, ["Team Yandex", "Team Spirit"], pros) == {11: "1", 12: "2", 13: "3"}  # a different handle isn't guessed


def test_the_live_map_from_either_side():
    game = dota.find_live([{"league_id": 0, "team_name_radiant": "Team Spirit", "team_name_dire": "Team Liquid"}, LIVE_GAME], TEAMS)
    assert game is LIVE_GAME  # a game outside a league isn't the series' map
    live = dota.live_map(game, TEAMS, {321580662: "101", 152962063: "201"}, HEROES, 3)
    assert live == {
        "n": 3, "clock": "37:12",
        "radiant": {"id": str(LIQUID), "name": "Team Liquid", "kills": 19}, "dire": {"id": str(SPIRIT), "name": "Team Spirit", "kills": 24},
        "gold_lead": {"id": str(SPIRIT), "amount": 18118}, "heroes": {"101": "Juggernaut", "201": "Faceless Void"},
    }


def test_the_clock_says_draft_before_the_horn():
    assert (dota.clock(2232), dota.clock(0), dota.clock(-40), dota.clock(None)) == ("37:12", "draft", "draft", "draft")


def test_finished_maps_are_the_series_own_in_order():
    assert dota.find_maps(PRO_MATCHES, TEAMS, int(BEGIN.timestamp())) == [9001, 9002]
    early = {"match_id": 8999, "start_time": int(BEGIN.timestamp()) - 17 * 60, "radiant_name": "Team Liquid", "dire_name": "Team Spirit"}
    assert dota.find_maps([early, *PRO_MATCHES], TEAMS, int(BEGIN.timestamp()))[0] == 8999  # OpenDota's clock runs 17 minutes ahead


def test_a_finished_maps_numbers_by_player():
    stats = dota.map_stats(_details(radiant_win=True), {321580662: "101", 77490514: "202"}, HEROES)
    assert stats["101"] == {"hero": "Faceless Void", "kills": 12, "deaths": 2, "assists": 9, "gpm": 612, "last_hits": 300, "won": True}
    assert stats["202"]["won"] is False and set(stats) == {"101", "202"}


def test_lines_say_the_hero_now_the_last_map_and_the_series_totals():
    maps = [{"n": 1, "players": {"101": dota.map_stats(_details(True), {321580662: "101"}, HEROES)["101"]}},
            {"n": 2, "players": {"101": dota.map_stats(_details(False, (4, 5, 6)), {321580662: "101"}, HEROES)["101"]}}]
    live = {"n": 3, "heroes": {"101": "Juggernaut"}}
    assert dota.lines(maps, live, final=False)["101"].headline == "On Juggernaut · map 3"
    between = dota.lines(maps, None, final=False)["101"]
    assert between.headline == "4/5/6 on Faceless Void · map 2"
    assert between.stats == [{"label": "K/D/A", "value": "16/7/15"}, {"label": "GPM", "value": "608"}, {"label": "Last hits", "value": "600"}, {"label": "Heroes", "value": "Faceless Void"}]
    assert dota.lines(maps, None, final=True)["101"].headline == "16/7/15 over 2 maps"


# ---------------------------------------------------------------- over a PandaScore series


def _series(status: str, games: list[tuple[str, int | None]]) -> dict:
    return {
        "id": 777, "name": "Grand final: TS vs TL", "status": status, "begin_at": BEGIN.strftime("%Y-%m-%dT%H:%M:%SZ"), "scheduled_at": BEGIN.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "number_of_games": 5, "match_type": "best_of", "videogame": {"slug": "dota-2", "name": "Dota 2"},
        "opponents": [{"type": "Team", "opponent": {"id": SPIRIT, "name": "Team Spirit", "acronym": "TS"}}, {"type": "Team", "opponent": {"id": LIQUID, "name": "Team Liquid", "acronym": "TL"}}],
        "results": [{"team_id": SPIRIT, "score": sum(1 for _, w in games if w == SPIRIT)}, {"team_id": LIQUID, "score": sum(1 for _, w in games if w == LIQUID)}],
        "games": [{"position": i + 1, "status": s, "winner": {"id": w, "type": "Team"}} for i, (s, w) in enumerate(games)],
        "winner_id": SPIRIT if status == "finished" else None, "winner_type": "Team",
    }


@pytest.fixture
def opendota_up(monkeypatch):
    calls = []
    monkeypatch.setattr(opendota, "ENABLED", True)
    monkeypatch.setattr(opendota, "live", lambda: calls.append("live") or [LIVE_GAME])
    monkeypatch.setattr(opendota, "pro_matches", lambda: calls.append("proMatches") or PRO_MATCHES)
    monkeypatch.setattr(opendota, "match", lambda mid: calls.append(f"match {mid}") or {9001: _details(True), 9002: _details(False, (4, 5, 6))}.get(mid))
    monkeypatch.setattr(opendota, "heroes", lambda: HEROES)
    monkeypatch.setattr(opendota, "items", lambda: {})
    monkeypatch.setattr(opendota, "pro_players", lambda: PROS)
    rosters = {f"/teams/{SPIRIT}": {"players": ROSTER[:2]}, f"/teams/{LIQUID}": {"players": ROSTER[2:]}}
    monkeypatch.setattr(pandascore, "get_json", lambda path, ttl, **params: rosters[path])
    monkeypatch.setattr(ps.bus, "sync_redis", lambda redis=FakeRedis(): redis)  # noqa: B008  one FakeRedis for every call: the series' remembered maps
    return calls


def test_a_live_dota_series_shows_the_map_inside_and_each_players_numbers(opendota_up):
    m = _series("running", [("finished", SPIRIT), ("finished", LIQUID), ("running", None)])
    snap = ps.parse_match(m)
    ps.PandascoreEsports()._dota(m, snap)
    assert snap.clock_label == "Map 3 · 37:12"
    assert [mp["n"] for mp in snap.state["dota"]["maps"]] == [1, 2] and snap.state["dota"]["live"]["dire"]["kills"] == 24
    assert snap.lines["101"].headline == "On Juggernaut · map 3"  # Yatoro, by his PandaScore id
    assert snap.lines["201"].stats[0] == {"label": "K/D/A", "value": "14/12/10"}
    assert "match 9001" in opendota_up and "match 8000" not in opendota_up  # yesterday's series isn't read


def test_other_games_and_an_unreachable_opendota_leave_the_series_as_it_was(opendota_up, monkeypatch):
    lol = {**_series("running", [("running", None)]), "videogame": {"slug": "league-of-legends"}}
    snap = ps.parse_match(lol)
    ps.PandascoreEsports()._dota(lol, snap)
    assert "dota" not in snap.state and opendota_up == []

    def down():
        raise AdapterError("OpenDota unreachable")

    monkeypatch.setattr(opendota, "live", down)
    m = _series("running", [("running", None)])
    snap = ps.parse_match(m)
    before = (snap.clock_label, dict(snap.lines))
    ps.PandascoreEsports()._dota(m, snap)
    assert (snap.clock_label, snap.lines) == before and "dota" not in snap.state


def test_a_finished_series_match_page_waits_for_opendota_to_parse_its_maps(opendota_up, monkeypatch):
    """A Dota 2 series just over whose maps OpenDota hasn't all got, or can't be reached: its match page's read
    is partial, to be read again. Hours on, a map OpenDota hasn't got isn't coming: whole as it is."""
    def series(ended: datetime) -> dict:
        return {**_series("finished", [("finished", SPIRIT), ("finished", LIQUID)]), "end_at": ended.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tournament": {"id": 3, "name": "Playoffs", "tier": "s"}, "league": {"id": 1, "name": "The International"}}

    m = series(datetime.now(timezone.utc) - timedelta(minutes=20))
    rosters = {f"/teams/{SPIRIT}": {"players": ROSTER[:2]}, f"/teams/{LIQUID}": {"players": ROSTER[2:]}}
    monkeypatch.setattr(pandascore, "get_json", lambda path, ttl, **params: m if path == "/matches/777" else rosters[path])
    adapter = ps.PandascoreEsports()
    game = adapter.match("777")
    assert game is not None and game.snapshot.status == "final" and not game.partial  # both maps' numbers in
    assert [mp["n"] for mp in game.snapshot.state["dota"]["maps"]] == [1, 2]

    monkeypatch.setattr(opendota, "match", lambda mid: {9001: _details(True)}.get(mid))  # map 2 not in OpenDota yet
    assert adapter.match("777").partial
    monkeypatch.setattr(opendota, "match", lambda mid: {9001: _details(True), 9002: {**_details(False), "version": None}}.get(mid))
    assert adapter.match("777").partial  # map 2 in, its replay not parsed yet

    def down():
        raise AdapterError("OpenDota unreachable")

    monkeypatch.setattr(opendota, "pro_matches", down)
    assert adapter.match("777").partial
    m = series(datetime.now(timezone.utc) - timedelta(seconds=ps.DOTA_WAIT + 60))
    assert not adapter.match("777").partial  # long over: what OpenDota hasn't got isn't coming


def test_each_maps_numbers_are_an_alert_once():
    def seen(maps):
        return moments.Seen("live", {"kind": "esports", "teams": [], "maps": [], "dota": {"maps": maps}}, None, "", "", teams=[str(SPIRIT)])

    one = [{"n": 1, "players": {"101": dota.map_stats(_details(True), {321580662: "101"}, HEROES)["101"]}}]
    [kda] = moments.detect("Dota 2", "Yatoro", "101", seen([]), seen(one))
    assert (kda.kind, kda.level, kda.title, kda.body, kda.priority) == ("kda", "minor", "Yatoro: 12/2/9 on Faceless Void", "Map 1 won · 612 GPM · 300 last hits", 60)
    assert moments.detect("Dota 2", "Yatoro", "101", seen(one), seen(one)) == []
    assert moments.detect("Dota 2", "Collapse", "102", seen([]), seen(one)) == []  # no numbers for him that map


def test_the_credits_name_opendota_while_it_runs(monkeypatch):
    monkeypatch.setattr(opendota, "ENABLED", True)
    assert "OpenDota" in [c["name"] for c in credits.credits({"pandascore_esports": 1}, stockfish=False)["also"]]
    monkeypatch.setattr(opendota, "ENABLED", False)
    assert "OpenDota" not in [c["name"] for c in credits.credits({"pandascore_esports": 1}, stockfish=False)["also"]]


def test_a_live_card_credits_opendota_beside_pandascore_once_it_has_the_map(opendota_up):
    adapter = ps.PandascoreEsports()
    m = _series("running", [("finished", SPIRIT), ("running", None)])
    snap = ps.parse_match(m)
    event = Event(id=5, title="Team Spirit vs Team Liquid", notes=None, competition="The International", start_utc=BEGIN, status="live")
    card = {"live": {"is_live": False, "player_stats": []}, "upcoming": [], "recent_results": [], "freshness": {}}
    structured._apply_snapshot(card, event, snap, None, adapter, BEGIN)
    assert card["live"]["source"] == "PandaScore"
    adapter._dota(m, snap)
    structured._apply_snapshot(card, event, snap, snap.lines["101"], adapter, BEGIN)
    assert card["live"]["source"] == "PandaScore and OpenDota"


# ---------------------------------------------------------------- the client


class FakeRedis:
    def __init__(self):
        self.data: dict[str, object] = {}

    def incr(self, key):
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]

    def expire(self, key, seconds):
        pass

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value


def test_a_match_is_kept_once_parsed_and_the_minute_is_never_overspent(monkeypatch):
    """A match OpenDota hasn't got is asked again a minute on; one it has but hasn't parsed, every PARSE_WAIT;
    a parsed one is kept for good."""
    sent: list[str] = []
    there = {"match": False, "parsed": False}
    clock = [1_800_000_000.0]

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        if request.url.path.endswith("/matches/9001"):
            if down["now"]:
                return httpx.Response(502)
            if not there["match"]:
                return httpx.Response(404, json={"error": "Not Found"})
            return httpx.Response(200, json={"radiant_win": True, "version": 22 if there["parsed"] else None, "players": [{"account_id": 1, "kills": 3}]})
        return httpx.Response(200, json=[])

    monkeypatch.setattr(time, "time", lambda: clock[0])
    down = {"now": False}
    monkeypatch.setattr(opendota, "ENABLED", True)
    monkeypatch.setattr(opendota, "_memo", {})
    monkeypatch.setattr(opendota, "_client", httpx.Client(transport=httpx.MockTransport(handler), headers=opendota._client.headers))
    asked = lambda: sent.count("/api/matches/9001")  # noqa: E731
    assert opendota.match(9001) is None and opendota.match(9001) is None and asked() == 1  # not there: asked again only a minute on
    clock[0] += opendota.MISS_WAIT + 1
    there["match"] = True
    first = opendota.match(9001)
    assert first is not None and first["version"] is None and not opendota.parsed(first) and asked() == 2
    assert opendota.match(9001) == first and asked() == 2  # not parsed yet: kept a while
    clock[0] += opendota.PARSE_WAIT + 1
    down["now"] = True
    assert opendota.match(9001) == first and asked() == 3  # asking again failed: the copy it has
    clock[0] += 5
    assert opendota.match(9001) == first and asked() == 3  # and still, without asking, for a minute
    clock[0] += opendota.MISS_WAIT + 1
    down["now"], there["parsed"] = False, True
    assert opendota.parsed(opendota.match(9001)) and asked() == 4
    cache.forget()  # another process, or after a deploy: the source cache's copy
    clock[0] += 10 * 86400
    assert opendota.parsed(opendota.match(9001)) and asked() == 4  # parsed: kept
    assert "sports-follow" in opendota._client.headers["user-agent"]
    for _ in range(opendota.FREE_MINUTE - opendota.MARGIN_MINUTE):  # a new minute: the three asks were days ago
        budget.take(opendota.BUDGET, lane_name="live")
    with pytest.raises(opendota.RateLimited):
        budget.take(opendota.BUDGET, lane_name="live")


def test_turned_off_it_asks_nothing(monkeypatch):
    monkeypatch.setattr(opendota, "ENABLED", False)
    monkeypatch.setattr(opendota, "_memo", {})
    with pytest.raises(AdapterError):
        opendota.live()


def test_each_maps_match_is_remembered_from_when_it_was_live():
    known = dota.remember({}, 1, 9001)
    known = dota.remember(known, 2, 9002)
    assert known == {"1": 9001, "2": 9002}
    assert dota.remember(known, 2, 9003) == known  # PandaScore still says map 2 while map 3 is on: kept
    assert dota.remember(known, 3, 9002) == known  # a game already numbered isn't numbered again


def test_remembered_maps_beat_the_list_and_a_finished_game_isnt_live(opendota_up, monkeypatch):
    adapter = ps.PandascoreEsports()
    m = _series("running", [("running", None)])
    adapter._dota(m, ps.parse_match(m))  # map 1 is live as OpenDota game 9043: remembered
    monkeypatch.setattr(opendota, "match", lambda mid: {9043: _details(True, (20, 0, 5))}.get(mid))
    over = {**_series("running", [("finished", SPIRIT), ("running", None)])}
    monkeypatch.setattr(opendota, "pro_matches", lambda: [*PRO_MATCHES, {"match_id": 9043, "start_time": 0}])  # 9043 just ended
    snap = ps.parse_match(over)
    adapter._dota(over, snap)
    assert [(mp["n"], mp["match_id"]) for mp in snap.state["dota"]["maps"]] == [(1, 9043)]  # not 9001 from the list
    assert snap.state["dota"]["live"] is None and snap.clock_label == "Map 2"  # 9043 is over: no stale live map
    assert snap.lines["101"].headline == "20/0/5 on Faceless Void · map 1"


def test_maps_are_numbered_when_either_source_is_behind():
    assert dota.number_maps([9001, 9002], 1) == [(1, 9001)]  # PandaScore hasn't marked map 2 finished yet
    assert dota.number_maps([9002, 9003], 3) == [(2, 9002), (3, 9003)]  # map 1 has left OpenDota's last 100
    assert dota.number_maps([], 2) == []


def test_a_series_finds_only_its_finished_maps(opendota_up):
    m = {**_series("running", [("finished", SPIRIT), ("running", None)]), "begin_at": (BEGIN - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    snap = ps.parse_match(m)
    ps.PandascoreEsports()._dota(m, snap)
    assert [(mp["n"], mp["match_id"]) for mp in snap.state["dota"]["maps"]] == [(1, 9001)]


# ---------------------------------------------------------------- each finished map's match page part, recorded

FIXTURES = Path(__file__).parent / "fixtures" / "opendota"
GRAND_FINAL = (9040114168, 9040268873, 9040460429, 9040578015)  # maps 1-4; Spirit took map 1, Yandex the rest
YANDEX = 136166
FINAL_TEAMS = [{"id": str(SPIRIT), "name": "Team Spirit"}, {"id": str(YANDEX), "name": "Team Yandex"}]
# Each player by their Steam account, as PandaScore names them (OpenDota calls watson "医者watson`").
FINAL_NAMES = {321580662: "Yatoro", 106305042: "Larl", 140835095: "Batyuk", 218231587: "not_me", 847565596: "rue",
               171262902: "watson", 312436974: "CHIRA_JUNIOR", 183719386: "AMMAR_THE_F", 93817671: "Maladych", 103735745: "Saksa"}
SPIRITS = (321580662, 106305042, 140835095, 218231587, 847565596)


def recorded(name: str):
    with gzip.open(FIXTURES / name, "rt") as f:
        return json.load(f)


def _final_map(match_id: int, n: int, details: dict | None = None) -> dict | None:
    heroes = {h["id"]: h["localized_name"] for h in recorded("heroes.json.gz")}
    items = {i["id"]: i["dname"] for i in recorded("items.json.gz").values()}
    players = {account: str(account) for account in FINAL_NAMES}
    return dota.map_detail(details or recorded(f"match-{match_id}.json.gz"), n, FINAL_TEAMS, players, {str(a): name for a, name in FINAL_NAMES.items()}, heroes, items)


def test_a_finished_maps_page_part_from_its_recorded_match():
    """Map 4: how it went in a line a moment, both sides' totals, the gold lead with each Roshan, all ten players
    with their items (the series' first team first, each side by net worth), and the draft in order."""
    part = _final_map(9040578015, 4)
    spirit, yandex = str(SPIRIT), str(YANDEX)
    assert (part["n"], part["length"], part["winner"], part["sides"]) == (4, "56:40", yandex, {yandex: "Radiant", spirit: "Dire"})
    assert [(e["clock"], e["title"], e["note"]) for e in part["events"]] == [
        ("1:28", "First blood to Spirit", "rue, Bane, on Saksa"),
        ("13:13", "Yandex take Spirit's mid and top T1", None),
        ("14:14", "Spirit take Yandex's bot T1", None),
        ("20:01", "Spirit take Yandex's mid T1", None),
        ("25:19", "Spirit take Yandex's top T1", None),
        ("26:04", "Fight: four Spirit heroes die, one Yandex", "Spirit's 4.4k lead gone"),
        ("26:55", "Yandex take Spirit's bot T1 and T2", None),
        ("28:56", "Roshan to Spirit", "Aegis to Yatoro"),
        ("30:45", "Fight: four Spirit heroes die, one Yandex", "Yandex 8.8k up after it"),
        ("31:53", "Yandex take Spirit's top and mid T2", None),
        ("34:35", "Fight: two Yandex heroes die, one Spirit", "Yandex's lead down to 7.9k"),
        ("39:23", "Roshan to Yandex", "Aegis to watson"),
        ("47:22", "Yandex take Spirit's top T3", None),
        ("50:44", "Roshan to Spirit, their second", "Aegis to Yatoro"),
        ("52:35", "Fight: four Spirit heroes die, one Yandex", "Yandex 12.5k up after it"),
        ("54:05", "Yandex take Spirit's mid T3", None),
        ("54:12", "Yandex break Spirit's mid and top barracks", None),
        ("55:44", "Yandex take Spirit's bot T3", None),
        ("56:19", "Yandex break Spirit's bot barracks", None),
        ("56:40", "Spirit's Ancient falls", "Yandex take map 4"),
    ]
    assert part["marks"] == [{"t": 1736, "team": spirit, "label": "R", "name": "Roshan"}, {"t": 2363, "team": yandex, "label": "R", "name": "Roshan"},
                             {"t": 3044, "team": spirit, "label": "R", "name": "Roshan"}]
    lead = part["lead"]
    assert (lead["team"], lead["what"], len(lead["by_minute"]), lead["by_minute"][26], lead["by_minute"][-1]) == (yandex, "gold", 57, -4377, 17494)
    assert {t["label"]: (t["values"][spirit], t["values"][yandex]) for t in part["totals"]} == {
        "Kills": ("37", "51"), "Net worth": ("99.3k", "158.8k"), "Towers taken": ("3", "9"), "Barracks taken": ("0", "6"), "Roshan": ("2", "1")}
    assert [(p["name"], p["team"]) for p in part["players"]][:6] == [("Yatoro", spirit), ("Batyuk", spirit), ("Larl", spirit), ("not_me", spirit), ("rue", spirit), ("watson", yandex)]
    yatoro = part["players"][0]
    assert (yatoro["id"], yatoro["as"], yatoro["kda"], yatoro["gpm"], yatoro["numbers"], yatoro["note"]) == (
        "321580662", "Sven", [11, 9, 7], 801, ["11/9/7", "801", "32.5k"], "52.2k damage to heroes · 782 last hits")
    assert yatoro["items"] == ["Satanic", "Swift Blink", "Black King Bar", "Monkey King Bar", "Linken's Sphere", "Daedalus"]
    assert part["players"][2]["items"] == ["Linken's Sphere", "Bottle", "Phase Boots", "Black King Bar", "Roshan's Banner"]  # an empty slot left out
    draft = [(d["hero"], d["team"], d["pick"], d["player"]) for d in part["draft"]]
    assert len(draft) == 24 and sum(d[2] for d in draft) == 10
    assert draft[:2] == [("Hoodwink", yandex, False, None), ("Lycan", yandex, False, None)]
    assert draft[7:9] == [("Earth Spirit", yandex, True, "CHIRA_JUNIOR"), ("Ember Spirit", spirit, True, "Larl")]
    assert draft[-1] == ("Sven", spirit, True, "Yatoro")


def test_every_map_of_the_recorded_series_says_its_own_moments():
    one, two, three = (_final_map(mid, n) for n, mid in enumerate(GRAND_FINAL[:3], 1))
    assert [e["title"] for e in one["events"] if e["title"].startswith("Tormentor")] == ["Tormentor to Spirit", "Tormentor to Spirit"]
    assert one["events"][-1]["title"] == "Yandex's Ancient falls" and one["winner"] == str(SPIRIT)
    assert [e["title"] for e in two["events"] if e["mark"]] == ["Roshan to Yandex", "Roshan to Yandex, their second", "Roshan to Yandex, their third"]
    assert three["events"][0]["clock"] == "-0:25" and three["events"][0]["title"] == "First blood to Spirit"  # before the horn
    assert all(e["t"] <= f["t"] for e, f in zip(three["events"], three["events"][1:]))


def test_before_its_replay_is_parsed_a_map_has_its_players_and_draft_only():
    doc = recorded(f"match-{GRAND_FINAL[3]}.json.gz")
    unparsed = {**{k: v for k, v in doc.items() if k not in ("radiant_gold_adv", "objectives", "teamfights")}, "version": None}
    part = _final_map(GRAND_FINAL[3], 4, unparsed)
    assert part["lead"] is None and part["events"] == [] and part["marks"] == []
    assert [t["label"] for t in part["totals"]] == ["Kills", "Net worth"] and len(part["players"]) == 10 and len(part["draft"]) == 24
    other = {**doc, "radiant_name": "Team Liquid", "radiant_team": {"name": "Team Liquid"}}
    assert _final_map(GRAND_FINAL[3], 4, other) is None  # not the series' two teams


@pytest.fixture
def grand_final(monkeypatch):
    """The grand final as PandaScore lists it (finished 20 minutes ago) over OpenDota's recorded matches."""
    docs = {mid: recorded(f"match-{mid}.json.gz") for mid in GRAND_FINAL}
    first = min(d["start_time"] for d in docs.values())
    monkeypatch.setattr(opendota, "ENABLED", True)
    monkeypatch.setattr(opendota, "live", lambda: [])
    monkeypatch.setattr(opendota, "pro_matches", lambda: [{k: d[k] for k in ("match_id", "start_time", "radiant_name", "dire_name")} for d in docs.values()])
    monkeypatch.setattr(opendota, "match", docs.get)
    monkeypatch.setattr(opendota, "heroes", lambda: {h["id"]: h["localized_name"] for h in recorded("heroes.json.gz")})
    monkeypatch.setattr(opendota, "items", lambda: {i["id"]: i["dname"] for i in recorded("items.json.gz").values()})
    monkeypatch.setattr(opendota, "pro_players", lambda: [{"account_id": a, "name": name, "team_name": "Team Spirit" if a in SPIRITS else "Team Yandex"} for a, name in FINAL_NAMES.items()])
    when = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    series = {
        "id": 888, "name": "Grand final: TS vs TY", "status": "finished", "begin_at": when(first - 600), "scheduled_at": when(first - 600),
        "end_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"), "number_of_games": 5, "match_type": "best_of",
        "videogame": {"slug": "dota-2", "name": "Dota 2"}, "tournament": {"id": 4, "name": "Playoffs", "tier": "s"}, "league": {"id": 2, "name": "BLAST Slam"},
        "opponents": [{"type": "Team", "opponent": {"id": SPIRIT, "name": "Team Spirit", "acronym": "TS"}}, {"type": "Team", "opponent": {"id": YANDEX, "name": "Team Yandex", "acronym": "TY"}}],
        "results": [{"team_id": SPIRIT, "score": 1}, {"team_id": YANDEX, "score": 3}],
        "games": [{"position": i + 1, "status": "finished", "winner": {"id": w, "type": "Team"}} for i, w in enumerate((SPIRIT, YANDEX, YANDEX, YANDEX))],
        "winner_id": YANDEX, "winner_type": "Team",
    }
    roster = [{"id": 5000 + i, "name": name} for i, name in enumerate(FINAL_NAMES.values())]
    pages = {"/matches/888": series, f"/teams/{SPIRIT}": {"players": roster[:5]}, f"/teams/{YANDEX}": {"players": roster[5:]}}
    monkeypatch.setattr(pandascore, "get_json", lambda path, ttl, **params: pages[path])
    monkeypatch.setattr(ps.bus, "sync_redis", lambda redis=FakeRedis(): redis)  # noqa: B008
    return series


def test_a_match_pages_read_has_every_maps_part_and_a_poll_none(grand_final):
    """The match page's own read carries each map's part (state "page_detail"), and the esports page passes it on
    with the two teams by id and short name; a poll, whose every state is stored and sent to each card, doesn't."""
    from sports_follow import sports

    game = ps.PandascoreEsports().match("888")
    assert game is not None and not game.partial
    parts = game.snapshot.state["page_detail"]["maps"]
    assert [p["n"] for p in parts] == [1, 2, 3, 4] and [p["winner"] for p in parts] == [str(SPIRIT), str(YANDEX), str(YANDEX), str(YANDEX)]
    assert parts[3]["players"][0]["name"] == "Yatoro" and parts[3]["players"][0]["id"] == "5000"  # by his PandaScore id
    names = {str(5000 + i): (name, "") for i, name in enumerate(FINAL_NAMES.values())}
    page = sports.get("Dota 2").match_page(game.snapshot.state, [], {}, names)
    assert page["teams"] == [{"id": str(SPIRIT), "name": "Team Spirit", "short": "Spirit"}, {"id": str(YANDEX), "name": "Team Yandex", "short": "Yandex"}]
    assert page["detail"]["maps"][3]["events"][-1]["title"] == "Spirit's Ancient falls"
    assert [m["best"] for m in page["maps"]] == ["Yatoro 19/1/12 on Ursa", "AMMAR_THE_F 20/3/7 on Timbersaw", "watson 14/0/14 on Nature's Prophet", "watson 19/5/14 on Anti-Mage"]

    snap = ps.parse_match(grand_final)
    ps.PandascoreEsports()._dota(grand_final, snap)
    assert [m["n"] for m in snap.state["dota"]["maps"]] == [1, 2, 3, 4] and "page_detail" not in snap.state


def test_item_names_come_from_opendotas_constants(monkeypatch):
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        return httpx.Response(200, json=recorded("items.json.gz"))

    monkeypatch.setattr(opendota, "ENABLED", True)
    monkeypatch.setattr(opendota, "_client", httpx.Client(transport=httpx.MockTransport(handler), headers=opendota._client.headers))
    names = opendota.items()
    assert names[1] == "Blink Dagger" and names[116] == "Black King Bar"
    assert opendota.items() == names and sent == ["/api/constants/items"]  # kept a week


def test_an_empty_list_of_heroes_or_pros_isnt_kept(monkeypatch):
    answers = {"/api/heroes": [[], [{"id": 1, "localized_name": "Anti-Mage"}]]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=answers[request.url.path].pop(0))

    monkeypatch.setattr(opendota, "ENABLED", True)
    monkeypatch.setattr(opendota, "_client", httpx.Client(transport=httpx.MockTransport(handler), headers=opendota._client.headers))
    with pytest.raises(AdapterError):
        opendota.heroes()
    assert opendota.heroes() == {1: "Anti-Mage"}  # asked again, not a week of "?"


# ---------------------------------------------------------------- the map being played, recorded

def _recorded_live() -> list[dict]:
    """Three league games' live readings (OpenDota's live list once a minute, 21 Oct 2026) and the buildings
    their parsed matches say fell, and when."""
    return recorded("live-recorded.json.gz")


def _standing(kills: list, side: str, lane: str, t: int) -> dict:
    gone = {key for at, key in kills if at <= t}
    return {"towers": [f"npc_dota_{side}_tower{tier}_{lane}" not in gone for tier in (1, 2, 3)],
            "barracks": [f"npc_dota_{side}_{kind}_rax_{lane}" not in gone for kind in ("melee", "range")]}


def test_the_live_buildings_are_what_the_parsed_matches_say():
    """At each reading, which towers and barracks stand, as the match's own record of what fell says. The
    live list runs a little behind (a tower that fell a minute ago can still show), never ahead: no reading
    has anything down that the match says stands, and nearly every one is exact."""
    exact = ahead = lanes = 0
    for game in _recorded_live():
        for r in game["readings"]:
            built = dota.buildings(r["building_state"])
            if built is None:
                assert r["building_state"] == dota.NO_BUILDINGS
                continue
            for side, ours in (("radiant", "goodguys"), ("dire", "badguys")):
                for lane in dota.LANES:
                    truth, live = _standing(game["building_kills"], ours, lane, r["game_time"]), built[side][lane]
                    lanes += 1
                    exact += live == truth
                    ahead += any(not shown and stands for shown, stands in zip(live["towers"] + live["barracks"], truth["towers"] + truth["barracks"]))
    assert lanes > 900 and ahead == 0 and exact / lanes > 0.98


def test_radiant_barracks_count_while_they_stand_and_dires_once_theyre_down():
    # Game 9054498345: Radiant's mid T3 down, both its barracks standing (7); Dire's top and mid T3 and
    # barracks all down (7 too).
    reading = next(r for g in _recorded_live() for r in g["readings"] if r["match_id"] == "9054498345" and r["game_time"] == 3190)
    built = dota.buildings(reading["building_state"])
    assert built["radiant"]["mid"] == {"towers": [False, False, False], "barracks": [True, True]}
    assert built["dire"]["mid"] == {"towers": [False, False, False], "barracks": [False, False]}
    assert built["radiant"]["top"] == {"towers": [False, False, True], "barracks": [True, True]}
    assert dota.buildings(dota.NO_BUILDINGS) is None and dota.buildings(0) is None and dota.buildings(None) is None


def test_the_gold_line_takes_each_newer_reading_once_the_horn_has_gone():
    line = dota.add_reading([], -30, 0)
    assert line == []  # still drafting
    line = dota.add_reading(line, 60, 120)
    line = dota.add_reading(line, 120, -340)
    assert dota.add_reading(line, 90, 500) is line and dota.add_reading(line, 120, 0) is line  # the clock stepped back
    assert line == [[60, 120], [120, -340]]
    long = []
    for t in range(1, 300):  # two and a half hours, a reading every 30 s
        long = dota.add_reading(long, t * 30, t)
    assert long[-1] == [299 * 30, 299] and long[0][0] > 299 * 30 - dota.LINE_SPAN  # the last two hours
    many = []
    for t in range(1, dota.READINGS + 50):
        many = dota.add_reading(many, t * 5, t)
    assert len(many) == dota.READINGS and many[-1] == [(dota.READINGS + 49) * 5, dota.READINGS + 49]


def test_the_map_being_played_has_its_line_buildings_and_ten_heroes():
    reading = next(r for g in _recorded_live() for r in g["readings"] if r["match_id"] == "9054431552" and r["game_time"] == 2147)
    teams = [{"id": "7", "name": "The Bot^$"}, {"id": "8", "name": "Dawn Bulls"}]  # the series' order: Dire first
    heroes = {p["hero_id"]: f"Hero {p['hero_id']}" for p in reading["players"]}
    named = reading["players"][0]
    part = dota.live_detail(reading, teams, {named["account_id"]: "42"}, {"42": "Their PandaScore name"}, heroes, 3, [[60, 0], [120, 500]])
    assert part["n"] == 3 and part["lead"] == {"team": "8", "what": "gold", "readings": [[60, 0], [120, 500]]}
    assert part["buildings"]["7"]["mid"] == {"towers": [False, False, False], "barracks": [True, True]}  # Dire's mid T3 down
    assert part["buildings"]["8"]["mid"] == {"towers": [False, True, True], "barracks": [True, True]}
    assert len(part["heroes"]) == 10 and [h["team"] for h in part["heroes"]] == ["8"] * 5 + ["7"] * 5  # Radiant's first
    radiant = [p for p in reading["players"] if p["team"] == 0]
    cores = sorted((p for p in radiant if p["fantasy_role"] == 1), key=lambda p: p["team_slot"])
    assert [h["as"] for h in part["heroes"][: len(cores)]] == [f"Hero {p['hero_id']}" for p in cores]
    assert next(h for h in part["heroes"] if h["id"] == "42")["name"] == "Their PandaScore name"
    assert dota.live_detail(reading, teams, {}, {}, heroes, 3, [[60, 0]])["lead"] is None  # one reading isn't a line


def test_a_live_maps_page_part_comes_with_the_read_and_its_line_grows_with_each(opendota_up, monkeypatch):
    m = _series("running", [("finished", SPIRIT), ("running", None)])
    game = dict(LIVE_GAME, building_state=dota.NO_BUILDINGS)
    clock = {"t": 600}

    def live():
        return [dict(game, game_time=clock["t"], radiant_lead=clock["t"] * 3)]

    monkeypatch.setattr(opendota, "live", live)
    adapter = ps.PandascoreEsports()
    for t in (600, 630, 660):
        clock["t"] = t
        snap = ps.parse_match(m)
        adapter._dota(m, snap)  # a poll: adds to the line, carries no page part
        assert "page_detail" not in snap.state
    snap = ps.parse_match(m)
    adapter._dota(m, snap, detail=True)
    now = snap.state["page_detail"]["live"]
    assert now["n"] == 2 and now["lead"]["readings"] == [[600, 1800], [630, 1890], [660, 1980]] and now["lead"]["team"] == str(LIQUID)
    assert now["buildings"] is None and [h["as"] for h in now["heroes"]] == ["Juggernaut", "Faceless Void"]


def test_map_ones_live_part_shows_before_any_map_is_over(opendota_up, monkeypatch):
    """The first map of a series: nothing finished yet, its live part is the page's all the same."""
    from sports_follow import sports

    m = _series("running", [("running", None)])
    monkeypatch.setattr(opendota, "live", lambda: [dict(LIVE_GAME, building_state=dota.NO_BUILDINGS)])
    snap = ps.parse_match(m)
    ps.PandascoreEsports()._dota(m, snap, detail=True)
    assert snap.state["page_detail"]["maps"] == [] and snap.state["page_detail"]["live"]["n"] == 1
    page = sports.get("Dota 2").match_page(snap.state, [], {}, {})
    assert page["detail"]["live"]["n"] == 1 and page["detail"]["maps"] == [] and [t["short"] for t in page["teams"]] == ["Spirit", "Liquid"]
