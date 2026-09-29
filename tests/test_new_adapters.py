"""Basketball, tennis and chess parsers against real responses recorded on 2026-09-29.

ESPN responses are in tests/fixtures/espn, Lichess ones in tests/fixtures/lichess; the tennis
scoreboards are trimmed to the matches the tests read.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from sports_follow.adapters import espn_basketball, espn_tennis, lichess_chess

FIXTURES = Path(__file__).parent / "fixtures"
CLARK, FEVER = "4433403", "5"
ALCARAZ, SABALENKA = "3782", "3038"
CARLSEN, LAZAVIK = "1503014", "13515110"


def load(name: str) -> dict:
    with gzip.open(FIXTURES / f"{name}.json.gz", "rt") as f:
        return json.load(f)


# ---------------------------------------------------------------- basketball


def test_basketball_schedule_titles_phases_and_results():
    fixtures = espn_basketball.parse_schedule(load("espn/basketball-schedule"), "wnba")
    game1 = next(f for f in fixtures if f.source_id == "401918015")
    assert game1.title == "Indiana Fever at Las Vegas Aces"
    assert game1.competition == "WNBA Postseason"
    assert game1.detail == "First Round - Game 1"
    assert game1.locator == {"league": "wnba", "event": "401918015"}
    assert espn_basketball.result_label(game1, [FEVER]) == "Lost 85–102"
    game2 = next(f for f in fixtures if f.source_id == "401918018")
    assert game2.status == "scheduled"
    assert game2.team_names == ["Las Vegas Aces", "Indiana Fever"]


def test_basketball_box_score_line_and_dnp():
    snap = espn_basketball.parse_summary(load("espn/basketball-summary-final"))
    assert snap.status == "final"
    assert snap.score_label == "Indiana Fever 85 – 102 Las Vegas Aces"
    assert snap.state["away"]["periods"] == ["23", "24", "18", "20"]
    line = snap.lines[CLARK]
    assert line.headline == "15 PTS · 3 REB · 10 AST"
    assert {s["label"]: s["value"] for s in line.stats}["FG"] == "6-17"
    assert any(l.headline == "Did not play (coach's decision)" for l in snap.lines.values())


def test_basketball_season_averages():
    stats, note = espn_basketball.parse_season_stats(load("espn/basketball-athlete-stats"))
    assert {s["label"]: s["value"] for s in stats}["Points"] == "20.9"
    assert note == "2025-26 regular season averages per game"


# ---------------------------------------------------------------- tennis


def test_tennis_scoreboard_gives_a_players_matches_with_set_scores():
    fixtures = espn_tennis.parse_scoreboard(load("espn/tennis-scoreboard-usopen"), "atp", "20260903")
    assert all(not f.locator["match"].count("-") for f in fixtures)  # singles only
    qf = next(f for f in fixtures if ALCARAZ in f.team_ids and f.detail == "Quarterfinal")
    assert qf.title == "Ben Shelton vs Carlos Alcaraz"
    assert qf.competition == "US Open"
    # From Alcaraz's side, loser's tiebreak points in brackets.
    assert espn_tennis.result_label(qf, [ALCARAZ]) == "Lost 7-6(5) 1-6 3-6 6-1 6-7(7)"
    final = next(f for f in fixtures if SABALENKA in f.team_ids and f.detail == "Final")
    assert espn_tennis.result_label(final, [SABALENKA]) == "Lost 4-6 7-5 2-6"


def test_tennis_match_snapshot_final_and_live():
    data = load("espn/tennis-scoreboard-usopen")
    qf = next(f for f in espn_tennis.parse_scoreboard(data, "atp", "20260903") if ALCARAZ in f.team_ids and f.detail == "Quarterfinal")
    snap = espn_tennis.parse_match(*espn_tennis.find_match(data, qf.source_id))
    assert snap.status == "final"
    assert snap.lines[ALCARAZ].headline == "Lost in 5 sets"
    assert snap.clock_label.startswith("(8) Ben Shelton (USA) bt (2) Carlos Alcaraz")

    live = load("espn/tennis-scoreboard-live")
    event = live["events"][0]
    singles = next(g for g in event["groupings"] if g["grouping"]["slug"] == "womens-singles")["competitions"][0]
    snap = espn_tennis.parse_match(event, singles)
    assert snap.status == "live"
    assert snap.score_label == "K. Efremova 3-6 6-4 3-4 M. Kubka"
    assert snap.clock_label == "3rd Set"
    assert all(l.headline == "Level in sets" for l in snap.lines.values())
    doubles = next(g for g in event["groupings"] if g["grouping"]["slug"] == "womens-doubles")["competitions"][0]
    assert "TBD" not in espn_tennis.parse_match(event, doubles).score_label


def test_tennis_ranking_row():
    row, updated = espn_tennis.parse_ranking(load("espn/tennis-rankings"), "3623")
    assert row["current"] == 1 and row["points"] == 11500.0
    assert updated.startswith("2026-09-17")


# ---------------------------------------------------------------- chess


def test_fide_names_resolve_with_initials_and_order():
    assert lichess_chess.pick_player(load("lichess/fide-search-carlsen"), "Carlsen")["id"] == int(CARLSEN)
    # FIDE lists Gukesh as "Gukesh D", and the search also returns Chaithanya Ramkumar Dommaraju:
    # every part of the name must match, the initial "D" counting for "Dommaraju".
    dommaraju = load("lichess/fide-search-dommaraju")
    assert any(r["name"] != "Gukesh D" for r in dommaraju)
    assert lichess_chess.pick_player(dommaraju, "Gukesh Dommaraju")["name"] == "Gukesh D"
    assert lichess_chess.pick_player(load("lichess/fide-search-gukesh"), "Gukesh Dommaraju")["name"] == "Gukesh D"
    assert lichess_chess.name_score("R Praggnanandhaa", "Praggnanandhaa, R") == 2
    assert lichess_chess.display_name("Carlsen, Magnus") == "Magnus Carlsen"


def test_chess_knockout_round_is_a_match():
    # The Esports World Cup grand final is one broadcast round holding eight Carlsen–Lazavik games.
    data = load("lichess/round-final")
    assert len(lichess_chess.games_of(data, CARLSEN)) == 8
    snap = lichess_chess.parse_board(data, CARLSEN)
    assert snap.status == "final"
    assert snap.score_label == "Magnus Carlsen 6–2 Denis Lazavik"
    assert snap.clock_label == "Magnus Carlsen won the match (8 games)"
    assert snap.state["match"] == {"games": 8, "score": "6–2"}
    assert snap.state["fen"]  # the board shown is the last game's
    assert snap.lines[CARLSEN].headline == "Won the match 6–2 vs Denis Lazavik"
    assert snap.lines[LAZAVIK].headline == "Lost the match 2–6 vs Magnus Carlsen"


def test_chess_fixture_and_result_label_from_each_side():
    from datetime import datetime, timezone

    data = load("lichess/round-final")
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    mine = lichess_chess.fixture_for(data["tour"], data["round"], lichess_chess.games_of(data, CARLSEN), CARLSEN, now)
    assert mine.source_id == f"{data['round']['id']}-{CARLSEN}"
    assert mine.title == "Magnus Carlsen vs Denis Lazavik"
    assert mine.status == "final"
    assert lichess_chess.result_label(mine, [CARLSEN]) == "Won 6–2"
    theirs = lichess_chess.fixture_for(data["tour"], data["round"], lichess_chess.games_of(data, LAZAVIK), LAZAVIK, now)
    assert lichess_chess.result_label(theirs, [LAZAVIK]) == "Lost 2–6"
    assert lichess_chess.fmt_points(2.5) == "2½" and lichess_chess.fmt_points(0.5) == "½" and lichess_chess.fmt_points(0) == "0"


def test_chess_single_game_round():
    data = load("lichess/round-final")
    first = data["games"][0]  # Carlsen–Lazavik 1-0, as if it were the round's only game
    single = {**data, "games": [first]}
    snap = lichess_chess.parse_board(single, CARLSEN)
    assert snap.score_label == "Magnus Carlsen 1–0 Denis Lazavik"
    assert snap.clock_label == "Magnus Carlsen won"
    assert snap.lines[CARLSEN].headline == "Won with White vs Denis Lazavik"
    assert snap.state["match"] is None


def test_chess_rate_limit_fails_the_whole_read(monkeypatch):
    """A 429 mid-read must not return a fixture list with holes (the card would lose results)."""
    import pytest
    from sports_follow.adapters.base import PlayerRef

    calls = {"n": 0}

    def fake_get_json(url, ttl, text=False):
        calls["n"] += 1
        if url.endswith("/broadcast/top"):
            raise lichess_chess.RateLimited("Lichess rate limit (429)")
        if "search" in url:
            return {"currentPageResults": []}
        return ""

    monkeypatch.setattr(lichess_chess, "get_json", fake_get_json)
    ref = PlayerRef("lichess_chess", CARLSEN, "Magnus Carlsen", [CARLSEN], ["Magnus Carlsen"], "https://lichess.org/fide/1503014/Carlsen_Magnus")
    with pytest.raises(lichess_chess.RateLimited):
        lichess_chess.LichessChess().fixtures(ref)


def test_tournament_pgn_gives_every_round_in_one_read():
    with gzip.open(FIXTURES / "lichess/tour-ewc-playoffs.pgn.gz", "rt") as f:
        rounds = lichess_chess.parse_pgn_rounds(f.read())
    assert len(rounds) == 6
    final = lichess_chess.games_of({"games": rounds["ohv5F74c"]}, CARLSEN)
    assert len(final) == 8
    assert lichess_chess.match_score(final, CARLSEN) == (6.0, 8)  # same as the round JSON says
    assert final[0]["players"][0] == {"name": "Carlsen, Magnus", "fideId": 1503014, "rating": 2823, "title": "GM"}
