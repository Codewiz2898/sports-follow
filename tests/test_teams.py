"""Following a team (teams.py, BHA-77): each sport's word for a result from one side (Sport.side_final), the noise
rules for a followed team's games (alerts.plan), and the path from the registry to a push, against a throwaway
database at alembic head (skipped without one; see tests/test_players.py for setting it up): an import fills the
registry as operators decided, search finds its teams, a follow reads the team's games and has them polled though
nobody follows a player in them, their moments reach the team's fans, and the result is told from each one's
side. Sources and Redis are made up here."""

from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import accounts, bus, notify, pipeline, server, sports, structured, teams
from sports_follow.adapters import ADAPTERS, KNOWN, Fixture, Line, PlayerRef, Provider, Snapshot, SourceTeam
from sports_follow.adapters.base import DIGITS
from sports_follow.alerts import Item, Watch, plan
from sports_follow.models import (Account, Event, EventPlayer, EventTeam, Follow, MatchFollow, Notification, NotificationMute, Player, PlayerIdentity,
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
    monkeypatch.setattr(server, "_sse", lambda body: body)
    assert asyncio.run(server.my_stream(SimpleNamespace(), fan="gooner")) == "stream"
    assert subscribed == [[f"match:{key['pitch:100']}"]]


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
