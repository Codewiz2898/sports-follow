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

from sports_follow import accounts, bus, live, match, matches, notify, pipeline, server, sports, structured
from sports_follow.adapters import ADAPTERS, KNOWN, Line, LiveGame, Snapshot
from sports_follow.adapters.base import DIGITS
from sports_follow.alerts import Item, Watch, plan
from sports_follow.models import Account, Event, EventPlayer, Follow, MatchFollow, Moment, Notification, Player, PlayerIdentity, PushSubscription
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
        ("yellow", "minor", "Arsenal v Chelsea: Yellow card for Rice (Arsenal)", ("41",)), ("goal", "key", "Arsenal v Chelsea: Own goal by Gabriel", ("4",))]


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
    """Redis as a dict."""

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False, get: bool = False):
        old = self.get(key)
        self[key] = value
        return old if get else True

    def exists(self, key: str) -> int:
        return int(key in self)


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


def game(fixture: str = "100", status: str = "live", start: datetime = NOW) -> LiveGame:
    return LiveGame(source_id=fixture, title="Arsenal vs Chelsea", competition="Premier League", start_utc=start,
                    snapshot=Snapshot(status=status, score_label="Arsenal 0 – 0 Chelsea", clock_label="10'", state=football(0, 0)), teams=["Arsenal", "Chelsea"])


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
    assert matches.offer(db, "fan", "pitch", "100", page) == {"following": True, "alerts": True, "note": None}
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
    with pytest.raises(matches.Refused) as over:
        matches.follow(db, "fan", "pitch", "200")
    assert (over.value.status, over.value.message) == (409, matches.OVER)
    # A source that can poll a game only through a followed player's fixtures: the button says so.
    monkeypatch.delattr(source.fake, "locator")
    named(source, "300")
    page, _ = match.view(db, "fan", "pitch", "300", NOW)
    assert page is not None
    assert matches.offer(db, "fan", "pitch", "300", page) == {"following": False, "alerts": False, "note": matches.NO_ALERTS}
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
