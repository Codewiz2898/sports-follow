"""A game's match page (match.py): each sport's sections under its board (Sport.match_page), and the page
itself, from what's stored and read, against a throwaway database at alembic head (skipped without one; see
tests/test_players.py for setting it up). Sources and Redis are made up here."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from sports_follow import bus, live, match, sports
from sports_follow.adapters import ADAPTERS, KNOWN, Line, LiveGame, PlayerRef, Snapshot
from sports_follow.adapters.base import DIGITS
from sports_follow.models import AthleteOverride, Event, EventPlayer, EventState, Follow, Moment, Player, PlayerCard, PlayerIdentity

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


@pytest.fixture
def redis(monkeypatch):
    """Redis as a dict: what each read left."""
    store: dict[str, str] = {}
    monkeypatch.setattr(bus, "sync_redis", lambda: SimpleNamespace(get=store.get, set=lambda key, value, ex=None: store.__setitem__(key, value)))
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

    fake = SimpleNamespace(system="pitch", sport="football", name="Pitch", fixture_pattern=DIGITS, live_now=lambda: on.live, match=one, credit=lambda snap: "Pitch", live_note=None, lag_note=None)
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

    # Over: the read that says so is the last.
    source.game = game("Arsenal 3 – 2 Chelsea", status="final")
    match.read("pitch", "100", NOW + timedelta(hours=2))
    page, ask = match.view(db, "fan", "pitch", "100", NOW + timedelta(hours=3))
    assert (page["status"], page["live"]["is_live"], page["live"]["score"], ask) == ("final", False, "Arsenal 3 – 2 Chelsea", False)


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


def card(minute: int) -> dict:
    return {"clock": f"{minute}'", "kind": "YELLOW", "text": f"Booked at {minute}", "team_id": HOME}


def test_a_cards_newest_events_come_first_and_a_reads_before_them():
    newest = [card(m) for m in (80, 70, 60)]
    assert match._merged(newest, [card(m) for m in (70, 60, 50, 40)]) == [card(m) for m in (80, 70, 60, 50, 40)]
    assert match._merged([card(80), card(60)], [card(70), card(60), card(50)]) == [card(80), card(60), card(50)]  # 70' taken back since
    assert match._merged(newest, [card(10)]) == newest  # where the read's go can't be told
    assert match._merged([], [card(10)]) == [card(10)]


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
