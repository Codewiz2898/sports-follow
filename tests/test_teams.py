"""Following a team (teams.py, BHA-77): each sport's word for a result from one side (Sport.side_final), the noise
rules for a followed team's games (alerts.plan), and the path from the registry to a push, against a throwaway
database at alembic head (skipped without one; see tests/test_players.py for setting it up): an import fills the
registry as operators decided, search finds its teams, a follow reads the team's games and has them polled though
nobody follows a player in them, their moments reach the team's fans, and the result is told from each one's
side. Sources and Redis are made up here."""

from __future__ import annotations

import asyncio
import gzip
import json
import os
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.orm import sessionmaker

from sports_follow import accounts, bus, notify, pipeline, server, sports, structured, teams
from sports_follow.adapters import ADAPTERS, KNOWN, AdapterError, Fixture, Line, PlayerRef, Provider, Snapshot, SourceTeam, budget, espn_basketball, espn_cricket
from sports_follow.adapters.base import DIGITS
from sports_follow.alerts import Item, Watch, plan
from sports_follow.models import (Account, Event, EventPlayer, EventState, EventTeam, Follow, MatchFollow, Notification, NotificationMute, Player, PlayerIdentity,
                                  PushSubscription, Team, TeamFollow, TeamOverride)
from sports_follow.sports.football import result_label

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime(2026, 10, 17, 14, tzinfo=timezone.utc)
ARS, CHE, TOT = "1", "2", "3"
NAMES = {ARS: "Arsenal", CHE: "Chelsea", TOT: "Tottenham Hotspur"}


def football(home: int, away: int, **more) -> dict:
    return {"kind": "football", "home": {"id": ARS, "name": "Arsenal", "score": str(home)}, "away": {"id": CHE, "name": "Chelsea", "score": str(away)}, **more}


# ---------------------------------------------------------------- each sport's word for a result from one side


def test_a_result_is_told_from_each_sides_view():
    rules = sports.get("football")
    assert rules is not None
    assert rules.side_final(football(2, 1), "Arsenal 2 – 1 Chelsea", "FT", ARS) == "Arsenal won 2–1 v Chelsea"
    assert rules.side_final(football(2, 1), "Arsenal 2 – 1 Chelsea", "FT", CHE) == "Chelsea lost 1–2 v Arsenal"
    assert rules.side_final(football(1, 1), "", "FT", CHE) == "Chelsea drew 1–1 v Arsenal"
    assert rules.side_final(football(1, 1, detail="After penalties"), "", "FT_PEN", ARS) == ""  # the game's own result says it
    assert rules.side_final(football(2, 1), "", "FT", TOT) == ""  # not one of its sides
    basketball = sports.get("basketball")
    assert basketball is not None
    board = {"kind": "basketball", "home": {"id": "13", "name": "Lakers", "score": "99", "winner": False}, "away": {"id": "2", "name": "Celtics", "score": "104", "winner": True}}
    assert basketball.side_final(board, "", "Final", "13") == "Lakers lost 99–104 v Celtics"
    # As a result card says it, where a schedule has no result yet (teams._refresh).
    assert [rules.side_result(football(2, 1), "", "FT", ARS), rules.side_result(football(1, 1), "", "FT", CHE), basketball.side_result(board, "", "Final", "13")] == ["Won 2–1", "Drew 1–1", "Lost 99–104"]
    assert rules.side_result(football(2, 1), "", "FT", TOT) == ""


def test_cricket_says_the_margin_and_esports_the_maps_or_the_forfeit():
    cricket = sports.get("cricket")
    assert cricket is not None
    board = {"kind": "cricket", "teams": [{"id": "6", "name": "India", "score": "300/5", "winner": True}, {"id": "5", "name": "Australia", "score": "243", "winner": False}]}
    assert cricket.side_final(board, "", "India won by 57 runs", "6") == "India won by 57 runs v Australia"
    assert cricket.side_final(board, "", "India won by 57 runs", "5") == "Australia lost by 57 runs v India"
    unmarked = {"kind": "cricket", "teams": [{"id": "6", "name": "India"}, {"id": "5", "name": "Australia"}]}
    assert cricket.side_final(unmarked, "", "Australia won by 4 wickets", "6") == "India lost by 4 wickets v Australia"  # the summary names the winner
    assert cricket.side_final(board, "", "Match tied", "6") == ""
    lol = sports.get("lol")
    assert lol is not None
    series = {"kind": "esports", "teams": [{"id": "126061", "name": "T1", "score": 3, "winner": True}, {"id": "2", "name": "Gen.G", "score": 1, "winner": False}]}
    assert lol.side_final(series, "", "", "2") == "Gen.G lost 1–3 v T1"
    sweep = {"kind": "esports", "teams": [{"id": "126061", "name": "T1", "score": 2}, {"id": "2", "name": "Gen.G", "score": 0}]}
    assert lol.side_final(sweep, "", "", "126061") == "T1 won 2–0 v Gen.G"  # a sweep's 0 is a score, flagged winner or not
    assert lol.side_final({**series, "forfeit": True}, "", "", "126061") == "T1 won by forfeit v Gen.G"
    assert lol.side_final({"kind": "battle_royale", "standings": []}, "", "", "1") == ""  # a lobby has no two sides
    assert [cricket.side_result(board, "", "India won by 57 runs", "5"), lol.side_result({**series, "forfeit": True}, "", "", "2")] == ["Lost by 57 runs", "Lost by forfeit"]


def test_only_team_sports_have_teams_to_follow():
    assert {s.key for s in sports.ALL if s.follow_teams} >= {"football", "cricket", "basketball", "valorant", "lol"}
    assert not any(sports.get(k).follow_teams for k in ("tennis", "chess", "bgmi", "freefire", "pubgmobile", "pubg", "apex"))  # type: ignore[union-attr]: one a side, or a lobby


# ---------------------------------------------------------------- the noise rules


GAME = 10


def item(id: int, kind: str, title: str, about: tuple[str, ...] = ("*",), player: int | None = None) -> Item:
    return Item(id, player, GAME, kind, "key", 80, title, "Arsenal 2 – 1 Chelsea", "/match/x/1", NOW, None, frozenset(about))


def test_a_teams_fans_hear_the_result_from_its_side_and_no_one_elses():
    games_own = item(1, "final", "Arsenal v Chelsea: Arsenal won")
    ours, theirs = item(2, "final", "Arsenal won 2–1 v Chelsea", ("*", "team:7")), item(3, "final", "Chelsea lost 1–2 v Arsenal", ("*", "team:8"))
    batch = [games_own, ours, theirs]
    [gooner] = plan(batch, {}, set(), [], NOW, games={GAME: Watch("key", teams=frozenset({"team:7"}))})
    assert gooner.moment_ids == [2] and gooner.title == "Arsenal won 2–1 v Chelsea"
    [blue] = plan(batch, {}, set(), [], NOW, games={GAME: Watch("key", teams=frozenset({"team:8"}))})
    assert blue.moment_ids == [3]
    [both] = plan(batch, {}, set(), [], NOW, games={GAME: Watch("key", teams=frozenset({"team:7", "team:8"}))})
    assert both.moment_ids == [2]  # once, though they follow both sides
    [neutral] = plan(batch, {}, set(), [], NOW, games={GAME: Watch("key")})  # the match followed by itself
    assert neutral.moment_ids == [1]
    # A fan who follows a player in it hears the result once, as their player's.
    [saka] = plan([*batch, item(4, "final", "Bukayo Saka: Arsenal 2 – 1 Chelsea", ("7",), player=5)], {5: "key"}, set(), [], NOW, games={GAME: Watch("key", teams=frozenset({"team:7"}))})
    assert saka.moment_ids == [4]
    # A goal is everyone's who follows the game, either team in it or the game itself.
    goal = item(5, "goal", "Arsenal v Chelsea: Saka puts Arsenal ahead", ("7",))
    assert [p.moment_ids for p in plan([goal], {}, set(), [], NOW, games={GAME: Watch("key", teams=frozenset({"team:8"}))})] == [[5]]


# ---------------------------------------------------------------- from the registry to a push


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL or "postgresql+psycopg://")
    with engine.begin() as c:
        c.execute(text("TRUNCATE account, player, event, event_state, player_line, follow, match_follow, moment, notification, notification_mute, push_subscription,"
                       " player_identity, player_card, team, team_override, team_follow, event_team RESTART IDENTITY CASCADE"))
    made = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def unit():
        s = made()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    for module in (structured, notify, pipeline, teams, server):
        monkeypatch.setattr(module, "session", unit)
    s = made()
    yield s
    s.close()
    engine.dispose()


class Store(dict):
    """Redis as a dict: what the locks, cooldowns and polls here ask of it."""

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False, get: bool = False):
        old = super().get(key)
        if nx and old is not None:
            return None
        self[key] = value
        return old if get else True

    def exists(self, key: str) -> int:
        return int(key in self)

    def delete(self, key: str) -> int:
        return int(self.pop(key, None) is not None)

    def eval(self, script: str, n: int, key: str, token: str) -> int:  # bus._RELEASE: free a lock its holder holds
        return self.delete(key) if super().get(key) == token else 0

    def publish(self, channel: str, message: str) -> int:
        return 0


@pytest.fixture
def redis(monkeypatch):
    store = Store()
    monkeypatch.setattr(bus, "sync_redis", lambda: store)
    return store


def fixture(source_id: str, start: datetime, status: str = "scheduled", home: str = ARS, away: str = CHE, score: tuple[int, int] | None = None) -> Fixture:
    result = {"home": {"id": home, "name": NAMES[home], "score": str(score[0])}, "away": {"id": away, "name": NAMES[away], "score": str(score[1])}} if score else None
    return Fixture(source_id=source_id, title=f"{NAMES[home]} vs {NAMES[away]}", competition="Premier League", start_utc=start, venue=None, status=status,
                   team_ids=[home, away], locator={"fixture": source_id}, team_names=[NAMES[home], NAMES[away]], result=result)


@pytest.fixture
def source(monkeypatch):
    """A made-up football source with a list of teams, each team's games and squad, and boards by a game's id."""
    on = SimpleNamespace(
        teams=[SourceTeam("pitch", ARS, "Arsenal", short="ARS", competition="Premier League", country="England"),
               SourceTeam("pitch", CHE, "Chelsea", short="CHE", competition="Premier League", country="England"),
               SourceTeam("pitch", TOT, "Tottenham Hotspur", short="TOT", competition="Premier League", country="England"),
               SourceTeam("pitch", "99", "Davis Cup Team", sport="tennis")],  # a sport whose teams aren't followed
        fixtures={}, squads={}, board=None, reads=[])

    def team_fixtures(team_id: str) -> list[Fixture]:
        on.reads.append(team_id)
        return list(on.fixtures.get(team_id, []))

    fake = SimpleNamespace(system="pitch", sports=("football", "tennis"), name="Pitch", fixture_pattern=DIGITS, team_id_pattern=DIGITS, live_cadence=15,
                           running=lambda: ("football", "tennis"), teams=lambda: list(on.teams), team_fixtures=team_fixtures, team=lambda team_id: on.squads.get(team_id),
                           result_label=result_label, snapshot=lambda locator, final=False: on.board, locator=lambda fixture: {"fixture": fixture},
                           credit=lambda snap: "Pitch", live_note=None, lag_note=None, budgets=lambda: (),
                           provider=Provider(name="Pitch", url="https://pitch.example", what="fixtures."))
    monkeypatch.setitem(ADAPTERS, "pitch", fake)
    monkeypatch.setitem(KNOWN, "pitch", fake)
    on.fake = fake
    return on


def team(db, external: str) -> Team:
    return db.scalars(select(Team).where(Team.system == "pitch", Team.external_id == external)).one()


@needs_db
def test_the_import_fills_the_registry_as_operators_decided(db, redis, source):
    assert teams.import_teams(source.fake, NOW) == 3  # not the tennis side
    arsenal = team(db, ARS)
    assert (arsenal.name, arsenal.sport, arsenal.short, arsenal.competitions, arsenal.country, arsenal.search_text) == ("Arsenal", "football", "ARS", ["Premier League"], "England", " arsenal | ars |")
    # Removed: its follows go, and no import writes it again; restored, the next one does.
    db.add(TeamFollow(fan_id="blue", team_id=team(db, CHE).id, alert_rules={}))
    db.commit()
    assert teams.remove(db, team(db, CHE)) == 1
    db.commit()
    teams.import_teams(source.fake, NOW)
    assert db.scalar(select(func.count()).select_from(Team).where(Team.external_id == CHE)) == 0 and db.scalar(select(func.count()).select_from(TeamFollow)) == 0
    assert teams.restore(db, "pitch", CHE)
    db.commit()
    teams.import_teams(source.fake, NOW)
    assert team(db, CHE).name == "Chelsea"
    # One the lists stop naming goes after a while, unless someone follows it or an operator kept it.
    source.teams = [t for t in source.teams if t.team_id == ARS]
    db.add(TeamFollow(fan_id="spurs", team_id=team(db, TOT).id, alert_rules={}))
    db.commit()
    teams.import_teams(source.fake, NOW + teams.STALE + timedelta(days=1))
    db.expire_all()
    assert sorted(t.external_id for t in db.scalars(select(Team))) == [ARS, TOT]
    # An empty list is the source failing, not every team gone.
    source.teams = []
    assert teams.import_teams(source.fake, NOW + 2 * teams.STALE) == 0
    assert db.scalar(select(func.count()).select_from(Team)) == 2


@needs_db
def test_search_finds_a_team_by_its_name_or_code(db, redis, source):
    teams.import_teams(source.fake, NOW)
    assert [t["name"] for t in teams.find(db, "arse")] == ["Arsenal"]
    assert [t["name"] for t in teams.find(db, "che")] == ["Chelsea"]
    assert [t["name"] for t in teams.find(db, "tot hot")] == ["Tottenham Hotspur"]
    [found] = teams.find(db, "tottenham", fan="spurs")
    assert (found["detail"], found["following"], found["followers"], found["next"]) == ("Premier League · England", False, 0, None)
    db.add(TeamFollow(fan_id="spurs", team_id=found["team_id"], alert_rules={}))
    db.commit()
    assert teams.find(db, "tottenham", fan="spurs")[0]["following"] is True
    assert teams.find(db, "arsenal", sport="cricket") == [] and teams.find(db, "a") == []
    # A team whose source isn't running isn't offered: its games couldn't be read.
    del ADAPTERS["pitch"]
    assert teams.find(db, "arsenal") == []


@needs_db
def test_following_a_team_reads_its_games_and_has_them_polled(db, redis, source, monkeypatch):
    monkeypatch.setattr(teams, "_now", lambda: NOW)
    monkeypatch.setattr(structured, "_now", lambda: NOW)
    teams.import_teams(source.fake, NOW)
    arsenal = team(db, ARS)
    source.fixtures[ARS] = [fixture("50", NOW - timedelta(days=7), "final", ARS, TOT, (2, 0)), fixture("51", NOW - timedelta(days=3), "final", CHE, ARS, (1, 1)),
                            fixture("100", NOW + timedelta(minutes=20)), fixture("101", NOW + timedelta(days=4), home=TOT, away=ARS)]
    source.squads[ARS] = SourceTeam("pitch", ARS, "Arsenal", players=[PlayerRef("pitch", "7", "Bukayo Saka", [ARS], ["Arsenal"])])
    with pytest.raises(teams.Refused) as nothing:
        teams.follow(db, "gooner", 9999)
    assert nothing.value.status == 404
    teams.follow(db, "gooner", arsenal.id)
    teams.follow(db, "gooner", arsenal.id)  # once
    db.commit()
    assert db.scalar(select(func.count()).select_from(TeamFollow)) == 1
    assert pipeline.due_work(NOW).teams == [arsenal.id]  # never read
    teams.refresh(arsenal.id)
    db.expire_all()
    shown = teams.page(db, arsenal.id, "gooner")
    assert shown is not None
    assert [(g["title"], g["versus"], g["match"]) for g in shown["upcoming"]] == [("Arsenal vs Chelsea", "Chelsea", "pitch/100"), ("Tottenham Hotspur vs Arsenal", "Tottenham Hotspur", "pitch/101")]
    assert [(r["result"], r["versus"]) for r in shown["recent"]] == [("Drew 1–1", "Chelsea"), ("Won 2–0", "Tottenham Hotspur")] and shown["form"] == ["D", "W"]
    assert [(p["name"], p["system"], p["athlete_id"]) for p in shown["squad"]] == [("Bukayo Saka", "pitch", "7")]
    assert (shown["following"], shown["alerts"], shown["followers"], shown["can_follow"], shown["source"]) == (True, "key", 1, True, "Pitch")
    events = {e.key: e for e in db.scalars(select(Event))}
    assert set(events) == {"pitch:50", "pitch:51", "pitch:100", "pitch:101"} and events["pitch:100"].participants == [ARS, CHE] and events["pitch:100"].sport == "Football"
    assert db.scalar(select(func.count()).select_from(EventTeam).where(EventTeam.team_id == arsenal.id)) == 4
    # Read now: not due again until it's stale; its game to come is armed at its sport's lead and polled though
    # nobody follows a player in it, the one in four days isn't.
    due = pipeline.due_work(NOW)
    assert due.teams == [] and due.polls == [events["pitch:100"].id]
    assert pipeline.due_work(NOW + timedelta(hours=1)).teams == [arsenal.id]
    # A read the source answers with nothing doesn't empty the page: its results are kept, and its games still to come.
    source.fixtures[ARS] = []
    monkeypatch.setattr(teams, "_now", lambda: NOW + timedelta(days=1))
    teams.refresh(arsenal.id)
    db.expire_all()
    again = teams.page(db, arsenal.id, "gooner")
    assert again is not None and [r["result"] for r in again["recent"]] == ["Drew 1–1", "Won 2–0"] and [g["match"] for g in again["upcoming"]] == ["pitch/101"]
    [mine] = teams.mine(db, "gooner")
    assert (mine["name"], mine["alerts"], mine["next"]["title"], mine["last"]["result"], mine["live"]) == ("Arsenal", "key", "Tottenham Hotspur vs Arsenal", "Drew 1–1", None)
    assert teams.set_level(db, "gooner", arsenal.id, "results") and not teams.set_level(db, "someone", arsenal.id, "off")
    teams.unfollow(db, "gooner", arsenal.id)
    db.commit()
    assert pipeline.due_work(NOW).polls == [] and teams.mine(db, "gooner") == []


@needs_db
def test_a_fan_follows_at_most_max_teams_and_only_ones_whose_games_can_be_read(db, redis, source, monkeypatch):
    teams.import_teams(source.fake, NOW)
    monkeypatch.setattr(teams, "MAX", 2)
    teams.follow(db, "fan", team(db, ARS).id)
    teams.follow(db, "fan", team(db, CHE).id)
    with pytest.raises(teams.Refused) as full:
        teams.follow(db, "fan", team(db, TOT).id)
    assert full.value.status == 429
    db.rollback()
    monkeypatch.delattr(source.fake, "team_fixtures")
    with pytest.raises(teams.Refused) as cant:
        teams.follow(db, "other", team(db, TOT).id)
    assert (cant.value.status, cant.value.message) == (409, teams.NO_SOURCE)
    db.rollback()
    monkeypatch.setattr(sports, "ENABLED", [s for s in sports.ALL if s.key != "football"])
    with pytest.raises(teams.Refused) as off:
        teams.follow(db, "other", team(db, TOT).id)
    assert off.value.status == 404 and teams.page(db, team(db, TOT).id, "other") is None


def board(home: int, away: int, clock: str, events: list[dict], saka: int | None = None, status: str = "live") -> Snapshot:
    lines = {"7": Line(headline=f"{saka} goal", stats=[{"label": "Goals", "value": str(saka)}])} if saka is not None else {}
    return Snapshot(status, f"Arsenal {home} – {away} Chelsea", clock, football(home, away), lines=lines, moments=events)


def goal(clock: str, who: str, athlete: str, side: str = ARS) -> dict:
    return {"clock": clock, "kind": "GOAL", "text": f"{who} ({NAMES[side]})", "athlete_ids": [athlete], "team_id": side, "own_goal": False}


@needs_db
def test_a_followed_teams_games_tell_its_fans_their_moments_and_its_result_from_its_side(db, redis, source, monkeypatch):
    pushed: list[tuple[str, str]] = []
    monkeypatch.setattr(structured, "_polled", lambda event_id, cadence: True)  # polled all along
    monkeypatch.setattr(notify, "dispatch", notify.send)  # at once, not in the background
    monkeypatch.setattr(notify, "push", lambda endpoint, p256dh, auth, payload, ttl, topic=None: pushed.append((endpoint.rsplit("/", 1)[1], payload["title"])))
    monkeypatch.setattr(notify.bus, "publish", lambda player_id, event: None)
    monkeypatch.setattr(structured.bus, "publish_match", lambda event_id, event: None)
    toasts: list[dict] = []
    monkeypatch.setattr(notify.bus, "publish_match", lambda event_id, event: toasts.append(event["moment"]) if "moment" in event else None)

    def heard() -> set[tuple[str, str]]:
        out = set(pushed)
        pushed.clear()
        return out

    teams.import_teams(source.fake, NOW)
    source.fixtures[ARS] = [fixture("100", NOW - timedelta(minutes=5), "live")]
    source.fixtures[CHE] = [fixture("100", NOW - timedelta(minutes=5), "live")]
    arsenal, chelsea = team(db, ARS), team(db, CHE)
    # gooner follows Arsenal, blue Chelsea, sides each, saka Arsenal and Saka; quiet Arsenal at results only; half
    # Chelsea, and Arsenal set to off. match follows the game by itself, and neither team.
    for fan, followed in (("gooner", [arsenal]), ("blue", [chelsea]), ("sides", [arsenal, chelsea]), ("saka", [arsenal]), ("quiet", [arsenal]), ("half", [arsenal, chelsea])):
        for t in followed:
            teams.follow(db, fan, t.id)
    teams.set_level(db, "quiet", arsenal.id, "results")
    teams.set_level(db, "half", arsenal.id, "off")
    db.commit()
    teams.refresh(arsenal.id)
    teams.refresh(chelsea.id)
    event = db.scalars(select(Event).where(Event.key == "pitch:100")).one()
    db.add_all([Player(id=5, slug="saka", name="Bukayo Saka", sport="Football"), PlayerIdentity(player_id=5, system="pitch", external_id="7", confidence=1.0)])
    db.flush()
    db.add_all([EventPlayer(event_id=event.id, player_id=5), Follow(fan_id="saka", player_id=5, alert_rules={}), MatchFollow(fan_id="match", event_id=event.id, alert_rules={}, keys=[event.key])])
    db.add_all([PushSubscription(fan_id=f, endpoint=f"https://fcm.googleapis.com/fcm/send/{f}", p256dh="k", auth="a") for f in ("gooner", "blue", "sides", "saka", "quiet", "half", "match")])
    db.commit()
    binding = {"adapter": "pitch", "locator": {"fixture": "100"}, "cadence": 15}
    assert event.live_binding == binding

    def poll(s: Snapshot) -> None:
        structured.apply_poll(event.id, binding, source.fake, [(5, "7")], s)

    poll(board(0, 0, "10'", [], 0))
    assert heard() == set()  # a first look
    # The fan stream carries a team's games on now and soon: this one, live, for its fans.
    assert teams.event_ids(db, "gooner") == [event.id] and teams.event_ids(db, "match") == []
    saka = goal("23'", "Saka", "7")
    poll(board(1, 0, "23'", [saka], 1))
    goal_23 = "Arsenal v Chelsea: Saka puts Arsenal ahead"
    assert heard() == {("gooner", goal_23), ("blue", goal_23), ("sides", goal_23), ("half", goal_23), ("match", goal_23), ("saka", "Goal: Bukayo Saka puts Arsenal ahead")}
    db.add(NotificationMute(fan_id="blue", event_id=event.id))  # "Mute this game": the rest of it isn't theirs
    db.commit()
    poll(board(1, 1, "60'", [goal("60'", "Palmer", "20", CHE), saka], 1))
    assert heard() == {(f, "Arsenal v Chelsea: Palmer equalises") for f in ("gooner", "sides", "saka", "half", "match")}
    poll(board(2, 1, "88'", [goal("88'", "Saka", "7"), goal("60'", "Palmer", "20", CHE), saka], 2))
    heard()
    poll(board(2, 1, "FT", [goal("88'", "Saka", "7"), goal("60'", "Palmer", "20", CHE), saka], 2, status="final"))
    assert heard() == {("gooner", "Arsenal won 2–1 v Chelsea"), ("sides", "Arsenal won 2–1 v Chelsea"), ("saka", "Bukayo Saka: Arsenal 2 – 1 Chelsea"), ("quiet", "Arsenal won 2–1 v Chelsea"),
                       ("half", "Chelsea lost 1–2 v Arsenal"), ("match", "Arsenal v Chelsea: Arsenal won")}  # half set Arsenal to off: Chelsea's side
    # The app's toasts, on the game's channel: the result from each side says whose, and the game's own whose sides
    # tell it too, so the app shows each fan theirs, as the push did.
    finals = {m["title"]: m for m in toasts if m["kind"] == "final"}
    assert finals["Arsenal won 2–1 v Chelsea"]["sides"] == [f"team:{arsenal.id}"] and finals["Chelsea lost 1–2 v Arsenal"]["sides"] == [f"team:{chelsea.id}"]
    assert finals["Arsenal v Chelsea: Arsenal won"]["told_by"] == sorted([f"team:{arsenal.id}", f"team:{chelsea.id}"])
    goal_told = next(m for m in toasts if m["kind"] == "goal")
    assert "sides" not in goal_told and "told_by" not in goal_told
    assert goal_told["teams"] == [f"team:{arsenal.id}", f"team:{chelsea.id}"]  # whose fans follow the game: the app's level for it
    assert teams.event_ids(db, "gooner") == []  # over: off the fan stream
    # The alerts list: the game's own moments since the follow, the result from their side.
    assert [i["title"] for i in server.activity(None, 50, "gooner", db)["items"]][:2] == ["Arsenal won 2–1 v Chelsea", "Arsenal v Chelsea: Late goal: Saka puts Arsenal ahead"]
    blue = [i["title"] for i in server.activity(None, 50, "blue", db)["items"]]
    assert blue[0] == "Chelsea lost 1–2 v Arsenal" and "Arsenal v Chelsea: Arsenal won" not in blue
    assert "Arsenal won 2–1 v Chelsea" not in [i["title"] for i in server.activity(None, 50, "quiet", db)["items"]][1:]  # results only: just the result
    # Following the game by itself, neither team: the game's own result, not a side's.
    match_only = [i["title"] for i in server.activity(None, 50, "match", db)["items"]]
    assert match_only[0] == "Arsenal v Chelsea: Arsenal won" and not {"Arsenal won 2–1 v Chelsea", "Chelsea lost 1–2 v Arsenal"} & set(match_only)
    # Both sides followed: the result once, as the push said it.
    finals = [i["title"] for i in server.activity(None, 50, "sides", db)["items"] if i["kind"] == "final"]
    assert finals == ["Arsenal won 2–1 v Chelsea"]
    assert [i["title"] for i in server.activity(None, 50, "half", db)["items"] if i["kind"] == "final"] == ["Chelsea lost 1–2 v Arsenal"]
    assert all(n.player_id is None or n.fan_id == "saka" for n in db.scalars(select(Notification)))


@needs_db
def test_the_fan_stream_carries_a_teams_games_from_a_day_and_a_half_before_they_start(db, redis, source, monkeypatch):
    monkeypatch.setattr(teams, "_now", lambda: NOW)
    monkeypatch.setattr(structured, "_now", lambda: NOW)
    teams.import_teams(source.fake, NOW)
    source.fixtures[ARS] = [fixture("100", NOW + timedelta(hours=35)), fixture("101", NOW + timedelta(hours=37), home=TOT, away=ARS),
                            fixture("50", NOW - timedelta(days=2), "final", ARS, TOT, (1, 0))]
    arsenal = team(db, ARS)
    teams.follow(db, "gooner", arsenal.id)
    db.commit()
    teams.refresh(arsenal.id)
    key = {e.key: e.id for e in db.scalars(select(Event))}
    assert teams.event_ids(db, "gooner", NOW) == [key["pitch:100"]]  # not the one in 37 hours, nor last week's
    assert teams.event_ids(db, "gooner", NOW + timedelta(hours=2)) == sorted([key["pitch:100"], key["pitch:101"]])
    assert teams.event_ids(db, "someone", NOW) == []

    # And the stream subscribes to those games' channels, with the fan's teams in its first frame.
    subscribed: list[list[str]] = []
    monkeypatch.setattr(server, "_stream", lambda channels, first, request: subscribed.append(channels) or "stream")
    monkeypatch.setattr(server, "_sse", lambda body, places=None: body)
    monkeypatch.setattr(server, "streaming", server.Counter())  # the stream's places, given back by its answer
    assert asyncio.run(server.my_stream(SimpleNamespace(client=None), fan="gooner")) == "stream"
    assert subscribed == [[f"match:{key['pitch:100']}"]]


@needs_db
def test_a_match_page_offers_its_teams_and_today_has_a_followed_teams_games(db, redis, source, monkeypatch):
    monkeypatch.setattr(teams, "_now", lambda: NOW)
    monkeypatch.setattr(structured, "_now", lambda: NOW)
    teams.import_teams(source.fake, NOW)
    arsenal, chelsea = team(db, ARS), team(db, CHE)
    source.fixtures[ARS] = [fixture("100", NOW + timedelta(days=2)), fixture("50", NOW - timedelta(days=1), "final", TOT, ARS, (0, 3))]
    teams.follow(db, "gooner", arsenal.id)
    db.commit()
    teams.refresh(arsenal.id)
    db.expire_all()
    event = db.scalars(select(Event).where(Event.key == "pitch:100")).one()
    # Before kick-off, from its fixture's sides (no board yet), in the game's order, Arsenal followed.
    offered = teams.in_game(db, "gooner", "pitch", {}, [event.id])
    assert [(t["name"], t["following"], t["can_follow"]) for t in offered] == [("Arsenal", True, True), ("Chelsea", False, True)]
    # Once it has a board, from its sides there; a side the registry doesn't have isn't offered.
    board = {"kind": "football", "home": {"id": CHE, "name": "Chelsea", "score": "0"}, "away": {"id": "999", "name": "Nobody FC", "score": "0"}}
    assert [t["team_id"] for t in teams.in_game(db, None, "pitch", board, [])] == [chelsea.id]
    stranger = teams.in_game(db, "someone", "pitch", {}, [event.id])
    assert not any(t["following"] for t in stranger) and len(stranger) == 2
    # A sport whose teams can't be followed now isn't offered.
    monkeypatch.setattr(sports, "ENABLED", [x for x in sports.ALL if x.key != "football"])
    assert teams.in_game(db, "gooner", "pitch", {}, [event.id]) == []
    monkeypatch.setattr(sports, "ENABLED", list(sports.ALL))
    # Following lists its games to come, which Today's Coming up shows, and its last result, Today's Results.
    [mine] = teams.mine(db, "gooner")
    assert [(u["title"], u["versus"]) for u in mine["upcoming"]] == [("Arsenal vs Chelsea", "Chelsea")]
    assert (mine["last"]["result"], mine["last"]["versus"]) == ("Won 3–0", "Tottenham Hotspur")


@needs_db
def test_a_match_page_tells_apart_two_teams_its_source_numbers_alike(db, redis, source, monkeypatch):
    # A source whose team ids carry more than its boards' (ESPN basketball's carry the league): "5" on a board is
    # the Cavaliers in one league and the Fever in the other, told apart by the side's name, or the game's title.
    monkeypatch.setattr(source.fake, "board_id", lambda team_id: team_id.rpartition(":")[2], raising=False)
    for team_id, name in (("nba:5", "Cleveland Cavaliers"), ("wnba:5", "Indiana Fever"), ("wnba:17", "Las Vegas Aces")):
        teams.upsert(db, "pitch", SourceTeam("pitch", team_id, name), "basketball", NOW)
    db.commit()
    board = {"kind": "basketball", "home": {"id": "5", "name": "Indiana Fever", "score": "80"}, "away": {"id": "17", "name": "Las Vegas Aces", "score": "90"}}
    assert [t["name"] for t in teams.in_game(db, None, "pitch", board, [])] == ["Indiana Fever", "Las Vegas Aces"]
    db.add(Event(key="pitch:777", sport="Basketball", title="Indiana Fever at Las Vegas Aces", participants=["5", "17"], status="scheduled"))
    db.commit()
    event_id = db.scalar(select(Event.id).where(Event.key == "pitch:777"))
    assert [t["name"] for t in teams.in_game(db, None, "pitch", {}, [event_id])] == ["Indiana Fever", "Las Vegas Aces"]  # before tip-off, by its title
    nameless = {"kind": "basketball", "home": {"id": "5", "score": "80"}, "away": {"id": "17", "name": "Las Vegas Aces", "score": "90"}}
    assert [t["name"] for t in teams.in_game(db, None, "pitch", nameless, [])] == ["Las Vegas Aces"]  # one it can't tell isn't offered


@needs_db
def test_a_spent_lane_share_is_no_failure_and_leaves_the_team_due_at_the_next_tick(db, redis, source, monkeypatch):
    # BUG-174: our own per-lane limit isn't the source failing: the team isn't waited on (structured.retry_at).
    monkeypatch.setattr(teams, "_now", lambda: NOW)
    teams.import_teams(source.fake, NOW)
    arsenal = team(db, ARS)
    teams.follow(db, "gooner", arsenal.id)
    db.commit()

    def spent(team_id: str) -> list[Fixture]:
        raise budget.RateLimited("pitch: the refresh share of its 10 requests a minute is spent")

    source.fake.team_fixtures = spent
    teams.refresh(arsenal.id)
    teams.refresh(arsenal.id)
    db.expire_all()
    assert team(db, ARS).health in (None, {}) and pipeline.due_work(NOW).teams == [arsenal.id]
    # A source that fails is waited on.
    source.fake.team_fixtures = lambda team_id: (_ for _ in ()).throw(AdapterError("Pitch returned HTTP 502"))
    teams.refresh(arsenal.id)
    db.expire_all()
    assert team(db, ARS).health["failures"] == 1 and pipeline.due_work(NOW).teams == []


@needs_db
def test_a_followed_teams_game_ending_has_its_games_read_again_at_the_next_tick(db, redis, source, monkeypatch):
    monkeypatch.setattr(teams, "_now", lambda: NOW)
    monkeypatch.setattr(structured, "_now", lambda: NOW)
    teams.import_teams(source.fake, NOW)
    source.fixtures[ARS] = [fixture("100", NOW - timedelta(minutes=100), "live")]
    source.fixtures[TOT] = [fixture("100", NOW - timedelta(minutes=100), "live")]
    arsenal, spurs = team(db, ARS), team(db, TOT)
    teams.follow(db, "gooner", arsenal.id)
    db.commit()
    teams.refresh(arsenal.id)
    teams.refresh(spurs.id)  # read by a page, followed by nobody
    event_id = db.scalar(select(Event.id).where(Event.key == "pitch:100"))
    assert event_id is not None and pipeline.due_work(NOW).teams == []
    teams.ended(db, event_id)
    db.commit()
    db.expire_all()
    assert (team(db, ARS).fetched_at, team(db, TOT).fetched_at is not None) == (None, True)
    assert pipeline.due_work(NOW).teams == [arsenal.id]
    # That read's schedule is the cache's, which still has the game on, with no result: it isn't kept blank, and
    # once the poll that saw it finish has its board, the result is that board's, from Arsenal's side.
    db.execute(update(Event).where(Event.id == event_id).values(status="final"))
    db.commit()
    teams.refresh(arsenal.id)
    db.expire_all()
    assert teams.mine(db, "gooner")[0]["last"] is None
    db.add(EventState(event_id=event_id, as_of=NOW, status="final", score_label="Arsenal 2 – 1 Chelsea", clock_label="FT", state=football(2, 1), source_url=None))
    db.commit()
    teams.refresh(arsenal.id)
    db.expire_all()
    assert teams.mine(db, "gooner")[0]["last"]["result"] == "Won 2–1"


@needs_db
def test_a_fans_team_follows_move_with_their_account_and_go_with_it(db, redis, source, monkeypatch):
    teams.import_teams(source.fake, NOW)
    monkeypatch.setattr(teams, "MAX", 2)
    db.add(Account(id=1, email="fan@example.com", fan_id="account"))
    db.add_all([TeamFollow(fan_id="account", team_id=team(db, ARS).id, alert_rules={"level": "everything"}),
                TeamFollow(fan_id="phone", team_id=team(db, ARS).id, alert_rules={}), TeamFollow(fan_id="phone", team_id=team(db, CHE).id, alert_rules={}),
                TeamFollow(fan_id="phone", team_id=team(db, TOT).id, alert_rules={})])
    db.commit()
    teams.merge(db, "phone", "account")
    db.commit()
    rows = {(r.team_id, (r.alert_rules or {}).get("level")) for r in db.scalars(select(TeamFollow).where(TeamFollow.fan_id == "account"))}
    assert rows == {(team(db, ARS).id, "everything"), (team(db, CHE).id, None)}  # the account's level kept; within MAX
    assert db.scalar(select(func.count()).select_from(TeamFollow).where(TeamFollow.fan_id == "phone")) == 0
    assert accounts.delete_account(db, 1) is not None
    db.commit()
    assert db.scalar(select(func.count()).select_from(TeamFollow)) == 0


@needs_db
def test_the_api_offers_teams_in_search_and_follows_them(db, redis, source, monkeypatch):
    queued: list[tuple] = []

    async def enqueue(function: str, *args, **options) -> None:
        queued.append((function, *args))

    class Counts(dict):
        async def incr(self, key: str) -> int:
            self[key] = self.get(key, 0) + 1
            return self[key]

        async def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
            return True

    monkeypatch.setattr(server, "enqueue", enqueue)
    monkeypatch.setitem(server.state, "redis", Counts())
    teams.import_teams(source.fake, NOW)
    arsenal = team(db, ARS).id
    found = server._search("arsenal", None, "gooner", lambda: False)  # pyright: ignore[reportPrivateUsage]
    assert [t["name"] for t in found["teams"]] == ["Arsenal"]
    shown = asyncio.run(server.team_page(arsenal, "gooner"))
    assert (shown["name"], shown["following"], shown["refreshing"]) == ("Arsenal", False, True) and queued == [("refresh_team", arsenal)]
    assert asyncio.run(server.team_page(arsenal, "gooner"))["refreshing"] is False  # asked once a while
    # And pages and follows ask at most teams.READS reads a minute of a source, whichever teams: a crawler, or
    # following and unfollowing team after team, spends no more; a followed team is read by the next tick anyway.
    monkeypatch.setattr(teams, "READS", 1)
    assert asyncio.run(server.team_page(team(db, CHE).id, "gooner"))["refreshing"] is False and len(queued) == 1
    asyncio.run(server.follow_team(team(db, CHE).id, "blue"))
    assert len(queued) == 1 and pipeline.due_work(NOW).teams == [team(db, CHE).id]
    monkeypatch.setattr(teams, "READS", 6)
    assert asyncio.run(server.follow_team(arsenal, "gooner")) == {"team_id": arsenal, "following": True}
    assert queued[-1] == ("refresh_team", arsenal)  # its games now, not at the next tick
    assert server.set_team_alerts(arsenal, {"level": "results"}, "gooner", db) == {"team_id": arsenal, "alerts": "results"}
    [listed] = asyncio.run(server.following("gooner"))["teams"]
    assert (listed["team_id"], listed["alerts"]) == (arsenal, "results")
    assert server.unfollow_team(arsenal, "gooner", db) == {"team_id": arsenal, "following": False}
    assert asyncio.run(server.following("gooner"))["teams"] == []
    with pytest.raises(server.HTTPException) as nothing:
        asyncio.run(server.team_page(9999, "gooner"))
    assert nothing.value.status_code == 404
    with pytest.raises(server.HTTPException):
        server.set_team_alerts(arsenal, {"level": "loud"}, "gooner", db)


@needs_db
def test_removing_a_team_takes_its_links_and_an_operator_can_add_one_the_lists_lack(db, redis, source, monkeypatch):
    monkeypatch.setattr(teams, "_now", lambda: NOW)
    teams.import_teams(source.fake, NOW)
    source.fixtures[ARS] = [fixture("100", NOW + timedelta(days=1))]
    arsenal = team(db, ARS)
    teams.follow(db, "gooner", arsenal.id)
    db.commit()
    teams.refresh(arsenal.id)
    assert db.scalar(select(func.count()).select_from(EventTeam)) == 1
    teams.remove(db, team(db, ARS))
    db.commit()
    assert db.scalar(select(func.count()).select_from(EventTeam)) == 0 and db.scalar(select(func.count()).select_from(TeamFollow)) == 0
    assert db.scalar(select(func.count()).select_from(Event)) == 1  # the game is everyone's: it stays
    assert db.scalars(select(TeamOverride.action)).all() == ["remove"]
    # Added by hand: read from its source and kept, so no import drops it, and it takes back the removal.
    source.squads["77"] = SourceTeam("pitch", "77", "Arsenal Women", competition="WSL")
    added = teams.add(db, "pitch", "77")
    db.commit()
    assert (added.name, added.sport) == ("Arsenal Women", "football")
    teams.import_teams(source.fake, NOW + 2 * teams.STALE)
    assert db.scalar(select(Team.name).where(Team.external_id == "77")) == "Arsenal Women"


# ---------------------------------------------------------------- every player's moments, and the whole of the list

ESPN = Path(__file__).parent / "fixtures" / "espn"


def recorded(name: str, parse: Callable[[dict], Snapshot]) -> Snapshot:
    """A game as ESPN gave it (tests/fixtures/espn, recorded), read by its adapter."""
    with gzip.open(ESPN / f"{name}.json.gz", "rt") as f:
        return parse(json.load(f))


def followed_game(db, monkeypatch, fixture_id: str, sport: str, title: str, side: Team, level: str = "key") -> Event:
    """A game of `side`'s, followed by "game" by itself and by "team" through `side`, both at `level`, each with a
    phone; polled all along, its pushes sent at once and dropped, so what each fan was sent is its notification rows,
    and no live card goes anywhere."""
    monkeypatch.setattr(structured, "_polled", lambda event_id, cadence: True)
    monkeypatch.setattr(structured.livecards, "polled", lambda event, snap: None)
    monkeypatch.setattr(notify, "dispatch", notify.send)
    monkeypatch.setattr(notify, "push", lambda *args, **kwargs: None)
    monkeypatch.setattr(notify.bus, "publish", lambda player_id, event: None)
    monkeypatch.setattr(notify.bus, "publish_match", lambda event_id, event: None)
    monkeypatch.setattr(structured.bus, "publish_match", lambda event_id, event: None)
    event = Event(key=f"pitch:{fixture_id}", sport=sport, title=title, status="live", live_binding={"adapter": "pitch", "locator": {"fixture": fixture_id}, "cadence": 15})
    db.add_all([side, event])
    db.flush()
    db.add_all([EventTeam(event_id=event.id, team_id=side.id), TeamFollow(fan_id="team", team_id=side.id, alert_rules={"level": level}),
                MatchFollow(fan_id="game", event_id=event.id, alert_rules={"level": level}, keys=[event.key])])
    db.add_all([PushSubscription(fan_id=f, endpoint=f"https://fcm.googleapis.com/fcm/send/{f}", p256dh="k", auth="a") for f in ("game", "team")])
    db.commit()
    return event


def sent(db, fan: str) -> list[str]:
    """Every line the fan's pushes said, oldest first."""
    return [line for n in db.scalars(select(Notification).where(Notification.fan_id == fan).order_by(Notification.id)) for line in n.lines or []]


def batting(runs: str, balls: int, out: str | None = None) -> Line:
    return Line(headline=f"{runs} ({balls})", stats=[{"label": "Runs", "value": runs}, {"label": "Balls", "value": str(balls)}, *([{"label": "Out", "value": out}] if out else [])])


def bowling(wickets: int, conceded: int, overs: str) -> Line:
    return Line(headline=f"{wickets}/{conceded} ({overs} ov)", stats=[{"label": "Wickets", "value": str(wickets)}, {"label": "Overs", "value": overs}, {"label": "Runs conceded", "value": str(conceded)}])


@needs_db
def test_a_teams_and_a_games_fans_hear_every_wicket_fifty_and_haul_whoever_it_is(db, redis, source, monkeypatch):
    # West Indies v India, an ODI as ESPN gave it at the end (India won by 8 wickets), India's players' lines alone,
    # Prasidh Krishna with a third wicket: so two bowlers reach three in one poll, two batters are out a poll apart,
    # and two reach fifty and a hundred in one.
    final = recorded("cricket-summary-final", espn_cricket.parse_summary)
    rohit, gill, kohli, kuldeep, prasidh = "34102", "1070173", "253802", "559235", "917159"
    india = {a: line for a, line in final.lines.items() if a in {rohit, gill, kohli, kuldeep, prasidh, "1060380", "234675", "1287032", "1175496", "1287033"}}
    india[prasidh] = bowling(3, 60, "10")
    bowlers = {a: line for a, line in india.items() if a not in {rohit, gill, kohli, "1060380"}}
    event = followed_game(db, monkeypatch, "700", "Cricket", "West Indies v India", Team(name="India", sport="cricket", system="pitch", external_id="6", search_text="india"))
    live = {**final.state, "teams": [{k: v for k, v in t.items() if k != "winner"} for t in final.state["teams"]], "summary": ""}

    def poll(status: str, score: str, lines: dict[str, Line]) -> None:
        over = status == "final"
        structured.apply_poll(event.id, event.live_binding, source.fake, [], Snapshot(status, score, final.clock_label if over else "", final.state if over else live, lines=lines, names=final.names))

    poll("live", "WI 200/4 (40 ov)", {**bowlers, kuldeep: bowling(2, 30, "8"), prasidh: bowling(2, 45, "8")})  # a first look
    poll("live", "WI 240/6 (45 ov)", {**bowlers, kuldeep: bowling(3, 34, "9"), prasidh: bowling(3, 50, "9")})
    poll("live", "IND 60/1 (9 ov)", {**bowlers, rohit: batting("32", 35, "c Andrew b Joseph"), gill: batting("25*", 30), kohli: batting("3*", 4)})
    poll("final", final.score_label, india)

    said = ["India v West Indies: Kuldeep Yadav takes 3 wickets", "India v West Indies: Prasidh Krishna takes 3 wickets",
            "India v West Indies: Kuldeep Yadav takes 4 wickets", "India v West Indies: Rohit Sharma out for 32",
            "India v West Indies: Shubman Gill reaches 50", "India v West Indies: Shubman Gill reaches 100", "India v West Indies: Shubman Gill out for 110",
            "India v West Indies: Virat Kohli reaches 50", "India v West Indies: Virat Kohli reaches 100"]
    result, theirs = "India v West Indies: India won by 8 wkts (50b rem)", "India won by 8 wkts (50b rem) v West Indies"
    for fan, told in (("game", result), ("team", theirs)):
        assert sorted(sent(db, fan)) == sorted([*said, told])
        assert sorted(i["title"] for i in server.activity(None, 50, fan, db)["items"]) == sorted([*said, told])


@needs_db
def test_a_basketball_teams_and_games_fans_hear_every_double_double(db, redis, source, monkeypatch):
    # Indiana Fever at Las Vegas Aces as ESPN gave it at the end; a poll before, A'ja Wilson, Caitlin Clark and Jackie
    # Young short of the double-doubles they finished with, and of the 20 points two of them passed.
    final = recorded("basketball-summary-final", espn_basketball.parse_summary)
    wilson, clark, young = "3149391", "4433403", "4065870"
    event = followed_game(db, monkeypatch, "800", "Basketball", "Indiana Fever at Las Vegas Aces",
                          Team(name="Las Vegas Aces", sport="basketball", system="pitch", external_id="17", search_text="las vegas aces"), "everything")
    earlier = {wilson: "8 PTS · 6 REB · 2 AST", clark: "5 PTS · 3 REB · 4 AST", young: "9 PTS · 2 REB · 5 AST"}
    live = {**final.state, **{side: {k: v for k, v in final.state[side].items() if k != "winner"} for side in ("home", "away")}, "detail": "5:00 - 3rd"}
    before = {a: Line(headline=earlier[a], stats=line.stats) if a in earlier else line for a, line in final.lines.items()}
    structured.apply_poll(event.id, event.live_binding, source.fake, [], Snapshot("live", "Indiana Fever 60 – 70 Las Vegas Aces", "5:00 - 3rd", live, lines=before, names=final.names))
    structured.apply_poll(event.id, event.live_binding, source.fake, [], final)

    game = "Indiana Fever at Las Vegas Aces"
    said = [f"{game}: A'ja Wilson: 20 points", f"{game}: A'ja Wilson: 30 points", f"{game}: A'ja Wilson: double-double",
            f"{game}: Caitlin Clark: double-double", f"{game}: Jackie Young: 20 points", f"{game}: Jackie Young: double-double"]
    for fan, told in (("game", f"{game}: Las Vegas Aces won"), ("team", "Las Vegas Aces won 102–85 v Indiana Fever")):
        assert sorted(sent(db, fan)) == sorted([*said, told])
        assert sorted(i["title"] for i in server.activity(None, 50, fan, db)["items"]) == sorted([*said, told])


def pages(db, fan: str, limit: int) -> list[list[str]]:
    """The fan's activity list, page after page, each page's titles."""
    out, before = [], None
    while True:
        page = server.activity(before, limit, fan, db)
        out.append([i["title"] for i in page["items"]])
        if page["next"] is None:
            return out
        before = page["next"]


def ended(db, event: Event, goals: int, sides: list[Team], at: int) -> None:
    """A finished game's own moments as its polls stored them: its goals, the last few found in the poll that saw it
    end, between its own result (first) and the result from each followed side (last)."""
    def goal(n: int) -> sports.Found:
        return sports.Found("goal", "key", f"{event.title}: goal {n}", "", f"goal@{n}", 85, ("7",))

    notify.record_game(db, event.id, [goal(n) for n in range(1, at)], "/match/pitch/1")
    notify.record_game(db, event.id, [sports.Found("final", "key", f"{event.title}: Arsenal won", "", "", 80, ("*",)), *(goal(n) for n in range(at, goals + 1)),
                                      *(sports.Found("final", "key", f"{event.title}: {t.name}'s result", "", f"team{t.id}", 80, ("*", teams.tag(t.id))) for t in sides)],
                       "/match/pitch/1")


@needs_db
def test_a_teams_fan_pages_through_every_one_of_its_games_moments(db, redis, source):
    teams.import_teams(source.fake, NOW)
    arsenal, chelsea = team(db, ARS), team(db, CHE)
    games = [Event(key=f"pitch:{n}", sport="Football", title=f"Game {n}", status="final") for n in range(1, 5)]
    db.add_all(games)
    db.flush()
    db.add_all([EventTeam(event_id=e.id, team_id=t.id) for e in games for t in (arsenal, chelsea)])
    db.add(TeamFollow(fan_id="gooner", team_id=arsenal.id, alert_rules={}))
    db.commit()
    for e in games:
        ended(db, e, 10, [arsenal, chelsea], at=8)
    db.commit()
    # Four games of ten goals and a result from Arsenal's side, 44 in all, newest first, ten a page: the game's own
    # result and Chelsea's aren't theirs, wherever the page breaks.
    want = [t for e in reversed(games) for t in (f"{e.title}: Arsenal's result", *(f"{e.title}: goal {n}" for n in range(10, 0, -1)))]
    got = pages(db, "gooner", 10)
    assert [len(p) for p in got] == [10, 10, 10, 10, 4]
    assert [t for p in got for t in p] == want


@needs_db
def test_a_fan_of_both_sides_gets_full_pages_of_their_game(db, redis, source):
    teams.import_teams(source.fake, NOW)
    arsenal, chelsea = team(db, ARS), team(db, CHE)
    event = Event(key="pitch:1", sport="Football", title="Arsenal v Chelsea", status="final")
    db.add(event)
    db.flush()
    db.add_all([EventTeam(event_id=event.id, team_id=arsenal.id), EventTeam(event_id=event.id, team_id=chelsea.id)])
    db.add_all([TeamFollow(fan_id="sides", team_id=t.id, alert_rules={}) for t in (arsenal, chelsea)])
    # And one who follows Arsenal and the game itself.
    db.add_all([TeamFollow(fan_id="both", team_id=arsenal.id, alert_rules={}), MatchFollow(fan_id="both", event_id=event.id, alert_rules={}, keys=[event.key])])
    db.commit()
    ended(db, event, 20, [arsenal, chelsea], at=19)
    db.commit()
    # Twenty goals and the result once, from the first of their sides', ten a page.
    want = ["Arsenal v Chelsea: Arsenal's result", *(f"Arsenal v Chelsea: goal {n}" for n in range(20, 0, -1))]
    for fan in ("sides", "both"):
        got = pages(db, fan, 10)
        assert [len(p) for p in got] == [10, 10, 1]
        assert [t for p in got for t in p] == want
