"""PUBG esports from PUBG's official API: match summaries, lobbies, standings, each player's line, alerts,
the adapter's index and the client's per-minute budget. Against small made-up documents in the API's
JSON:API shape (checked against a real tournament match, 13 Oct 2026), none of theirs committed."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sports_follow import credits, moments, registry
from sports_follow.adapters import pubg, pubg_esports as pe
from sports_follow.adapters import KNOWN, PlayerRef, budget

T0 = datetime(2026, 10, 13, 6, 30, tzinfo=timezone.utc)
TEAMS = ["4AM", "DNS", "TWIS", "AT", "FLC", "VP", "GEN", "S8UL"]  # a lobby of 8, the fewest that counts
TGLTN = "account.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"  # 4AM_TGLTN's account at one event
PRO = "4am_tgltn"  # who he is at every event: his team's tag and his name


def _account(team: str, i: int, tgltn: str = TGLTN) -> str:
    return tgltn if (team, i) == ("4AM", 0) else f"account.{team.lower()}{i}".ljust(40, "0")


def _doc(mid: str, at: datetime, places: dict[str, int], kills: dict[str, int], map_name: str = "Baltic_Main", teams: list[str] | None = None, duration: int = 1800,
         tgltn: str = TGLTN) -> dict:
    """A tournament match: each team's placement, and its first player's kills (the others none). tgltn:
    the account 4AM_TGLTN plays it on."""
    included, rosters = [], []
    for t in teams or TEAMS:
        parts = []
        for i in range(2):
            pid = f"{mid}-{t}-{i}"
            included.append({"type": "participant", "id": pid, "attributes": {"stats": {
                "name": f"{t}_{'TGLTN' if (t, i) == ('4AM', 0) else f'p{i}'}", "playerId": _account(t, i, tgltn), "kills": kills.get(t, 0) if i == 0 else 0,
                "damageDealt": 100.4 * (kills.get(t, 0) if i == 0 else 0), "assists": 1, "DBNOs": 1, "timeSurvived": 900, "winPlace": places[t]}}})
            parts.append({"type": "participant", "id": pid})
        rosters.append({"type": "roster", "id": f"{mid}-{t}", "attributes": {"stats": {"rank": places[t], "teamId": 1}, "won": "true" if places[t] == 1 else "false"}, "relationships": {"participants": {"data": parts}}})
    return {"data": {"type": "match", "id": mid, "attributes": {"createdAt": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "duration": duration, "mapName": map_name, "gameMode": "esports-squad"}}, "included": rosters + included}


REST = {"FLC": 5, "VP": 6, "GEN": 7, "S8UL": 8}  # the other four, last in every game, no finishes
GAME1 = _doc("m1", T0, {"4AM": 1, "DNS": 2, "TWIS": 3, "AT": 4, **REST}, {"4AM": 6, "DNS": 2, "TWIS": 4, "AT": 0})
GAME2 = _doc("m2", T0 + timedelta(minutes=40), {"4AM": 4, "DNS": 1, "TWIS": 2, "AT": 3, **REST}, {"4AM": 1, "DNS": 9, "TWIS": 3, "AT": 2}, "Desert_Main")


def test_a_match_is_kept_as_each_teams_placing_and_its_players_numbers():
    s = pe.summarize_match(GAME1)
    assert (s["id"], s["at"], s["map"], s["duration"]) == ("m1", "2026-10-13T06:30:00Z", "Erangel", 1800)
    assert [(r["rank"], r["team"]) for r in s["rosters"]][:4] == [(1, "4AM"), (2, "DNS"), (3, "TWIS"), (4, "AT")]
    assert s["rosters"][0]["players"][0] == {"id": TGLTN, "name": "4AM_TGLTN", "kills": 6, "damage": 602, "assists": 1, "knocks": 1}
    assert pe.team_tag(["TWIS_Lu", "TWIS_xmpl", "Other"]) == "TWIS" and pe.display_name("4AM_TGLTN", "4AM") == "TGLTN"


def test_matches_make_lobbies_by_session_and_by_the_teams_in_them():
    late = _doc("m3", T0 + timedelta(hours=9), {"4AM": 2, "DNS": 1, "TWIS": 3, "AT": 4, **REST}, {})  # a later session
    group_b = [f"B{i}" for i in range(8)]
    other = _doc("m4", T0 + timedelta(minutes=20), {t: i + 1 for i, t in enumerate(group_b)}, {}, teams=group_b)  # group B
    lobbies = pe.group_lobbies("as-pgsc4t", [pe.summarize_match(d) for d in (GAME2, late, GAME1, other)])
    assert [(pe.lobby_key(lb), lb["day_number"], lb["group"], [m["id"] for m in lb["matches"]]) for lb in lobbies] == [
        ("as-pgsc4t:2026-10-13:1", 1, "A", ["m1", "m2"]), ("as-pgsc4t:2026-10-13:2", 1, "B", ["m4"]), ("as-pgsc4t:2026-10-13:3", 2, None, ["m3"])]


def test_only_competition_counts():
    places = {t: i + 1 for i, t in enumerate(TEAMS)}
    exhibition = _doc("x1", T0, places, {"4AM": 41}, duration=826)  # 14 minutes, 41 finishes: seen in the tournament shard
    wild = _doc("x2", T0, places, {"4AM": 47})  # a full-length match with 47 finishes
    small = _doc("x3", T0, {"4AM": 1, "DNS": 2, "TWIS": 3}, {}, teams=["4AM", "DNS", "TWIS"])
    assert [pe.competitive(pe.summarize_match(d)) for d in (GAME1, exhibition, wild, small)] == [True, False, False, False]
    assert pe.group_lobbies("as-pgsc4t", [pe.summarize_match(d) for d in (exhibition, wild, small)]) == []


def test_a_tournament_listed_twice_is_one_lobby():
    one = pe.group_lobbies("kr-26pws2f", [pe.summarize_match(GAME1), pe.summarize_match(GAME2)])
    again = pe.group_lobbies("kr_26pws2f", [pe.summarize_match(GAME1), pe.summarize_match(GAME2)])
    later = pe.group_lobbies("kr_26pws2f", [pe.summarize_match(_doc("m9", T0 + timedelta(days=1), {t: i + 1 for i, t in enumerate(TEAMS)}, {}))])
    assert [pe.lobby_key(lb) for lb in pe.PubgEsports._unique([*one, *again, *later])] == ["kr-26pws2f:2026-10-13:1", "kr_26pws2f:2026-10-14:1"]


def _lobby(*docs):
    return pe.group_lobbies("as-pgsc4t", [pe.summarize_match(d) for d in docs])[0]


def test_standings_use_placement_points_and_finishes():
    table = pe.standings(_lobby(GAME1, GAME2))
    # 4AM: 10+6 then 4+1 = 21; DNS: 6+2 then 10+9 = 27; TWIS: 5+4 then 6+3 = 18; AT: 4 then 5+2 = 11.
    assert [(r["place"], r["name"], r["points"], r["kills"], r["wins"]) for r in table][:4] == [(1, "DNS", 27.0, 11, 1), (2, "4AM", 21.0, 7, 1), (3, "TWIS", 18.0, 7, 0), (4, "AT", 11.0, 2, 0)]
    assert table[1]["games"] == [{"n": 1, "place": 1, "kills": 6, "points": 16.0}, {"n": 2, "place": 4, "kills": 1, "points": 5.0}]


def test_a_lobby_on_now_and_over_later():
    lobby = _lobby(GAME1, GAME2)
    live = pe.parse_lobby(lobby, T0 + timedelta(hours=1, minutes=30))
    assert (live.status, live.score_label, live.clock_label) == ("live", "DNS lead · 27 pts", "After match 2")
    assert live.state["kind"] == "battle_royale" and live.state["game"] == "PUBG" and live.state["stage"] == "Day 1"
    assert [(g["n"], g["map"], g["winner"]) for g in live.state["games"]] == [(1, "Erangel", "4AM"), (2, "Miramar", "DNS")]
    line = live.lines[PRO]  # TGLTN's own numbers over the lobby, beside 4AM's standing
    assert line.headline == "7 finishes · 702 dmg"  # 602.4 and 100.4 damage, each rounded
    assert line.stats[:4] == [{"label": "Team place", "value": "2nd"}, {"label": "Team points", "value": "21"}, {"label": "Assists", "value": "2"}, {"label": "Knocks", "value": "2"}]
    over = pe.parse_lobby(lobby, T0 + timedelta(hours=5))  # no new match for 90 minutes after the last ended
    assert (over.status, over.score_label, over.clock_label) == ("final", "DNS win · 27 pts", "Final standings")
    f = pe.parse_fixture(lobby, T0 + timedelta(hours=5))
    assert (f.source_id, f.title, f.competition, f.status, f.locator) == ("as-pgsc4t:2026-10-13:1", "PUBG Global Series · Day 1", "PUBG Global Series", "final", {"tournament": "as-pgsc4t", "day": "2026-10-13", "n": 1})
    assert f.sport == "" and pe.PubgEsports().sports == ("pubg",)  # its source's one sport: PUBG
    assert pe.PubgEsports().result_label(f, ["4AM"]) == "2nd of 8 · 21 pts"
    assert len(f"pubg_esports:{f.source_id}") <= 64


def test_tournament_names_from_their_ids():
    assert [pe.event_name(t) for t in ("as-pgsc4t", "kr_26pws2f", "eu-pecff2", "sea-m26p24", "cn-dyg26")] == [
        "PUBG Global Series", "PUBG Weekly Series", "PUBG Europe Championship", "PUBG Southeast Asia", "PUBG China"]


def test_a_players_form_over_their_lobbies():
    stats, note = pe.player_form([_lobby(GAME1, GAME2)], PRO)
    assert stats == [{"label": "Lobbies", "value": "1"}, {"label": "Avg place", "value": "2.0"}, {"label": "Finishes", "value": "7"}, {"label": "Damage / game", "value": "351"}, {"label": "Games won", "value": "1"}]
    assert note.startswith("Last 1 lobby")


def test_a_chicken_dinner_and_the_lobby_result_reach_the_player():
    one, two = (pe.parse_lobby(_lobby(*docs), T0 + timedelta(hours=1)) for docs in ((GAME1,), (GAME1, GAME2)))

    def seen(snap):
        line = snap.lines[PRO]
        return moments.Seen(snap.status, snap.state, {"headline": line.headline, "stats": line.stats}, snap.score_label, snap.clock_label, lineups=True, teams=["4AM"])

    found = moments.detect("PUBG", "TGLTN", PRO, seen(one), seen(two))
    assert [(f.kind, f.title) for f in found] == [("game", "TGLTN: 4AM 4th in match 2")]
    over = pe.parse_lobby(_lobby(GAME1, GAME2), T0 + timedelta(hours=5))
    [final] = moments.detect("PUBG", "TGLTN", PRO, seen(two), seen(over))
    assert final.title == "TGLTN: 4AM finish 2nd · 21 pts"


# ---------------------------------------------------------------- the adapter's index


@pytest.fixture
def index(monkeypatch):
    """The adapter over a fake PUBG: one tournament with today's two matches."""
    calls = []
    monkeypatch.setattr(pubg, "tournaments", lambda max_age=600: [{"id": "as-pgsc4t", "createdAt": (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")}])
    now = datetime.now(timezone.utc) - timedelta(minutes=50)
    docs = {"m1": _doc("m1", now - timedelta(minutes=40), *[GAME1_PLACES, GAME1_KILLS]), "m2": _doc("m2", now, *[GAME2_PLACES, GAME2_KILLS])}

    def matches(tournament, max_age):
        calls.append((tournament, max_age))
        return [{"id": mid, "createdAt": d["data"]["attributes"]["createdAt"]} for mid, d in docs.items()]

    monkeypatch.setattr(pubg, "tournament_matches", matches)
    monkeypatch.setattr(pubg, "match", lambda mid, summarize: summarize(docs[mid]))
    return pe.PubgEsports(), calls


GAME1_PLACES, GAME1_KILLS = {"4AM": 1, "DNS": 2, "TWIS": 3, "AT": 4, **REST}, {"4AM": 6, "DNS": 2, "TWIS": 4, "AT": 0}
GAME2_PLACES, GAME2_KILLS = {"4AM": 4, "DNS": 1, "TWIS": 2, "AT": 3, **REST}, {"4AM": 1, "DNS": 9, "TWIS": 3, "AT": 2}


def test_a_player_is_found_by_name_and_their_lobbies_by_team_and_name(index):
    adapter, _ = index
    [ref] = adapter.search("tgltn")
    assert (ref.system, ref.athlete_id, ref.name, ref.team_ids, ref.game) == ("pubg_esports", PRO, "TGLTN", ["4AM"], "PUBG")
    assert adapter.find_player("4AM_TGLTN").athlete_id == PRO  # the in-game name works too
    assert adapter.find_player("p0") is None  # a name on every team: the agent decides
    assert adapter.player(PRO).team_names == ["4AM"] and adapter.player("4am_nobody") is None
    [f] = adapter.fixtures(ref)
    assert f.status == "live" and f.title == "PUBG Global Series · Day 1"


def test_live_lobbies_are_read_once_a_tournament_a_round(index):
    adapter, calls = index
    calls.clear()
    loc = {"tournament": "as-pgsc4t", "day": adapter.lobbies()[0]["day"], "n": 1}
    calls.clear()
    snaps = adapter.snapshots({1: loc, 2: loc, 3: {**loc, "n": 9}})
    assert calls == [("as-pgsc4t", adapter.LIVE_AGE)]  # one list read for both lobbies, and fresh
    assert snaps[1].clock_label == "After match 2" and 3 not in snaps


def test_registry_rows_from_pubg_players(index):
    adapter, _ = index
    rows = {r["name"]: r for r in registry.roster_rows(KNOWN["pubg_esports"], pe.rostered(adapter.players()))}
    tgltn = rows["TGLTN"]
    assert tgltn["qid"].startswith("pubg:") and len(tgltn["qid"]) <= 20 and len({r["qid"] for r in rows.values()}) == len(rows)
    assert (tgltn["aliases"], tgltn["teams"], tgltn["league"], tgltn["ids"]) == (["4AM_TGLTN"], ["4AM"], "pubg", {"pubg_esports": PRO})


# ---------------------------------------------------------------- a pro on many event accounts


def _ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


SECOND = "account." + "b" * 32  # TGLTN's account at the next event


@pytest.fixture
def two_events(monkeypatch):
    """The adapter over a fake PUBG: TGLTN played last week's PAS on one account and plays today's PGS on
    another, as PUBG gives a pro a new account for each event (TGLTN, FLC_Kickstart, GEN_Salute and more
    on 13 to 14 Oct 2026)."""
    now = datetime.now(timezone.utc)
    docs = {
        "am-pas2f2": {"p1": _doc("p1", now - timedelta(days=6), GAME1_PLACES, GAME1_KILLS), "p2": _doc("p2", now - timedelta(days=6) + timedelta(minutes=40), GAME2_PLACES, GAME2_KILLS)},
        "as-pgs10gsg": {"g1": _doc("g1", now - timedelta(minutes=50), GAME1_PLACES, GAME1_KILLS, tgltn=SECOND)},
    }
    monkeypatch.setattr(pubg, "tournaments", lambda max_age=600: [{"id": "am-pas2f2", "createdAt": _ago(days=8)}, {"id": "as-pgs10gsg", "createdAt": _ago(days=1)}])
    monkeypatch.setattr(pubg, "tournament_matches", lambda t, max_age: [{"id": mid, "createdAt": d["data"]["attributes"]["createdAt"]} for mid, d in docs[t].items()])
    monkeypatch.setattr(pubg, "match", lambda mid, summarize: summarize(next(d[mid] for d in docs.values() if mid in d)))
    return pe.PubgEsports()


def test_a_pro_on_two_event_accounts_is_one_athlete_with_every_lobby(two_events):
    # BHA-216: search on stann.in showed TGLTN twice, one row an account each, and a fan following one
    # row had only the lobbies played on that account: none of the next event's, and no alerts from it.
    adapter = two_events
    [ref] = adapter.search("TGLTN")
    assert (ref.athlete_id, ref.name, ref.team_ids) == (PRO, "TGLTN", ["4AM"])
    assert [r.ref.athlete_id for r in pe.rostered(adapter.players())].count(PRO) == 1  # one registry row
    fixtures = adapter.fixtures(ref)
    assert [(f.competition, f.status) for f in fixtures] == [("PUBG Americas Series", "final"), ("PUBG Global Series", "live")]
    today = adapter.snapshot(fixtures[-1].locator)
    assert today.lines[PRO].headline == "6 finishes · 602 dmg"  # his line, played on the second account
    assert adapter.stats(ref, [])[0][0] == {"label": "Lobbies", "value": "1"}  # last week's, finished


def test_a_fan_following_an_event_account_keeps_the_pro(two_events):
    # Bound before pros were one athlete: by an event account, with the name and team the pro had there.
    adapter = two_events
    old = PlayerRef("pubg_esports", TGLTN, "TGLTN", ["4AM"], ["4AM"], league="pubg", game="PUBG")
    assert len(adapter.fixtures(old)) == 2  # every lobby, either account, even before the binding moves
    moved = adapter.rekey(old)
    assert moved == replace(old, athlete_id=PRO)  # what the follow is bound by from then on
    assert adapter.rekey(moved) is None
    assert not adapter.id_pattern.fullmatch(TGLTN) and adapter.id_pattern.fullmatch(PRO)  # an account isn't followed any more


def test_a_pro_is_their_team_and_name_and_a_nameless_player_is_nobody():
    assert pe.pro_id("FLC", "TGLTN") == pe.pro_id("flc", "tgltn") == "flc_tgltn"
    assert pe.pro_id("MiTH", "Baren-") == "mith_baren-" and pe.pro_id("4AM", "") is None
    lobby = _lobby(GAME1)
    lobby["matches"][0]["rosters"][0]["players"][1]["name"] = ""  # 4AM's second player, no name given
    assert set(pe.players_of([lobby])) == {pe.pro_id(t, p) for t in TEAMS for p in ("p0", "p1")} - {"4am_p0", "4am_p1"} | {PRO}


def test_the_credits_name_pubg_with_its_trademark_notice():
    [entry] = credits.credits({"pubg_esports": 1}, stockfish=False)["data"]
    assert entry["name"] == "PUBG" and entry["what"].startswith("PUBG: official tournament matches")
    assert "trademarks of PUBG Corporation" in entry["note"]


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


def test_the_minute_is_shared_a_gone_match_is_remembered_and_a_spent_minute_reads_what_is_kept(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/gone"):
            return httpx.Response(404, json={"errors": [{"title": "Not Found"}]})
        if request.url.path == "/tournaments":
            return httpx.Response(200, json={"data": [{"type": "tournament", "id": "as-pgsc4t", "attributes": {"createdAt": "2026-10-09T07:04:22Z"}}]})
        return httpx.Response(200, json=GAME1)

    redis = FakeRedis()
    monkeypatch.setattr(pubg, "_client", httpx.Client(transport=httpx.MockTransport(handler), headers=pubg._client.headers))
    monkeypatch.setattr(pubg, "token", lambda: "pubg-key")
    monkeypatch.setattr(pubg.bus, "sync_redis", lambda: redis)
    assert pubg.tournaments() == [{"id": "as-pgsc4t", "createdAt": "2026-10-09T07:04:22Z"}]
    assert seen[0].headers["authorization"] == "Bearer pubg-key" and "pubg-key" not in str(seen[0].url)
    assert pubg.match("gone", pe.summarize_match) is None and pubg.match("gone", pe.summarize_match) is None
    assert sum(r.url.path.endswith("/gone") for r in seen) == 1  # asked once, remembered
    assert pubg.match("m1", pe.summarize_match)["map"] == "Erangel" and pubg.match("m1", pe.summarize_match)["id"] == "m1"
    for _ in range(8):  # the minute's other requests
        budget.take(pubg.BUDGET, lane_name="live")
    assert pubg.tournaments(max_age=0) == [{"id": "as-pgsc4t", "createdAt": "2026-10-09T07:04:22Z"}]  # spent: the kept list
    with pytest.raises(pubg.RateLimited):
        pubg.tournament_matches("never-read", max_age=0)


def test_a_circuits_events_are_rated_the_weekly_series_and_unknown_codes_arent():
    assert [pe.event_tier(t) for t in ("as-pgsc4t", "eu-pgc2026", "eu-pecff2", "kr-26pws2f", "sea-xyz")] == [3, 3, 2, 0, 0]


def test_the_schedule_is_the_lobbies_that_started_on_those_days_from_the_index(index):
    adapter, calls = index
    today = datetime.now(timezone.utc).date()
    [lobby] = adapter.schedule(today - timedelta(days=7), today + timedelta(days=14))
    assert (lobby.status, lobby.tier, lobby.competition) == ("live", 3, "PUBG Global Series")
    assert adapter.schedule(today + timedelta(days=1), today + timedelta(days=14)) == []
