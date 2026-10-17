"""Dota 2 inside the map, from OpenDota, laid over a PandaScore series: matching teams and players, the
live map, finished maps' numbers, the series board's state, alerts, and the client's budget. Against small
made-up responses in OpenDota's shapes (checked against its live list, proMatches and a match, 13 Oct
2026), none committed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sports_follow import credits, moments, structured
from sports_follow.adapters import dota, opendota, pandascore, pandascore_esports as ps
from sports_follow.adapters import budget
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
    return {"radiant_win": radiant_win, "players": [_player(321580662, 41, *yatoro, True), _player(113331514, 129, 3, 4, 20, True), _player(152962063, 8, 7, 6, 5, False), _player(77490514, 26, 1, 8, 11, False)]}


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


def test_a_match_is_kept_once_complete_and_the_minute_is_never_overspent(monkeypatch):
    sent: list[str] = []
    parsed = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        if request.url.path.endswith("/matches/9001"):
            players = [{"account_id": 1, "kills": 3 if parsed["done"] else None}]
            return httpx.Response(200, json={"radiant_win": True, "players": players})
        return httpx.Response(200, json=[])

    monkeypatch.setattr(opendota, "ENABLED", True)
    monkeypatch.setattr(opendota, "_client", httpx.Client(transport=httpx.MockTransport(handler), headers=opendota._client.headers))
    monkeypatch.setattr(opendota.bus, "sync_redis", lambda: FakeRedis.instance)
    FakeRedis.instance = FakeRedis()
    assert opendota.match(9001)["players"][0]["kills"] is None  # not parsed yet: not kept
    parsed["done"] = True
    assert opendota.match(9001)["players"][0]["kills"] == 3
    assert opendota.match(9001)["players"][0]["kills"] == 3 and sent.count("/api/matches/9001") == 2  # kept now
    assert "sports-follow" in opendota._client.headers["user-agent"]
    for _ in range(opendota.FREE_MINUTE - opendota.MARGIN_MINUTE - 2):
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
