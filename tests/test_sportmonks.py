"""Sportmonks football parsers, against small made-up responses in Sportmonks' shapes (their terms
don't allow republishing real ones, so none are committed)."""

from __future__ import annotations

import threading
import time
from datetime import date
from types import SimpleNamespace

import pytest

from sports_follow import moments, sports
from sports_follow.adapters import sportmonks, sportmonks_football as sm
from sports_follow.adapters import budget

HOME, AWAY = 688, 34
STAR, SUB, UNUSED, KEEPER = 4125, 7001, 7002, 7003


def _detail(kind: str, value) -> dict:
    return {"type": {"developer_name": kind}, "data": {"value": value}}


def _team(team_id: int, name: str, code: str, location: str, winner: bool | None) -> dict:
    return {"id": team_id, "name": name, "short_code": code, "meta": {"location": location, "winner": winner}}


def _match(state: str, home_goals: int, away_goals: int, **extra) -> dict:
    return {
        "id": 555,
        "starting_at_timestamp": 1788710400,
        "state": {"developer_name": state, "name": {"FT": "Full Time", "INPLAY_2ND_HALF": "2nd Half", "NS": "Not Started", "HT": "Half-Time"}.get(state, state)},
        "league": {"name": "Super Lig"},
        "round": {"name": "7"},
        "venue": {"name": "Papara Park"},
        "participants": [_team(AWAY, "Galatasaray", "GAL", "away", state == "FT" and away_goals > home_goals), _team(HOME, "Trabzonspor", "TRA", "home", state == "FT" and home_goals > away_goals)],
        "scores": [
            {"description": "1ST_HALF", "score": {"goals": 1, "participant": "home"}},
            {"description": "CURRENT", "score": {"goals": home_goals, "participant": "home"}},
            {"description": "CURRENT", "score": {"goals": away_goals, "participant": "away"}},
        ],
        **extra,
    }


LINEUPS = [
    {"player_id": STAR, "team_id": HOME, "type_id": 11, "details": [_detail("GOALS", 2), _detail("ASSISTS", 1), _detail("SHOTS_TOTAL", 4), _detail("MINUTES_PLAYED", 67), _detail("YELLOWCARDS", 1), _detail("ACCURATE_PASSES", 21)]},
    {"player_id": SUB, "team_id": HOME, "type_id": 12, "details": [_detail("MINUTES_PLAYED", 12)]},
    {"player_id": UNUSED, "team_id": HOME, "type_id": 12, "details": []},
    {"player_id": KEEPER, "team_id": AWAY, "type_id": 11, "details": [_detail("MINUTES_PLAYED", 67), _detail("YELLOWREDCARDS", 1)]},
]
EVENTS = [
    {"type": {"developer_name": "GOAL"}, "minute": 4, "participant_id": HOME, "player_id": STAR, "player_name": "Mohamed Salah", "related_player_id": SUB, "related_player_name": "Ozan Tufan"},
    {"type": {"developer_name": "OWNGOAL"}, "minute": 30, "participant_id": AWAY, "player_id": KEEPER, "player_name": "A. Keeper"},
    {"type": {"developer_name": "PENALTY"}, "minute": 45, "extra_minute": 2, "participant_id": HOME, "player_id": STAR, "player_name": "Mohamed Salah"},
    {"type": {"developer_name": "YELLOWCARD"}, "minute": 50, "participant_id": AWAY, "player_name": None, "coach_id": 9},
    {"type": {"developer_name": "YELLOWREDCARD"}, "minute": 60, "participant_id": AWAY, "player_id": KEEPER, "player_name": "A. Keeper"},
    {"type": {"developer_name": "GOAL"}, "minute": 62, "participant_id": HOME, "player_id": SUB, "player_name": "Ozan Tufan", "rescinded": True},
    {"type": {"developer_name": "SUBSTITUTION"}, "minute": 66, "participant_id": HOME, "player_id": SUB, "player_name": "Ozan Tufan"},
]


def test_fixtures_read_teams_round_and_result():
    finished, upcoming = sm.parse_fixtures([_match("FT", 3, 0), {**_match("NS", 0, 0), "id": 556, "round": {"name": "Semi-finals"}}])
    assert finished.title == "Trabzonspor vs Galatasaray"
    assert (finished.competition, finished.detail, finished.venue) == ("Super Lig", "Matchday 7", "Papara Park")
    assert finished.team_ids == [str(HOME), str(AWAY)] and finished.locator == {"fixture": "555"}
    assert finished.start_utc.isoformat() == "2026-09-06T16:00:00+00:00"
    assert sm.SportmonksFootball().result_label(finished, [str(HOME)]) == "Won 3–0"
    assert sm.SportmonksFootball().result_label(finished, [str(AWAY)]) == "Lost 0–3"
    assert (upcoming.status, upcoming.detail, upcoming.result) == ("scheduled", "Semi-finals", None)
    friendly = sm.parse_fixtures([{**_match("FT", 1, 0), "league": {"name": "Club Friendlies 3"}, "round": None}])[0]
    assert (friendly.competition, friendly.detail) == ("Club Friendlies", None)


def test_states_map_to_our_four():
    assert [sm._status({"developer_name": s}) for s in ("NS", "INPLAY_1ST_HALF", "HT", "FT_PEN", "POSTPONED", "TBA")] == ["scheduled", "live", "live", "final", "postponed", "scheduled"]


def test_a_stopped_match_stays_live_on_its_day():
    # BUG-052 (H3 t_interrupt): INTERRUPTED and SUSPENDED were "postponed", so a match stopped for
    # weather or floodlights left the live lane until the next schedule read (up to 30 minutes), and the
    # goals after the restart arrived that late.
    kick_off = int(time.time()) - 3600
    for state, clock in (("INTERRUPTED", "Interrupted"), ("SUSPENDED", "Suspended")):
        snap = sm.parse_match(_match(state, 1, 0, starting_at_timestamp=kick_off, lineups=LINEUPS))
        assert (snap.status, snap.clock_label) == ("live", clock)
        assert snap.lines[str(STAR)].headline == "2 goals, 1 assist"
        assert sm.parse_fixtures([_match(state, 1, 0, starting_at_timestamp=kick_off)])[0].status == "live"
    # Still stopped six hours after kick-off, it's off, as postponed, cancelled and abandoned matches are.
    assert sm.parse_match(_match("SUSPENDED", 1, 0, starting_at_timestamp=kick_off - 6 * 3600)).status == "postponed"
    assert [sm._status({"developer_name": s}) for s in ("POSTPONED", "CANCELLED", "ABANDONED")] == ["postponed"] * 3


def test_a_live_match_has_score_clock_scorers_lines_and_moments():
    periods = [{"ticking": False, "minutes": 47, "counts_from": 0, "period_length": 45}, {"ticking": True, "minutes": 67, "counts_from": 45, "period_length": 45}]
    snap = sm.parse_match(_match("INPLAY_2ND_HALF", 3, 0, periods=periods, lineups=LINEUPS, events=EVENTS))
    assert (snap.status, snap.score_label, snap.clock_label) == ("live", "Trabzonspor 3 – 0 Galatasaray", "67'")
    assert snap.state["home"] == {"id": str(HOME), "name": "Trabzonspor", "abbr": "TRA", "score": "3", "scorers": ["Mohamed Salah 4'", "A. Keeper 30' (OG)", "Mohamed Salah 45+2' (pen)"]}
    assert snap.state["away"]["scorers"] == []

    star = snap.lines[str(STAR)]
    assert star.headline == "2 goals, 1 assist"
    # The labels moments.py reads for goal, assist and card alerts.
    assert star.stats == [{"label": "Goals", "value": "2"}, {"label": "Assists", "value": "1"}, {"label": "Shots", "value": "4"}, {"label": "Yellow", "value": "1"}, {"label": "Minutes", "value": "67"}]
    assert snap.lines[str(SUB)].headline == "Came on"
    assert snap.lines[str(UNUSED)].headline == "On the bench"
    assert {"label": "Red", "value": "1"} in snap.lines[str(KEEPER)].stats  # a second yellow is a red

    # Newest first; the coach's card isn't a moment, and the goal VAR ruled out is marked so (its alert is corrected).
    assert [(m["clock"], m["kind"], m["text"]) for m in snap.moments] == [
        ("62'", "DISALLOWED", "Ozan Tufan (Trabzonspor)"),
        ("60'", "RED", "A. Keeper (Galatasaray), second yellow"),
        ("45+2'", "GOAL", "Mohamed Salah (Trabzonspor), penalty"),
        ("30'", "GOAL", "A. Keeper (Galatasaray), own goal"),
        ("4'", "GOAL", "Mohamed Salah (Trabzonspor), assist Ozan Tufan"),
    ]
    assert snap.moments[-1]["athlete_ids"] == [str(STAR), str(SUB)]


def test_a_goal_ruled_out_corrects_the_goals_alert():
    # Ozan Tufan's 62' goal was told, keyed by its minute; Sportmonks has since rescinded it.
    snap = sm.parse_match(_match("INPLAY_2ND_HALF", 3, 0, lineups=LINEUPS, events=EVENTS))

    def seen(athlete: int) -> moments.Seen:
        line = snap.lines[str(athlete)]
        return moments.Seen(snap.status, snap.state, {"headline": line.headline, "stats": line.stats}, snap.score_label, snap.clock_label, events=snap.moments)

    [f] = moments.detect("Football", "Ozan Tufan", str(SUB), seen(SUB), seen(SUB), [("goal", "goal@62'")])
    assert (f.kind, f.title, f.detail) == ("correction", "Goal ruled out: Ozan Tufan", "goal:goal@62'")
    # Mohamed Salah's goals stand.
    assert moments.detect("Football", "Mohamed Salah", str(STAR), seen(STAR), seen(STAR), [("goal", "goal@4'"), ("goal", "goal@45+2'")]) == []


def test_every_goal_stays_in_the_events_however_many_cards():
    # Fourteen cards after the 4' goal and the ruled-out 5' one: the newest dozen alone would drop both,
    # and a goal's alert reads its play there (whether it stands, what it was).
    cards = [{"type": {"developer_name": "YELLOWCARD"}, "minute": 10 + n, "participant_id": AWAY, "player_id": KEEPER, "player_name": "A. Keeper"} for n in range(14)]
    ruled_out = {"type": {"developer_name": "GOAL"}, "minute": 5, "participant_id": HOME, "player_id": SUB, "player_name": "Ozan Tufan", "rescinded": True}
    snap = sm.parse_match(_match("INPLAY_2ND_HALF", 1, 0, events=[EVENTS[0], ruled_out, *cards]))
    assert [(m["clock"], m["kind"]) for m in snap.moments[12:]] == [("5'", "DISALLOWED"), ("4'", "GOAL")]


def test_clock_in_stoppage_time_and_breaks():
    stoppage = [{"ticking": True, "minutes": 93, "counts_from": 45, "period_length": 45}]
    assert sm.clock(_match("INPLAY_2ND_HALF", 1, 1, periods=stoppage)) == "90+3'"
    assert sm.clock(_match("HT", 1, 1)) == "Half-time"
    assert sm.clock(_match("FT", 1, 1)) == "Full time"


def test_no_lines_before_kickoff():
    assert sm.parse_match(_match("NS", 0, 0, lineups=LINEUPS)).lines == {}


def test_player_keeps_current_teams_club_first():
    player = {
        "id": STAR, "display_name": "Mohamed Salah", "date_of_birth": "1992-06-15",
        "teams": [
            {"team_id": 18546, "end": None, "team": {"name": "Egypt", "type": "national"}},
            {"team_id": 8, "end": "2026-07-01", "team": {"name": "Liverpool", "type": "domestic"}},
            {"team_id": HOME, "end": "2029-06-30", "team": {"name": "Trabzonspor", "type": "domestic"}},
        ],
    }
    ref = sm.parse_player(player, today=date(2026, 9, 30))
    assert (ref.system, ref.athlete_id, ref.born) == ("sportmonks_football", str(STAR), "1992-06-15")
    assert ref.team_ids == [str(HOME), "18546"] and ref.team_names == ["Trabzonspor", "Egypt"]
    assert sm.parse_player({"id": 1, "display_name": "Lautaro Martínez\u00a0"}).name == "Lautaro Martínez"  # padded, as Sportmonks sends some


def test_season_stats_add_up_this_seasons_competitions_for_the_players_teams():
    def row(team, league, current, **values):
        return {"team_id": team, "season": {"name": "2026/2027", "is_current": current, "league": {"name": league}}, "details": [{"type": {"developer_name": k}, "value": {"total": v}} for k, v in values.items()]}

    player = {"statistics": [
        row(HOME, "Super Lig", True, APPEARANCES=6, GOALS=7, ASSISTS=2, MINUTES_PLAYED=481),
        row(HOME, "Turkish Cup", True, APPEARANCES=1, GOALS=1),
        row(8, "Premier League", False, APPEARANCES=38, GOALS=29),
        row(99, "Club Friendlies", True, APPEARANCES=3),
    ]}
    stats, note = sm.parse_season_stats(player, [str(HOME)])
    assert stats == [{"label": "Apps", "value": "7"}, {"label": "Goals", "value": "8"}, {"label": "Assists", "value": "2"}, {"label": "Minutes", "value": "481"}]
    assert note == "2026/2027 season, all competitions"
    assert sm.parse_season_stats({"statistics": [row(HOME, "Super Lig", True, GOALS=7)]}, [str(HOME)])[1] == "Super Lig 2026/2027"
    assert sm.parse_season_stats({}, [str(HOME)]) == ([], "")


EGYPT = 18546
PLAYER = {
    "id": STAR, "display_name": "Mohamed Salah",
    "teams": [
        {"team_id": HOME, "end": "2099-06-30", "team": {"name": "Trabzonspor", "type": "domestic"}},
        {"team_id": EGYPT, "end": None, "team": {"name": "Egypt", "type": "national"}},
    ],
    "statistics": [{"team_id": HOME, "season": {"name": "2026/2027", "is_current": True, "league": {"name": "Super Lig"}}, "details": [{"type": {"developer_name": "GOALS"}, "value": {"total": 7}}]}],
}


def _serve(monkeypatch, answer) -> list[tuple[str, str | None]]:
    """Sportmonks answering each request from answer(path, include): a document, or an HTTP status for an
    error. Every request it's sent is noted (path, include)."""
    sent = []

    def get(url, params=None, headers=None):
        path, include = url.removeprefix(sportmonks.BASE), (params or {}).get("include")
        sent.append((path, include))
        doc = answer(path, include)
        if isinstance(doc, int):
            return SimpleNamespace(status_code=doc, headers={}, json=lambda: {"message": "made up"})
        return SimpleNamespace(status_code=200, headers={}, json=lambda: doc)

    monkeypatch.setattr(sportmonks, "token", lambda: "example")
    monkeypatch.setattr(sportmonks._client, "get", get)
    return sent


def test_who_a_player_is_and_their_season_are_one_read(monkeypatch):
    """A preview asks player() then stats(): one Player request answers both, each being about a second
    from the server. A search still reads names and teams alone."""
    sent = _serve(monkeypatch, lambda path, include: {"data": [PLAYER] if "/search/" in path else PLAYER})
    adapter = sm.SportmonksFootball()
    ref = adapter.player(str(STAR))
    assert ref is not None and ref.team_names == ["Trabzonspor", "Egypt"]
    assert adapter.stats(ref, []) == ([{"label": "Goals", "value": "7"}], "Super Lig 2026/2027")
    assert sent == [(f"/football/players/{STAR}", sm.PLAYER_STATS_INCLUDE)]
    sent.clear()
    adapter.search("salah")
    assert [include for _, include in sent] == [sm.PLAYER_INCLUDE]


def test_who_a_player_is_doesnt_hang_on_their_statistics(monkeypatch):
    """A read with the statistics that fails (a plan without them, a timeout) is asked again without: the fan's
    search result is still that athlete, not "no such athlete". A source out of requests isn't asked twice."""
    sent = _serve(monkeypatch, lambda path, include: 403 if include == sm.PLAYER_STATS_INCLUDE else {"data": PLAYER})
    ref = sm.SportmonksFootball().player(str(STAR))
    assert ref is not None and ref.name == "Mohamed Salah"
    assert [include for _, include in sent] == [sm.PLAYER_STATS_INCLUDE, sm.PLAYER_INCLUDE]
    sent = _serve(monkeypatch, lambda path, include: 429)
    assert sm.SportmonksFootball().player(str(STAR)) is None and len(sent) == 1


def test_an_id_sportmonks_has_no_player_by_costs_one_request(monkeypatch):
    """BUG-128: a made-up id was asked again without the statistics, two of the hour's Player requests for each."""
    sent = _serve(monkeypatch, lambda path, include: 404)
    assert sm.SportmonksFootball().player("90000001") is None
    assert sent == [("/football/players/90000001", sm.PLAYER_STATS_INCLUDE)]


def test_a_preview_reads_the_player_once_and_club_and_country_side_by_side(monkeypatch):
    """A football preview before following (search.preview) waits on each round of requests, about a second
    each from the server (Grafana, 17 Oct: four in a row, 4.4 s). It's three rounds: the player with their
    season, their teams' fixtures (club and country at once), the last match for their line."""
    from sports_follow import search

    both_teams = threading.Barrier(2, timeout=5)  # broken unless the two teams' reads are out together
    lanes = []

    def answer(path, include):
        if path == f"/football/players/{STAR}":
            return {"data": PLAYER}
        if path.startswith("/football/fixtures/between/"):
            lanes.append(budget.lane())
            both_teams.wait()
            return {"data": [_match("FT", 2, 1)] if path.endswith(f"/{HOME}") else []}  # no internationals on the plan
        if path == "/football/fixtures/555":
            return {"data": _match("FT", 2, 1, lineups=LINEUPS, events=EVENTS)}
        raise AssertionError(f"a preview mustn't ask for {path}")

    sent = _serve(monkeypatch, answer)
    monkeypatch.setitem(search.ADAPTERS, sm.SportmonksFootball.system, sm.SportmonksFootball())
    search._previews.clear()
    with budget.in_lane("fan"):
        out = search.preview(sm.SportmonksFootball.system, str(STAR), None)
    # A fan is waiting, in the threads too: background work would stop at the live reserve.
    assert lanes == ["fan", "fan"] and [r["lane"] for r in budget.usage([sportmonks.BUDGET])] == ["fan"]
    assert out is not None and out["stats"] == [{"label": "Goals", "value": "7"}]
    assert out["last"]["player_contribution"] == "2 goals, 1 assist"
    paths = [path for path, _ in sent]
    assert [p for p in paths if p.startswith("/football/players/")] == [f"/football/players/{STAR}"]
    assert sorted(p.rsplit("/", 1)[1] for p in paths if p.startswith("/football/fixtures/between/")) == sorted([str(HOME), str(EGYPT)])
    assert paths[-1] == "/football/fixtures/555" and len(paths) == 4


def test_a_team_outside_the_plan_leaves_the_others_fixtures_and_a_spent_hour_stops_the_read(monkeypatch):
    ref = sm.parse_player(PLAYER)

    def outside(path, include):
        return {"data": [_match("FT", 2, 1)]} if path.endswith(f"/{HOME}") else 403

    _serve(monkeypatch, outside)
    assert [f.source_id for f in sm.SportmonksFootball().fixtures(ref)] == ["555"]
    _serve(monkeypatch, lambda path, include: {"data": []} if path.endswith(f"/{HOME}") else 429)
    with pytest.raises(sportmonks.RateLimited):
        sm.SportmonksFootball().fixtures(ref)


def test_history_pages_step_back_ninety_days_per_team():
    ref = sm.parse_player({"id": 1, "display_name": "X", "teams": [{"team_id": 1, "team": {"type": "domestic"}}, {"team_id": 2, "team": {"type": "national"}}]})
    pages = sm.SportmonksFootball().history_pages(ref, date(2026, 9, 30))
    assert pages[:2] == [{"team": "1", "from": "2026-03-04", "to": "2026-06-01"}, {"team": "2", "from": "2026-03-04", "to": "2026-06-01"}]
    assert pages[-1]["from"] == "2025-06-07" and len(pages) == 8


def test_background_reads_stop_at_the_live_reserve(source_budgets):
    assert sportmonks.spare(sportmonks.FIXTURES)  # nothing read yet
    budget.observe(sportmonks.BUDGET, sportmonks.FIXTURES, sportmonks.LIVE_RESERVE, 600)  # what Sportmonks says is left
    assert not sportmonks.spare(sportmonks.FIXTURES)
    budget.take(sportmonks.BUDGET, sportmonks.FIXTURES, "live")  # live polls still go
    with pytest.raises(sportmonks.RateLimited):
        budget.take(sportmonks.BUDGET, sportmonks.FIXTURES, "history")
    source_budgets.data.clear()  # the hour has reset since
    assert sportmonks.spare(sportmonks.FIXTURES)


def test_live_matches_come_fifty_to_a_request(monkeypatch):
    calls = []

    def get_json(path, ttl, **params):
        calls.append((path, ttl, params))
        ids = path.rsplit("/", 1)[1].split(",")
        return {"data": [{**_match("INPLAY_2ND_HALF", 1, 0), "id": int(i)} for i in ids if i != "999"]}

    monkeypatch.setattr(sportmonks, "get_json", get_json)
    locators = {event: {"fixture": str(1000 + event)} for event in range(60)}
    locators[60] = {"fixture": "1000"}  # two events, one match: asked for once
    locators[61] = {"fixture": "999"}  # a match Sportmonks doesn't return
    snaps = sm.SportmonksFootball().snapshots(locators)
    assert [len(path.rsplit("/", 1)[1].split(",")) for path, _, _ in calls] == [50, 11]
    assert all(path.startswith("/football/fixtures/multi/") and ttl == 0 and params == {"include": sm.MATCH_INCLUDE} for path, ttl, params in calls)
    assert set(snaps) == set(range(61))
    assert snaps[0].score_label == "Trabzonspor 1 – 0 Galatasaray" and snaps[60] is snaps[0]


def test_one_live_match_may_come_back_as_an_object(monkeypatch):
    monkeypatch.setattr(sportmonks, "get_json", lambda path, ttl, **params: {"data": _match("HT", 1, 1)})
    assert sm.SportmonksFootball().snapshots({"e": {"fixture": "555"}})["e"].clock_label == "Half-time"


def test_an_uncached_read_is_not_kept(monkeypatch):
    response = SimpleNamespace(status_code=200, headers={}, json=lambda: {"data": []})
    monkeypatch.setattr(sportmonks, "token", lambda: "example")
    monkeypatch.setattr(sportmonks._client, "get", lambda *args, **kwargs: response)
    sportmonks.get_json("/football/fixtures/multi/1,2", ttl=0)
    assert sportmonks._cache.entries == {}
    sportmonks.get_json("/football/fixtures/1", ttl=5)
    assert len(sportmonks._cache.entries) == 1


def _confirmed(value: bool) -> list[dict]:
    return [{"type_id": 159, "values": {"home": "3-4-2-1", "away": "4-2-3-1"}}, {"type_id": 572, "values": {"confirmed": value}}]


def test_a_predicted_line_up_is_nobodys_team_sheet():
    # BHA-43 (seen 15 Oct 2026, a Bundesliga match two days out): Sportmonks fills the line-ups with its
    # own prediction, eleven starters a side, marked unconfirmed. Armed an hour and a quarter ahead, the
    # poll would have told fans "starts" from a guess.
    predicted = [{"player_id": STAR, "team_id": HOME, "type_id": 11}, {"player_id": KEEPER, "team_id": AWAY, "type_id": 11}]
    assert sm.parse_match(_match("NS", 0, 0, lineups=predicted, metadata=_confirmed(False))).squad == {}
    published = sm.parse_match(_match("NS", 0, 0, lineups=LINEUPS, metadata=_confirmed(True)))
    assert published.squad == {str(STAR): "starting", str(SUB): "bench", str(UNUSED): "bench", str(KEEPER): "starting"}


def test_a_predicted_line_up_makes_no_lines_after_kick_off_either():
    # The clubs' sheets still unconfirmed at kick-off: a guessed starter would be told as one, and a
    # player missing from the guess would read as left out.
    predicted = [{"player_id": STAR, "team_id": HOME, "type_id": 11}]
    assert sm.parse_match(_match("INPLAY_1ST_HALF", 0, 0, lineups=predicted, metadata=_confirmed(False))).lines == {}
    assert sm.parse_match(_match("INPLAY_1ST_HALF", 0, 0, lineups=LINEUPS, metadata=_confirmed(True))).lines


def test_the_match_poll_asks_whether_the_line_ups_are_confirmed():
    assert "metadata" in sm.MATCH_INCLUDE.split(";")


def test_the_team_sheet_before_kick_off_and_goal_details():
    snap = sm.parse_match(_match("NS", 0, 0, lineups=LINEUPS))
    assert snap.lines == {}  # a team sheet isn't a line
    assert snap.squad == {str(STAR): "starting", str(SUB): "bench", str(UNUSED): "bench", str(KEEPER): "starting"}
    live = sm.parse_match(_match("INPLAY_2ND_HALF", 3, 0, lineups=LINEUPS, events=EVENTS))
    penalty, own_goal, opener = [m for m in live.moments if m["kind"] == "GOAL"]
    assert (opener["team_id"], opener["assist"], opener["own_goal"]) == (str(HOME), "Ozan Tufan", False)
    assert (own_goal["own_goal"], own_goal["assist"]) == (True, None)
    assert penalty["clock"] == "45+2'" and penalty["athlete_ids"] == [str(STAR)]


def test_live_now_lists_every_match_in_play_with_its_starting_elevens(monkeypatch):
    named = [{**row, "player_name": name} for row, name in zip(LINEUPS, ["Mohamed Salah", "Ozan Tufan", "Bench Warmer", "A. Keeper"])]
    in_play = _match("INPLAY_2ND_HALF", 2, 1, lineups=named, events=EVENTS[:1])
    asked = []

    def get_json(path, ttl, **params):
        asked.append((path, sportmonks._bucket(path)))
        return {"data": [in_play, _match("FT", 1, 0, id=556)]}  # a match just over isn't on now

    monkeypatch.setattr(sportmonks, "get_json", get_json)
    [game] = sm.SportmonksFootball().live_now()
    assert asked == [("/football/livescores/inplay", "Fixture")]  # Sportmonks counts livescores as fixtures
    assert (game.source_id, game.title, game.competition, game.teams) == ("555", "Trabzonspor vs Galatasaray", "Super Lig", ["Trabzonspor", "Galatasaray"])
    assert game.snapshot.status == "live" and game.snapshot.state["home"]["score"] == "2"
    # The starting elevens only, as the line-ups name them, with their team.
    assert [(p.system, p.athlete_id, p.name, p.team_names) for p in game.players] == [
        ("sportmonks_football", str(STAR), "Mohamed Salah", ["Trabzonspor"]), ("sportmonks_football", str(KEEPER), "A. Keeper", ["Galatasaray"])]


def test_a_live_matchs_players_come_attackers_first_the_sides_in_turn():
    def starter(pid: int, team: int, position: int, name: str) -> dict:
        return {"player_id": pid, "team_id": team, "type_id": 11, "position_id": position, "player_name": name}

    lineups = [starter(1, HOME, 24, "Home Keeper"), starter(2, HOME, 27, "Home Striker"), starter(3, AWAY, 25, "Away Back"), starter(4, AWAY, 27, "Away Striker")]
    [game] = sm.parse_live([_match("INPLAY_1ST_HALF", 0, 0, lineups=lineups)], "sportmonks_football")
    assert [p.name for p in game.players] == ["Home Striker", "Away Striker", "Home Keeper", "Away Back"]


def test_a_fixture_carries_both_sides_places_in_their_table():
    placed = _match("FT", 1, 0)
    placed["participants"][0]["meta"]["position"], placed["participants"][1]["meta"]["position"] = 14, 2  # away first, as Sportmonks may list them
    assert sm.parse_fixtures([placed])[0].facts == {"table": [2, 14]}  # home's place, then away's
    assert sm.parse_fixtures([_match("FT", 1, 0)])[0].facts == {}


def test_the_schedule_is_every_league_match_between_two_days_counted_as_fixtures(monkeypatch):
    asked = []

    def get_json(path, ttl, **params):
        asked.append((path, sportmonks._bucket(path), params))
        page = params["page"]
        return {"data": [_match("FT", 2, 1, id=554 + page)], "pagination": {"has_more": page < 2}}

    monkeypatch.setattr(sportmonks, "get_json", get_json)
    games = sm.SportmonksFootball().schedule(date(2026, 10, 9), date(2026, 10, 30))
    assert [g.source_id for g in games] == ["555", "556"] and games[0].result["home"]["score"] == "2"
    # No team: every match of the plan's leagues, a page of 50 a request, each a Fixture request.
    assert [(path, bucket, params["per_page"], params["page"]) for path, bucket, params in asked] == [
        ("/football/fixtures/between/2026-10-09/2026-10-30", "Fixture", 50, 1), ("/football/fixtures/between/2026-10-09/2026-10-30", "Fixture", 50, 2)]
    assert set(asked[0][2]["include"].split(";")) >= {"participants", "scores", "state", "league", "round"}


def _spell(spell_id: int, player_id: int, name: str, kind: str, category: str, start: str, end: str | None, **extra) -> dict:
    return {"id": spell_id, "player_id": player_id, "category": category, "start_date": start, "end_date": end, "completed": False,
            "type": {"name": kind.replace("_", " ").title(), "developer_name": kind},
            "player": {"id": player_id, "display_name": name, "date_of_birth": "2001-03-24"}, **extra}


def test_who_is_out_is_each_teams_list_injuries_bans_and_the_rest():
    teams = sm.parse_sidelined([
        {"id": 19, "name": "Arsenal", "sidelined": [
            _spell(1, 4125, "William Saliba ", "BACK_INJURY", "injury", "2026-07-19", "2026-12-11"),
            _spell(2, 7001, "Kai Havertz", "THIGH_PROBLEMS", "injury", "2026-09-24", None),
            _spell(3, 7002, "Declan Rice", "RED_CARD_SUSPENSION", "suspended", "2026-10-10", "2026-10-20"),
            _spell(4, 7003, "Wataru Endo", "NO_ELIGIBILITY", "suspended", "2026-09-03", "2027-01-28"),
            _spell(7, 7005, "Paul Pogba", "SUSPENSION_THROUGH_SPORTS_COURT", "suspended", "2026-03-01", None),  # a ban too
            _spell(8, 7006, "Away With Country", "CALLED_UP_TO_NATIONAL_TEAM", "suspended", "2026-10-10", "2026-10-15"),
            _spell(5, 7004, "Old Knock", "KNOCK", "injury", "2026-01-01", "2026-01-08", completed=True),  # over: not listed
            {"id": 6, "category": "injury", "type": {}, "player": None},  # no player: nobody's
        ]},
        {"id": 20, "name": "Chelsea", "sidelined": []},  # nobody out, but read: a spell gone from it is over
        {"id": 21, "name": "Spurs"},  # an answer without the list: as if not read, so nobody's spell ends
    ])
    assert set(teams) == {"19", "20"} and teams["20"] == []
    saliba, havertz, rice, endo, pogba, away = teams["19"]
    assert (saliba.source_id, saliba.ref.athlete_id, saliba.ref.name, saliba.ref.team_ids, saliba.ref.team_names, saliba.ref.born) == ("1", "4125", "William Saliba", ["19"], ["Arsenal"], "2001-03-24")
    assert (saliba.kind, saliba.what, saliba.since, saliba.until) == ("injury", "Back Injury", date(2026, 7, 19), date(2026, 12, 11))
    assert havertz.until is None
    assert [(a.kind, a.what) for a in (rice, endo, pogba, away)] == [
        ("suspension", "Red Card Suspension"), ("other", "No Eligibility"), ("suspension", "Suspension Through Sports Court"), ("other", "Called Up To National Team")]


def test_who_is_out_is_read_a_season_at_a_time_counted_as_teams(monkeypatch):
    asked = []

    def get_json(path, ttl, **params):
        asked.append((path, sportmonks._bucket(path), ttl, params))
        if path == "/football/leagues":
            return {"data": [{"id": 8, "currentseason": {"id": 28083}}, {"id": 82, "currentseason": {"id": 28321}}, {"id": 9, "currentseason": None}]}
        season = path.rsplit("/", 1)[1]
        return {"data": [{"id": int(season[-2:]), "name": f"Team {season}", "sidelined": [_spell(int(season), 1, "A Player", "KNOCK", "injury", "2026-10-01", None)]}]}

    monkeypatch.setattr(sportmonks, "get_json", get_json)
    teams = sm.SportmonksFootball().sidelined()
    assert {team: [a.source_id for a in listed] for team, listed in teams.items()} == {"83": ["28083"], "21": ["28321"]}
    # The leagues with their seasons on now, kept a day; then each season's teams with who's out, uncached.
    assert [(path, bucket, ttl) for path, bucket, ttl, _ in asked] == [
        ("/football/leagues", "League", 24 * 3600), ("/football/teams/seasons/28083", sportmonks.TEAMS, 0), ("/football/teams/seasons/28321", sportmonks.TEAMS, 0)]
    assert asked[0][3]["include"] == sm.LEAGUES_INCLUDE and set(asked[1][3]["include"].split(";")) == {"sidelined.player", "sidelined.type"}


def test_a_match_page_reads_one_match_with_its_timeline_lines_and_both_elevens(monkeypatch):
    def starter(pid: int, team: int, position: int, name: str) -> dict:
        return {"player_id": pid, "team_id": team, "type_id": 11, "position_id": position, "player_name": name, "details": [_detail("MINUTES_PLAYED", 60)]}

    lineups = [starter(1, HOME, 24, "Home Keeper"), starter(2, HOME, 27, "Home Striker"), starter(3, AWAY, 25, "Away Back"),
               {"player_id": 5, "team_id": AWAY, "type_id": 12, "player_name": "Away Sub", "details": []}]
    asked = []

    def get_json(path, ttl, **params):
        asked.append((path, sportmonks._bucket(path), params["include"]))
        return {"data": _match("INPLAY_2ND_HALF", 1, 0, lineups=lineups, events=EVENTS[:1])}

    monkeypatch.setattr(sportmonks, "get_json", get_json)
    game = sm.SportmonksFootball().match("555")
    assert asked == [("/football/fixtures/555", "Fixture", sm.MATCH_INCLUDE)]  # one request, counted as a fixture
    assert game is not None and (game.source_id, game.title, game.teams) == ("555", "Trabzonspor vs Galatasaray", ["Trabzonspor", "Galatasaray"])
    # The home eleven, then the away one, attackers first; not the bench.
    assert [(p.name, p.team_names) for p in game.players] == [("Home Striker", ["Trabzonspor"]), ("Home Keeper", ["Trabzonspor"]), ("Away Back", ["Galatasaray"])]
    assert game.snapshot.moments[0]["kind"] == "GOAL" and game.snapshot.lines["2"].headline == "Started"
    # A guessed line-up names nobody yet; a match Sportmonks hasn't got is none.
    monkeypatch.setattr(sportmonks, "get_json", lambda path, ttl, **params: {"data": _match("NS", 0, 0, lineups=lineups, metadata=_confirmed(False))})
    assert sm.SportmonksFootball().match("555").players == []
    monkeypatch.setattr(sportmonks, "get_json", lambda path, ttl, **params: {"data": None})
    assert sm.SportmonksFootball().match("555") is None


def test_a_match_page_reads_a_finished_match_whole_with_every_card_and_who_won(monkeypatch):
    # A made-up full-time match: three goals, and fourteen bookings besides, more than a live poll keeps.
    bookings = [{"type": {"developer_name": "YELLOWCARD"}, "minute": 20 + m, "participant_id": AWAY, "player_id": KEEPER, "player_name": f"Booked {m}"} for m in range(14)]
    lineups = [{"player_id": STAR, "team_id": HOME, "type_id": 11, "position_id": 27, "player_name": "Mohamed Salah", "details": [_detail("GOALS", 2), _detail("MINUTES_PLAYED", 90)]}]
    x = _match("FT", 2, 1, lineups=lineups, events=[EVENTS[0], EVENTS[1], *bookings])
    monkeypatch.setattr(sportmonks, "get_json", lambda path, ttl, **params: {"data": x})
    game = sm.SportmonksFootball().match("555")
    assert game is not None and (game.snapshot.status, game.snapshot.score_label) == ("final", "Trabzonspor 2 – 1 Galatasaray")
    assert len(game.snapshot.moments) == 16 and game.snapshot.moments[-1]["clock"] == "4'"  # the whole timeline, newest first
    assert len(sm.parse_match(x).moments) == 13  # a poll keeps the newest dozen, and every goal
    assert game.snapshot.lines[str(STAR)].headline == "2 goals"
    state = game.snapshot.state
    assert sports.get("football").verdict(state, game.snapshot.score_label, game.snapshot.clock_label) == "Trabzonspor won"
