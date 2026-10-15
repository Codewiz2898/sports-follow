"""Basketball, tennis and chess parsers against real responses recorded on 2026-09-29.

ESPN responses are in tests/fixtures/espn, Lichess ones in tests/fixtures/lichess; the tennis
scoreboards are trimmed to the matches the tests read.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from sports_follow.adapters import budget, espn_basketball, espn_tennis, lichess_chess
from sports_follow.adapters.base import AdapterError, PlayerRef

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


DE_MINAUR, MOLCAN, MANNARINO, BASILASHVILI = "2651", "2635", "1266", "2498"
# ESPN's status type for a cancelled match (a Lisbon doubles match, 13 Oct 2026): "post", not completed.
CANCELED = {"id": "5", "name": "STATUS_CANCELED", "state": "post", "completed": False, "description": "Canceled", "detail": "Canceled", "shortDetail": "Canceled"}


def test_tennis_walkovers_and_retirements_say_so():
    # Shanghai, recorded 2026-10-13: de Minaur went through when Molcan withdrew (no sets), Mannarino when
    # Basilashvili retired at 6-3 2-2. They read "Won " with nothing after it and "Won in 1 sets".
    data = load("espn/tennis-scoreboard-walkover")
    fixtures = {f.source_id: f for f in espn_tennis.parse_scoreboard(data, "atp", "20261013")}
    walkover, retired = fixtures["184927"], fixtures["184881"]
    assert (walkover.status, walkover.result["ending"], retired.result["ending"]) == ("final", "walkover", "retired")
    assert [espn_tennis.result_label(walkover, [p]) for p in (DE_MINAUR, MOLCAN)] == ["Won (w/o)", "Lost (w/o)"]
    assert [espn_tennis.result_label(retired, [p]) for p in (MANNARINO, BASILASHVILI)] == ["Won 6-3 2-2 ret.", "Lost 3-6 2-2 ret."]
    assert espn_tennis.result_label(fixtures["184929"], [DE_MINAUR]) == "Won 7-6(6) 6-4"  # played out: as before

    snap = espn_tennis.parse_match(*espn_tennis.find_match(data, "184927"))
    assert (snap.status, snap.state["ending"]) == ("final", "walkover")
    assert (snap.lines[DE_MINAUR].headline, snap.lines[MOLCAN].headline) == ("Won by walkover", "Withdrew")
    assert snap.clock_label == "(9) Alex de Minaur (AUS) bt Alex Molcan (SVK) w/o"
    snap = espn_tennis.parse_match(*espn_tennis.find_match(data, "184881"))
    assert snap.state["ending"] == "retired" and snap.score_label == "A. Mannarino 6-3 2-2 N. Basilashvili"
    assert (snap.lines[MANNARINO].headline, snap.lines[BASILASHVILI].headline) == ("Won, opponent retired", "Retired")
    snap = espn_tennis.parse_match(*espn_tennis.find_match(data, "184950"))
    assert (snap.state["ending"], snap.lines[DE_MINAUR].headline) == (None, "Won in 2 sets")
    # One set, should a match be one: no "1 sets".
    event, comp = espn_tennis.find_match(data, "186292")
    comp["status"]["type"].update(name="STATUS_FINAL", description="Final")
    comp["competitors"][0]["linescores"] = comp["competitors"][0]["linescores"][:1]
    comp["competitors"][1]["linescores"] = comp["competitors"][1]["linescores"][:1]
    assert {l.headline for l in espn_tennis.parse_match(event, comp).lines.values()} == {"Won in 1 set", "Lost in 1 set"}


def test_a_cancelled_tennis_match_is_no_result():
    data = load("espn/tennis-scoreboard-walkover")
    event, comp = espn_tennis.find_match(data, "184954")  # de Minaur's quarterfinal, not yet played
    comp["status"]["type"] = CANCELED
    [qf] = [f for f in espn_tennis.parse_scoreboard(data, "atp", "20261013") if f.source_id == "184954"]
    assert (qf.status, qf.result) == ("postponed", None)
    snap = espn_tennis.parse_match(event, comp)
    assert (snap.status, snap.clock_label) == ("postponed", "Canceled")
    # Any match ESPN has over but not completed is no result either (an abandoned one, say).
    comp["status"]["type"] = {**CANCELED, "name": "STATUS_ABANDONED", "description": "Abandoned"}
    assert espn_tennis.parse_match(event, comp).status == "postponed"


def test_tennis_win_loss_leaves_walkovers_out(monkeypatch):
    # The tours count a walkover in no one's win–loss: de Minaur is 2–0, not 3–0, and Molcan 1–0, not
    # 1–1. A retirement counts (Mannarino's win).
    data = load("espn/tennis-scoreboard-walkover")
    monkeypatch.setattr(espn_tennis.espn, "get_json", lambda url, ttl: data if "/scoreboard" in url else {"rankings": [{"ranks": []}]})
    adapter = espn_tennis.EspnTennis()

    def win_loss(athlete_id: str) -> str:
        ref = PlayerRef("espn_tennis", athlete_id, "", [athlete_id], [""], league="atp")
        return {s["label"]: s["value"] for s in adapter.stats(ref, [])[0]}["Win–loss"]

    assert (win_loss(DE_MINAUR), win_loss(MOLCAN), win_loss(MANNARINO)) == ("2–0", "1–0", "1–0")


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


def test_every_lichess_request_goes_to_an_address_lichess_has(monkeypatch):
    # 15 Oct 2026: API was set twice, the second time ending in "/", so every request went to
    # https://lichess.org/api//… and Lichess answered 404: chess had no fixtures, games, ratings or search.
    from sports_follow.adapters.base import PlayerRef

    urls = []

    def fake_get_json(url, ttl, text=False):
        urls.append(url)
        return "" if text else {"currentPageResults": [], "rounds": [], "tour": {}}

    monkeypatch.setattr(lichess_chess, "get_json", fake_get_json)
    chess = lichess_chess.LichessChess()
    ref = PlayerRef("lichess_chess", CARLSEN, "Magnus Carlsen", [CARLSEN], ["Magnus Carlsen"], "https://lichess.org/fide/1503014/Carlsen_Magnus")
    for read in (lambda: chess.fixtures(ref), lambda: chess.find_player("Magnus Carlsen"), lambda: chess.snapshot({"round": "abc", "game": "x"})):
        try:
            read()
        except Exception:  # noqa: BLE001 — empty answers may not parse; only the addresses matter here
            pass
    assert urls
    assert all(url.startswith("https://lichess.org/") and "//" not in url.split("://", 1)[1] for url in urls), urls
    assert any(url.startswith("https://lichess.org/api/fide/player") for url in urls)
    # The addresses the reads above don't reach (a tournament's rounds, its PGN, a player's stats): API ends in "/".
    assert lichess_chess.API.endswith("/") and 'f"{API}/' not in Path(lichess_chess.__file__).read_text()


CARLSEN_PAGE = "https://lichess.org/fide/1503014/Carlsen_Magnus"


def lichess_answering(monkeypatch, answers: dict[str, httpx.Response]) -> None:
    """Lichess as a fake: each URL's answer, an empty one for the rest; nothing cached, no spacing."""
    monkeypatch.setattr(lichess_chess, "_client", httpx.Client(transport=httpx.MockTransport(lambda request: answers.get(str(request.url)) or httpx.Response(200, json={"games": []}))))
    monkeypatch.setattr(lichess_chess, "_cache", {})
    monkeypatch.setattr(lichess_chess, "SPACING", 0.0)


def test_a_429_on_lichess_fide_pages_pauses_only_them(monkeypatch):
    # 15 Oct 2026: Lichess's FIDE player pages and endpoint ignore the token, and one limit holds every
    # caller from a datacenter. Its 429s paused the broadcasts live boards read as well, and chess went blank.
    lichess_answering(monkeypatch, {CARLSEN_PAGE: httpx.Response(429, text="Too many requests. Try again later.")})
    with pytest.raises(lichess_chess.RateLimited, match=r"\(429\) on /fide/1503014/Carlsen_Magnus; lichess-fide paused for 60 s: Too many requests"):
        lichess_chess.get_json(CARLSEN_PAGE, ttl=0, text=True)
    with pytest.raises(lichess_chess.RateLimited, match="lichess-fide asked us to slow down"):
        lichess_chess.get_json("https://lichess.org/api/fide/player/1503014", ttl=0)  # the same limit
    assert lichess_chess.get_json("https://lichess.org/api/broadcast/-/-/abcdefgh", ttl=0) == {"games": []}  # live boards still read
    assert lichess_chess.get_json("https://lichess.org/api/fide/player?q=Carlsen", ttl=0) == {"games": []}  # FIDE search has no such limit


def test_a_429_on_lichess_broadcasts_pauses_them_for_its_three_minute_window(monkeypatch):
    said = "Use an oauth token to get 120 requests per minute. Contact us for more."
    lichess_answering(monkeypatch, {"https://lichess.org/api/broadcast/top": httpx.Response(429, json={"error": said})})
    with pytest.raises(lichess_chess.RateLimited, match=r"on /api/broadcast/top; lichess paused for 180 s: .*oauth token"):
        lichess_chess.get_json("https://lichess.org/api/broadcast/top", ttl=0)
    with pytest.raises(lichess_chess.RateLimited, match="lichess asked us to slow down"):
        lichess_chess.get_json("https://lichess.org/api/broadcast/-/-/abcdefgh", ttl=0)
    paused_until = budget.STORE.get("budget:lichess:paused")
    assert paused_until and 170 < paused_until - time.time() <= 180


def test_lichess_reads_fide_records_through_search_by_id(monkeypatch):
    # FIDE search answers an id with that one player and isn't behind the limit datacenters share.
    urls = []

    def fake_get_json(url, ttl, text=False):
        urls.append(url)
        if url == "https://lichess.org/api/fide/player?q=46616543":
            return load("lichess/fide-search-id-gukesh")
        if url.startswith("https://lichess.org/api/fide/player?q="):
            return []
        raise AdapterError(f"Lichess returned HTTP 404 for {url}")

    monkeypatch.setattr(lichess_chess, "get_json", fake_get_json)
    chess = lichess_chess.LichessChess()
    gukesh = chess.player("46616543")
    assert gukesh and gukesh.name == "Gukesh D"
    stats, _ = chess.stats(gukesh, [])
    assert {"label": "Classical", "value": "2710"} in stats and {"label": "Title", "value": "GM"} in stats
    assert not any("/fide/player/" in url for url in urls)
    # Were search to stop answering ids, the player endpoint is asked, and its 404 still means no such player.
    with pytest.raises(AdapterError, match="HTTP 404"):
        chess.player("1")
    assert urls[-2:] == ["https://lichess.org/api/fide/player?q=1", "https://lichess.org/api/fide/player/1"]


def test_a_refused_fide_page_gives_what_it_last_listed_and_search_still_finds_events(monkeypatch):
    page = '<a href="/broadcast/tata-steel-2026/round-1/abcdefgh">Tata Steel 2026</a>'
    answers = {CARLSEN_PAGE: httpx.Response(200, text=page)}
    lichess_answering(monkeypatch, answers)
    kept: dict[str, tuple[str, int | None]] = {}
    redis = SimpleNamespace(get=lambda key: kept[key][0] if key in kept else None, set=lambda key, value, ex=None: kept.__setitem__(key, (value, ex)))
    monkeypatch.setattr(lichess_chess.bus, "sync_redis", lambda: redis)
    chess = lichess_chess.LichessChess()
    ref = PlayerRef("lichess_chess", CARLSEN, "Magnus Carlsen", [CARLSEN], ["Magnus Carlsen"], CARLSEN_PAGE)
    assert chess._page_rounds(ref) == ["abcdefgh"]
    assert kept == {f"lichess:fide-page:{CARLSEN}": ('["abcdefgh"]', lichess_chess.STALE_PAGE)}
    lichess_chess._cache.clear()  # a restart: nothing read in this process
    answers[CARLSEN_PAGE] = httpx.Response(429, text="Too many requests.")
    assert chess._page_rounds(ref) == ["abcdefgh"]  # what the last copy listed stands in
    kept.clear()
    assert chess._page_rounds(ref) == []  # never read: Lichess's FIDE pages are paused, and that's no error
    # The player's events are still found by search, and no FIDE page refusal fails the read.
    answers["https://lichess.org/api/broadcast/search?q=carlsen"] = httpx.Response(200, json={"currentPageResults": []})
    answers["https://lichess.org/api/broadcast/top"] = httpx.Response(200, json={"active": [], "upcoming": []})
    assert chess.fixtures(ref) == []


def test_tournament_pgn_gives_every_round_in_one_read():
    with gzip.open(FIXTURES / "lichess/tour-ewc-playoffs.pgn.gz", "rt") as f:
        rounds = lichess_chess.parse_pgn_rounds(f.read())
    assert len(rounds) == 6
    final = lichess_chess.games_of({"games": rounds["ohv5F74c"]}, CARLSEN)
    assert len(final) == 8
    assert lichess_chess.match_score(final, CARLSEN) == (6.0, 8)  # same as the round JSON says
    assert final[0]["players"][0] == {"name": "Carlsen, Magnus", "fideId": 1503014, "rating": 2823, "title": "GM"}


def test_tournament_pgn_reads_tags_with_escaped_quotes():
    # Campeonato de Sevilla 2026 (recorded 2026-10-13): its name ends in an escaped quote
    # (Guadaíra\"), and every game of the broadcast lost its tags, so no player was found in it.
    with gzip.open(FIXTURES / "lichess/tour-sevilla-round1.pgn.gz", "rt") as f:
        rounds = lichess_chess.parse_pgn_rounds(f.read())
    games = rounds["6gyT8UBN"]
    assert len(games) == 6 and all(g["players"][0]["fideId"] and g["status"] != "*" for g in games)
    [game] = lichess_chess.games_of({"games": games}, "24543241")
    assert game["players"][0] == {"name": "Garcia Jimenez, Santiago", "fideId": 24543241, "rating": 2278, "title": None}
    assert game["status"] == "1-0"
    # A value is unescaped: \" is a quote, \\ a backslash.
    pgn = '[White "O\\"Neill, Pat \\\\ IM"]\n[Black "B"]\n[GameURL "https://lichess.org/broadcast/t/r/abcdefgh/ijklmnop"]\n'
    assert lichess_chess.parse_pgn_rounds(pgn)["abcdefgh"][0]["players"][0]["name"] == 'O"Neill, Pat \\ IM'


def test_chess_live_now_shows_the_top_boards_still_being_played(monkeypatch):
    def board(gid: str, white: tuple, black: tuple, status: str = "*") -> dict:
        side = lambda p: {"name": p[0], "fideId": p[1], "rating": p[2], "title": "GM", "clock": 360000}  # noqa: E731
        return {"id": gid, "status": status, "fen": "8/8/8/8/8/8/8/8 w - - 0 40", "players": [side(white), side(black)]}

    starts = int(time.time() * 1000) - 3600 * 1000
    round_now = {
        "round": {"id": "r5", "name": "Round 5", "ongoing": True, "startsAt": starts, "url": "https://lichess.org/broadcast/x/round-5/r5"},
        "tour": {"id": "t1", "name": "Grand Swiss"},
        "games": [
            board("g1", ("Gukesh, D", 46616543, 2777), ("Carlsen, Magnus", 1503014, 2837)),
            board("g2", ("Erigaisi, Arjun", 35009192, 2780), ("Nakamura, Hikaru", 2016192, 2800), status="1-0"),  # over
            board("g3", ("Praggnanandhaa, R", 25059530, 2758), ("Caruana, Fabiano", 2020009, 2795)),
            board("g4", ("Lower, Rated", 1, 2400), ("Also, Lower", 2, 2390)),
        ],
    }
    asked = []

    def fake_get_json(url, ttl, text=False):
        asked.append(url.removeprefix(lichess_chess.API))
        if url.endswith("broadcast/top"):
            return {"active": [{"tour": round_now["tour"], "round": round_now["round"]}, {"tour": {"name": "Done"}, "round": {"id": "r9", "finished": True}}]}
        return round_now

    monkeypatch.setattr(lichess_chess, "get_json", fake_get_json)
    games = lichess_chess.LichessChess().live_now()
    assert asked == ["broadcast/top", "broadcast/-/-/r5"]  # a finished round isn't read
    # The highest rated pairs still playing, LIVE_BOARDS of them, each with both players followable by FIDE id.
    assert [(g.title, g.competition, [p.athlete_id for p in g.players]) for g in games] == [
        ("D Gukesh v Magnus Carlsen", "Grand Swiss · Round 5", ["46616543", "1503014"]),
        ("R Praggnanandhaa v Fabiano Caruana", "Grand Swiss · Round 5", ["25059530", "2020009"])]
    assert all(g.snapshot.status == "live" and g.snapshot.state["kind"] == "chess" for g in games)
    # A board is a fixture of each of its players, as a followed player's card has it: Today shows it once, as theirs.
    assert games[0].fixtures == ["r5-46616543", "r5-1503014"]
