"""The sources' side of following a team (BHA-77): each source's list of teams (teams()), one team's games
(team_fixtures()) and its squad now (team()), with each result said from the team's side (result_label).

Against small made-up responses in the sources' shapes (Sportmonks' and PandaScore's terms don't allow committing
real ones), and ESPN's basketball schedule recorded on 2026-09-29 (tests/fixtures/espn)."""

from __future__ import annotations

import gzip
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from sports_follow import sports
from sports_follow.adapters import KNOWN, AdapterError, PlayerRef, espn, espn_basketball as eb, pandascore, pandascore_esports as ps, sportmonks
from sports_follow.adapters import sportmonks_cricket as sc, sportmonks_football as sm

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    with gzip.open(FIXTURES / f"{name}.json.gz", "rt") as f:
        return json.load(f)


def _days(span: str) -> int:
    """How many days a request's window spans, from the two dates in it."""
    first, last = (date.fromisoformat(d) for d in re.findall(r"\d{4}-\d{2}-\d{2}", span)[:2])
    return (last - first).days


def test_every_source_with_a_list_of_teams_can_say_a_teams_games_and_squad():
    listing = {system for system, a in KNOWN.items() if callable(getattr(a, "teams", None))}
    assert listing == {"sportmonks_football", "sportmonks_cricket", "espn_basketball", "pandascore_esports"}
    for system in listing:
        adapter = KNOWN[system]
        assert callable(getattr(adapter, "team_fixtures", None)) and callable(getattr(adapter, "team", None)), system
        assert isinstance(getattr(adapter, "team_id_pattern", None), re.Pattern), system


# ---------------------------------------------------------------- football (Sportmonks)

ARSENAL, CHELSEA, ENGLAND = 19, 18, 462


def _side(team_id: int, name: str, location: str, winner: bool | None = None) -> dict:
    return {"id": team_id, "name": name, "short_code": name[:3].upper(), "meta": {"location": location, "winner": winner}}


def _game(fixture_id: int, home: tuple[int, str], away: tuple[int, str], state: str, at: int, goals: tuple[int, int] = (0, 0)) -> dict:
    final = state == "FT"
    return {
        "id": fixture_id, "starting_at_timestamp": at, "state": {"developer_name": state}, "league": {"name": "Premier League"}, "round": {"name": "8"},
        "participants": [_side(*home, "home", final and goals[0] > goals[1]), _side(*away, "away", final and goals[1] > goals[0])],
        "scores": [{"description": "CURRENT", "score": {"goals": goals[0], "participant": "home"}}, {"description": "CURRENT", "score": {"goals": goals[1], "participant": "away"}}],
    }


def test_football_lists_the_plans_leagues_teams_a_season_at_a_time(monkeypatch):
    asked = []

    def get_json(path, ttl, **params):
        asked.append((path, sportmonks._bucket(path), ttl, params))
        if path == "/football/leagues":
            return {"data": [
                {"id": 2, "name": "Champions League", "sub_type": "international", "currentseason": {"id": 25580}},
                {"id": 8, "name": "Premier League", "sub_type": "domestic", "currentseason": {"id": 25583}},
                {"id": 9, "name": "Off League", "currentseason": None},
            ]}
        if path.endswith("/25583"):
            return {"data": [
                {"id": ARSENAL, "name": "Arsenal ", "short_code": "ARS", "country": {"name": "England"}},
                {"id": CHELSEA, "name": "Chelsea", "short_code": "CHE", "country": {"name": "England"}},
            ]}
        return {"data": [
            {"id": ARSENAL, "name": "Arsenal", "short_code": "ARS", "country": {"name": "England"}},
            {"id": 77001, "name": "Winner Group A", "placeholder": True},  # a bracket's slot, not a team
        ]}

    monkeypatch.setattr(sportmonks, "get_json", get_json)
    teams = sm.SportmonksFootball().teams()
    # Each league's teams, the domestic league's first: a club in two is listed under each (teams.py makes it one).
    assert [(t.team_id, t.name, t.short, t.competition, t.country) for t in teams] == [
        ("19", "Arsenal", "ARS", "Premier League", "England"), ("18", "Chelsea", "CHE", "Premier League", "England"),
        ("19", "Arsenal", "ARS", "Champions League", "England")]
    assert all(t.system == "sportmonks_football" and t.sport == "" and t.tier == 0 and t.players == [] for t in teams)
    # The leagues with their seasons on now (sidelined()'s read, kept a day), then a Team request a season, kept a day.
    assert [(path, bucket, ttl) for path, bucket, ttl, _ in asked] == [
        ("/football/leagues", "League", 24 * 3600), ("/football/teams/seasons/25583", "Team", 24 * 3600), ("/football/teams/seasons/25580", "Team", 24 * 3600)]
    assert asked[0][3]["include"] == sm.LEAGUES_INCLUDE and asked[1][3]["include"] == "country"  # not who's out


def test_football_team_fixtures_are_one_teams_window_and_a_players_are_their_teams(monkeypatch):
    soon, later, before = 1792000000, 1793000000, 1789000000
    asked = []

    def get_json(path, ttl, **params):
        asked.append((path, ttl, params))
        team = int(path.rsplit("/", 1)[1])
        if team == ENGLAND:
            raise AdapterError("Sportmonks returned HTTP 403: not in your plan")
        rows = [_game(2, (CHELSEA, "Chelsea"), (ARSENAL, "Arsenal"), "NS", later), _game(1, (ARSENAL, "Arsenal"), (CHELSEA, "Chelsea"), "FT", before, (3, 0)),
                _game(3, (ARSENAL, "Arsenal"), (CHELSEA, "Chelsea"), "NS", soon)]
        return {"data": rows + rows[:1], "pagination": {"has_more": False}}  # one held twice: the list moved

    monkeypatch.setattr(sportmonks, "get_json", get_json)
    adapter = sm.SportmonksFootball()
    games = adapter.team_fixtures(str(ARSENAL))
    assert [g.source_id for g in games] == ["1", "3", "2"] and all(g.team_ids[1 - g.team_names.index("Arsenal")] == "18" for g in games)
    assert [(g.team_ids, g.team_names) for g in games][0] == (["19", "18"], ["Arsenal", "Chelsea"])
    assert adapter.result_label(games[0], [str(ARSENAL)]) == "Won 3–0" and adapter.result_label(games[0], [str(CHELSEA)]) == "Lost 0–3"
    path, ttl, params = asked[0]
    assert path.startswith("/football/fixtures/between/") and path.endswith("/19") and _days(path) == adapter.WINDOW_BACK + adapter.WINDOW_AHEAD
    assert ttl == 1800 and params["include"] == sm.LIST_INCLUDE
    # A player's games are their teams', read the same way: a team outside the plan has none for them, but a
    # team's own read of it fails, so its follow says so.
    ref = PlayerRef("sportmonks_football", "4125", "Bukayo Saka", [str(ARSENAL), str(ENGLAND)], ["Arsenal", "England"])
    assert [(f.source_id, f.team_ids) for f in adapter.fixtures(ref)] == [(g.source_id, g.team_ids) for g in games]
    with pytest.raises(AdapterError):
        adapter.team_fixtures(str(ENGLAND))


def test_a_football_teams_squad_is_who_plays_for_it_now(monkeypatch):
    asked = []
    team = {"id": ARSENAL, "name": "Arsenal", "short_code": "ARS", "country": {"name": "England"}, "players": [
        {"player_id": 1, "end": "2031-06-30", "player": {"id": 1, "display_name": "Bukayo Saka ", "date_of_birth": "2001-09-05"}},
        {"player_id": 2, "end": "2026-06-30", "player": {"id": 2, "display_name": "Gone In June"}},  # contract over
        {"player_id": 3, "end": None, "player": None},  # no player with the row: nobody to follow
        {"player_id": 4, "end": None, "player": {"id": 4, "common_name": "Rice"}},
        {"player_id": 1, "end": "2031-06-30", "player": {"id": 1, "display_name": "Bukayo Saka"}},  # held twice
    ]}
    monkeypatch.setattr(sportmonks, "get_json", lambda path, ttl, **params: asked.append((path, sportmonks._bucket(path), ttl, params)) or {"data": team})
    found = sm.SportmonksFootball().team(str(ARSENAL))
    assert found is not None and (found.team_id, found.name, found.short, found.country) == ("19", "Arsenal", "ARS", "England")
    assert [(p.athlete_id, p.name, p.team_ids, p.team_names, p.born) for p in found.players] == [
        ("1", "Bukayo Saka", ["19"], ["Arsenal"], "2001-09-05"), ("4", "Rice", ["19"], ["Arsenal"], None)]
    assert asked == [("/football/teams/19", "Team", 6 * 3600, {"include": "players.player;country"})]
    monkeypatch.setattr(sportmonks, "get_json", lambda path, ttl, **params: {"message": "No result(s) found"})
    assert sm.SportmonksFootball().team("999999") is None


# ---------------------------------------------------------------- cricket (Sportmonks)

INDIA = {"id": 10, "name": "India", "code": "IND", "national_team": True}
AFGHANISTAN = {"id": 46, "name": "Afghanistan", "code": "AFG", "national_team": True}
MUMBAI = {"id": 6, "name": "Mumbai Indians", "code": "MI", "national_team": False}
T20I = {"name": "Twenty20 International", "type": "phase"}
IPL = {"name": "Indian Premier League", "type": "league"}
OPENER, BENCHED = 3338, 9001


def _cmatch(match_id: int, status: str, start: datetime, home: dict, away: dict, league: dict = T20I, season: int = 1715, lineup: list[int] | None = None, **extra) -> dict:
    return {
        "id": match_id, "status": status, "type": "T20I", "round": "1st T20I", "season_id": season, "starting_at": start.isoformat().replace("+00:00", "Z"),
        "league": league, "stage": {"name": "Afghanistan tour of India"}, "localteam": home, "visitorteam": away,
        "localteam_id": home["id"], "visitorteam_id": away["id"], "runs": [], "lineup": [{"id": p} for p in lineup or []], **extra,
    }


def _cricket_source(monkeypatch, matches: list[dict], squads: dict[str, dict] | None = None) -> list[tuple[str, str, dict]]:
    asked: list[tuple[str, str, dict]] = []

    def get_json(path, ttl, **params):
        asked.append((path, sportmonks._bucket(path), params))
        if path == f"{sportmonks.CRICKET}/fixtures":
            side, team = next((k, v) for k, v in params.items() if k in ("filter[localteam_id]", "filter[visitorteam_id]"))
            key = "localteam_id" if "local" in side else "visitorteam_id"
            return {"data": [m for m in matches if str(m[key]) == str(team)]}
        if "/squad/" in path:
            return {"data": (squads or {}).get(path.split("/teams/")[1], {})}
        raise AssertionError(path)

    monkeypatch.setattr(sportmonks, "get_json", get_json)
    return asked


def test_cricket_lists_its_competitions_teams_nations_as_international(monkeypatch):
    asked = []
    seasons = {
        "1715": {"id": 1715, "teams": [INDIA, AFGHANISTAN]},
        "1689": {"id": 1689, "teams": [MUMBAI, {"id": 0, "name": "No Id"}, {"id": 7, "name": " "}]},
    }

    def get_json(path, ttl, **params):
        asked.append((path, sportmonks._bucket(path), ttl, params))
        if path.endswith("/leagues"):
            return {"data": [{"id": 3, "season_id": 1715, **T20I}, {"id": 1, "season_id": 1689, **IPL}, {"id": 9, "season_id": None, "name": "Old Cup"}]}
        return {"data": seasons[path.rsplit("/", 1)[1]]}

    monkeypatch.setattr(sportmonks, "get_json", get_json)
    teams = sc.SportmonksCricket().teams()
    assert [(t.team_id, t.name, t.short, t.competition) for t in teams] == [
        ("10", "India", "IND", "International"), ("46", "Afghanistan", "AFG", "International"), ("6", "Mumbai Indians", "MI", "Indian Premier League")]
    assert all(t.system == "sportmonks_cricket" and t.sport == "" and t.players == [] for t in teams)
    # The competitions (each naming its season on now), then each season with its teams: each counted on its own endpoint.
    assert [(bucket, ttl, params) for _, bucket, ttl, params in asked] == [
        ("Cricket leagues", 24 * 3600, {}), ("Cricket seasons/{id}", 24 * 3600, {"include": "teams"}), ("Cricket seasons/{id}", 24 * 3600, {"include": "teams"})]
    assert [path.rsplit("/", 1)[1] for path, _, _, _ in asked[1:]] == ["1715", "1689"]


def test_a_cricket_teams_games_are_every_match_where_a_players_keep_their_xi(monkeypatch):
    now = datetime.now(timezone.utc)
    played = _cmatch(1, "Finished", now - timedelta(days=10), INDIA, AFGHANISTAN, lineup=[OPENER], winner_team_id=10, note="India won by 57 runs")
    dropped = _cmatch(2, "Finished", now - timedelta(days=5), AFGHANISTAN, INDIA, lineup=[BENCHED])  # the player wasn't in the XI
    called_off = _cmatch(3, "Aban.", now - timedelta(days=3), INDIA, AFGHANISTAN)
    coming = _cmatch(4, "NS", now + timedelta(days=7), AFGHANISTAN, INDIA)  # no XI yet, nor a squad: squad not announced
    asked = _cricket_source(monkeypatch, [coming, dropped, called_off, played])
    adapter = sc.SportmonksCricket()
    games = adapter.team_fixtures("10")
    assert [(g.source_id, g.status) for g in games] == [("1", "final"), ("2", "final"), ("3", "postponed"), ("4", "scheduled")]
    assert games[0].team_ids == ["10", "46"] and games[1].team_ids == ["46", "10"] and games[1].team_names == ["Afghanistan", "India"]
    assert games[3].detail == "1st T20I"  # not a player's "squad not announced"
    # The result as the source says it, whichever side asks.
    assert adapter.result_label(games[0], ["10"]) == adapter.result_label(games[0], ["46"]) == "India won by 57 runs"
    windows = [params["filter[starts_between]"] for _, bucket, params in asked if bucket == "Cricket fixtures"]
    assert len(windows) == 2 and {_days(w) for w in windows} == {adapter.WINDOW_BACK + adapter.WINDOW_AHEAD}
    # A player of theirs keeps only the matches they're in: the same reads, with the XI filter.
    asked.clear()
    ref = PlayerRef("sportmonks_cricket", str(OPENER), "Opener", ["10"], ["India"])
    assert [f.source_id for f in adapter.fixtures(ref)] == ["1", "3", "4"]
    assert [bucket for _, bucket, _ in asked] == ["Cricket fixtures", "Cricket fixtures", "Cricket teams/{id}/squad/{id}"]


def test_a_cricket_teams_squad_is_its_next_or_last_seasons(monkeypatch):
    now = datetime.now(timezone.utc)
    squad = {"id": 6, "name": "Mumbai Indians", "code": "MI", "national_team": False, "squad": [
        {"id": 3338, "fullname": "Rohit Sharma", "dateofbirth": "1987-04-30", "squad": {"season_id": 1600}},
        {"id": 3339, "fullname": "Jasprit Bumrah", "dateofbirth": "1993-01-01", "squad": {"season_id": 1600}},  # Sportmonks' "don't know"
        {"id": 3340, "fullname": ""},
    ]}
    # A franchise between seasons: its last match, months ago, says whose squad it is.
    last_ipl = _cmatch(11, "Finished", now - timedelta(days=140), MUMBAI, {"id": 5, "name": "Chennai Super Kings", "national_team": False}, league=IPL, season=1600)
    asked = _cricket_source(monkeypatch, [last_ipl], {"6/squad/1600": squad})
    found = sc.SportmonksCricket().team("6")
    assert found is not None and (found.team_id, found.name, found.short, found.competition) == ("6", "Mumbai Indians", "MI", "Indian Premier League")
    assert [(p.athlete_id, p.name, p.team_ids, p.team_names, p.born) for p in found.players] == [
        ("3338", "Rohit Sharma", ["6"], ["Mumbai Indians"], "1987-04-30"), ("3339", "Jasprit Bumrah", ["6"], ["Mumbai Indians"], None)]
    windows = [params["filter[starts_between]"] for _, bucket, params in asked if bucket == "Cricket fixtures"]
    assert {_days(w) for w in windows} == {sc.SQUAD_BACK + sc.SportmonksCricket.WINDOW_AHEAD}
    assert asked[-1][0] == f"{sportmonks.CRICKET}/teams/6/squad/1600"


def test_a_cricket_teams_season_is_its_next_match_s_else_its_last(monkeypatch):
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    last = _cmatch(1, "Finished", now - timedelta(days=30), INDIA, AFGHANISTAN, season=1700)
    later = _cmatch(2, "NS", now + timedelta(days=40), INDIA, AFGHANISTAN, season=1716)
    nxt = _cmatch(3, "NS", now + timedelta(days=4), INDIA, AFGHANISTAN, season=1715)
    stale = _cmatch(4, "NS", now - timedelta(days=20), INDIA, AFGHANISTAN, season=1600)  # never played, still "to come"
    unnamed = _cmatch(5, "Finished", now - timedelta(days=1), INDIA, AFGHANISTAN, season=0)
    assert (sc.squad_match([last, later, nxt, stale, unnamed], now) or {}).get("id") == 3
    assert (sc.squad_match([last, stale, unnamed], now) or {}).get("id") == 1
    assert sc.squad_match([stale, unnamed], now) is None


def test_a_cricket_team_with_no_recent_match_or_squad_is_read_without_one(monkeypatch):
    nepal = {"id": 97, "name": "Nepal", "code": "NEP", "national_team": True}
    played = _cmatch(21, "Finished", datetime.now(timezone.utc) - timedelta(days=9), nepal, AFGHANISTAN, season=1715)
    teams = {"99": {"id": 99, "name": "Old Side", "code": "OS", "national_team": False}, "97": nepal}

    def get_json(path, ttl, **params):
        if path.endswith("/fixtures"):
            return {"data": [played] if params.get("filter[localteam_id]") == "97" else []}
        if "/squad/" in path:
            return {"data": None}  # no squad for that season
        return {"data": teams.get(path.rsplit("/", 1)[1])}

    monkeypatch.setattr(sportmonks, "get_json", get_json)
    adapter = sc.SportmonksCricket()
    old, nation = adapter.team("99"), adapter.team("97")
    assert old is not None and (old.name, old.short, old.players) == ("Old Side", "OS", [])
    assert nation is not None and (nation.name, nation.competition, nation.players) == ("Nepal", "International", [])
    assert adapter.team("98") is None


# ---------------------------------------------------------------- basketball (ESPN)

FEVER = "5"


def _league(*teams: dict) -> dict:
    return {"sports": [{"id": "40", "leagues": [{"id": "46", "teams": [{"team": t} for t in teams]}]}]}


def test_basketball_lists_the_nba_and_wnba_teams_by_ids_that_carry_their_league(monkeypatch):
    asked = []
    lists = {
        "nba": _league({"id": "5", "abbreviation": "CLE", "displayName": "Cleveland Cavaliers", "isActive": True, "isAllStar": False},
                       {"id": "31", "abbreviation": "LBJ", "displayName": "Team LeBron", "isAllStar": True}),
        "wnba": _league({"id": "5", "abbreviation": "IND", "displayName": "Indiana Fever", "isActive": True},
                        {"id": "99", "displayName": "Old Side", "isActive": False}),
    }

    def get_json(url, ttl):
        asked.append((url, ttl))
        return lists[url.split("/basketball/")[1].split("/")[0]]

    monkeypatch.setattr(espn, "get_json", get_json)
    teams = eb.EspnBasketball().teams()
    # ESPN numbers each league's teams apart: the Cavaliers and the Fever are both 5.
    assert [(t.team_id, t.name, t.short, t.competition) for t in teams] == [("nba:5", "Cleveland Cavaliers", "CLE", "NBA"), ("wnba:5", "Indiana Fever", "IND", "WNBA")]
    assert asked == [(f"{espn.SITE}/basketball/nba/teams", 24 * 3600), (f"{espn.SITE}/basketball/wnba/teams", 24 * 3600)]
    pattern = eb.EspnBasketball.team_id_pattern
    assert all(pattern.fullmatch(t.team_id) for t in teams)
    assert not any(pattern.fullmatch(x) for x in ("5", "nfl:5", "mens-college-basketball:5", "nba:5/roster", "nba:"))


def test_a_basketball_teams_games_name_it_as_teams_are_named_and_say_its_results(monkeypatch):
    schedule = load("espn/basketball-schedule")  # the Fever's, recorded
    asked = []
    monkeypatch.setattr(espn, "get_json", lambda url, ttl: asked.append((url, ttl)) or schedule)
    adapter = eb.EspnBasketball()
    games = adapter.team_fixtures("wnba:5")
    assert [url for url, _ in asked] == [f"{espn.SITE}/basketball/wnba/teams/5/schedule{s}" for s in ("", "?seasontype=2", "?seasontype=3")]
    assert games and all("wnba:5" in g.team_ids and len(g.team_ids) == len(g.team_names) == 2 for g in games)  # which side is the team
    game1 = next(g for g in games if g.source_id == "401918015")
    assert game1.team_names == ["Indiana Fever", "Las Vegas Aces"] and game1.locator == {"league": "wnba", "event": "401918015"}
    assert adapter.result_label(game1, ["wnba:5"]) == adapter.result_label(game1, [FEVER]) == "Lost 85–102"
    assert adapter.result_label(game1, [game1.team_ids[1]]) == "Won 102–85"
    # A player's are the same games, by ESPN's own numbers, as before.
    ref = PlayerRef("espn_basketball", "4433403", "Caitlin Clark", [FEVER], ["Indiana Fever"], league="wnba")
    theirs = adapter.fixtures(ref)
    assert [f.source_id for f in theirs] == [g.source_id for g in games]
    assert [f.team_ids for f in theirs] == [[t.removeprefix("wnba:") for t in g.team_ids] for g in games]
    starts = [f.start_utc for f in theirs if f.start_utc]
    assert starts == sorted(starts) and len({f.source_id for f in theirs}) == len(theirs)
    with pytest.raises(AdapterError):
        adapter.team_fixtures("5")  # no league: not one of its team ids, and nothing is asked
    assert len(asked) == 6
    # Its boards keep ESPN's numbers: a result is told from the team's side by the number its id carries.
    assert adapter.board_id("wnba:5") == "5"
    board = {"kind": "basketball", "home": {"id": "5", "name": "Indiana Fever", "score": "85", "winner": False}, "away": {"id": "17", "name": "Las Vegas Aces", "score": "102", "winner": True}}
    assert sports.of("basketball").side_final(board, "", "Final", adapter.board_id("wnba:5")) == "Indiana Fever lost 85–102 v Las Vegas Aces"


def test_a_basketball_teams_games_fail_only_when_no_phase_can_be_read(monkeypatch):
    def get_json(url, ttl):
        raise AdapterError("ESPN returned HTTP 502")

    monkeypatch.setattr(espn, "get_json", get_json)
    adapter = eb.EspnBasketball()
    with pytest.raises(AdapterError):
        adapter.team_fixtures("nba:13")
    assert adapter.fixtures(PlayerRef("espn_basketball", "1966", "LeBron James", ["13"], ["Los Angeles Lakers"], league="nba")) == []


def test_a_basketball_teams_squad_is_its_roster(monkeypatch):
    roster = {"team": {"id": "13", "abbreviation": "LAL", "displayName": "Los Angeles Lakers"}, "athletes": [
        {"id": "1966", "displayName": "LeBron James", "dateOfBirth": "1984-12-30T08:00Z", "links": [{"href": "https://www.espn.com/nba/player/_/id/1966/lebron-james"}]},
        {"id": "3945274", "fullName": "Luka Doncic", "displayDOB": "28/2/1999"},
        {"id": "", "displayName": "No Id"},
    ]}
    asked = []
    monkeypatch.setattr(espn, "get_json", lambda url, ttl: asked.append((url, ttl)) or roster)
    found = eb.EspnBasketball().team("nba:13")
    assert found is not None and (found.team_id, found.name, found.short, found.competition) == ("nba:13", "Los Angeles Lakers", "LAL", "NBA")
    # Each followable as a search result is: ESPN's own number for their team, and their league.
    assert [(p.athlete_id, p.name, p.team_ids, p.team_names, p.league, p.born) for p in found.players] == [
        ("1966", "LeBron James", ["13"], ["Los Angeles Lakers"], "nba", "1984-12-30"), ("3945274", "Luka Doncic", ["13"], ["Los Angeles Lakers"], "nba", "1999-02-28")]
    assert found.players[0].profile_url == "https://www.espn.com/nba/player/_/id/1966/lebron-james"
    assert asked == [(f"{espn.SITE}/basketball/nba/teams/13/roster", 6 * 3600)]
    assert eb.EspnBasketball().team("13") is None and len(asked) == 1  # not one of its ids: nothing asked
    assert eb.parse_roster({"athletes": []}, "nba") is None


# ---------------------------------------------------------------- esports (PandaScore)

SEN, NRG, T1 = 1234, 5678, 126061


def _club(team_id: int, name: str, acronym: str, location: str) -> dict:
    return {"id": team_id, "name": name, "acronym": acronym, "location": location, "slug": name.lower()}


def _tournament(tournament_id: int, tier: str, slug: str, *teams: dict) -> dict:
    return {"id": tournament_id, "tier": tier, "videogame": {"slug": slug}, "teams": list(teams)}


TOURNAMENTS = {
    "/tournaments/running": [_tournament(3, "a", "valorant", _club(SEN, "Sentinels", "SEN", "US"), _club(NRG, "NRG ", "NRG", "US"))],
    "/tournaments/upcoming": [_tournament(9, "s", "valorant", _club(SEN, "Sentinels", "SEN", "US")), _tournament(10, "b", "league-of-legends", _club(T1, "T1", "T1", "KR"))],
    "/tournaments/past": [_tournament(11, "b", "starcraft-2", _club(5, "Solo", "", "FR")), _tournament(12, "b", "cs-go", {"id": 7, "name": ""})],
}


def test_esports_lists_the_teams_in_its_titles_rated_events_with_their_best_tier(monkeypatch):
    asked = []

    def get_json(path, ttl, **params):
        asked.append((path, ttl, params))
        return TOURNAMENTS[path]

    monkeypatch.setattr(pandascore, "get_json", get_json)
    adapter = ps.PandascoreEsports()
    teams = adapter.teams()
    assert [(t.team_id, t.name, t.sport, t.game, t.short, t.competition, t.country, t.tier) for t in teams] == [
        ("1234", "Sentinels", "valorant", "Valorant", "SEN", "Valorant", "United States", 3),  # an A league and an S event: S
        ("5678", "NRG", "valorant", "Valorant", "NRG", "Valorant", "United States", 2),
        ("126061", "T1", "lol", "League of Legends", "T1", "League of Legends", "South Korea", 1),
    ]  # a 1v1 title's (StarCraft) and a team without a name aren't
    assert [path for path, _, _ in asked] == ["/tournaments/running", "/tournaments/upcoming", "/tournaments/past"]
    by_path = {path: (ttl, params) for path, ttl, params in asked}
    assert all(ttl == 24 * 3600 and params["filter[tier]"] == "s,a,b" and params["page[size]"] == 100 for ttl, params in by_path.values())
    assert _days(by_path["/tournaments/upcoming"][1]["range[begin_at]"]) == adapter.WINDOW_AHEAD
    assert _days(by_path["/tournaments/past"][1]["range[end_at]"]) == adapter.WINDOW_BACK
    assert "range[begin_at]" not in by_path["/tournaments/running"][1] and "range[end_at]" not in by_path["/tournaments/running"][1]


def test_esports_lists_only_the_titles_that_run(monkeypatch):
    asked = []
    monkeypatch.setattr(pandascore, "get_json", lambda path, ttl, **params: asked.append(params) or TOURNAMENTS[path])
    adapter = ps.PandascoreEsports()
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key != "valorant"))
    assert [t.name for t in adapter.teams()] == ["T1"]  # a Valorant event the source sent anyway is left out
    assert all("26" not in params["filter[videogame_id]"].split(",") for params in asked)
    asked.clear()
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key not in adapter.sports))
    assert adapter.teams() == [] and asked == []  # not one request


def _series(match_id: int, scheduled: str, status: str, sen: int = 0, nrg: int = 0, winner: int | None = None) -> dict:
    return {
        "id": match_id, "status": status, "scheduled_at": scheduled, "begin_at": None if status == "not_started" else scheduled,
        "number_of_games": 3, "match_type": "best_of", "forfeit": False, "draw": False, "winner_id": winner, "winner_type": "Team",
        "opponents": [{"type": "Team", "opponent": _club(SEN, "Sentinels", "SEN", "US")}, {"type": "Team", "opponent": _club(NRG, "NRG", "NRG", "US")}],
        "results": [{"team_id": SEN, "score": sen}, {"team_id": NRG, "score": nrg}],
        "league": {"name": "VCT Americas"}, "tournament": {"id": 3, "tier": "a"}, "videogame": {"slug": "valorant"},
    }


def test_an_esports_teams_games_are_its_series_over_the_window(monkeypatch):
    asked = []
    rows = [_series(778, "2026-10-20T19:00:00Z", "not_started"), _series(777, "2026-10-11T19:00:00Z", "finished", 2, 1, SEN)]
    monkeypatch.setattr(pandascore, "get_json", lambda path, ttl, **params: asked.append((path, ttl, params)) or rows)
    adapter = ps.PandascoreEsports()
    games = adapter.team_fixtures(str(SEN))
    assert [(g.source_id, g.status, g.team_ids, g.sport) for g in games] == [("777", "final", ["1234", "5678"], "valorant"), ("778", "scheduled", ["1234", "5678"], "valorant")]
    assert adapter.result_label(games[0], [str(SEN)]) == "Won 2–1" and adapter.result_label(games[0], [str(NRG)]) == "Lost 1–2"
    path, ttl, params = asked[0]
    assert (path, ttl, params["filter[opponent_id]"]) == ("/matches", 1800, "1234") and _days(params["range[scheduled_at]"]) == adapter.WINDOW_BACK + adapter.WINDOW_AHEAD
    ref = PlayerRef("pandascore_esports", "31001", "TenZ", [str(SEN)], ["Sentinels"], league="valorant")
    assert [(f.source_id, f.team_ids) for f in adapter.fixtures(ref)] == [(g.source_id, g.team_ids) for g in games]


def test_an_esports_team_read_by_itself_is_of_its_own_game():
    # An operator's team (teams add) is of its game, not the source's first (Valorant).
    team = ps.parse_team({"id": 3216, "name": "Natus Vincere", "acronym": "NAVI", "location": "UA", "current_videogame": {"slug": "cs-go"}, "players": []})
    assert team is not None and (team.sport, team.game, team.short, team.competition, team.country) == ("cs2", "CS2", "NAVI", "CS2", "Ukraine")
