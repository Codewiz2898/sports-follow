"""Following a game by itself (matches.py, BHA-270): each sport's view of a whole game (Sport.game_moments),
the noise rules for a followed game (alerts.plan), and the path from a follow to a push, against a
throwaway database at alembic head (skipped without one; see tests/test_players.py for setting it up): a
follow makes the game's event, the tick polls it though nobody follows a player in it, its own moments
reach its fans, once for a fan who follows a player in it too, and stop when it ends. Sources and Redis
are made up here."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import accounts, bus, controls, live, livecards, match, matches, metrics, notify, pipeline, server, sports, structured
from sports_follow import db as db_module
from sports_follow.adapters import ADAPTERS, KNOWN, Fixture, Line, LiveGame, Snapshot, budget, lichess_chess
from sports_follow.adapters.base import DIGITS
from sports_follow.alerts import Item, Watch, plan
from sports_follow.models import (Account, Event, EventPlayer, EventState, Follow, MatchFollow, Moment, Notification, NotificationMute, Player, PlayerCard, PlayerIdentity,
                                  PushSubscription, SourceBinding)
from sports_follow.sports.base import Played

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime(2026, 10, 17, 14, tzinfo=timezone.utc)
HOME, AWAY = "1", "2"


def football(home: int, away: int) -> dict:
    return {"kind": "football", "home": {"id": HOME, "name": "Arsenal", "score": str(home)}, "away": {"id": AWAY, "name": "Chelsea", "score": str(away)}}


def goal(clock: str, who: str, athlete: str, team: str = HOME, **more) -> dict:
    side = "Arsenal" if team == HOME else "Chelsea"
    return {"clock": clock, "kind": "GOAL", "text": f"{who} ({side})", "athlete_ids": [athlete], "team_id": team, "own_goal": False, **more}


# ---------------------------------------------------------------- each sport's view of a whole game


def test_football_tells_every_goal_and_card_whoevers_with_what_it_did():
    rules = sports.get("football")
    before = Played("live", football(0, 0), "Arsenal 0 – 0 Chelsea", "22'", [])
    saka = goal("23'", "Saka", "7", assist="Ødegaard")
    now = Played("live", football(1, 0), "Arsenal 1 – 0 Chelsea", "23'", [saka])
    [found] = rules.game_moments("Arsenal v Chelsea", before, now, [])
    assert (found.kind, found.level, found.title, found.body, found.detail, found.about, found.priority) == (
        "goal", "key", "Arsenal v Chelsea: Saka puts Arsenal ahead", "Arsenal 1 – 0 Chelsea · 23' · assist Ødegaard", "goal@23'", ("7",), 90)
    # A card, and a late equaliser that's an own goal: each about the player it names, the yellow minor.
    later = [goal("88'", "Gabriel", "4", own_goal=True), {"clock": "60'", "kind": "YELLOW", "text": "Rice (Arsenal)", "athlete_ids": ["41"], "team_id": HOME}, saka]
    found = rules.game_moments("Arsenal v Chelsea", now, Played("live", football(1, 1), "Arsenal 1 – 1 Chelsea", "88'", later), [("goal", "goal@23'")])
    assert [(f.kind, f.level, f.title, f.about) for f in found] == [
        ("yellow", "minor", "Arsenal v Chelsea: Yellow card for Rice (Arsenal)", ("41",)), ("goal", "key", "Arsenal v Chelsea: Own goal by Gabriel", ("og:4",))]  # not one of his goals


def test_football_corrects_a_goal_told_that_the_source_rules_out():
    rules = sports.get("football")
    told = [("goal", "goal@23'")]
    was = Played("live", football(1, 0), "Arsenal 1 – 0 Chelsea", "24'", [goal("23'", "Saka", "7")])
    ruled_out = {**goal("23'", "Saka", "7"), "kind": "DISALLOWED"}
    [found] = rules.game_moments("Arsenal v Chelsea", was, Played("live", football(0, 0), "Arsenal 0 – 0 Chelsea", "26'", [ruled_out]), told)
    assert (found.kind, found.title, found.detail) == ("correction", "Arsenal v Chelsea: Goal ruled out, Saka", "goal:goal@23'")
    assert rules.game_moments("Arsenal v Chelsea", was, Played("live", football(0, 0), "", "27'", [ruled_out]), [*told, ("correction", "goal:goal@23'")]) == []


def test_a_first_look_tells_only_the_result():
    rules = sports.get("football")
    on = Played("live", football(2, 0), "Arsenal 2 – 0 Chelsea", "61'", [goal("23'", "Saka", "7"), goal("50'", "Kane", "9")])
    assert rules.game_moments("Arsenal v Chelsea", None, on, []) == []
    [final] = rules.game_moments("Arsenal v Chelsea", None, Played("final", football(2, 0), "Arsenal 2 – 0 Chelsea", "FT", on.events), [])
    assert (final.kind, final.title, final.body, final.about) == ("final", "Arsenal v Chelsea: Arsenal won", "Arsenal 2 – 0 Chelsea", ("*",))
    [start] = rules.game_moments("Arsenal v Chelsea", Played("scheduled", football(0, 0)), Played("live", football(0, 0), "Arsenal 0 – 0 Chelsea", "1'"), [])
    assert (start.kind, start.title, start.about) == ("start", "Arsenal v Chelsea: under way", ("*",))


def line(**stats: str) -> dict:
    return {"headline": "", "stats": [{"label": k.replace("_", " ").capitalize(), "value": v} for k, v in stats.items()]}


def test_cricket_tells_every_named_players_wickets_and_fifties_in_their_own_words():
    rules = sports.get("cricket")
    names = {"253802": "Virat Kohli", "1": "Pat Cummins"}
    before = Played("live", {"kind": "cricket"}, "IND 180/2", "", lines={"253802": line(Runs="48*", Balls="40"), "1": line(Wickets="2", Overs="8", Runs_conceded="40")}, names=names)
    now = Played("live", {"kind": "cricket"}, "IND 214/3", "", lines={
        "253802": {"headline": "82 (61)", "stats": [{"label": "Runs", "value": "82"}, {"label": "Balls", "value": "61"}, {"label": "Out", "value": "c Head b Cummins"}]},
        "1": line(Wickets="3", Overs="9", Runs_conceded="44"), "2": line(Runs="51*", Balls="30")}, names=names)
    found = rules.game_moments("India v Australia", before, now, [])
    assert [(f.kind, f.title, f.about) for f in found] == [
        ("fifty", "India v Australia: Virat Kohli reaches 50", ("253802",)), ("out", "India v Australia: Virat Kohli out for 82", ("253802",)),
        ("wickets", "India v Australia: Pat Cummins takes 3 wickets", ("1",))]  # a line of someone unnamed isn't told
    # The first lines of a game under way say what happened before: nothing from them.
    assert rules.game_moments("India v Australia", Played("live", {"kind": "cricket"}, names=names), now, []) == []


def test_chess_tells_the_board_turning_once_from_the_side_it_moved_toward():
    rules = sports.get("chess")

    def board(band: int, told: int = 0) -> Played:
        state = {"kind": "chess", "game_id": "g1", "white": {"name": "Gukesh D", "id": "46616543"}, "black": {"name": "Magnus Carlsen", "id": "1503014"},
                 "eval": {"cp": band * 200, "band": band, "raw": band, "told": told, "move": 31}}
        return Played("live", state)

    [found] = rules.game_moments("Gukesh v Carlsen", board(0), board(-2, -2), [])
    assert (found.kind, found.level, found.title, found.about) == ("swing", "key", "Gukesh v Carlsen: Magnus Carlsen is winning", ("46616543", "1503014"))
    [level] = rules.game_moments("Gukesh v Carlsen", board(-2, -2), board(0), [])
    assert level.title == "Gukesh v Carlsen: Gukesh D is back level"


def test_esports_tells_each_map_and_each_lobby_match_by_who_took_it():
    rules = sports.get("cs2")
    teams = lambda a, b: [{"id": "1", "name": "Vitality", "score": a}, {"id": "2", "name": "FaZe", "score": b}]  # noqa: E731
    before = Played("live", {"kind": "esports", "teams": teams(0, 0), "maps": [{"n": 1, "winner": None}]})
    now = Played("live", {"kind": "esports", "teams": teams(0, 1), "maps": [{"n": 1, "winner": "2"}, {"n": 2, "winner": None}]})
    [found] = rules.game_moments("Vitality v FaZe", before, now, [])
    assert (found.kind, found.level, found.title, found.body, found.about) == ("map", "key", "Vitality v FaZe: FaZe take map 1", "Vitality 0–1 FaZe", ("*",))
    standings = [{"id": "a", "name": "GodLike Esports", "place": 1, "points": 22}, {"id": "b", "name": "Team SouL", "place": 2, "points": 18}]
    lobby = lambda games: {"kind": "battle_royale", "win_word": "Booyah", "standings": standings, "games": games}  # noqa: E731
    [won] = sports.get("bgmi").game_moments("BGMI Masters", Played("live", lobby([])), Played("live", lobby([{"n": 1, "map": "Erangel", "winner": "a"}])), [])
    assert (won.kind, won.title, won.body) == ("dinner", "BGMI Masters: Booyah, GodLike Esports", "Match 1 · Erangel · GodLike Esports top, 22 pts")


def test_tennis_tells_each_set_once_from_the_side_that_won_it():
    sets = lambda a, b: {"headline": "", "stats": [{"label": "Sets", "value": f"{a}–{b}"}]}  # noqa: E731
    names = {"1": "Jannik Sinner", "2": "Carlos Alcaraz"}
    found = sports.get("tennis").game_moments("Sinner v Alcaraz", Played("live", {"kind": "tennis"}, lines={"1": sets(0, 0), "2": sets(0, 0)}, names=names),
                                              Played("live", {"kind": "tennis"}, lines={"1": sets(0, 1), "2": sets(1, 0)}, names=names), [])
    assert [f.title for f in found] == ["Sinner v Alcaraz: Carlos Alcaraz wins the 1st set"]


# ---------------------------------------------------------------- the noise rules for a followed game

GAME, KOHLI = 10, 1


def item(id: int, kind: str, title: str, *, player: int | None = None, about: tuple[str, ...] = ("*",), level: str = "key", priority: int = 70, ago: int = 0, corrects: int | None = None) -> Item:
    return Item(id, player, GAME, kind, level, priority, title, "IND 214/3", "/match/x/1", NOW - timedelta(seconds=ago), corrects, frozenset(about))


def test_a_followed_games_own_moments_reach_its_fans_by_their_level():
    batch = [item(1, "out", "India v Australia: Virat Kohli out for 82", about=("253802",)), item(2, "set", "A minor one", level="minor", priority=30)]
    [p] = plan(batch, {}, set(), [], NOW, games={GAME: Watch("key")})
    assert (p.tag, p.player_id, p.moment_ids, p.loud, p.title) == ("e10", None, [1], True, "India v Australia: Virat Kohli out for 82")
    assert plan(batch, {}, set(), [], NOW, games={GAME: Watch("results")}) == []
    assert plan(batch, {}, {GAME}, [], NOW, games={GAME: Watch("key")}) == []  # muted
    assert plan(batch, {}, set(), [], NOW) == []  # a game the fan doesn't follow


def test_a_lapsed_follow_sends_only_what_came_before_it_lapsed():
    result, after = item(1, "final", "India v Australia: India won by 6 wickets", ago=120), item(2, "correction", "Later", ago=0)
    [p] = plan([result, after], {}, set(), [], NOW, games={GAME: Watch("key", until=NOW - timedelta(seconds=60))})
    assert p.moment_ids == [1]


def test_a_fan_who_follows_the_game_and_a_player_in_it_hears_each_moment_once():
    theirs = item(1, "out", "Virat Kohli out for 82", player=KOHLI, about=("253802",))
    games_own = item(2, "out", "India v Australia: Virat Kohli out for 82", about=("253802",))
    other = item(3, "wickets", "India v Australia: Pat Cummins takes 3 wickets", about=("1",))
    [p] = plan([theirs, games_own, other], {KOHLI: "key"}, set(), [], NOW, games={GAME: Watch("key")})
    assert sorted(p.moment_ids) == [1, 3]  # Kohli's out as their player's; the game's own other moment beside it
    # The result is everyone's: the game's goes once, as their player's.
    [r] = plan([item(4, "final", "Virat Kohli: India won", player=KOHLI, about=("253802",)), item(5, "final", "India v Australia: India won")], {KOHLI: "key"}, set(), [], NOW, games={GAME: Watch("key")})
    assert r.moment_ids == [4]
    # A player set to results only doesn't hear their out: the game's own does say it.
    [g] = plan([theirs, games_own], {KOHLI: "results"}, set(), [], NOW, games={GAME: Watch("key")})
    assert g.moment_ids == [2]


# ---------------------------------------------------------------- from a follow to a push


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL or "postgresql+psycopg://")
    with engine.begin() as c:
        c.execute(text("TRUNCATE account, player, event, event_state, player_line, follow, match_follow, moment, notification, notification_mute, push_subscription, player_identity, player_card RESTART IDENTITY CASCADE"))
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

    for module in (live, match, structured, notify, pipeline):
        monkeypatch.setattr(module, "session", unit)
    s = made()
    yield s
    s.close()
    engine.dispose()


class Store(dict):
    """Redis as a dict, whose keys expire as they're told to by its own clock (seconds; a test moves it on)."""

    def __init__(self):
        super().__init__()
        self.clock, self.expiry = 0.0, {}

    def get(self, key, default=None):
        if key in self.expiry and self.expiry[key] <= self.clock:
            self.pop(key, None)
            self.expiry.pop(key)
        return super().get(key, default)

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False, get: bool = False):
        old = self.get(key)
        self[key] = value
        if ex:
            self.expiry[key] = self.clock + ex
        return old if get else True

    def exists(self, key: str) -> int:
        return int(self.get(key) is not None)

    def mget(self, keys: list[str]) -> list:
        return [self.get(k) for k in keys]

    def publish(self, channel: str, message: str) -> int:
        return 0


@pytest.fixture
def redis(monkeypatch):
    store = Store()
    monkeypatch.setattr(bus, "sync_redis", lambda: store)
    return store


@pytest.fixture
def source(monkeypatch):
    """A made-up football source that reads one game, and polls one by its id alone."""
    on = SimpleNamespace(game=None)
    fake = SimpleNamespace(system="pitch", sports=("football",), name="Pitch", fixture_pattern=DIGITS, live_cadence=15, match=lambda fixture: on.game,
                           locator=lambda fixture: {"fixture": fixture}, credit=lambda snap: "Pitch", live_note=None, lag_note=None, budgets=lambda: ())
    monkeypatch.setitem(ADAPTERS, "pitch", fake)
    monkeypatch.setitem(KNOWN, "pitch", fake)
    on.fake = fake
    return on


def game(fixture: str = "100", status: str = "live", start: datetime = NOW, fixtures: list[str] | None = None) -> LiveGame:
    return LiveGame(source_id=fixture, title="Arsenal vs Chelsea", competition="Premier League", start_utc=start,
                    snapshot=Snapshot(status=status, score_label="Arsenal 0 – 0 Chelsea", clock_label="10'", state=football(0, 0)), teams=["Arsenal", "Chelsea"],
                    fixtures=fixtures or [])


def named(source, fixture: str = "100", **kwargs) -> None:
    """The game read for its match page, as a fan opening it asks: what makes a source's game one here."""
    source.game = game(fixture, **kwargs)
    match.read("pitch", fixture, NOW)


@needs_db
def test_following_a_game_nobody_follows_a_player_in_makes_it_polled(db, redis, source):
    named(source)
    event_id = matches.follow(db, "fan", "pitch", "100")
    db.commit()
    event = db.get(Event, event_id)
    assert event is not None
    assert (event.key, event.sport, event.title, event.status, event.live_binding) == ("pitch:100", "Football", "Arsenal vs Chelsea", "live", {"adapter": "pitch", "locator": {"fixture": "100"}, "cadence": 15})
    assert matches.follow(db, "fan", "pitch", "100") == event_id and db.scalar(select(func.count()).select_from(MatchFollow)) == 1  # once
    page, _ = match.view(db, "fan", "pitch", "100", NOW)
    assert page is not None
    assert matches.offer(db, "fan", "pitch", "100", page) == {"following": True, "event_id": event_id, "alerts": True, "held": False, "note": None}
    assert matches.offer(db, "someone", "pitch", "100", page)["following"] is False
    # The tick polls it by itself, nobody's player in it, at its source's pace (one job a game: no lane).
    assert pipeline.due_work(NOW).polls == [event_id]
    [mine] = matches.mine(db, "fan")
    assert (mine["match"], mine["sport"], mine["status"], mine["live"], mine["alerts"]) == ("pitch/100", "football", "live", None, "key")
    matches.unfollow(db, "fan", "pitch", "100")
    db.commit()
    assert matches.mine(db, "fan") == [] and pipeline.due_work(NOW).polls == []


@needs_db
def test_a_made_up_address_a_game_over_or_one_no_source_polls_makes_no_follow(db, redis, source, monkeypatch):
    with pytest.raises(matches.Refused) as made_up:
        matches.follow(db, "fan", "pitch", "404")
    assert made_up.value.status == 404
    with pytest.raises(matches.Refused):
        matches.follow(db, "fan", "nowhere", "1")
    named(source, "200", status="final")
    assert matches.follow(db, "fan", "pitch", "200")  # a read that found it over isn't sure yet (a mini-match goes on)
    db.rollback()
    match.read("pitch", "200", NOW + timedelta(seconds=match.CONFIRM + 1))  # read again, still over: settled
    with pytest.raises(matches.Refused) as over:
        matches.follow(db, "fan", "pitch", "200")
    assert (over.value.status, over.value.message) == (409, matches.OVER)
    # A source that can poll a game only through a followed player's fixtures: the button says so.
    monkeypatch.delattr(source.fake, "locator")
    named(source, "300")
    page, _ = match.view(db, "fan", "pitch", "300", NOW)
    assert page is not None
    assert matches.offer(db, "fan", "pitch", "300", page) == {"following": False, "event_id": None, "alerts": False, "held": False, "note": matches.NO_ALERTS}
    with pytest.raises(matches.Refused) as cant:
        matches.follow(db, "fan", "pitch", "300")
    assert cant.value.status == 409
    assert db.scalar(select(func.count()).select_from(Event)) == 0 and db.scalar(select(func.count()).select_from(MatchFollow)) == 0


@needs_db
def test_a_fan_follows_at_most_max_games_at_once(db, redis, source, monkeypatch):
    monkeypatch.setattr(matches, "MAX", 2)
    for fixture in ("1", "2", "3"):
        named(source, fixture)
    matches.follow(db, "fan", "pitch", "1")
    matches.follow(db, "fan", "pitch", "2")
    with pytest.raises(matches.Refused) as full:
        matches.follow(db, "fan", "pitch", "3")
    assert full.value.status == 429
    matches.unfollow(db, "fan", "pitch", "1")
    matches.follow(db, "fan", "pitch", "3")
    assert [m["key"] for m in matches.mine(db, "fan")] == ["pitch:2", "pitch:3"]


def snap(home: int, away: int, clock: str, events: list[dict], saka: int | None, status: str = "live") -> Snapshot:
    lines = {"7": Line(headline=f"{saka} goal" if saka else "Started", stats=[{"label": "Goals", "value": str(saka)}])} if saka is not None else {}
    return Snapshot(status, f"Arsenal {home} – {away} Chelsea", clock, football(home, away), lines=lines, moments=events)


def listen(monkeypatch) -> list[tuple[str, str]]:
    """Every push sent, as (fan, title), from polls applied at once: polled all along, sent in the foreground."""
    pushed: list[tuple[str, str]] = []
    monkeypatch.setattr(structured, "_polled", lambda event_id, cadence: True)
    monkeypatch.setattr(notify, "dispatch", notify.send)
    monkeypatch.setattr(notify, "push", lambda endpoint, p256dh, auth, payload, ttl, topic=None: pushed.append((endpoint.rsplit("/", 1)[1], payload["title"])))
    monkeypatch.setattr(notify.bus, "publish", lambda player_id, event: None)
    monkeypatch.setattr(notify.bus, "publish_match", lambda event_id, event: None)
    monkeypatch.setattr(structured.bus, "publish_match", lambda event_id, event: None)
    return pushed


def saka_followed_by(db, event_id: int, *fans: str) -> None:
    """Saka (player 5, "7" in the source) in the game, followed by these fans; every fan named here or later has a phone."""
    if db.get(Player, 5) is None:
        db.add_all([Player(id=5, slug="saka", name="Bukayo Saka", sport="Football"), PlayerIdentity(player_id=5, system="pitch", external_id="7", confidence=1.0)])
        db.flush()
        db.add(EventPlayer(event_id=event_id, player_id=5))
    db.add_all([Follow(fan_id=f, player_id=5, alert_rules={}) for f in fans])
    db.commit()


def phones(db, *fans: str) -> None:
    db.add_all([PushSubscription(fan_id=f, endpoint=f"https://fcm.googleapis.com/fcm/send/{f}", p256dh="k", auth="a") for f in fans])
    db.commit()


@needs_db
def test_a_followed_games_own_moments_reach_its_fans_once_and_stop_when_it_ends(db, redis, source, monkeypatch):
    pushed: list[tuple[str, str]] = []
    toasts: list[tuple[int, str]] = []
    monkeypatch.setattr(structured, "_polled", lambda event_id, cadence: True)  # polled all along
    monkeypatch.setattr(notify, "dispatch", notify.send)  # at once, not in the background
    monkeypatch.setattr(notify, "push", lambda endpoint, p256dh, auth, payload, ttl, topic=None: pushed.append((endpoint.rsplit("/", 1)[1], payload["title"])))
    monkeypatch.setattr(notify.bus, "publish", lambda player_id, event: None)
    monkeypatch.setattr(notify.bus, "publish_match", lambda event_id, event: toasts.append((event_id, event.get("moment", {}).get("title") or event["type"])))
    monkeypatch.setattr(structured.bus, "publish_match", lambda event_id, event: toasts.append((event_id, event.get("moment", {}).get("title") or event["type"])))

    def heard() -> set[tuple[str, str]]:
        out = set(pushed)
        pushed.clear()
        return out

    named(source)
    event_id = matches.follow(db, "fan", "pitch", "100")
    matches.follow(db, "both", "pitch", "100")
    # "both" follows Saka too; "saka" only Saka. Each has a phone.
    db.add_all([Player(id=5, slug="saka", name="Bukayo Saka", sport="Football"), PlayerIdentity(player_id=5, system="pitch", external_id="7", confidence=1.0)])
    db.flush()
    db.add_all([EventPlayer(event_id=event_id, player_id=5), Follow(fan_id="both", player_id=5, alert_rules={}), Follow(fan_id="saka", player_id=5, alert_rules={})])
    db.add_all([PushSubscription(fan_id=f, endpoint=f"https://fcm.googleapis.com/fcm/send/{f}", p256dh="k", auth="a") for f in ("fan", "both", "saka")])
    db.commit()
    binding = {"adapter": "pitch", "locator": {"fixture": "100"}, "cadence": 15}

    def poll(s: Snapshot) -> None:
        structured.apply_poll(event_id, binding, source.fake, [(5, "7")], s)

    poll(snap(0, 0, "10'", [], 0))
    assert heard() == set()  # a first look
    saka = goal("23'", "Saka", "7")
    poll(snap(1, 0, "23'", [saka], 1))
    assert heard() == {("fan", "Arsenal v Chelsea: Saka puts Arsenal ahead"), ("both", "Goal: Bukayo Saka puts Arsenal ahead"), ("saka", "Goal: Bukayo Saka puts Arsenal ahead")}
    assert (event_id, "match") in toasts and (event_id, "Arsenal v Chelsea: Saka puts Arsenal ahead") in toasts  # the board, and the game's own toast
    poll(snap(1, 1, "60'", [goal("60'", "Palmer", "20", AWAY), saka], 1))
    assert heard() == {("fan", "Arsenal v Chelsea: Palmer equalises"), ("both", "Arsenal v Chelsea: Palmer equalises")}
    [board] = matches.mine(db, "fan")
    assert board["live"]["score"] == "Arsenal 1 – 1 Chelsea" and board["live"]["match"] == "pitch/100"
    poll(snap(1, 1, "FT", [goal("60'", "Palmer", "20", AWAY), saka], 1, status="final"))
    assert heard() == {("fan", "Arsenal v Chelsea: Draw"), ("both", "Bukayo Saka: Arsenal 1 – 1 Chelsea"), ("saka", "Bukayo Saka: Arsenal 1 – 1 Chelsea")}

    # The tick: the game's over, so its follows lapse, and nothing it says after reaches them.
    db.commit()
    pipeline.due_work()
    assert matches.mine(db, "fan") == [] and all(f.until is not None for f in db.scalars(select(MatchFollow)))
    db.commit()
    late = notify.record_game(db, event_id, [sports.Found("correction", "key", "Arsenal v Chelsea: Goal ruled out, Palmer", "", "goal:goal@60'", 85, ("20",))], "/match/pitch/100")
    db.commit()
    notify.send(late)
    assert heard() == set()

    # The Alerts list: the game's own moments while it was followed, but those a followed player's say too.
    assert [i["title"] for i in server.activity(None, 50, "fan", db)["items"]] == ["Arsenal v Chelsea: Draw", "Arsenal v Chelsea: Palmer equalises", "Arsenal v Chelsea: Saka puts Arsenal ahead"]
    both = server.activity(None, 50, "both", db)["items"]
    assert [i["title"] for i in both] == ["Bukayo Saka: Arsenal 1 – 1 Chelsea", "Arsenal v Chelsea: Palmer equalises", "Goal: Bukayo Saka puts Arsenal ahead"]
    assert [(i["player_id"], i.get("sport")) for i in both] == [(5, None), (None, "football"), (5, None)]
    pushes = db.scalars(select(Notification).where(Notification.fan_id == "fan")).all()
    assert [(n.player_id, n.event_id) for n in pushes] == [(None, event_id)] * 3  # the push log, led by the game's own


@needs_db
def test_a_followed_game_to_come_is_armed_and_lapses_if_it_never_starts(db, redis, source):
    named(source, start=NOW + timedelta(minutes=20), status="scheduled")
    event_id = matches.follow(db, "fan", "pitch", "100")
    db.commit()
    assert db.get(Event, event_id).status == "scheduled"  # type: ignore[union-attr]
    assert pipeline.due_work(NOW).polls == [event_id]  # armed at its sport's lead, and polled
    with db.begin_nested():
        db.get(Event, event_id).status = "postponed"  # type: ignore[union-attr]
    db.commit()
    pipeline.due_work(NOW + timedelta(hours=2))
    assert db.scalar(select(MatchFollow.until)) is None  # called off for now: it may be on again
    pipeline.due_work(NOW + matches.LAPSE_AFTER + timedelta(hours=1))
    db.expire_all()
    assert db.scalar(select(MatchFollow.until)) is not None


@needs_db
def test_a_fans_followed_games_move_with_their_account_and_go_with_it(db, redis, source):
    for fixture in ("1", "2"):
        named(source, fixture)
    matches.follow(db, "anon", "pitch", "1")
    matches.follow(db, "anon", "pitch", "2")
    matches.follow(db, "acct-1", "pitch", "2")
    db.add(Account(id=1, fan_id="acct-1", email="fan@example.com"))
    db.commit()
    accounts.adopt(db, "anon", 1, 0)
    db.commit()
    assert sorted(m["key"] for m in matches.mine(db, "acct-1")) == ["pitch:1", "pitch:2"] and matches.mine(db, "anon") == []
    accounts.delete_account(db, 1)
    db.commit()
    assert db.scalar(select(func.count()).select_from(MatchFollow)) == 0


@needs_db
def test_the_game_moments_are_kept_apart_from_its_players_on_the_match_page(db, redis, source):
    named(source)
    event_id = matches.follow(db, "fan", "pitch", "100")
    notify.record_game(db, event_id, [sports.Found("goal", "key", "Arsenal v Chelsea: Saka scores", "", "goal@23'", 85, ("7",))], "/match/pitch/100")
    db.commit()
    [moment] = db.scalars(select(Moment)).all()
    assert (moment.player_id, moment.key, moment.about, moment.url) == (None, f"{event_id}:game:goal:goal@23'", ["7"], "/match/pitch/100")
    page, _ = match.view(db, "fan", "pitch", "100", NOW)
    assert page is not None and page["moments"] == []  # its board and timeline say the game's own
    assert json.loads(redis["match:pitch:100"])["game"]["title"] == "Arsenal vs Chelsea"


@needs_db
def test_a_tests_second_innings_wickets_are_each_players_own_and_their_fans_hear_each_once(db, redis, source):
    # Day 5 of Bangladesh v Afghanistan as ESPN had it (tests/fixtures/espn/cricket-summary-test-live): Litton Das
    # reaches his second-innings hundred and is out, and Hasan Mahmud is out, in one poll.
    named(source)
    event_id = matches.follow(db, "fan", "pitch", "100")
    db.add_all([Player(id=9, slug="litton-das", name="Litton Das", sport="Cricket"), PlayerIdentity(player_id=9, system="pitch", external_id="536936", confidence=1.0)])
    db.commit()
    rules = sports.get("cricket")
    names = {"536936": "Litton Das", "926629": "Hasan Mahmud"}
    batting = lambda head, runs, out=None: {"headline": head, "stats": [{"label": "Runs", "value": runs}, *([{"label": "Out", "value": out}] if out else [])]}  # noqa: E731
    before = Played("live", {"kind": "cricket"}, lines={"536936": batting("53 (89) & 98* (110)", "98*"), "926629": batting("2 (5) & 1* (4)", "1*")}, names=names)
    now = Played("live", {"kind": "cricket"}, lines={"536936": batting("53 (89) & 102 (117)", "102", "c †Ikram Alikhil b Mohammad Saleem"),
                                                     "926629": batting("2 (5) & 1 (6)", "1", "lbw b Zia-ur-Rehman")}, names=names)
    found = rules.game_moments("Bangladesh v Afghanistan", before, now, [])
    assert [(f.title, f.about) for f in found] == [("Bangladesh v Afghanistan: Litton Das reaches 100", ("536936",)), ("Bangladesh v Afghanistan: Litton Das out for 102", ("536936",)),
                                                   ("Bangladesh v Afghanistan: Hasan Mahmud out for 1", ("926629",))]
    assert len(notify.record_game(db, event_id, found, "/match/pitch/100")) == 3  # each player's wicket its own
    # Litton Das's own: his first innings' out, and the second's as his fans heard them.
    notify.record(db, 9, event_id, [sports.Found("out", "key", "Litton Das out for 53", "", "out")])
    notify.record(db, 9, event_id, rules.during("Litton Das", "536936", before.seen("536936"), now.seen("536936")))
    db.commit()
    rows = db.scalars(select(Moment).order_by(Moment.id)).all()
    hundred, litton_out, mahmud_out, first_out, his_hundred, his_out = notify.for_games(db, notify.as_items(rows))
    assert [i.title for i in (first_out, his_hundred, his_out)] == ["Litton Das out for 53", "Litton Das reaches 100", "Litton Das out for 102"]
    # A fan who follows him and the game, told one side of a moment in an earlier push, isn't told the other side of it:
    # his hundred and second-innings out are his; his first-innings out isn't the game's second, nor his Mahmud's.
    now_ = datetime.now(timezone.utc)
    assert plan([his_hundred, his_out], {9: "key"}, set(), [], now_, games={event_id: Watch("key")}, heard=[hundred, litton_out]) == []
    assert plan([hundred, litton_out], {9: "key"}, set(), [], now_, games={event_id: Watch("key")}, heard=[his_hundred, his_out]) == []
    [told] = plan([litton_out, mahmud_out], {9: "key"}, set(), [], now_, games={event_id: Watch("key")}, heard=[first_out])
    assert told.moment_ids == [litton_out.id, mahmud_out.id]


@needs_db
def test_a_game_followed_again_after_a_while_doesnt_tell_what_happened_meanwhile(db, redis, source, monkeypatch):
    # A fan follows the game, and unfollows; Saka's fans keep it polled. The game's own last poll, kept to find
    # its next moments against, is too old by the time another fan follows it: that's a first look.
    pushed = listen(monkeypatch)
    named(source)
    event_id = matches.follow(db, "first", "pitch", "100")
    db.commit()
    saka_followed_by(db, event_id, "saka")
    phones(db, "first", "later", "saka")
    binding = {"adapter": "pitch", "locator": {"fixture": "100"}, "cadence": 15}
    poll = lambda s: structured.apply_poll(event_id, binding, source.fake, [(5, "7")], s)  # noqa: E731
    poll(snap(0, 0, "10'", [], 0))
    matches.unfollow(db, "first", "pitch", "100")
    db.commit()
    poll(snap(1, 0, "23'", [goal("23'", "Saka", "7")], 1))
    assert set(pushed) == {("saka", "Goal: Bukayo Saka puts Arsenal ahead")}
    pushed.clear()
    redis.clock += structured.POLL_GAP + 1
    matches.follow(db, "later", "pitch", "100")
    db.commit()
    poll(snap(1, 0, "60'", [goal("23'", "Saka", "7")], 1))
    poll(snap(2, 0, "61'", [goal("61'", "Kane", "9"), goal("23'", "Saka", "7")], 1))
    assert pushed == [("later", "Arsenal v Chelsea: Kane scores")]  # what came since they followed, not before


@needs_db
def test_a_game_here_as_two_events_is_followed_as_the_fans_players(db, redis, source):
    # A chess board is a fixture of each of its players: two events, one polled for each player's fans.
    source.game = game("100", fixtures=["100", "101"])
    match.read("pitch", "100", NOW)
    binding = lambda f: {"adapter": "pitch", "locator": {"fixture": f}, "cadence": 15}  # noqa: E731
    db.add_all([Event(id=1, key="pitch:100", sport="Football", title="A v B", status="live", live_binding=binding("100")),
                Event(id=2, key="pitch:101", sport="Football", title="A v B", status="live", live_binding=binding("101")),
                Player(id=5, slug="a", name="A", sport="Football"), Player(id=6, slug="b", name="B", sport="Football")])
    db.flush()
    db.add_all([EventPlayer(event_id=1, player_id=5), EventPlayer(event_id=2, player_id=6), Follow(fan_id="other", player_id=5, alert_rules={}), Follow(fan_id="fan", player_id=6, alert_rules={})])
    db.commit()
    assert matches.follow(db, "fan", "pitch", "100") == 2  # their player's: one notification, each moment once
    assert matches.follow(db, "someone", "pitch", "100") == 1  # nobody's of theirs: the first


@needs_db
def test_a_followed_game_of_a_source_polled_together_joins_its_lane_and_one_polled_alone_waits_for_room(db, redis, source, monkeypatch):
    named(source)
    event_id = matches.follow(db, "fan", "pitch", "100")
    db.commit()
    source.fake.spare = lambda: False  # down to what its live games need
    assert pipeline.due_work(NOW).polls == []  # nobody's player in it: it waits
    saka_followed_by(db, event_id, "saka")
    assert pipeline.due_work(NOW).polls == [event_id]  # a followed player's game keeps the reserve
    monkeypatch.setattr(source.fake, "snapshots", lambda locators: {}, raising=False)
    monkeypatch.setattr(source.fake, "spare", lambda: True)
    db.execute(text("DELETE FROM follow"))
    db.commit()
    due = pipeline.due_work(NOW)
    assert (due.lanes, due.polls) == (["pitch"], [])  # a source that fetches many games at once: its lane
    assert structured.lane_events("pitch") == [event_id]


@needs_db
def test_a_game_whose_sport_is_turned_off_mid_game_stops_saying_live_and_comes_back_with_it(db, redis, source, monkeypatch):
    # BUG-203 (H16): a game turned off in the panel mid-series stayed live, never polled: its fans' cards said LIVE with a
    # frozen score for up to six days, no end card came down their phones, and its follow counted toward the cap.
    ended: list[int] = []
    monkeypatch.setattr(livecards, "ended", lambda ids: ended.extend(ids))
    named(source)
    event_id = matches.follow(db, "fan", "pitch", "100")
    saka_followed_by(db, event_id, "saka")
    fresh = {"fixtures": NOW.isoformat()}
    db.add_all([SourceBinding(player_id=5, purpose="fixtures", adapter="pitch", priority=10, health={},
                              locator={"system": "pitch", "athlete_id": "7", "name": "Bukayo Saka", "team_ids": [HOME], "team_names": ["Arsenal"]}),
                PlayerCard(player_id=5, status="ready", version=1, is_live=True, freshness=fresh,
                           card={"live": {"is_live": True, "event_id": event_id, "score": "Arsenal 1 – 0 Chelsea", "clock": "23'"}, "upcoming": [], "recent_results": [], "freshness": fresh}),
                EventState(event_id=event_id, as_of=NOW, status="live", score_label="Arsenal 1 – 0 Chelsea", clock_label="23'", state=football(1, 0))])
    db.commit()
    assert pipeline.due_work(NOW).polls == [event_id]
    moments_before = db.scalar(select(func.count()).select_from(Moment))

    controls.turn_sport("football", False)
    due = pipeline.due_work(NOW)
    db.expire_all()
    card = db.get(PlayerCard, 5)
    assert card is not None and db.get(Event, event_id).status == "postponed"  # type: ignore[union-attr]  # put off, not over
    assert due.polls == [] and ended == [event_id]  # its live card comes down the phones
    assert (card.is_live, card.card["live"]["is_live"]) == (False, False)
    [mine] = matches.mine(db, "fan")
    assert mine["live"]["is_live"] is False and mine["live"]["score"] == "Arsenal 1 – 0 Chelsea"  # Today's board: no longer LIVE
    pipeline.due_work(NOW + timedelta(minutes=1))
    assert ended == [event_id]  # once

    # Back on: Saka's games are read again at the next tick, and that read brings the game back as its source has it.
    controls.turn_sport("football", True)
    due = pipeline.due_work(NOW + timedelta(minutes=5))
    assert due.polls == [] and due.refreshes == [5]
    over = Fixture("100", "Arsenal vs Chelsea", "Premier League", NOW, None, "final", [HOME, AWAY], {"fixture": "100"}, ["Arsenal", "Chelsea"])
    structured.upsert_game(db, source.fake, "Football", over)
    db.commit()
    # Over meanwhile: final, with nothing told for it (its result is old news by now: BUG-259), and its follow lapses.
    assert db.get(Event, event_id).status == "final"  # type: ignore[union-attr]
    pipeline.due_work(NOW + timedelta(minutes=6))
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Moment)) == moments_before and matches.mine(db, "fan") == []


@needs_db
def test_a_game_with_no_start_time_its_source_says_is_next_is_armed_and_its_start_told(db, redis, source, monkeypatch):
    # BUG-211 (H17): a chess round set to start after the previous one has no start time: it was never armed or polled,
    # so a game played between two refreshes never told its start, its swings or its result.
    listen(monkeypatch)
    db.add_all([Player(id=5, slug="saka", name="Bukayo Saka", sport="Football"), PlayerIdentity(player_id=5, system="pitch", external_id="7", confidence=1.0)])
    db.flush()
    db.add(Follow(fan_id="saka", player_id=5, alert_rules={}))
    db.commit()
    saka = db.get(Player, 5)
    assert saka is not None

    def read(source_id: str, next_up: bool) -> Event:
        fixture = Fixture(source_id, "Arsenal vs Chelsea", "Premier League", None, None, "scheduled", [HOME, AWAY], {"fixture": source_id}, ["Arsenal", "Chelsea"])
        fixture.next_up = next_up  # what the source says of it: the game before it has started
        event = structured.upsert_event(db, source.fake, saka, fixture)
        db.commit()
        return event

    event = read("100", False)
    assert event.status == "scheduled" and pipeline.due_work(NOW).polls == []  # no start, and not next: nothing arms it
    event = read("100", True)
    assert event.status == "armed" and pipeline.due_work(NOW).polls == [event.id]
    binding = {"adapter": "pitch", "locator": {"fixture": "100"}, "cadence": 15}
    structured.apply_poll(event.id, binding, source.fake, [(5, "7")], snap(0, 0, "", [], None, status="scheduled"))
    structured.apply_poll(event.id, binding, source.fake, [(5, "7")], snap(0, 0, "1'", [], 0))
    assert db.scalars(select(Moment.kind).where(Moment.event_id == event.id)).all() == ["start"]
    # One the source no longer says is next (the game before it ended hours ago) is disarmed; one armed as next that's
    # never brought to an end is closed six days after it was made, as a game with a start is six days after it.
    other = read("101", True)
    assert other.status == "armed"
    assert read("101", False).status == "scheduled" and pipeline.due_work(NOW).polls == [event.id]
    read("101", True)
    db.execute(text("UPDATE event SET created_at = :at WHERE id = :id"), {"at": NOW - timedelta(days=7), "id": other.id})
    db.commit()
    pipeline.due_work(NOW)
    db.expire_all()
    assert (db.get(Event, other.id).status, db.get(Event, event.id).status) == ("final", "live")  # type: ignore[union-attr]


@needs_db
def test_the_alerts_list_pages_through_players_and_games_moments_alike(db, redis, source):
    named(source)
    event_id = matches.follow(db, "fan", "pitch", "100")
    db.commit()
    saka_followed_by(db, event_id, "fan")
    for n in range(5):  # a player's moment, then a game's own, in turn, none the same as another
        notify.record(db, 5, event_id, [sports.Found("assist", "key", f"Assist {n}", "", f"a{n}")])
        notify.record_game(db, event_id, [sports.Found("red", "key", f"Red card {n}", "", f"r{n}", 80, ("99",))], "/match/pitch/100")
    db.commit()
    titles, before = [], None
    while True:
        page = server.activity(before, 3, "fan", db)
        titles += [i["title"] for i in page["items"]]
        if page["next"] is None:
            break
        before = page["next"]
    assert titles == [t for n in reversed(range(5)) for t in (f"Red card {n}", f"Assist {n}")]


@needs_db
def test_an_accounts_followed_games_stay_within_max_when_a_device_brings_more(db, redis, source, monkeypatch):
    monkeypatch.setattr(matches, "MAX", 2)
    for fixture, hours in (("1", 3), ("2", 1), ("3", 2)):
        named(source, fixture, start=NOW + timedelta(hours=hours), status="scheduled")
    matches.follow(db, "anon", "pitch", "1")
    matches.follow(db, "anon", "pitch", "2")
    matches.follow(db, "acct-1", "pitch", "3")
    db.add(Account(id=1, fan_id="acct-1", email="fan@example.com"))
    db.commit()
    accounts.adopt(db, "anon", 1, 0)
    db.commit()
    assert [m["key"] for m in matches.mine(db, "acct-1")] == ["pitch:2", "pitch:3"]  # the soonest that fit


@needs_db
def test_a_board_followed_then_a_player_on_it_followed_settles_on_the_players_event(db, redis, source, monkeypatch):
    # Follow the board first (nobody's player of the fan's in it: the first event), then tap a player on it,
    # whose fixture is the board's other event. The tick moves the follow there: one notification, each moment once.
    pushed = listen(monkeypatch)
    source.game = game("100", fixtures=["100", "101"])
    match.read("pitch", "100", NOW)
    binding = lambda f: {"adapter": "pitch", "locator": {"fixture": f}, "cadence": 15}  # noqa: E731
    db.add_all([Event(id=1, key="pitch:100", sport="Football", title="A v B", status="live", live_binding=binding("100")),
                Event(id=2, key="pitch:101", sport="Football", title="A v B", status="live", live_binding=binding("101")),
                Player(id=6, slug="b", name="Bo", sport="Football")])
    db.commit()
    assert matches.follow(db, "fan", "pitch", "100") == 1
    db.add_all([EventPlayer(event_id=2, player_id=6), PlayerIdentity(player_id=6, system="pitch", external_id="6", confidence=1.0), Follow(fan_id="fan", player_id=6, alert_rules={})])
    db.commit()
    phones(db, "fan")
    pipeline.due_work(NOW)
    assert db.scalar(select(MatchFollow.event_id)) == 2
    # The page at either address says so, and stops it by its event.
    for fixture in ("100", "101"):
        page, _ = match.view(db, "fan", "pitch", fixture, NOW)
        assert page is not None
        assert matches.offer(db, "fan", "pitch", fixture, page)["following"] and matches.offer(db, "fan", "pitch", fixture, page)["event_id"] == 2
    # Bo's result, and the game's own, at one poll of event 2: once, as theirs.
    structured.apply_poll(2, binding("101"), source.fake, [(6, "6")], Snapshot("live", "A 0 – 0 B", "80'", football(0, 0), lines={"6": Line("Started", [])}))
    structured.apply_poll(2, binding("101"), source.fake, [(6, "6")], Snapshot("final", "A 1 – 0 B", "FT", football(1, 0), lines={"6": Line("Started", [])}))
    assert [fan for fan, _ in pushed] == ["fan"]
    matches.unfollow_event(db, "fan", 2)
    db.commit()
    assert db.scalar(select(func.count()).select_from(MatchFollow)) == 0


@needs_db
def test_a_goal_is_told_once_though_its_minute_is_revised_or_its_scorers_line_comes_a_poll_later(db, redis, source, monkeypatch):
    pushed = listen(monkeypatch)
    named(source)
    event_id = matches.follow(db, "fan", "pitch", "100")
    matches.follow(db, "both", "pitch", "100")
    db.commit()
    saka_followed_by(db, event_id, "both")
    phones(db, "fan", "both")
    binding = {"adapter": "pitch", "locator": {"fixture": "100"}, "cadence": 15}
    poll = lambda s: structured.apply_poll(event_id, binding, source.fake, [(5, "7")], s)  # noqa: E731
    poll(snap(0, 0, "44'", [], 0))
    # The source's events have the goal (its play id 501) before Saka's line counts it.
    poll(snap(1, 0, "45'", [goal("45'", "Saka", "7", id="501")], 0))
    assert sorted(pushed) == [("both", "Arsenal v Chelsea: Saka puts Arsenal ahead"), ("fan", "Arsenal v Chelsea: Saka puts Arsenal ahead")]
    pushed.clear()
    # Then his line counts it, and the source revises its minute: the same play, told already.
    poll(snap(1, 0, "46'", [goal("45+2'", "Saka", "7", id="501")], 1))
    assert pushed == []
    # The other way round: his line counts his second before the source's events have it ("goal2" from his
    # side), then the events do (by its id): his fans who follow the game too hear it once.
    poll(snap(2, 0, "60'", [goal("45+2'", "Saka", "7", id="501")], 2))
    assert sorted(pushed) == [("both", "Goal: Bukayo Saka (2)")]
    pushed.clear()
    poll(snap(2, 0, "61'", [goal("60'", "Saka", "7", id="502"), goal("45+2'", "Saka", "7", id="501")], 2))
    assert pushed == [("fan", "Arsenal v Chelsea: Saka scores")]
    pushed.clear()
    # A card: the source's events name it a poll before his line counts it, which only counts it ("red1").
    red = {"clock": "70'", "kind": "RED", "text": "Saka (Arsenal)", "athlete_ids": ["7"], "team_id": HOME, "id": "601"}
    plays = [red, goal("60'", "Saka", "7", id="502"), goal("45+2'", "Saka", "7", id="501")]
    poll(snap(2, 0, "70'", plays, 2))
    assert sorted(pushed) == [("both", "Arsenal v Chelsea: Red card for Saka (Arsenal)"), ("fan", "Arsenal v Chelsea: Red card for Saka (Arsenal)")]
    pushed.clear()
    sent_off = Snapshot("live", "Arsenal 2 – 0 Chelsea", "71'", football(2, 0), lines={"7": Line("2 goals", [{"label": "Goals", "value": "2"}, {"label": "Red", "value": "1"}])}, moments=plays)
    poll(sent_off)
    assert pushed == []  # "Red card: Bukayo Saka", from his side: the same card


@needs_db
def test_a_game_armed_that_never_starts_lapses_a_day_past_its_start(db, redis, source):
    start = datetime.now(timezone.utc) - timedelta(minutes=10)  # the database's clock decides whether it's over
    named(source, start=start, status="scheduled")
    matches.follow(db, "fan", "pitch", "100")
    db.commit()
    db.execute(text("UPDATE event SET status = 'armed'"))  # polled, and its source still says it's to come
    db.commit()
    pipeline.due_work(start + timedelta(minutes=10))
    assert db.scalar(select(MatchFollow.until)) is None
    pipeline.due_work(start + matches.LAPSE_AFTER + timedelta(minutes=1))
    db.expire_all()
    assert db.scalar(select(MatchFollow.until)) is not None
    db.execute(text("UPDATE event SET start_utc = start_utc - interval '2 days'"))
    db.commit()
    with pytest.raises(matches.Refused):
        matches.follow(db, "other", "pitch", "100")  # a game that never came can't be followed again either


@needs_db
def test_a_followed_game_held_back_isnt_late_and_its_fans_are_told(db, redis, source, monkeypatch):
    named(source)
    event_id = matches.follow(db, "fan", "pitch", "100")
    db.commit()
    source.fake.spare = lambda: False
    monkeypatch.setattr(db_module, "session", structured.session)  # metrics.live's own session: the test database
    behind = lambda: sum(n for n, _ in metrics.live()["behind"])  # noqa: E731
    assert behind() == 0  # held back on purpose: not late
    page, _ = match.view(db, "fan", "pitch", "100", NOW)
    assert page is not None
    assert matches.offer(db, "fan", "pitch", "100", page) | {} == {"following": True, "event_id": event_id, "alerts": True, "held": True, "note": matches.HELD}
    assert matches.mine(db, "fan")[0]["held"] is True
    source.fake.spare = lambda: True
    assert behind() == 1 and matches.mine(db, "fan")[0]["held"] is False  # room again: polled, and late until it is


@needs_db
def test_a_polls_own_moments_are_kept_as_its_baseline_only_once_committed(db, redis, source, monkeypatch):
    named(source)
    event_id = matches.follow(db, "fan", "pitch", "100")
    db.commit()
    monkeypatch.setattr(structured, "_polled", lambda event_id, cadence: True)

    def fails(*args, **kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(notify, "record_game", fails)
    with pytest.raises(RuntimeError):
        structured.apply_poll(event_id, {"adapter": "pitch", "locator": {"fixture": "100"}, "cadence": 15}, source.fake, [], snap(0, 0, "10'", [], None))
    assert f"game:{event_id}" not in redis


def test_lichess_keeps_a_reserve_for_followed_players_boards():
    # With a token or without (the allowance is the token's or Lichess's anonymous one): up to its reserve.
    adapter, kept = lichess_chess.LichessChess(), lichess_chess.BUDGET
    [window] = kept.windows
    assert kept.reserve > 0 and adapter.spare()
    with budget.in_lane("live"):
        for _ in range(window.requests - window.margin - kept.reserve):
            budget.take(kept)
    assert not adapter.spare()  # what's left is the live boards': a board nobody follows a player in waits
    with budget.in_lane("live"):
        budget.take(kept)  # which a followed player's board may still spend


@needs_db
def test_a_game_nobody_follows_a_player_in_waits_at_every_round_while_its_source_is_short(db, redis, source, monkeypatch):
    named(source)
    event_id = matches.follow(db, "fan", "pitch", "100")
    db.commit()
    read: list[dict] = []
    source.fake.snapshot = lambda locator, final=False: read.append(locator) or snap(0, 0, "10'", [], None)
    source.fake.spare = lambda: False
    assert structured.poll_once(event_id) == ("idle", 0, False) and read == []  # a poll already going stops too
    source.fake.spare = lambda: True
    assert structured.poll_once(event_id)[0] == "live" and read == [{"fixture": "100"}]


@needs_db
def test_a_board_followed_before_anything_listed_its_other_address_still_settles_and_stays_muted(db, redis, source, monkeypatch):
    # Followed from a list that names one fixture of the board; Live now (or its match page) later names both.
    source.game = game("100")
    match.read("pitch", "100", NOW)
    binding = lambda f: {"adapter": "pitch", "locator": {"fixture": f}, "cadence": 15}  # noqa: E731
    db.add_all([Event(id=1, key="pitch:100", sport="Football", title="A v B", status="live", live_binding=binding("100")),
                Event(id=2, key="pitch:101", sport="Football", title="A v B", status="live", live_binding=binding("101")),
                Player(id=6, slug="b", name="Bo", sport="Football")])
    db.commit()
    assert matches.follow(db, "fan", "pitch", "100") == 1
    db.add_all([EventPlayer(event_id=2, player_id=6), Follow(fan_id="fan", player_id=6, alert_rules={}), NotificationMute(fan_id="fan", event_id=1)])
    db.commit()
    pipeline.due_work(NOW)
    assert db.scalar(select(MatchFollow.event_id)) == 1  # nothing has said 101 is this board yet
    source.game = game("100", fixtures=["100", "101"])
    match.read("pitch", "100", NOW + timedelta(minutes=2))
    pipeline.due_work(NOW)
    db.expire_all()
    assert db.scalar(select(MatchFollow.event_id)) == 2
    assert set(db.scalars(select(NotificationMute.event_id).where(NotificationMute.fan_id == "fan"))) == {1, 2}  # still muted


@needs_db
def test_a_players_goal_isnt_taken_for_an_own_goal_or_a_goal_listed_again_long_before(db, redis, source, monkeypatch):
    # A fan follows the game and Gabriel; "saka" follows Saka. Neither an own goal of Gabriel's earlier nor a goal of
    # Saka's the source dropped and listed again (told again by the game) may hide a real goal later: twice beats never.
    pushed = listen(monkeypatch)
    named(source)
    event_id = matches.follow(db, "both", "pitch", "100")
    db.commit()
    saka_followed_by(db, event_id, "both")
    db.add_all([Player(id=4, slug="gabriel", name="Gabriel", sport="Football"), PlayerIdentity(player_id=4, system="pitch", external_id="4", confidence=1.0)])
    db.flush()
    db.add_all([EventPlayer(event_id=event_id, player_id=4), Follow(fan_id="both", player_id=4, alert_rules={})])
    db.commit()
    phones(db, "both")
    binding = {"adapter": "pitch", "locator": {"fixture": "100"}, "cadence": 15}

    def poll(home: int, away: int, clock: str, plays: list[dict], gabriel: int, saka: int) -> None:
        lines = {"4": Line("Started", [{"label": "Goals", "value": str(gabriel)}]), "7": Line("Started", [{"label": "Goals", "value": str(saka)}])}
        structured.apply_poll(event_id, binding, source.fake, [(4, "4"), (5, "7")], Snapshot("live", f"Arsenal {home} – {away} Chelsea", clock, football(home, away), lines=lines, moments=plays))

    poll(0, 0, "10'", [], 0, 0)
    own = goal("20'", "Gabriel", "4", own_goal=True, id="701")
    poll(0, 1, "20'", [own], 0, 0)
    assert pushed == [("both", "Arsenal v Chelsea: Own goal by Gabriel")]
    pushed.clear()
    poll(1, 1, "30'", [own], 1, 0)  # his own goal at the other end: his line counts it before the events list it
    assert pushed == [("both", "Goal: Gabriel")]
    pushed.clear()

    saka = goal("40'", "Saka", "7", id="501")
    poll(2, 1, "40'", [saka, own], 1, 1)
    assert pushed == [("both", "Goal: Bukayo Saka puts Arsenal ahead")]  # once, as his
    pushed.clear()
    poll(2, 1, "41'", [{**saka, "id": "503"}, own], 1, 1)  # the source drops it and lists it again, a new id
    assert pushed == [("both", "Arsenal v Chelsea: Saka puts Arsenal ahead")]  # told again by the game: twice beats never
    pushed.clear()
    db.execute(text("UPDATE moment SET created_at = created_at - interval '20 minutes'"))  # all that was a while ago
    db.commit()
    poll(3, 1, "75'", [{**saka, "id": "503"}, own], 1, 2)  # his real second, which his line counts first
    assert pushed == [("both", "Goal: Bukayo Saka (2)")]


@needs_db
def test_one_read_of_several_boards_teaches_each_follow_its_own_boards_addresses(db, redis, source):
    binding = lambda f: {"adapter": "pitch", "locator": {"fixture": f}, "cadence": 15}  # noqa: E731
    db.add_all([Event(id=n, key=f"pitch:{f}", sport="Football", title="A v B", status="live", live_binding=binding(f)) for n, f in ((1, "100"), (2, "200"))])
    db.add_all([MatchFollow(fan_id="fan", event_id=1, alert_rules={}, keys=["pitch:100"]), MatchFollow(fan_id="other", event_id=2, alert_rules={}, keys=["pitch:200"])])
    db.commit()
    matches.learn(db, "pitch", [["100", "101"], ["200", "201"], ["300"]])  # Live now's boards: each its two fixtures
    db.commit()
    assert dict(db.execute(select(MatchFollow.fan_id, MatchFollow.keys)).all()) == {"fan": ["pitch:100", "pitch:101"], "other": ["pitch:200", "pitch:201"]}
