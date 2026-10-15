"""Live now (live.py): the games the running sources have on, league-wide, with the players a fan can follow
from each, against a throwaway database at alembic head (the registry's JSONB lookups). Skipped without
one; see tests/test_players.py for setting it up. Sources and Redis are made up here."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from sports_follow import live, registry, sports
from sports_follow.adapters import ADAPTERS, LiveGame, PlayerRef, Snapshot
from sports_follow.models import Follow, Player, PlayerCard, PlayerIdentity

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime(2026, 10, 15, 14, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE athlete, athlete_override, player, follow, moment, starter RESTART IDENTITY CASCADE"))
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def redis(monkeypatch):
    """Redis as a dict: what each source's last read left."""
    store: dict[str, str] = {}
    fake = SimpleNamespace(get=store.get, set=lambda key, value, ex=None: store.__setitem__(key, value))
    monkeypatch.setattr(live.bus, "sync_redis", lambda: fake)
    return store


def snapshot(score: str) -> Snapshot:
    return Snapshot(status="live", score_label=score, clock_label="67'", state={"kind": "football"})


@pytest.fixture
def sources(monkeypatch):
    """Two made-up sources that list what they have on: one names its players, one only its teams."""
    for system in [s for s, a in ADAPTERS.items() if callable(getattr(a, "live_now", None))]:
        monkeypatch.delitem(ADAPTERS, system)
    on = SimpleNamespace(pitch=[], arena=[])
    monkeypatch.setitem(ADAPTERS, "pitch", SimpleNamespace(system="pitch", sport="football", live_now=lambda: on.pitch))
    monkeypatch.setitem(ADAPTERS, "arena", SimpleNamespace(system="arena", sport="esports", live_now=lambda: on.arena))
    return on


def ref(aid: str, name: str, team: str) -> PlayerRef:
    return PlayerRef(system="pitch", athlete_id=aid, name=name, team_ids=[], team_names=[team])


def athlete(qid: str, sport: str, name: str, ids: dict, **extra) -> dict:
    return {"qid": qid, "sport": sport, "name": name, "aliases": [], "search_text": registry.search_text(name, []), "birth_date": None, "birth_precision": None,
            "country": None, "teams": [], "league": None, "title": None, "ids": ids, "image": None, "sitelinks": 1, "current": True, **extra}


def test_a_games_players_come_on_stann_first_then_best_known_then_the_sources_order(db, redis, sources):
    # On stann and followed by two fans; a star the registry knows well; two the registry doesn't know.
    db.add(Player(id=5, slug="kane", name="Harry Kane", sport="Football"))
    db.flush()
    db.add_all([PlayerCard(player_id=5, status="ready", card={}), PlayerIdentity(player_id=5, system="pitch", external_id="9", confidence=1.0),
                Follow(fan_id="fan", player_id=5, alert_rules={}), Follow(fan_id="other", player_id=5, alert_rules={})])
    registry.upsert(db, [athlete("Q1", "football", "Jamal Musiala", {"pitch": "7"}, sitelinks=60), athlete("Q2", "football", "Leroy Sane", {"pitch": "8"}, sitelinks=40)])
    db.commit()
    sources.pitch = [
        LiveGame(source_id="100", title="Bayern vs Leipzig", competition="Bundesliga", start_utc=NOW - timedelta(minutes=67), snapshot=snapshot("Bayern 2 – 1 Leipzig"),
                 players=[ref("1", "Keeper One", "Leipzig"), ref("8", "Leroy Sane", "Bayern"), ref("2", "Defender Two", "Leipzig"), ref("9", "Harry Kane", "Bayern"), ref("7", "Jamal Musiala", "Bayern")],
                 teams=["Bayern", "Leipzig"]),
        LiveGame(source_id="101", title="Quiet vs Small", competition="Bundesliga", start_utc=None, snapshot=snapshot("0 – 0"), players=[ref("3", "Nobody", "Quiet")]),
    ]
    assert live.read("pitch", NOW) == 2
    [football], stale = live.offer(db, "fan", NOW)
    assert stale == ["arena"]  # never read: the caller queues a read
    assert football["key"] == "football" and [g["title"] for g in football["games"]] == ["Bayern vs Leipzig", "Quiet vs Small"]  # the followed player's game first
    game = football["games"][0]
    assert game["live"]["score"] == "Bayern 2 – 1 Leipzig" and game["live"]["is_live"] is True and game["key"] == "pitch:100"
    # Kane is on stann (and this fan follows him), then the registry's best known, then the source's order; PLAYERS of them.
    assert [(p["name"], p["player_id"], p["following"]) for p in game["players"]] == [
        ("Harry Kane", 5, True), ("Jamal Musiala", None, False), ("Leroy Sane", None, False), ("Keeper One", None, False)]
    assert (game["players"][1]["system"], game["players"][1]["athlete_id"]) == ("pitch", "7")  # followed by the source's id


def test_a_game_naming_only_its_teams_offers_the_pros_the_registry_knows(db, redis, sources):
    registry.upsert(db, [athlete("pandascore:1", "esports", "Yatoro", {"arena": "11"}, teams=["Team Spirit"], sitelinks=3, league="dota-2"),
                         athlete("pandascore:2", "esports", "Collapse", {"arena": "12"}, teams=["Team Spirit"]),
                         athlete("pandascore:3", "esports", "Retired Pro", {"arena": "13"}, teams=["Team Spirit"], current=False),
                         athlete("pandascore:4", "esports", "Elsewhere", {"arena": "14"}, teams=["Other Team"])])
    db.commit()
    sources.arena = [LiveGame(source_id="7", title="Team Spirit vs Tundra", competition="The International", start_utc=None, snapshot=snapshot("1 – 0"), teams=["Team Spirit", "Tundra"])]
    live.read("arena", NOW)
    [esports] = live.offer(db, None, NOW)[0]
    assert [(p["name"], p["athlete_id"], p["league"]) for p in esports["games"][0]["players"]] == [("Yatoro", "11", "dota-2"), ("Collapse", "12", None)]


def test_a_read_is_served_for_a_minute_and_a_sport_thats_off_shows_nothing(db, redis, sources, monkeypatch):
    sources.pitch = [LiveGame(source_id="100", title="A vs B", competition="Cup", start_utc=None, snapshot=snapshot("1 – 0"))]
    live.read("pitch", NOW)
    live.read("arena", NOW)
    assert live.offer(db, None, NOW + timedelta(seconds=30))[1] == []  # fresh: nothing to ask
    assert live.offer(db, None, NOW + timedelta(seconds=90))[1] == ["pitch", "arena"]  # a minute on: read again
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ENABLED if s.key != "football"))
    assert live.offer(db, None, NOW)[0] == []
