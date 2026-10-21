"""A game's match page (match.py): each sport's sections under its board (Sport.match_page), and the page
itself, from what's stored and read, against a throwaway database at alembic head (skipped without one; see
tests/test_players.py for setting it up). Sources and Redis are made up here."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from sports_follow import bus, live, match, registry, sports
from sports_follow.adapters import ADAPTERS, KNOWN, Line, LiveGame, PlayerRef, Snapshot, Stream
from sports_follow.adapters.base import DIGITS
from sports_follow.models import AthleteOverride, Event, EventPlayer, EventState, Follow, Moment, Player, PlayerCard, PlayerIdentity, PlayerLine

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime(2026, 10, 16, 14, tzinfo=timezone.utc)
HOME, AWAY = "1", "2"
FOOTBALL = {"kind": "football", "home": {"id": HOME, "name": "Arsenal", "score": "2", "scorers": []}, "away": {"id": AWAY, "name": "Chelsea", "score": "1", "scorers": []}}


# ---------------------------------------------------------------- each sport's sections


def test_football_has_a_timeline_each_goal_and_card_on_the_side_it_counts_for():
    events = [
        {"clock": "62'", "kind": "GOAL", "text": "Palmer (Chelsea)", "team_id": AWAY, "athlete_ids": ["9"]},
        {"clock": "30'", "kind": "GOAL", "text": "Colwill (Chelsea), own goal", "team_id": AWAY, "own_goal": True},
        {"clock": "12'", "kind": "YELLOW", "text": "Rice (Arsenal)", "team_id": HOME},
        {"clock": "5'", "kind": "DISALLOWED", "text": "Saka (Arsenal)", "team_id": HOME},
    ]
    page = sports.get("football").match_page(FOOTBALL, events, {}, {})
    assert page == {"timeline": [
        {"clock": "62'", "kind": "GOAL", "text": "Palmer (Chelsea)", "side": 1},
        {"clock": "30'", "kind": "GOAL", "text": "Colwill (Chelsea), own goal", "side": 0},  # Arsenal's goal
        {"clock": "12'", "kind": "YELLOW", "text": "Rice (Arsenal)", "side": 0},
        {"clock": "5'", "kind": "DISALLOWED", "text": "Saka (Arsenal)", "side": 0},
    ]}
    assert sports.get("football").match_page(FOOTBALL, [], {}, {}) == {}


def line(**stats: str) -> Line:
    return {"headline": "", "stats": [{"label": k.replace("_", " "), "value": v} for k, v in stats.items()]}


def test_cricket_has_a_scorecard_each_side_in_the_order_they_batted_with_who_bowled_at_it():
    state = {"kind": "cricket", "teams": [{"name": "India", "score": "221/7"}, {"name": "Afghanistan", "score": "94"}],
             "innings": [{"team": "Afghanistan", "period": 1}, {"team": "India", "period": 2}]}
    lines = {
        "1": line(Runs="108", Balls="34", **{"4s": "9", "6s": "8"}, Strike_rate="317.6", Out="c Gurbaz b Khan"),
        "2": line(Runs="50*", Balls="35", **{"4s": "4", "6s": "2"}, Strike_rate="142.9"),
        "3": {"headline": "", "stats": [{"label": "Runs", "value": "12"}, {"label": "Balls", "value": "9"}, {"label": "Wickets", "value": "3"}, {"label": "Overs", "value": "4"},
                                         {"label": "Runs conceded", "value": "21"}, {"label": "Economy", "value": "5.25"}]},
        "4": line(Runs="7"),  # nobody the source named: no row
    }
    names = {"1": ("Abhishek Sharma", "India"), "2": ("Sanju Samson", "India"), "3": ("Rashid Khan", "Afghanistan")}
    page = sports.get("cricket").match_page(state, [], lines, names)
    afghanistan, india = page["scorecard"]  # Afghanistan batted first
    assert (afghanistan["team"], afghanistan["score"], [b["name"] for b in afghanistan["batting"]], afghanistan["bowling"]) == ("Afghanistan", "94", ["Rashid Khan"], [])
    assert (india["team"], india["score"]) == ("India", "221/7")
    assert india["batting"][0] == {"name": "Abhishek Sharma", "runs": "108", "balls": "34", "fours": "9", "sixes": "8", "strike_rate": "317.6", "out": "c Gurbaz b Khan"}
    assert india["batting"][1]["out"] == ""  # not out
    assert india["bowling"] == [{"name": "Rashid Khan", "overs": "4", "runs": "21", "wickets": "3", "economy": "5.25"}]  # who bowled at India
    assert sports.get("cricket").match_page({"kind": "cricket", "teams": []}, [], lines, names) == {}


def test_esports_has_a_series_map_by_map_or_a_lobbys_whole_standings():
    series = {"kind": "esports", "teams": [{"id": "10", "name": "Team Spirit"}, {"id": "20", "name": "Tundra"}],
              "maps": [{"n": 1, "status": "finished", "winner": "10"}, {"n": 2, "status": "running", "winner": None}],
              "dota": {"maps": [{"n": 1, "players": {"11": {"hero": "Faceless Void", "kills": 12, "deaths": 2, "assists": 8}, "12": {"hero": "Lion", "kills": 3, "deaths": 6, "assists": 20}}}]}}
    page = sports.get("Dota 2").match_page(series, [], {}, {"11": ("Yatoro", "Team Spirit"), "12": ("Mira", "Team Spirit")})
    assert page == {"maps": [{"n": 1, "status": "finished", "winner": "Team Spirit", "best": "Yatoro 12/2/8 on Faceless Void"},
                             {"n": 2, "status": "running", "winner": None, "best": None}]}
    lobby = {"kind": "battle_royale", "standings": [{"id": "a", "name": "GodLike", "place": 1, "points": 87, "kills": 40, "wins": 2, "games": []},
                                                     {"id": "b", "name": "Soul", "place": 2, "points": 60, "kills": 30, "wins": 1, "games": []}],
             "games": [{"n": 1, "map": "Erangel", "status": "finished", "winner": "b"}]}
    page = sports.get("BGMI").match_page(lobby, [], {}, {})
    assert page["standings"][0] == {"place": 1, "name": "GodLike", "points": 87, "kills": 40, "wins": 2}
    assert page["games"] == [{"n": 1, "map": "Erangel", "winner": "Soul"}]


def test_a_sport_without_sections_lists_the_key_events_and_a_chess_board_says_it_all():
    events = [{"clock": "Q3 4:12", "kind": "FOUL", "text": "Flagrant 1", "athlete_ids": []}]
    assert sports.of("golf").match_page({}, events, {}, {}) == {"events": [{"clock": "Q3 4:12", "kind": "FOUL", "text": "Flagrant 1"}]}
    assert sports.get("chess").match_page({"kind": "chess"}, [], {}, {}) == {}


def test_a_live_chess_game_lists_its_moves_so_far_and_those_added_since():
    # Its read's moves (the PGN's) under whichever board is newest, and the moves added since as the board gave them.
    told = {"moves": [[1, "d4", "Nf6"], [2, "c4", "e6"]], "more": ["g1f3", 7], "opening": "Indian"}
    state = {"kind": "chess", "fen": "x", "match": {"games": 2, "score": "1½–½"}, "page_detail": told}
    assert sports.get("chess").match_page(state, [], {}, {}) == {"moves": ["1. d4 Nf6", "2. c4 e6"], "more": ["g1f3"], "opening": "Indian", "ending": None, "game": 3}
    # Once it's over, the finished game's own moves stand, not a live read's.
    over = {"kind": "chess", "moves": [[1, "d4", "Nf6"], [2, "c4", "e6"], [3, "Nf3", "d5"]], "opening": "QGD", "ending": "resigned", "page_detail": told}
    assert sports.get("chess").match_page(over, [], {}, {}) == {"moves": ["1. d4 Nf6", "2. c4 e6", "3. Nf3 d5"], "opening": "QGD", "ending": "resigned", "game": None}


def test_a_finished_chess_game_lists_its_moves_in_pairs_with_its_opening():
    state = {"kind": "chess", "moves": [[1, "d4", "Nf6"], [2, "c4", "e6"], [3, "Nf3", None]], "opening": "Queen's Gambit Declined", "ending": "resigned",
             "match": {"games": 8, "score": "6–2"}}
    assert sports.get("chess").match_page(state, [], {}, {}) == {"moves": ["1. d4 Nf6", "2. c4 e6", "3. Nf3"], "opening": "Queen's Gambit Declined", "ending": "resigned", "game": 8}
    black_first = {"kind": "chess", "moves": [[12, None, "e5"], [13, "Nf3", "Nc6"], "junk", [14, None, None]]}  # a game set up with Black to move
    assert sports.get("chess").match_page(black_first, [], {}, {})["moves"] == ["12... e5", "13. Nf3 Nc6"]


@pytest.mark.parametrize(("sport", "state", "clock", "said"), [
    ("football", FOOTBALL, "FT", "Arsenal won"),
    ("football", {**FOOTBALL, "away": {**FOOTBALL["away"], "score": "2"}}, "FT", "Draw"),
    ("football", {**FOOTBALL, "away": {**FOOTBALL["away"], "score": "2"}, "detail": "Full Time After Penalties"}, "FT_PEN", ""),  # the board's goals don't say who
    ("cricket", {"kind": "cricket"}, "India won by 57 runs", "India won by 57 runs"),
    ("cricket", {"kind": "cricket"}, "Match tied (India won the Super Over)", "Match tied (India won the Super Over)"),
    ("cricket", {"kind": "cricket"}, "Result", ""),  # the source's summary still to come: the league's line says it, or nothing
    ("chess", {"kind": "chess"}, "Magnus Carlsen won the match (8 games)", "Magnus Carlsen won the match (8 games)"),
    ("Valorant", {"kind": "esports", "teams": [{"id": "1", "name": "Sentinels", "score": 2, "winner": True}, {"id": "2", "name": "NRG", "score": 1, "winner": False}]}, "Final", "Sentinels won 2–1"),
    ("Valorant", {"kind": "esports", "forfeit": True, "teams": [{"id": "1", "name": "Sentinels", "score": 0}, {"id": "2", "name": "NRG", "score": 0, "winner": True}]}, "Forfeit", "NRG won by forfeit"),
    ("BGMI", {"kind": "battle_royale", "standings": [{"name": "Soul", "place": 2}, {"name": "GodLike", "place": 1}]}, "", "GodLike won the lobby"),
    ("BGMI", {"kind": "battle_royale", "standings": [{"name": "Soul", "place": 1}, {"name": "GodLike", "place": 1}]}, "", ""),  # level at the top: nobody won it alone
    ("tennis", {"kind": "tennis", "players": [{"name": "Sinner"}, {"name": "Alcaraz", "winner": True}]}, "", "Alcaraz won"),
    ("basketball", {"kind": "basketball", "home": {"name": "Celtics", "score": "101"}, "away": {"name": "Knicks", "score": "99"}}, "Final", "Celtics won"),
    ("golf", {}, "", ""),  # a sport no module knows: the board says it
])
def test_a_finished_game_says_who_won_as_its_sport_words_it(sport, state, clock, said):
    assert sports.of(sport).verdict(state, "", clock) == said


# ---------------------------------------------------------------- the page


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL or "postgresql+psycopg://")
    with engine.begin() as c:
        c.execute(text("TRUNCATE athlete, athlete_override, player, follow, moment, starter, event RESTART IDENTITY CASCADE"))
    made = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def unit():
        s = made()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    monkeypatch.setattr(live, "session", unit)  # the worker's reads rank players in sessions of their own
    monkeypatch.setattr(match, "session", unit)
    s = made()
    yield s
    s.close()
    engine.dispose()


class Store(dict):
    """Redis as a dict, and how long it was asked to keep each key."""

    def __init__(self):
        super().__init__()
        self.ttl: dict[str, int | None] = {}

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self[key], self.ttl[key] = value, ex


@pytest.fixture
def redis(monkeypatch):
    """Redis as a dict: what each read left (the league's too)."""
    store = Store()
    monkeypatch.setattr(bus, "sync_redis", lambda: store)
    return store


def ref(aid: str, name: str, team: str) -> PlayerRef:
    return PlayerRef(system="pitch", athlete_id=aid, name=name, team_ids=[], team_names=[team])


ELEVENS = [ref("7", "Bukayo Saka", "Arsenal"), ref("9", "Harry Kane", "Arsenal"), ref("20", "Cole Palmer", "Chelsea")]
GOAL = {"clock": "62'", "kind": "GOAL", "text": "Palmer (Chelsea)", "team_id": AWAY, "athlete_ids": ["20"]}


def game(score: str = "Arsenal 2 – 1 Chelsea", status: str = "live", players: list[PlayerRef] | None = None, **snap) -> LiveGame:
    return LiveGame(source_id="100", title="Arsenal vs Chelsea", competition="Premier League", start_utc=NOW - timedelta(minutes=70),
                    snapshot=Snapshot(status=status, score_label=score, clock_label="70'", state=FOOTBALL, **snap), players=players or [], teams=["Arsenal", "Chelsea"])


@pytest.fixture
def source(monkeypatch):
    """A made-up football source that lists what it has on and reads one game."""
    on = SimpleNamespace(live=[], game=None, read=[])

    def one(fixture: str) -> LiveGame | None:
        on.read.append(fixture)
        return on.game

    fake = SimpleNamespace(system="pitch", sports=("football",), name="Pitch", fixture_pattern=DIGITS, live_now=lambda: on.live, match=one, credit=lambda snap: "Pitch", live_note=None, lag_note=None)
    for system in [s for s, a in ADAPTERS.items() if callable(getattr(a, "live_now", None))]:
        monkeypatch.delitem(ADAPTERS, system)
    monkeypatch.setitem(ADAPTERS, "pitch", fake)
    monkeypatch.setitem(KNOWN, "pitch", fake)
    return on


@needs_db
def test_a_game_nobody_follows_is_live_nows_until_its_own_read_comes(db, redis, source):
    source.live = [game(players=[ELEVENS[2], ELEVENS[0]])]
    live.read("pitch", NOW)
    page, ask = match.view(db, "fan", "pitch", "100", NOW + timedelta(seconds=5))
    assert ask  # never read: the API asks for one
    assert (page["key"], page["match"], page["sport"], page["title"], page["competition"], page["status"]) == ("pitch:100", "pitch/100", "football", "Arsenal vs Chelsea", "Premier League", "live")
    assert (page["live"]["score"], page["live"]["is_live"], page["live"]["as_of"]) == ("Arsenal 2 – 1 Chelsea", True, NOW.isoformat())
    assert [(s["name"], [p["name"] for p in s["players"]]) for s in page["sides"]] == [("Chelsea", ["Cole Palmer"]), ("Arsenal", ["Bukayo Saka"])]
    assert page["moments"] == [] and page["event_ids"] == [] and page["page"] == {}

    # Its own read: both elevens, every line, the source's events, newer than Live now's.
    source.game = game("Arsenal 2 – 2 Chelsea", players=ELEVENS, moments=[GOAL], lines={"20": Line(headline="1 goal", stats=[])})
    assert match.read("pitch", "100", NOW + timedelta(seconds=10)) and source.read == ["100"]
    page, ask = match.view(db, "fan", "pitch", "100", NOW + timedelta(seconds=20))
    assert not ask  # read ten seconds ago
    assert (page["live"]["score"], page["live"]["source"]) == ("Arsenal 2 – 2 Chelsea", "Pitch")
    assert page["page"] == {"timeline": [{"clock": "62'", "kind": "GOAL", "text": "Palmer (Chelsea)", "side": 1}]}
    assert [(s["name"], [(p["name"], p["system"], p["athlete_id"]) for p in s["players"]]) for s in page["sides"]] == [
        ("Arsenal", [("Bukayo Saka", "pitch", "7"), ("Harry Kane", "pitch", "9")]), ("Chelsea", [("Cole Palmer", "pitch", "20")])]
    assert match.view(db, "fan", "pitch", "100", NOW + timedelta(seconds=10 + match.FRESH + 1))[1]  # a minute on: asked again

    # Over: the read that says so is read once more, CONFIRM on, and that's the last.
    over = NOW + timedelta(hours=2)
    source.game = game("Arsenal 3 – 2 Chelsea", status="final")
    match.read("pitch", "100", over)
    page, ask = match.view(db, "fan", "pitch", "100", over + timedelta(minutes=10))
    assert (page["status"], page["live"]["is_live"], page["live"]["score"], ask) == ("final", False, "Arsenal 3 – 2 Chelsea", False)
    assert match.view(db, "fan", "pitch", "100", over + timedelta(seconds=match.CONFIRM + 1))[1]
    match.read("pitch", "100", over + timedelta(seconds=match.CONFIRM + 1))
    assert not match.view(db, "fan", "pitch", "100", over + timedelta(days=3))[1]


@needs_db
def test_where_to_watch_a_game_comes_with_its_read_the_organisers_main_broadcast_first(db, redis, source):
    source.live = [game()]
    live.read("pitch", NOW)
    assert match.view(db, "fan", "pitch", "100", NOW)[0]["streams"] == []  # Live now's copy: none till its read
    source.game = game()
    source.game.streams = [Stream("https://www.twitch.tv/costreamer", "es"), Stream("https://kick.com/league_en", "en", official=True, main=True),
                           Stream("ftp://example.com/x", "en", official=True, main=True)]
    match.read("pitch", "100", NOW + timedelta(seconds=10))
    page, _ = match.view(db, "fan", "pitch", "100", NOW + timedelta(seconds=20))
    assert [(s["platform"], s["language_name"], s["official"], s["embed"]) for s in page["streams"]] == [
        ("Kick", "English", True, {"player": "kick", "id": "league_en"}), ("Twitch", "Spanish", False, {"player": "twitch", "id": "costreamer"})]
    assert json.loads(redis["match:pitch:100"])["game"]["streams"][0] == {"url": "https://www.twitch.tv/costreamer", "language": "es", "official": False, "main": False}


@needs_db
def test_a_game_no_source_has_named_isnt_one(db, redis, source):
    assert match.view(db, "fan", "pitch", "404", NOW) == (None, False)  # not on Live now, never read, nobody's
    source.game = None
    match.read("pitch", "404", NOW)  # read anyway (a game Live now had), and the source hasn't got it
    assert match.view(db, "fan", "pitch", "404", NOW) == (None, False)
    assert not match.read("pitch", "1:2", NOW)  # an id of the wrong shape asks nothing
    assert source.read == ["404"]


def followed(db, events: list[dict] | None = None) -> None:
    """Arsenal v Chelsea stored (event 40, "pitch:100"), polled for Saka, whom "fan" follows, and Kane, on
    Sports Follow: Saka's live card has its board at 71' and the source's newest events, as a card keeps them."""
    db.add_all([Player(id=5, slug="saka", name="Bukayo Saka", sport="Football"), Player(id=6, slug="kane", name="Harry Kane", sport="Football"),
                Event(id=40, key="pitch:100", sport="Football", title="Arsenal vs Chelsea", competition="Premier League", notes="Matchday 8", status="live", start_utc=NOW)])
    db.flush()
    block = {"is_live": True, "event_id": 40, "event": "Arsenal vs Chelsea · Matchday 8", "competition": "Premier League", "score": "Arsenal 2 – 1 Chelsea",
             "clock": "71'", "state": FOOTBALL, "moments": [GOAL] if events is None else events, "source": "Pitch", "as_of": (NOW + timedelta(seconds=30)).isoformat(), "headline": "1 goal"}
    db.add_all([PlayerCard(player_id=5, status="ready", card={"live": block}, is_live=True), PlayerCard(player_id=6, status="ready", card={"live": {"is_live": False}}),
                PlayerIdentity(player_id=5, system="pitch", external_id="7", confidence=1.0), PlayerIdentity(player_id=6, system="pitch", external_id="9", confidence=1.0),
                EventPlayer(event_id=40, player_id=5), EventPlayer(event_id=40, player_id=6), Follow(fan_id="fan", player_id=5, alert_rules={}),
                EventState(event_id=40, as_of=NOW, status="live", score_label="Arsenal 1 – 1 Chelsea", clock_label="60'", state=FOOTBALL)])
    db.commit()


@needs_db
def test_a_followed_players_game_is_their_live_cards_with_the_moments_found_in_it(db, redis, source):
    followed(db)
    for i, (pid, kind, title) in enumerate([(5, "start", "Bukayo Saka's game has started"), (6, "lineup", "Harry Kane starts"), (5, "goal", "Goal: Bukayo Saka"), (6, "assist", "Assist: Harry Kane")]):
        db.add(Moment(key=f"m{i}", player_id=pid, event_id=40, kind=kind, level="key", title=title, body="2–1 · 64'", created_at=NOW + timedelta(seconds=i)))
    db.commit()

    page, ask = match.view(db, "fan", "pitch", "100", NOW + timedelta(seconds=40))
    assert ask  # for the line-ups: no read yet
    # The live card's board, as fresh as its polls, over the state stored at 60'.
    assert (page["live"]["score"], page["live"]["clock"], page["status"], page["detail"], page["event_ids"]) == ("Arsenal 2 – 1 Chelsea", "71'", "live", "Matchday 8", [40])
    assert page["page"] == {"timeline": [{"clock": "62'", "kind": "GOAL", "text": "Palmer (Chelsea)", "side": 1}]}
    # What was found for each player in it, newest first, but what they all get alike.
    assert [m["title"] for m in page["moments"]] == ["Assist: Harry Kane", "Goal: Bukayo Saka"]
    # No read has its line-ups yet: the players here in it, as one side.
    [side] = page["sides"]
    assert (side["name"], [(p["name"], p["player_id"], p["following"]) for p in side["players"]]) == ("", [("Bukayo Saka", 5, True), ("Harry Kane", 6, False)])

    # A chess board's other address (the other player's fixture) is the same game, once a read names both.
    source.game = game(players=ELEVENS)
    source.game.fixtures = ["100", "101"]
    match.read("pitch", "101", NOW)
    assert match.view(db, "fan", "pitch", "101", NOW + timedelta(seconds=40))[0]["event_ids"] == [40]


SERIES = {"kind": "esports", "teams": [{"id": "10", "name": "Team Spirit"}, {"id": "20", "name": "Team Yandex"}],
          "maps": [{"n": 1, "status": "finished", "winner": "10"}, {"n": 2, "status": "running", "winner": None}]}


@needs_db
def test_a_reads_detail_shows_under_a_followed_players_newer_card(db, redis, source):
    """A poll carries no map detail (each poll's state is stored and sent to every card); the game's own read
    does (a Dota 2 map's draft and gold), and it shows under the followed player's newer board."""
    maps = [{"n": 1, "events": [{"t": 88, "clock": "1:28", "title": "First blood to Spirit", "note": None, "team": "10", "mark": None}]}]
    teams = [{"id": "10", "name": "Team Spirit", "short": "Spirit"}, {"id": "20", "name": "Team Yandex", "short": "Yandex"}]
    source.game = LiveGame(source_id="100", title="Team Spirit vs Team Yandex", competition="BLAST Slam", start_utc=NOW, teams=["Team Spirit", "Team Yandex"],
                           snapshot=Snapshot(status="live", score_label="1–0", clock_label="Map 2", state={**SERIES, "page_detail": {"teams": teams, "maps": maps}}))
    match.read("pitch", "100", NOW)
    db.add_all([Player(id=5, slug="yatoro", name="Yatoro", sport="Dota 2"),
                Event(id=40, key="pitch:100", sport="Dota 2", title="Team Spirit vs Team Yandex", competition="BLAST Slam", status="live", start_utc=NOW)])
    db.flush()
    block = {"is_live": True, "event_id": 40, "event": "Team Spirit vs Team Yandex", "competition": "BLAST Slam", "score": "1–0", "clock": "Map 2 · 3:10",
             "state": SERIES, "moments": [], "source": "Pitch", "as_of": (NOW + timedelta(seconds=30)).isoformat()}
    db.add_all([PlayerCard(player_id=5, status="ready", card={"live": block}, is_live=True), EventPlayer(event_id=40, player_id=5)])
    db.commit()
    page, _ = match.view(db, "fan", "pitch", "100", NOW + timedelta(seconds=40))
    assert page["live"]["clock"] == "Map 2 · 3:10"  # the card's board: newer than the read
    assert page["page"]["detail"] == {"maps": maps, "live": None} and page["page"]["teams"] == teams


def card(minute: int) -> dict:
    return {"clock": f"{minute}'", "kind": "YELLOW", "text": f"Booked at {minute}", "team_id": HOME}


def test_a_cards_newest_events_come_first_and_a_reads_before_them():
    newest = [card(m) for m in (80, 70, 60)]
    assert match._merged(newest, [card(m) for m in (70, 60, 50, 40)]) == [card(m) for m in (80, 70, 60, 50, 40)]
    assert match._merged([card(80), card(60)], [card(70), card(60), card(50)]) == [card(80), card(60), card(50)]  # 70' taken back since
    assert match._merged(newest, [card(10)]) == newest  # where the read's go can't be told
    assert match._merged([], [card(10)]) == [card(10)]
    # Two alike in one minute (one player booked twice, say): each shows once, whether the newest board holds
    # both or only the later.
    read = [card(80), card(60), card(60), card(50)]
    assert match._merged([card(80), card(60), card(60)], read) == read
    assert match._merged([card(80), card(60)], read) == read


@needs_db
def test_a_followed_players_card_keeps_the_newest_events_and_a_read_those_before(db, redis, source):
    # Eleven bookings and a goal: the card keeps the newest ten, an older read has all but the newest.
    events = [{**GOAL, "clock": "90'"}, *(card(m) for m in range(85, 30, -5))]
    followed(db, events[:10])
    source.game = game(players=ELEVENS, moments=events[1:])
    match.read("pitch", "100", NOW)  # before the card's poll
    page, _ = match.view(db, "fan", "pitch", "100", NOW + timedelta(seconds=40))
    assert page["live"]["clock"] == "71'"  # the card's board
    assert [e["clock"] for e in page["page"]["timeline"]] == [e["clock"] for e in events] and len(events) == 12


@needs_db
def test_a_sport_turned_off_has_no_match_pages(db, redis, source, monkeypatch):
    followed(db)
    source.live = [game(players=ELEVENS)]
    live.read("pitch", NOW)
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key != "football"))
    assert match.view(db, "fan", "pitch", "100", NOW) == (None, False)


@needs_db
def test_an_esports_game_turned_off_has_no_match_page_while_its_source_runs_for_another(db, redis, monkeypatch):
    """A source of several games: each series is its game's sport (LiveGame.sport), its pros the registry knows
    in that game; one of a game turned off has no page, while the source's other games' do."""
    series = {"kind": "esports", "game": "Valorant", "teams": [{"id": "10", "name": "Sentinels", "score": 1}, {"id": "20", "name": "NRG", "score": 0}], "maps": []}
    games = {"1": LiveGame(source_id="1", title="Sentinels vs NRG", competition="VCT", start_utc=NOW, teams=["Sentinels", "NRG"], league="valorant", sport="valorant",
                           snapshot=Snapshot(status="live", score_label="Sentinels 1 – 0 NRG", clock_label="Map 2", state=series)),
             "2": LiveGame(source_id="2", title="Vitality vs FaZe", competition="Major", start_utc=NOW, teams=["Vitality", "FaZe"], league="cs-go", sport="cs2",
                           snapshot=Snapshot(status="live", score_label="Vitality 1 – 0 FaZe", clock_label="Map 2", state={**series, "game": "CS2", "teams": []}))}
    arena = SimpleNamespace(system="arena", sports=("cs2", "valorant"), name="Arena", fixture_pattern=DIGITS, match=games.get, credit=lambda snap: "Arena", live_note=None, lag_note=None)
    monkeypatch.setitem(ADAPTERS, "arena", arena)
    monkeypatch.setitem(KNOWN, "arena", arena)
    registry.upsert(db, [{"qid": "pandascore:7", "sport": "valorant", "name": "TenZ", "aliases": [], "search_text": " tenz |", "birth_date": None, "country": None,
                          "teams": ["Sentinels"], "league": "valorant", "title": None, "ids": {"arena": "7"}, "image": None, "sitelinks": 3, "current": True}])
    db.commit()
    assert match.read("arena", "1", NOW) and match.read("arena", "2", NOW)
    page, _ = match.view(db, "fan", "arena", "1", NOW)
    assert page["sport"] == "valorant" and [p["name"] for s in page["sides"] for p in s["players"]] == ["TenZ"]  # the game's pros, by its sport
    assert match.view(db, "fan", "arena", "2", NOW)[0]["sport"] == "cs2"
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key != "valorant"))
    assert match.view(db, "fan", "arena", "1", NOW) == (None, False) and match.view(db, "fan", "arena", "2", NOW)[0] is not None


@needs_db
def test_a_read_that_found_nothing_isnt_asked_again_every_minute(db, redis, source):
    followed(db)
    source.game = None  # the source hasn't got it (it filters the series out, say)
    match.read("pitch", "100", NOW)
    page, ask = match.view(db, "fan", "pitch", "100", NOW + timedelta(minutes=10))
    assert page["event_ids"] == [40] and not ask  # what's stored still shows


@needs_db
def test_a_scorecard_names_a_substitute_who_bowled_but_only_the_elevens_are_offered(db, redis, source):
    state = {"kind": "cricket", "teams": [{"id": "1", "name": "India", "score": "180/4"}, {"id": "2", "name": "Australia", "score": "60/3"}],
             "innings": [{"team": "India", "period": 1}, {"team": "Australia", "period": 2}]}
    bowled = Line(headline="2/20 (4 ov)", stats=[{"label": "Wickets", "value": "2"}, {"label": "Overs", "value": "4"}, {"label": "Runs conceded", "value": "20"}, {"label": "Economy", "value": "5.00"}])
    batted = Line(headline="80* (50)", stats=[{"label": "Runs", "value": "80*"}, {"label": "Balls", "value": "50"}])
    source.game = LiveGame(source_id="100", title="India v Australia", competition="T20I", start_utc=NOW, teams=["India", "Australia"],
                           snapshot=Snapshot(status="live", score_label="IND 180/4 · AUS 60/3", clock_label="", state=state, lines={"1": batted, "30": bowled}),
                           players=[ref("1", "Virat Kohli", "India")], bench=[ref("30", "Impact Bowler", "Australia")])
    match.read("pitch", "100", NOW)
    page, _ = match.view(db, "fan", "pitch", "100", NOW)
    [india] = page["page"]["scorecard"]
    assert ([b["name"] for b in india["batting"]], [w["name"] for w in india["bowling"]]) == (["Virat Kohli"], ["Impact Bowler"])
    assert [p["name"] for s in page["sides"] for p in s["players"]] == ["Virat Kohli"]


@needs_db
def test_a_removed_player_isnt_offered(db, redis, source):
    db.add(AthleteOverride(qid="Q1", sport="football", action="remove", name="Harry Kane", ids={"pitch": "9"}))
    db.commit()
    source.game = game(players=ELEVENS)
    match.read("pitch", "100", NOW)
    page, _ = match.view(db, None, "pitch", "100", NOW)
    assert [p["name"] for s in page["sides"] for p in s["players"]] == ["Bukayo Saka", "Cole Palmer"]


@needs_db
def test_a_finished_game_is_its_final_state(db, redis, source, monkeypatch):
    # Its source turned off since: what's stored still shows, and nothing is asked of it.
    monkeypatch.delitem(ADAPTERS, "pitch")
    db.add(Event(id=41, key="pitch:200", sport="Football", title="Arsenal vs Chelsea", competition="Premier League", status="final", start_utc=NOW))
    db.flush()
    db.add(EventState(event_id=41, as_of=NOW, status="final", score_label="Arsenal 3 – 2 Chelsea", clock_label="Full time", state=FOOTBALL))
    db.commit()
    page, ask = match.view(db, "fan", "pitch", "200", NOW + timedelta(days=1))
    assert (page["status"], page["live"]["is_live"], page["live"]["score"], page["live"]["clock"], page["sport"], ask) == ("final", False, "Arsenal 3 – 2 Chelsea", "Full time", "football", False)


@needs_db
def test_a_big_result_is_named_by_the_league_and_a_crowd_opening_it_costs_two_reads(db, redis, source):
    over = NOW - timedelta(days=1)
    league_game = {"key": "pitch:300", "sport": "football", "title": "Arsenal vs Chelsea", "competition": "Premier League", "detail": "Matchday 8",
                   "start_utc": over.isoformat(), "status": "final", "rank": 80, "word": "3–2", "line": "Arsenal beat Chelsea", "why": "1st v 2nd", "group": ""}
    draw = {**league_game, "key": "pitch:302", "word": "1–1", "line": ""}  # a sport whose card says the score alone
    redis.set("league:games:pitch", json.dumps({"as_of": (NOW - timedelta(hours=1)).isoformat(), "games": [league_game, draw]}))
    assert match.view(db, "fan", "pitch", "301", NOW) == (None, False)  # not the league's, nor anyone's: no read
    assert match.view(db, "fan", "pitch", "302", NOW)[0]["verdict"] == "1–1"

    page, ask = match.view(db, "fan", "pitch", "300", NOW)
    assert ask  # never read: the first look asks for one
    assert (page["title"], page["competition"], page["detail"], page["status"], page["live"], page["verdict"]) == (
        "Arsenal vs Chelsea", "Premier League", "Matchday 8", "final", None, "Arsenal beat Chelsea")  # the league's line till it comes

    source.game = game("Arsenal 3 – 2 Chelsea", status="final", players=ELEVENS, moments=[GOAL])
    source.game.snapshot.state = {**FOOTBALL, "home": {**FOOTBALL["home"], "score": "3"}, "away": {**FOOTBALL["away"], "score": "2"}}
    assert match.read("pitch", "300", NOW + timedelta(seconds=5))
    assert redis.ttl["match:pitch:300"] == match.KEEP  # the first read that finds it over isn't the last word yet
    assert not match.view(db, None, "pitch", "300", NOW + timedelta(minutes=10))[1]  # nor read every minute till then
    confirm = NOW + timedelta(seconds=5 + match.CONFIRM + 1)
    assert match.view(db, None, "pitch", "300", confirm)[1]
    assert match.read("pitch", "300", confirm)  # still over: settled
    assert redis.ttl["match:pitch:300"] == match.FINAL_KEEP  # kept a week, not KEEP's hours
    for later in (timedelta(minutes=1), timedelta(hours=7), timedelta(days=6)):  # the crowd, all week
        page, ask = match.view(db, None, "pitch", "300", confirm + later)
        assert not ask
    assert source.read == ["300", "300"]
    assert (page["status"], page["live"]["score"], page["verdict"], page["yours"]) == ("final", "Arsenal 3 – 2 Chelsea", "Arsenal won", [])
    assert page["page"] == {"timeline": [{"clock": "62'", "kind": "GOAL", "text": "Palmer (Chelsea)", "side": 1}]}


@needs_db
def test_a_game_to_come_is_read_every_half_hour_till_its_line_ups_are_near(db, redis, source):
    start = NOW + timedelta(days=2)
    redis.set("league:games:pitch", json.dumps({"as_of": NOW.isoformat(), "games": [
        {"key": "pitch:400", "sport": "football", "title": "Chelsea vs Arsenal", "competition": "Premier League", "detail": None, "start_utc": start.isoformat(),
         "status": "scheduled", "rank": 80, "word": "", "line": "", "why": "", "group": ""}]}))
    page, ask = match.view(db, "fan", "pitch", "400", NOW)
    assert (page["status"], page["start_utc"], page["live"], page["verdict"], ask) == ("scheduled", start.isoformat(), None, "", True)
    source.game = game("Chelsea vs Arsenal", status="scheduled")
    source.game.start_utc = start
    match.read("pitch", "400", NOW)
    assert redis.ttl["match:pitch:400"] == match.KEEP
    assert not match.view(db, "fan", "pitch", "400", NOW + timedelta(minutes=5))[1]  # days off: not every minute
    assert match.view(db, "fan", "pitch", "400", NOW + timedelta(minutes=31))[1]
    near = start - timedelta(hours=1)  # its line-ups are due
    match.read("pitch", "400", near)
    assert match.view(db, "fan", "pitch", "400", near + timedelta(seconds=match.FRESH + 1))[1]  # read as often as a live game


@needs_db
def test_a_game_called_off_is_read_every_half_hour_whenever_it_was_due(db, redis, source):
    followed(db)  # its event started at NOW
    db.execute(Event.__table__.update().where(Event.id == 40).values(status="postponed"))
    db.execute(EventState.__table__.delete())
    db.execute(PlayerCard.__table__.update().values(is_live=False))
    db.commit()
    source.game = game("Arsenal vs Chelsea", status="postponed")
    match.read("pitch", "100", NOW + timedelta(hours=3))
    assert not match.view(db, "fan", "pitch", "100", NOW + timedelta(hours=3, minutes=5))[1]  # abandoned, its start long gone: not every minute
    assert match.view(db, "fan", "pitch", "100", NOW + timedelta(hours=3, minutes=31))[1]


@needs_db
def test_a_fans_players_in_a_game_with_their_lines_and_results(db, redis, source):
    followed(db)
    db.add(PlayerLine(event_id=40, player_id=5, as_of=NOW, stats={"headline": "1 goal", "stats": [{"label": "Goals", "value": "1"}]}))
    db.execute(EventPlayer.__table__.update().where(EventPlayer.player_id == 5).values(result={"title": "Arsenal vs Chelsea", "result": "Won 2–1"}))
    db.commit()
    source.game = game(players=ELEVENS, lines={"7": Line(headline="2 goals", stats=[]), "9": Line(headline="Started", stats=[])})
    match.read("pitch", "100", NOW)
    page, _ = match.view(db, "fan", "pitch", "100", NOW + timedelta(seconds=40))
    # Saka, whom the fan follows: his line as the poll stored it, and his result. Kane, whom nobody here follows, isn't theirs.
    assert page["yours"] == [{"player_id": 5, "name": "Bukayo Saka", "sport": "Football", "result": "Won 2–1", "headline": "1 goal", "stats": [{"label": "Goals", "value": "1"}]}]
    db.add(Follow(fan_id="fan", player_id=6, alert_rules={}))
    db.commit()
    saka, kane = match.view(db, "fan", "pitch", "100", NOW + timedelta(seconds=40))[0]["yours"]  # by name
    assert (saka["name"], kane["name"], kane["headline"], kane["result"]) == ("Bukayo Saka", "Harry Kane", "Started", None)  # no poll stored his: the read's
    assert match.view(db, "someone else", "pitch", "100", NOW)[0]["yours"] == [] and match.view(db, None, "pitch", "100", NOW)[0]["yours"] == []


@needs_db
def test_a_read_at_full_time_is_confirmed_before_it_beats_newer_boards(db, redis, source):
    """A football source read right at the whistle: no lines yet, a goal it hasn't corrected. The state a poll
    stored after it shows, the read is asked for again CONFIRM on, and the read then, with its lines, is the
    page's for good, over the state stored before it."""
    followed(db)
    source.game = game("Arsenal 3 – 2 Chelsea", status="final", players=ELEVENS, moments=[GOAL, {**GOAL, "clock": "88'"}])
    match.read("pitch", "100", NOW)
    db.add(EventState(event_id=40, as_of=NOW + timedelta(minutes=5), status="final", score_label="Arsenal 3 – 1 Chelsea", clock_label="Full time", state=FOOTBALL))
    db.execute(PlayerCard.__table__.update().values(is_live=False))
    db.commit()
    page, ask = match.view(db, "fan", "pitch", "100", NOW + timedelta(minutes=10))
    assert (page["live"]["score"], ask) == ("Arsenal 3 – 1 Chelsea", False)  # the newer stored board; the read is due at CONFIRM
    assert redis.ttl["match:pitch:100"] == match.KEEP

    later = NOW + timedelta(seconds=match.CONFIRM + 1)
    assert match.view(db, "fan", "pitch", "100", later)[1]
    source.game = game("Arsenal 3 – 1 Chelsea", status="final", players=ELEVENS, moments=[GOAL], lines={"7": Line(headline="2 goals", stats=[])})
    match.read("pitch", "100", later)
    page, ask = match.view(db, "fan", "pitch", "100", later + timedelta(days=1))
    assert (page["live"]["source"], [e["clock"] for e in page["page"]["timeline"]], ask) == ("Pitch", ["62'"], False)  # the settled read's: 88' taken back
    assert redis.ttl["match:pitch:100"] == match.FINAL_KEEP


@needs_db
def test_a_partial_read_of_a_finished_game_is_read_again_till_its_whole(db, redis, source):
    """A chess board decided while its round goes on (a mini-match's next game may follow), or whose moves
    couldn't be read: its read is partial, so it isn't settled, and is asked for again every CONFIRM."""
    followed(db)
    chess = {"kind": "chess", "white": {"id": "7", "name": "A"}, "black": {"id": "9", "name": "B"}, "result": "1-0"}
    source.game = game("A 1–0 B", status="final", players=ELEVENS[:2])
    source.game.snapshot.state, source.game.partial = chess, True
    match.read("pitch", "100", NOW)
    match.read("pitch", "100", NOW + timedelta(hours=1))  # still partial an hour on
    assert redis.ttl["match:pitch:100"] == match.KEEP
    assert match.view(db, "fan", "pitch", "100", NOW + timedelta(hours=1, seconds=match.CONFIRM + 1))[1]

    # The round over, the moves read: settled at once (it was first found over long enough ago), and its board the page's.
    source.game.snapshot.state, source.game.partial = {**chess, "moves": [[1, "e4", "e5"]]}, False
    match.read("pitch", "100", NOW + timedelta(hours=2))
    assert redis.ttl["match:pitch:100"] == match.FINAL_KEEP
    db.add(EventState(event_id=40, as_of=NOW + timedelta(hours=3), status="final", score_label="A 1–0 B", clock_label="A won", state=chess))
    db.commit()
    page, ask = match.view(db, "fan", "pitch", "100", NOW + timedelta(days=2))
    assert (page["status"], page["live"]["source"], page["page"]["moves"], ask) == ("final", "Pitch", ["1. e4 e5"], False)


@needs_db
def test_a_game_that_goes_on_after_a_read_called_it_over_shows_live(db, redis, source):
    """A knockout mini-match's first game decided, its round still on: the read calls the board over (partial).
    Live now has its next game on minutes later: that shows, and the game is read every minute again. Live
    now's list a moment behind the read (under LAG) doesn't."""
    chess = {"kind": "chess", "white": {"id": "7", "name": "A"}, "black": {"id": "9", "name": "B"}, "result": "1-0"}
    source.game = game("A 1–0 B", status="final", players=ELEVENS[:2])
    source.game.snapshot.state, source.game.partial = chess, True
    match.read("pitch", "100", NOW)
    assert json.loads(redis["match:pitch:100"])["partial"]

    source.live = [game("A 1–0 B", players=ELEVENS[:2])]
    live.read("pitch", NOW + timedelta(seconds=30))  # behind the result
    page, ask = match.view(db, "fan", "pitch", "100", NOW + timedelta(minutes=2))
    assert (page["status"], page["live"]["is_live"], ask) == ("final", False, False)

    live.read("pitch", NOW + timedelta(minutes=5))  # game 2 on
    page, ask = match.view(db, "fan", "pitch", "100", NOW + timedelta(minutes=5, seconds=10))
    assert (page["status"], page["live"]["is_live"], ask) == ("live", True, True)  # live, and read again now, not CONFIRM on


@needs_db
def test_a_read_that_stays_partial_settles_once_its_waited_long_enough(db, redis, source):
    source.game = game("Arsenal 3 – 2 Chelsea", status="final", players=ELEVENS)
    source.game.partial = True  # a part its source never fills in (a round Lichess never marks finished)
    match.read("pitch", "100", NOW)
    match.read("pitch", "100", NOW + timedelta(hours=1))
    assert redis.ttl["match:pitch:100"] == match.KEEP
    match.read("pitch", "100", NOW + timedelta(seconds=match.PARTIAL + 1))
    assert redis.ttl["match:pitch:100"] == match.FINAL_KEEP
    assert not json.loads(redis["match:pitch:100"])["partial"]
    assert not match.view(db, "fan", "pitch", "100", NOW + timedelta(days=2))[1]
