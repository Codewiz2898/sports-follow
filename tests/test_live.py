"""Live now (live.py): the games the running sources have on, league-wide, with the players a fan can follow
from each, against a throwaway database at alembic head (the registry's JSONB lookups). Skipped without
one; see tests/test_players.py for setting it up. Sources and Redis are made up here."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from sports_follow import live, registry, sports
from sports_follow.adapters import ADAPTERS, LiveGame, PlayerRef, Snapshot
from sports_follow.models import AthleteOverride, Event, Follow, Player, PlayerCard, PlayerIdentity

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime(2026, 10, 15, 14, tzinfo=timezone.utc)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL)
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

    monkeypatch.setattr(live, "session", unit)  # the worker's read ranks players in a session of its own
    s = made()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def redis(monkeypatch):
    """Redis as a dict: what each source's last read left. And this process's shared ranking empty (live.offer)."""
    store: dict[str, str] = {}
    fake = SimpleNamespace(get=store.get, set=lambda key, value, ex=None: store.__setitem__(key, value))
    monkeypatch.setattr(live.bus, "sync_redis", lambda: fake)
    monkeypatch.setattr(live, "_shared", {}, raising=False)
    return store


def snapshot(score: str) -> Snapshot:
    return Snapshot(status="live", score_label=score, clock_label="67'", state={"kind": "football"})


@pytest.fixture
def sources(monkeypatch):
    """Two made-up sources that list what they have on: one names its players, one only its teams."""
    for system in [s for s, a in ADAPTERS.items() if callable(getattr(a, "live_now", None))]:
        monkeypatch.delitem(ADAPTERS, system)
    on = SimpleNamespace(pitch=[], arena=[])
    monkeypatch.setitem(ADAPTERS, "pitch", SimpleNamespace(system="pitch", sports=("football",), live_now=lambda: on.pitch))
    monkeypatch.setitem(ADAPTERS, "arena", SimpleNamespace(system="arena", sports=("dota2", "cs2"), live_now=lambda: on.arena))  # two esports games
    return on


def ref(aid: str, name: str, team: str) -> PlayerRef:
    return PlayerRef(system="pitch", athlete_id=aid, name=name, team_ids=[], team_names=[team])


def athlete(qid: str, sport: str, name: str, ids: dict, **extra) -> dict:
    return {"qid": qid, "sport": sport, "name": name, "aliases": [], "search_text": registry.search_text(name, []), "birth_date": None, "birth_precision": None,
            "country": None, "teams": [], "league": None, "title": None, "ids": ids, "image": None, "sitelinks": 1, "current": True, **extra}


def test_a_games_players_come_on_the_app_first_then_best_known_then_the_sources_order(db, redis, sources):
    # On the app and followed by two fans; two stars the registry knows by the source's id, and one by name only;
    # two it doesn't know.
    db.add(Player(id=5, slug="kane", name="Harry Kane", sport="Football"))
    db.flush()
    db.add_all([PlayerCard(player_id=5, status="ready", card={}), PlayerIdentity(player_id=5, system="pitch", external_id="9", confidence=1.0),
                Follow(fan_id="fan", player_id=5, alert_rules={}), Follow(fan_id="other", player_id=5, alert_rules={})])
    registry.upsert(db, [athlete("Q1", "football", "Jamal Musiala", {"pitch": "7"}, sitelinks=60), athlete("Q2", "football", "Leroy Sane", {"pitch": "8"}, sitelinks=40),
                         athlete("Q3", "football", "Thomas Muller", {}, sitelinks=50)])
    db.commit()
    sources.pitch = [
        LiveGame(source_id="100", title="Bayern vs Leipzig", competition="Bundesliga", start_utc=NOW - timedelta(minutes=67), snapshot=snapshot("Bayern 2 – 1 Leipzig"),
                 players=[ref("1", "Keeper One", "Leipzig"), ref("8", "Leroy Sane", "Bayern"), ref("2", "Defender Two", "Leipzig"), ref("9", "Harry Kane", "Bayern"),
                          ref("6", "Thomas Muller", "Bayern"), ref("7", "Jamal Musiala", "Bayern")],
                 teams=["Bayern", "Leipzig"]),
        LiveGame(source_id="101", title="Quiet vs Small", competition="Bundesliga", start_utc=None, snapshot=snapshot("0 – 0"), players=[ref("3", "Nobody", "Quiet")]),
    ]
    assert live.read("pitch", NOW) == 2
    [football], stale = live.offer(db, "fan", NOW)
    assert stale == ["arena"]  # never read: the caller asks for a read
    assert football["key"] == "football" and [g["title"] for g in football["games"]] == ["Bayern vs Leipzig", "Quiet vs Small"]  # the followed player's game first
    game = football["games"][0]
    assert game["live"]["score"] == "Bayern 2 – 1 Leipzig" and game["live"]["is_live"] is True and game["key"] == "pitch:100"
    # Kane is on the app (and this fan follows him); then the registry's best known, by the source's id or by name; PLAYERS of them.
    assert [(p["name"], p["player_id"], p["following"]) for p in game["players"]] == [
        ("Harry Kane", 5, True), ("Jamal Musiala", None, False), ("Thomas Muller", None, False), ("Leroy Sane", None, False)]
    assert (game["players"][1]["system"], game["players"][1]["athlete_id"]) == ("pitch", "7")  # followed by the source's id


def test_a_removed_player_isnt_offered(db, redis, sources):
    db.add(AthleteOverride(qid="Q2", sport="football", action="remove", name="Leroy Sane", ids={"pitch": "8"}))
    db.commit()
    sources.pitch = [LiveGame(source_id="100", title="A vs B", competition="Cup", start_utc=None, snapshot=snapshot("1 – 0"),
                              players=[ref("8", "Leroy Sane", "A"), ref("1", "Someone Else", "B")])]
    live.read("pitch", NOW)
    assert [p["name"] for p in live.offer(db, None, NOW)[0][0]["games"][0]["players"]] == ["Someone Else"]


def test_a_game_naming_only_its_teams_offers_its_titles_pros_the_registry_knows(db, redis, sources):
    registry.upsert(db, [athlete("pandascore:1", "dota2", "Yatoro", {"arena": "11"}, teams=["Team Spirit"], sitelinks=3, league="dota-2"),
                         athlete("pandascore:2", "dota2", "Collapse", {"arena": "12"}, teams=["Team Spirit"], league="dota-2"),
                         athlete("pandascore:5", "cs2", "Donk", {"arena": "15"}, teams=["Team Spirit"], sitelinks=9, league="cs-go"),  # same team, another title
                         athlete("pandascore:3", "dota2", "Retired Pro", {"arena": "13"}, teams=["Team Spirit"], current=False, league="dota-2"),
                         athlete("pandascore:4", "dota2", "Elsewhere", {"arena": "14"}, teams=["Other Team"], league="dota-2")])
    db.commit()
    sources.arena = [LiveGame(source_id="7", title="Team Spirit vs Tundra", competition="The International", start_utc=None, snapshot=snapshot("1 – 0"),
                              teams=["Team Spirit", "Tundra"], league="dota-2", sport="dota2")]
    live.read("arena", NOW)
    [dota] = live.offer(db, None, NOW)[0]
    assert (dota["key"], dota["label"]) == ("dota2", "Dota 2")  # each game a sport of its own
    assert [(p["name"], p["athlete_id"], p["league"]) for p in dota["games"][0]["players"]] == [("Yatoro", "11", "dota-2"), ("Collapse", "12", "dota-2")]


def test_a_source_of_several_games_offers_each_games_under_its_sport_and_none_of_a_game_turned_off(db, redis, sources, monkeypatch):
    def on(source_id: str, title: str, sport: str) -> LiveGame:
        return LiveGame(source_id=source_id, title=title, competition="Cup", start_utc=None, snapshot=snapshot("1 – 0"), teams=title.split(" vs "), tier=3, sport=sport)

    sources.arena = [on("1", "Team Spirit vs Tundra", "dota2"), on("2", "Vitality vs FaZe", "cs2")]
    live.read("arena", NOW)
    assert [(s["key"], [g["title"] for g in s["games"]]) for s in live.offer(db, None, NOW)[0]] == [("cs2", ["Vitality vs FaZe"]), ("dota2", ["Team Spirit vs Tundra"])]
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key != "cs2"))
    assert [s["key"] for s in live.offer(db, None, NOW)[0]] == ["dota2"]
    # A read from before games said their sport: a source of several can't say whose, so none is offered.
    redis[live._key("arena")] = json.dumps({**live.last_read("arena"), "games": [{k: v for k, v in g.items() if k != "sport"} for g in live.last_read("arena")["games"]]})
    assert live.offer(db, None, NOW)[0] == []


def test_the_biggest_events_games_come_first_however_followed_or_known(db, redis, sources):
    # A source that rates its events (an esports tournament's tier): a bigger tier's game comes first, though a
    # smaller one's pro is better known and followed here; among equals, the source's order.
    db.add(Player(id=6, slug="yatoro", name="Yatoro", sport="Dota 2"))
    db.flush()
    db.add_all([PlayerCard(player_id=6, status="ready", card={}), PlayerIdentity(player_id=6, system="arena", external_id="11", confidence=1.0),
                Follow(fan_id="fan", player_id=6, alert_rules={})])
    registry.upsert(db, [athlete("pandascore:1", "dota2", "Yatoro", {"arena": "11"}, teams=["Team Spirit"], sitelinks=9, league="dota-2")])
    db.commit()

    def on(source_id: str, title: str, tier: int) -> LiveGame:
        return LiveGame(source_id=source_id, title=title, competition="Cup", start_utc=None, snapshot=snapshot("1 – 0"), teams=title.split(" vs "), league="dota-2", tier=tier, sport="dota2")

    sources.arena = [on("1", "Team Spirit vs Tundra", 1), on("2", "T1 vs Gen.G", 3), on("3", "Cloud9 vs Karmine Corp", 2), on("4", "FURIA vs Legacy", 2)]
    live.read("arena", NOW)
    [esports] = live.offer(db, "fan", NOW)[0]
    assert [g["title"] for g in esports["games"]] == ["T1 vs Gen.G", "Cloud9 vs Karmine Corp", "FURIA vs Legacy", "Team Spirit vs Tundra"]
    assert [(p["name"], p["following"]) for p in esports["games"][3]["players"]] == [("Yatoro", True)]


def test_a_big_events_game_outlasts_the_cuts_however_little_known(db, redis, sources, monkeypatch):
    # A busy day: smaller events' games with better known pros outnumber what a read keeps, and what the API
    # ranks; the big event's game, with no pro the registry knows, is kept by both.
    registry.upsert(db, [athlete(f"pandascore:{i}", "dota2", f"Pro {i}", {"arena": str(i)}, teams=[f"Team {i}"], sitelinks=10 - i, league="dota-2") for i in (1, 2, 3)])
    db.commit()

    def on(source_id: str, title: str, tier: int) -> LiveGame:
        return LiveGame(source_id=source_id, title=title, competition="Cup", start_utc=None, snapshot=snapshot("1 – 0"), teams=title.split(" vs "), league="dota-2", tier=tier, sport="dota2")

    sources.arena = [on("1", "Team 1 vs A", 1), on("2", "Team 2 vs B", 1), on("3", "Team 3 vs C", 1), on("4", "T1 vs Gen.G", 3)]
    monkeypatch.setattr(live, "GAMES", 1)  # the API ranks GAMES * 2 of a sport's games
    live.read("arena", NOW)
    assert [g["title"] for g in live.offer(db, None, NOW)[0][0]["games"]] == ["T1 vs Gen.G"]
    monkeypatch.setattr(live, "READ_GAMES", 3)
    live.read("arena", NOW)
    assert [g["title"] for g in live.last_read("arena")["games"]] == ["T1 vs Gen.G", "Team 1 vs A", "Team 2 vs B"]


def test_a_read_is_served_for_a_minute_asked_for_again_then_dropped(db, redis, sources, monkeypatch):
    sources.pitch = [LiveGame(source_id="100", title="A vs B", competition="Cup", start_utc=None, snapshot=snapshot("1 – 0"))]
    live.read("pitch", NOW)
    live.read("arena", NOW)
    assert live.offer(db, None, NOW + timedelta(seconds=30)) == ([{"key": "football", "label": "Football", "group": "", "games": [
        {"key": "pitch:100", "match": "pitch/100", "title": "A vs B", "competition": "Cup", "start_utc": None, "live": live.last_read("pitch")["games"][0]["live"], "players": [], "event_ids": []}]}], [])
    games, stale = live.offer(db, None, NOW + timedelta(seconds=90))
    assert stale == ["pitch", "arena"] and games  # a minute on: still served, and a read asked for
    assert live.offer(db, None, NOW + timedelta(seconds=live.SERVE + 1))[0] == []  # too old to call live
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ENABLED if s.key != "football"))
    assert live.offer(db, None, NOW)[0] == []


def test_a_game_names_the_events_it_is_here_by_the_sources_fixture_ids(db, redis, sources):
    # A followed player's card names its live game by event id: an event's key is "system:fixture id". A chess
    # board is a fixture of each of its players; a game no one here follows is no event.
    db.add_all([Event(key="pitch:100", sport="football", title="A vs B"), Event(key="pitch:r5-7", sport="football", title="C v D"),
                Event(key="pitch:r5-8", sport="football", title="C v D"), Event(key="other:101", sport="football", title="E vs F")])
    db.commit()
    ids = {key: event_id for key, event_id in db.execute(text("SELECT key, id FROM event"))}
    sources.pitch = [LiveGame(source_id="100", title="A vs B", competition="Cup", start_utc=None, snapshot=snapshot("1 – 0")),
                     LiveGame(source_id="r5:g1", title="C v D", competition="Cup", start_utc=None, snapshot=snapshot("0 – 0"), fixtures=["r5-7", "r5-8"]),
                     LiveGame(source_id="101", title="E vs F", competition="Cup", start_utc=None, snapshot=snapshot("2 – 2"))]
    live.read("pitch", NOW)
    games = {g["title"]: g["event_ids"] for g in live.offer(db, None, NOW)[0][0]["games"]}
    assert games == {"A vs B": [ids["pitch:100"]], "C v D": sorted([ids["pitch:r5-7"], ids["pitch:r5-8"]]), "E vs F": []}  # another source's 101 isn't this one
    # Each opens its match page (match.py), a board by the first of its players' fixtures.
    assert [g["match"] for g in live.offer(db, None, NOW)[0][0]["games"]] == ["pitch/100", "pitch/r5-7", "pitch/101"]


def test_fans_looking_within_a_few_seconds_share_one_ranking_and_only_their_follows_are_their_own(db, redis, sources):
    # BUG-268: every look at Live now read and ranked each source's games again and looked up who's on Sports Follow
    # (their whole cards, their follower counts): the same league-wide work for every fan, every minute each.
    db.add(Player(id=5, slug="kane", name="Harry Kane", sport="Football"))
    db.flush()
    db.add_all([PlayerCard(player_id=5, status="ready", card={}), PlayerIdentity(player_id=5, system="pitch", external_id="9", confidence=1.0),
                Follow(fan_id="fan", player_id=5, alert_rules={}), Follow(fan_id="other", player_id=5, alert_rules={})])
    db.commit()
    sources.pitch = [LiveGame(source_id="100", title="Bayern vs Leipzig", competition="Bundesliga", start_utc=None, snapshot=snapshot("Bayern 2 – 1 Leipzig"),
                              players=[ref("9", "Harry Kane", "Bayern"), ref("1", "Keeper One", "Leipzig")])]
    live.read("pitch", NOW)
    queries: list[str] = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda conn, cursor, statement, *rest: queries.append(statement))

    def kane(fan: str | None, at: datetime) -> tuple[bool, int]:
        [football] = live.offer(db, fan, at)[0]
        [player] = [p for p in football["games"][0]["players"] if p["name"] == "Harry Kane"]
        return player["following"], player["followers"]

    assert kane("fan", NOW) == (True, 2)
    first = len(queries)
    assert kane("stranger", NOW + timedelta(seconds=1)) == (False, 2)
    assert kane(None, NOW + timedelta(seconds=2)) == (False, 2)
    assert len(queries) - first == 1  # the stranger's follows; nobody's needs none
    # A new read from the source is served at once.
    sources.pitch[0].snapshot = snapshot("Bayern 3 – 1 Leipzig")
    live.read("pitch", NOW + timedelta(seconds=3))
    assert live.offer(db, "fan", NOW + timedelta(seconds=3))[0][0]["games"][0]["live"]["score"] == "Bayern 3 – 1 Leipzig"
    # And a few seconds on, the ranking is made again: a fan followed since counts.
    db.add(Follow(fan_id="third", player_id=5, alert_rules={}))
    db.commit()
    assert kane("third", NOW + timedelta(seconds=live.SHARED + 5)) == (True, 3)


def test_a_read_its_sources_share_for_the_minute_refuses_keeps_the_last_and_is_no_failure(db, redis, sources, monkeypatch):
    """A source's share of its minute for this lane spent (budget.RateLimited: our own limit, not the source failing)
    leaves the last read served and is no failed job: the next look asks again, as a player's refresh does."""
    from sports_follow.adapters import budget

    sources.pitch = [LiveGame(source_id="100", title="A vs B", competition="Cup", start_utc=None, snapshot=snapshot("1 – 0"))]
    live.read("pitch", NOW)
    before = live.last_read("pitch")

    def spent():
        raise budget.RateLimited("pitch: the refresh share of its 10 requests a minute is spent")

    monkeypatch.setitem(ADAPTERS, "pitch", SimpleNamespace(system="pitch", sports=("football",), live_now=spent))
    assert live.read("pitch", NOW + timedelta(seconds=30)) == 0
    assert live.last_read("pitch") == before
